import json
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

WHISPER_MODEL_SIZE = "large-v3"
OLLAMA_MODEL = "exaone3.5:7.8b"
LANGUAGE = "ko"

# Syncthing이 파일 쓰기를 마쳤는지 확인하는 설정
# FILE_STABILITY_INTERVAL 초 간격으로 FILE_STABILITY_ROUNDS 회 연속 크기가 같으면 완료로 판단
FILE_STABILITY_INTERVAL = 2   # 초
FILE_STABILITY_ROUNDS = 3     # 연속 확인 횟수
FILE_STABILITY_MAX_WAIT = 120 # 최대 대기 시간 (초)

WORK_START = dtime(9, 0)
WORK_END = dtime(18, 0)
WORK_DAYS = frozenset({0, 1, 2, 3, 4})  # 월~금

SYSTEM_PROMPT = (
    "당신은 정신과 상담 내용을 요약하는 전문 보조 도구입니다. "
    "주어진 상담 대화 텍스트를 한국어로 요약해 주세요. "
    "반드시 텍스트에 명시된 내용만 사용하고, "
    "텍스트에 없는 내용은 절대 추가하거나 추론하지 마세요."
)
# ─────────────────────────────────────────────────────────────────────────────


def is_work_hours() -> bool:
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
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_processed(processed: set[str]) -> None:
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, ensure_ascii=False, indent=2)


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
    print(f"[STT] 변환 중... ({audio_path.name})")
    model = get_whisper_model()
    segments, _ = model.transcribe(str(audio_path), language=LANGUAGE)
    text = "\n".join(seg.text.strip() for seg in segments if seg.text.strip())
    print(f"[STT] 완료 ({audio_path.name})")
    return text


def run_summary(text: str, filename: str) -> str:
    print(f"[요약] 요약 중... ({filename})")
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"다음 상담 내용을 요약해 주세요:\n\n{text}"},
        ],
    )
    summary = response.message.content
    print(f"[요약] 완료 ({filename})")
    return summary


def process_file(audio_path: Path, processed: set[str]) -> None:
    name = audio_path.name
    print(f"[감지] {name}")

    if not wait_for_stable(audio_path):
        print(f"[오류] 파일 안정화 시간 초과, 건너뜀: {name}")
        return

    stem = audio_path.stem

    try:
        transcript = run_stt(audio_path)
    except Exception as e:
        print(f"[오류] STT 실패 ({name}): {e}")
        return

    try:
        txt_path = RESULTS_DIR / f"{stem}.txt"
        txt_path.write_text(transcript, encoding="utf-8")
    except Exception as e:
        print(f"[오류] 텍스트 저장 실패 ({name}): {e}")
        return

    summary_ok = False
    try:
        summary = run_summary(transcript, name)
        summary_path = RESULTS_DIR / f"{stem}_요약.txt"
        summary_path.write_text(summary, encoding="utf-8")
        summary_ok = True
    except Exception as e:
        print(f"[오류] 요약 실패 ({name}): {e}")
    finally:
        with _lock:
            processed.add(name)
            save_processed(processed)

    if summary_ok:
        print(f"[완료] {stem}.txt / {stem}_요약.txt 저장됨")
    else:
        print(f"[완료] {stem}.txt 저장됨 (요약 실패)")


class RecordingHandler(FileSystemEventHandler):
    def __init__(self, processed: set[str]):
        self.processed = processed
        self._in_progress: set[str] = set()

    def _try_queue(self, path: Path) -> bool:
        """중복 처리를 막고 처리 대상이면 True 반환."""
        if path.suffix.lower() != ".m4a":
            return False
        name = path.name
        with _lock:
            if name in self.processed or name in self._in_progress:
                return False
            self._in_progress.add(name)
        return True

    def _release(self, name: str) -> None:
        with _lock:
            self._in_progress.discard(name)

    def _dispatch(self, path: Path) -> None:
        if not self._try_queue(path):
            return
        def run():
            try:
                process_file(path, self.processed)
            finally:
                self._release(path.name)
        threading.Thread(target=run, daemon=True).start()

    def on_created(self, event):
        if not event.is_directory:
            self._dispatch(Path(event.src_path))

    def on_moved(self, event):
        # Syncthing은 임시 파일을 최종 파일명으로 rename한다
        if not event.is_directory:
            self._dispatch(Path(event.dest_path))


def scan_existing(handler: RecordingHandler) -> None:
    """시작 시 미처리 파일을 백그라운드에서 처리."""
    existing = [
        f for f in RECORDINGS_DIR.glob("*.m4a")
        if f.name not in handler.processed
    ]
    if not existing:
        return
    print(f"[시작] 미처리 파일 {len(existing)}개 발견, 순차 처리합니다...")
    for audio_path in existing:
        handler._dispatch(audio_path)


def main():
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    processed = load_processed()
    print(f"처리 완료 기록: {len(processed)}개")
    print(f"감시 폴더: {RECORDINGS_DIR}")
    print(f"업무 시간: 평일 {WORK_START.strftime('%H:%M')}~{WORK_END.strftime('%H:%M')} 자동 실행")
    print("종료하려면 Ctrl+C\n")

    observer = None
    handler = None
    try:
        while True:
            if is_work_hours():
                if observer is None:
                    print(f"[시작] {datetime.now().strftime('%m/%d %H:%M')} 업무 시간 — 감시 시작")
                    handler = RecordingHandler(processed)
                    observer = Observer()
                    observer.schedule(handler, str(RECORDINGS_DIR), recursive=False)
                    observer.start()
                    scan_existing(handler)
                time.sleep(30)
            else:
                if observer is not None:
                    print(f"[대기] {datetime.now().strftime('%m/%d %H:%M')} 업무 시간 종료 — 내일 {WORK_START.strftime('%H:%M')}에 재시작")
                    observer.stop()
                    observer.join()
                    observer = None
                    handler = None
                time.sleep(60)
    except KeyboardInterrupt:
        print("\n종료 중...")
        if observer is not None:
            observer.stop()
            observer.join()
    print("종료됨")


if __name__ == "__main__":
    main()
