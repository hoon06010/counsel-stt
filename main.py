import json
import os
import queue
import sys
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path

from faster_whisper import WhisperModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
import ollama

# ── 설정 ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path.home() / "상담"
RECORDINGS_DIR = BASE_DIR / "recordings"
RESULTS_DIR = BASE_DIR / "results"
PROCESSED_FILE = BASE_DIR / "processed.json"
LOG_FILE = BASE_DIR / "log.txt"
LOG_MAX_BYTES = 5 * 1024 * 1024

WHISPER_MODEL_SIZE = "large-v3"
OLLAMA_MODEL = "exaone3.5:7.8b"
LANGUAGE = "ko"

# 이 값을 넘는 구간은 발화가 아닌 것으로 보고 버린다 (무음 환각 방지).
# 실측: 실제 발화 0.002~0.004, 무음에서 지어낸 문장 0.825
NO_SPEECH_THRESHOLD = 0.6

# Syncthing이 파일 쓰기를 마쳤는지 확인하는 설정
# FILE_STABILITY_INTERVAL 초 간격으로 FILE_STABILITY_ROUNDS 회 연속 크기가 같으면 완료로 판단
FILE_STABILITY_INTERVAL = 2   # 초
FILE_STABILITY_ROUNDS = 3     # 연속 확인 횟수
FILE_STABILITY_MAX_WAIT = 120 # 최대 대기 시간 (초)

WORK_START = dtime(9, 0)
WORK_END = dtime(20, 0)
WORK_DAYS = frozenset({0, 1, 2, 3, 4, 5, 6})  # 매일 (월~일)

POLL_INTERVAL = 30    # 업무시간 중 루프 주기 (초)
RESCAN_INTERVAL = 600 # 폴더 재스캔 주기 (초) — 놓친 이벤트/실패 파일 회수

SYSTEM_PROMPT = (
    "당신은 정신과 상담 내용을 요약하는 전문 보조 도구입니다. "
    "주어진 상담 대화 텍스트를 한국어로 요약해 주세요. "
    "반드시 텍스트에 명시된 내용만 사용하고, "
    "텍스트에 없는 내용은 절대 추가하거나 추론하지 마세요."
)
# ─────────────────────────────────────────────────────────────────────────────


class _Tee:
    """터미널과 로그 파일에 동시에 출력. pythonw 실행 시에도 로그가 남는다."""

    def __init__(self, stream, log_handle):
        self._stream = stream
        self._log = log_handle
        self._at_line_start = True

    def write(self, data: str) -> int:
        if self._stream is not None:
            try:
                self._stream.write(data)
                self._stream.flush()
            except Exception:
                pass

        for part in data.splitlines(keepends=True):
            if self._at_line_start and part.strip():
                self._log.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S "))
            self._log.write(part)
            self._at_line_start = part.endswith("\n")
        self._log.flush()
        return len(data)

    def flush(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass
        self._log.flush()

    # ── 파일 객체 흉내 ───────────────────────────────────────────────────────
    # huggingface_hub의 tqdm 진행바가 sys.stderr의 아래 속성들을 조회한다.
    # 없으면 Whisper 모델 최초 다운로드 중 AttributeError로 죽는다.

    def isatty(self) -> bool:
        # False를 반환해야 tqdm이 진행바 대신 조용한 출력을 쓴다 (로그 폭증 방지)
        return False

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", None) or "utf-8"

    @property
    def errors(self) -> str:
        return getattr(self._stream, "errors", None) or "replace"

    def fileno(self) -> int:
        if self._stream is not None:
            try:
                return self._stream.fileno()
            except Exception:
                pass
        # pythonw에는 표준 스트림이 없다. 로그 핸들의 fd로 대신한다.
        return self._log.fileno()

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        # sys.stdout을 닫아버리면 이후 print가 모두 실패하므로 flush만 한다
        self.flush()


def setup_logging() -> None:
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
        old = LOG_FILE.with_suffix(".old.txt")
        old.unlink(missing_ok=True)
        LOG_FILE.rename(old)

    handle = open(LOG_FILE, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, handle)
    sys.stderr = _Tee(sys.stderr, handle)


# 테스트용 스위치. COUNSEL_STT_ALWAYS_ON=1 이면 업무시간 게이트를 통과시킨다.
# 테스트하려고 WORK_START/WORK_END 상수를 고쳤다가 원복을 잊는 사고를 막는다.
# 실사용에서는 이 변수를 설정하지 않는다.
ALWAYS_ON = os.environ.get("COUNSEL_STT_ALWAYS_ON") == "1"


def is_work_hours() -> bool:
    if ALWAYS_ON:
        return True
    now = datetime.now()
    return now.weekday() in WORK_DAYS and WORK_START <= now.time() < WORK_END


_lock = threading.Lock()
_model_lock = threading.Lock()
_whisper_model: WhisperModel | None = None


def get_whisper_model() -> WhisperModel:
    global _whisper_model
    with _model_lock:
        if _whisper_model is None:
            print("[초기화] Whisper large-v3 모델 로딩 중 (첫 실행 시 시간이 걸립니다)...")
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device="cpu",
                compute_type="int8",
            )
            print("[초기화] 모델 로딩 완료")
        return _whisper_model


def load_processed() -> set[str]:
    """처리 이력을 읽는다. 손상되어 있으면 경고 후 빈 이력으로 시작한다.

    이 함수가 예외를 던지면 pythonw 실행 시 화면에 아무것도 남기지 않고
    죽어버리므로, 어떤 경우에도 set을 반환해야 한다.
    """
    if not PROCESSED_FILE.exists():
        return set()

    try:
        # utf-8-sig: 메모장으로 직접 편집하면 BOM이 붙는다. BOM이 없어도 그대로 읽힌다.
        # utf-8로 읽으면 BOM을 손상으로 판정해 이력을 통째로 버린다.
        with open(PROCESSED_FILE, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"[경고] {PROCESSED_FILE.name} 읽기 실패 ({e}) — 빈 이력으로 시작합니다")
        return set()

    if not isinstance(data, list):
        print(f"[경고] {PROCESSED_FILE.name} 형식이 리스트가 아님 — 빈 이력으로 시작합니다")
        return set()

    names = {x for x in data if isinstance(x, str)}
    if len(names) != len(data):
        print(f"[경고] {PROCESSED_FILE.name}에 문자열이 아닌 항목이 있어 무시했습니다")
    return names


def save_processed(processed: set[str]) -> None:
    """임시 파일에 쓴 뒤 os.replace로 교체 — 쓰기 도중 종료돼도 원본이 남는다."""
    tmp = PROCESSED_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(processed), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PROCESSED_FILE)
    except OSError as e:
        print(f"[오류] 처리 이력 저장 실패: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def wait_for_stable(path: Path) -> bool:
    """파일 크기가 FILE_STABILITY_ROUNDS 회 연속 동일할 때까지 대기."""
    prev_size = -1
    stable_count = 0
    elapsed = 0

    while elapsed < FILE_STABILITY_MAX_WAIT:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False

        if size > 0 and size == prev_size:
            stable_count += 1
            if stable_count >= FILE_STABILITY_ROUNDS:
                return True
        else:
            stable_count = 0

        prev_size = size
        time.sleep(FILE_STABILITY_INTERVAL)
        elapsed += FILE_STABILITY_INTERVAL

    return False


def run_stt(audio_path: Path) -> str:
    """음성을 텍스트로 변환. 발화가 없으면 빈 문자열을 반환한다.

    무음 구간에서 Whisper는 학습 데이터에 흔한 문구("시청해주셔서 감사합니다." 등)를
    지어낸다. 그대로 두면 빈 결과 가드를 통과해 요약까지 만들어지므로 두 겹으로 막는다.
      1) vad_filter — 발화 없는 구간을 모델에 아예 넣지 않는다 (실측: 환각 완전 제거)
      2) no_speech_prob — VAD를 통과한 잔여 환각을 걸러낸다
         (실측: 실제 발화 0.002~0.004, 환각 0.825)
    condition_on_previous_text=False는 같은 문장을 반복 생성하는 루프를 줄인다.
    """
    print(f"[STT] 변환 중... ({audio_path.name})")
    model = get_whisper_model()
    segments, _ = model.transcribe(
        str(audio_path),
        language=LANGUAGE,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    kept, dropped = [], 0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if seg.no_speech_prob > NO_SPEECH_THRESHOLD:
            dropped += 1
            continue
        kept.append(text)

    if dropped:
        print(f"[STT] 발화로 보기 어려운 구간 {dropped}개 제외 ({audio_path.name})")
    print(f"[STT] 완료 ({audio_path.name})")
    return "\n".join(kept)


def run_summary(text: str, filename: str) -> str:
    print(f"[요약] 요약 중... ({filename})")
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"다음 상담 내용을 요약해 주세요:\n\n{text}"},
        ],
    )
    summary = (response.message.content or "").strip()
    if not summary:
        raise RuntimeError("요약 응답이 비어 있음")
    print(f"[요약] 완료 ({filename})")
    return summary


def _record_failure(name: str) -> None:
    """실패를 기록하고 남은 재시도 횟수를 알린다.

    processed에는 넣지 않으므로 주기 재스캔이 다시 큐에 넣는다.
    MAX_RETRIES를 넘기면 _try_queue가 걸러낸다.
    """
    with _lock:
        count = _failures.get(name, 0) + 1
        _failures[name] = count
    if count >= MAX_RETRIES:
        print(f"[중단] {name} — {count}회 실패, 더 이상 재시도하지 않습니다")
    else:
        print(f"[재시도 예정] {name} — {count}/{MAX_RETRIES}회 실패")


def process_file(audio_path: Path, processed: set[str]) -> None:
    name = audio_path.name
    print(f"[감지] {name}")

    if not wait_for_stable(audio_path):
        print(f"[오류] 파일 안정화 시간 초과, 건너뜀: {name}")
        _record_failure(name)
        return

    stem = audio_path.stem
    txt_path = RESULTS_DIR / f"{stem}.txt"

    # 요약만 실패해 재시도로 돌아온 경우, 이미 만들어 둔 텍스트를 재사용한다.
    # STT는 수십 분이 걸리므로 다시 돌리지 않는다.
    transcript = ""
    try:
        if txt_path.exists():
            transcript = txt_path.read_text(encoding="utf-8").strip()
            if transcript:
                print(f"[재시도] {name} — 기존 변환 텍스트를 재사용합니다")
    except OSError as e:
        print(f"[경고] 기존 텍스트 읽기 실패 ({name}): {e} — 다시 변환합니다")
        transcript = ""

    if not transcript:
        try:
            transcript = run_stt(audio_path)
        except Exception as e:
            print(f"[오류] STT 실패 ({name}): {e}")
            _record_failure(name)
            return

    if not transcript.strip():
        print(f"[건너뜀] {name} — STT 결과 없음 (무음/녹음 실패 추정)")
        with _lock:
            processed.add(name)
            save_processed(processed)
        return

    try:
        txt_path.write_text(transcript, encoding="utf-8")
    except Exception as e:
        print(f"[오류] 텍스트 저장 실패 ({name}): {e}")
        _record_failure(name)
        return

    try:
        summary = run_summary(transcript, name)
        summary_path = RESULTS_DIR / f"{stem}_요약.txt"
        summary_path.write_text(summary, encoding="utf-8")
    except Exception as e:
        # processed에 넣지 않는다. 주기 재스캔이 요약만 다시 시도한다
        # (위에서 기존 .txt를 재사용하므로 STT는 건너뛴다).
        print(f"[오류] 요약 실패 ({name}): {e}")
        print(f"[완료] {stem}.txt 저장됨 (요약은 나중에 다시 시도)")
        _record_failure(name)
        return

    with _lock:
        processed.add(name)
        save_processed(processed)
    print(f"[완료] {stem}.txt / {stem}_요약.txt 저장됨")


_work_queue: "queue.Queue[tuple[Path, RecordingHandler]]" = queue.Queue()


def worker_loop() -> None:
    """큐에 쌓인 녹음 파일을 한 번에 하나씩 처리.

    Whisper large-v3는 CPU/메모리를 모두 점유하고 WhisperModel 인스턴스는
    동시 transcribe 호출에 안전하지 않으므로 반드시 직렬로 처리한다.
    """
    while True:
        audio_path, handler = _work_queue.get()
        try:
            process_file(audio_path, handler.processed)
        except Exception as e:
            print(f"[오류] 처리 중 예외 ({audio_path.name}): {e}")
            _record_failure(audio_path.name)
        finally:
            handler._release(audio_path.name)
            _work_queue.task_done()


# handler는 업무시간 경계마다 새로 생성되지만 큐에는 이전 handler가 넣은 파일이
# 남아 있을 수 있다. 진행 중 목록을 인스턴스에 두면 새 handler가 같은 파일을
# 다시 큐에 넣어 중복 처리된다. 모듈 전역으로 두어 handler 수명과 분리한다.
_in_progress: set[str] = set()

# STT/저장 실패 횟수. MAX_RETRIES 초과 시 더 이상 재시도하지 않는다.
_failures: dict[str, int] = {}
MAX_RETRIES = 3


class RecordingHandler(FileSystemEventHandler):
    def __init__(self, processed: set[str]):
        self.processed = processed

    def _try_queue(self, path: Path) -> bool:
        """중복 처리를 막고 처리 대상이면 True 반환."""
        if path.suffix.lower() != ".m4a":
            return False
        name = path.name
        with _lock:
            if name in self.processed or name in _in_progress:
                return False
            if _failures.get(name, 0) >= MAX_RETRIES:
                return False
            _in_progress.add(name)
        return True

    def _release(self, name: str) -> None:
        with _lock:
            _in_progress.discard(name)

    def _try_queue_and_put(self, path: Path) -> bool:
        """처리 대상이면 큐에 넣고 True 반환."""
        if not self._try_queue(path):
            return False
        _work_queue.put((path, self))
        return True

    def _dispatch(self, path: Path) -> None:
        self._try_queue_and_put(path)

    def on_created(self, event):
        if not event.is_directory:
            self._dispatch(Path(event.src_path))

    def on_moved(self, event):
        # Syncthing은 임시 파일을 최종 파일명으로 rename한다
        if not event.is_directory:
            self._dispatch(Path(event.dest_path))


def scan_existing(handler: RecordingHandler, quiet: bool = False) -> None:
    """미처리 파일을 큐에 넣는다.

    시작 시 한 번, 이후 RESCAN_INTERVAL 간격으로 호출된다. 주기 호출은
    watchdog이 놓친 이벤트와 실패 후 방치된 파일(A-6)을 회수하는 역할을 한다.
    중복은 _try_queue가 processed/_in_progress로 걸러낸다.

    glob 직후 Syncthing이 파일을 지우면 f.stat()이 FileNotFoundError를 던진다.
    이 함수는 메인 스레드에서 호출되므로 예외가 새면 프로세스가 죽는다.
    """
    try:
        candidates = []
        for f in RECORDINGS_DIR.glob("*.m4a"):
            if f.name in handler.processed:
                continue
            try:
                candidates.append((f.stat().st_mtime, f))
            except OSError:
                continue  # 스캔 도중 사라진 파일 — 다음 스캔에서 다시 본다
        existing = [f for _, f in sorted(candidates, key=lambda x: x[0])]
    except Exception as e:
        print(f"[오류] 폴더 스캔 실패: {e}")
        return

    queued = [f for f in existing if handler._try_queue_and_put(f)]
    if not queued:
        return
    if quiet:
        print(f"[재스캔] 미처리 파일 {len(queued)}개를 큐에 넣었습니다")
    else:
        print(f"[시작] 미처리 파일 {len(queued)}개 발견, 순차 처리합니다...")


def main():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    setup_logging()

    threading.Thread(target=worker_loop, daemon=True).start()

    processed = load_processed()
    print(f"처리 완료 기록: {len(processed)}개")
    print(f"감시 폴더: {RECORDINGS_DIR}")
    print(f"로그 파일: {LOG_FILE}")
    print(f"업무 시간: 매일 {WORK_START.strftime('%H:%M')}~{WORK_END.strftime('%H:%M')} 자동 실행")
    print("종료하려면 Ctrl+C\n")

    observer = None
    # handler는 한 번만 만들어 재사용한다. 매일 새로 만들면 큐에 남은 파일을
    # 다시 큐에 넣을 여지가 생긴다 (진행 중 목록은 전역이라 안전하지만,
    # processed 참조까지 매번 새로 엮을 이유가 없다).
    handler = RecordingHandler(processed)
    last_scan = 0.0
    try:
        while True:
            if is_work_hours():
                if observer is None:
                    print(f"[시작] {datetime.now().strftime('%m/%d %H:%M')} 업무 시간 — 감시 시작")
                    observer = Observer()
                    observer.schedule(handler, str(RECORDINGS_DIR), recursive=False)
                    observer.start()
                    scan_existing(handler)
                    last_scan = time.monotonic()
                elif time.monotonic() - last_scan >= RESCAN_INTERVAL:
                    scan_existing(handler, quiet=True)
                    last_scan = time.monotonic()
                time.sleep(POLL_INTERVAL)
            else:
                if observer is not None:
                    print(f"[대기] {datetime.now().strftime('%m/%d %H:%M')} 업무 시간 종료 — 내일 {WORK_START.strftime('%H:%M')}에 재시작")
                    observer.stop()
                    observer.join()
                    observer = None
                    if _work_queue.qsize():
                        print(f"[대기] 남은 {_work_queue.qsize()}개는 계속 처리합니다")
                time.sleep(60)
    except KeyboardInterrupt:
        print("\n종료 중...")
        if _work_queue.qsize():
            print(f"[주의] 미처리 {_work_queue.qsize()}개는 다음 실행 시 다시 처리됩니다")
        if observer is not None:
            observer.stop()
            observer.join()
    print("종료됨")


if __name__ == "__main__":
    main()
