# PLAN — counsel-stt 윈도우 테스트 점검 (통합본)

대상: Galaxy Book4 Edge (Windows on ARM / Snapdragon X) — 윈도우 PC의 Claude Code로 진행
작성일: 2026-07-25
기준 코드: `main.py` (341줄) — `python -m py_compile main.py` 통과, 문법 오류 없음

> 이 문서는 이전 `CLAUDE.md`의 테스트 계획(T1~T5)과 신규 코드 점검 결과를 **현재 코드 기준으로 통합**한 것입니다.
> 구 계획의 T1(동시 추론)·T4(빈 STT 요약)는 이미 수정되어 제외했습니다. 아래 "해결된 이슈" 참고.

---

## 0. 이미 해결된 이슈 (재테스트 불필요)

| 구 항목 | 내용 | 현재 상태 |
|---|---|---|
| T1 | 여러 파일 동시 `transcribe()` → 크래시/결과 오염 | **해결.** `_work_queue` 직렬 큐 + 단일 워커 스레드로 한 번에 하나만 처리 (`main.py:225-242`) |
| T4 | 빈 STT 결과를 LLM에 넘겨 요약을 창작 | **해결.** 빈 transcript면 요약 없이 건너뛰고 processed에 기록 (`main.py:192-197`) |

구 `CLAUDE.md`의 줄 번호 인용은 현재 파일과 맞지 않으니 참고하지 마세요.

---

## A. 코드 점검 결과 (실행 전 확인 필요)

심각도 순입니다. 각 항목의 검증은 C장 테스트 절차에 연결돼 있습니다.

### A-1. `ollama` 버전 불일치 — 요약이 항상 실패 (터질 확률 높음)

`main.py:169`
```python
summary = (response.message.content or "").strip()
```
`response.message` 속성 접근은 **ollama 0.4.0 이상** 전용입니다.
`requirements.txt`는 `ollama>=0.3.0`이라 0.3.x가 깔리면 `ollama.chat()`이 **dict**를 반환해
`AttributeError: 'dict' object has no attribute 'message'`로 요약 단계가 매번 실패합니다.

- 검증: **Step 2**
- 조치안: `requirements.txt`를 `ollama>=0.4.0`으로 수정

### A-2. `processed.json` 손상 시 앱이 시작 불가 (구 T2/T3 — 여전히 유효)

`main.py:113-117`
```python
def load_processed() -> set[str]:
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, encoding="utf-8") as f:
            return set(json.load(f))   # 예외 처리 없음
```
이 호출은 `main.py:300`에 있고 `try` 블록은 **309줄부터** 시작합니다.
즉 파일이 손상되면 `KeyboardInterrupt` 핸들러에도 안 걸리고 **시작 즉시 크래시**합니다.
`pythonw` 자동 실행 중이면 화면에 아무것도 안 뜨고 조용히 죽습니다.

손상 경로는 `main.py:120-122`의 `save_processed()` — `open(..., "w")`로 파일을 먼저 비우고 씁니다.

> 정확히 짚자면: `save_processed()`는 워커 스레드에서만 호출되고 Ctrl+C는 메인 스레드로 가므로
> **Ctrl+C가 쓰기를 직접 끊지는 않습니다.** 하지만 인터프리터 종료 시 데몬 워커가 쓰기 도중
> 강제 종료되거나, 정전·강제 종료(작업 관리자)로는 충분히 손상됩니다.

실제 동작 확인 결과:

| 손상 형태 | 결과 |
|---|---|
| `["a.m4a"` (불완전) | `JSONDecodeError` → 시작 불가 |
| `` (빈 파일) | `JSONDecodeError` → 시작 불가 |
| `{"file": "a.m4a"}` | 크래시는 없으나 `{'file'}`로 로드 → **이력 유실, 전체 재처리** |
| `[1,2,3]` | `{1,2,3}`으로 로드 → 파일명 비교가 항상 False → **전체 재처리** |

- 검증: **Step 7**
- 조치안: `load_processed()`를 `try/except (JSONDecodeError, OSError)`로 감싸고 타입 검증(`isinstance(x, str)`) 추가.
  `save_processed()`는 임시 파일에 쓴 뒤 `os.replace()`로 원자적 교체

### A-3. `_Tee`에 파일 객체 인터페이스가 부족 — 첫 실행에서 터질 수 있음

`main.py:45-76`의 `_Tee`는 `write`/`flush`만 구현합니다.
`sys.stdout`/`sys.stderr`를 이걸로 갈아끼우는데, Whisper large-v3 최초 다운로드(약 3GB) 시
`huggingface_hub`의 tqdm 진행바가 `sys.stderr.isatty()` / `.encoding` / `.fileno()`를 호출하면
`AttributeError`가 납니다.

- 부작용: 진행바가 `\r`을 대량 출력 → `log.txt` 폭증 (5MB 로테이션이 즉시 돌아감)
- 검증: **Step 3**(회피) / **Step 8**(실제 노출)
- 조치안: `_Tee`에 `isatty() -> False`, `encoding`, `errors`, `fileno()` 추가

### A-4. `scan_existing` 예외가 프로세스를 죽임

`main.py:284`
```python
key=lambda f: f.stat().st_mtime,
```
`glob` 직후 Syncthing이 파일을 지우거나 이름을 바꾸면 `FileNotFoundError`.
`main()`의 `try`는 `KeyboardInterrupt`만 잡으므로 **전체 프로세스 종료**입니다.

- 조치안: `scan_existing` 전체를 `try/except Exception`으로 감싸기

### A-5. 업무시간 경계에서 중복 처리 가능 (구 T5 관련)

`main.py:314`에서 매 업무일마다 `RecordingHandler`를 **새로 생성**합니다.
새 handler의 `_in_progress`는 비어 있는데, 큐에 남은 파일은 아직 `processed`에도 없습니다.
→ 18:00 이후 큐가 남은 상태로 다음 날 09:00에 같은 파일 이벤트가 오면 **두 번 처리**될 수 있습니다.

구 T5가 물었던 "경계에서 처리가 유실되는가"는 **유실되지 않습니다** —
워커 스레드가 큐 항목과 handler 참조를 그대로 들고 있어 완료 후 `processed.add()`가 정상 호출됩니다.
실제 문제는 유실이 아니라 **중복**입니다.

- 검증: **Step 6-6**
- 조치안: `_in_progress`를 모듈 전역으로 올리거나, handler는 한 번만 만들고 observer만 재생성

### A-6. 실패한 파일이 재시작 전까지 재시도되지 않음

STT 실패(`main.py:189`)나 텍스트 저장 실패(`main.py:203`) 시 `processed`에 추가되지 않고
`_in_progress`에서만 해제됩니다. 새 파일 이벤트가 없으니 재시작 전까지 방치됩니다.
(재시작하면 `scan_existing`이 다시 잡으므로 치명적이진 않음 — 인지만 하고 갈 것)

### A-7. `.m4a` 확장자만 감지 — 테스트 시 걸림돌

`main.py:252` `if path.suffix.lower() != ".m4a"`.
테스트용 wav/mp3를 넣으면 **아무 반응이 없어** 고장으로 오인하기 쉽습니다.
→ 샘플은 반드시 `.m4a`로 준비 (C장 준비 섹션 참고)

### A-8. 사소한 항목

- `setup_logging()`이 연 로그 핸들을 닫지 않음 (프로세스 종료 시 OS가 정리 — 무해)
- 콘솔 인코딩: 윈도우 기본 cp949 → `set PYTHONUTF8=1` 권장
- `_lock` 하나로 `processed`와 `_in_progress`를 함께 보호 — 중첩 획득 없어 데드락 없음 (문제 없음)

---

## B. 설치 단계 리스크 (ARM64 고유)

- `ctranslate2` — README대로 `pip install ctranslate2 --only-binary :all:` 먼저. win-arm64 wheel이 없으면 여기서 막힘
- `av` (PyAV) — `faster-whisper`의 오디오 디코딩용. **win-arm64 wheel 미제공 가능성** 있음.
  실패 시 대안: x64 Python을 에뮬레이션으로 설치, 또는 ffmpeg로 m4a→wav 선변환
- Python **3.10 이상 필수** (`WhisperModel | None` 문법). README대로 3.12+ 권장

---

## C. 테스트 절차

### 공통 준비

```powershell
# 초기화 (각 테스트 시작 전)
Remove-Item -Recurse -Force ~/상담 -ErrorAction SilentlyContinue
python -c "
from pathlib import Path
(Path.home() / '상담/recordings').mkdir(parents=True, exist_ok=True)
(Path.home() / '상담/results').mkdir(parents=True, exist_ok=True)
print('디렉터리 생성 완료')
"
```

**테스트용 오디오 준비** — A-7 때문에 반드시 `.m4a`여야 합니다.

```powershell
winget install Gyan.FFmpeg   # ffmpeg 없을 때
```

```powershell
ffmpeg -f lavfi -i "sine=frequency=440:duration=10" -c:a aac -b:a 128k test_a.m4a
ffmpeg -f lavfi -i "sine=frequency=880:duration=10" -c:a aac -b:a 128k test_b.m4a
ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 5 -c:a aac -b:a 128k test_silent.m4a
ffmpeg -f lavfi -i "sine=frequency=440:duration=60" -c:a aac -b:a 128k test_large.m4a
```

> 사인파는 한국어가 아니라 STT 결과가 비거나 잡음입니다. **엔드투엔드 요약 품질 확인용으로는
> 실제 한국어 음성 1개(30초~1분)를 따로 준비**하세요. 사인파는 배관 동작 확인용입니다.

확인: `ls test_*.m4a` — 각각 수십 KB 이상이어야 함 (0 bytes면 ffmpeg 재설치)

---

### Step 0 — 환경 확인

```powershell
python --version                                          # 3.12+ 인지
python -c "import platform; print(platform.machine())"    # ARM64 확인
pip show ollama ctranslate2 faster-whisper av
ollama list                                               # exaone3.5:7.8b 있는지
```
- [ ] Python 3.12+  - [ ] ollama 0.4.0+  - [ ] av 설치됨  - [ ] exaone 모델 있음

### Step 1 — import 검증 (STT 없이)

```powershell
python -c "from faster_whisper import WhisperModel; import watchdog, ollama; print('OK')"
```
실패하면 B장 설치 리스크. 다음 단계 무의미.

### Step 2 — Ollama 단독 검증 → **A-1 판정**

```powershell
python -c "import ollama; r=ollama.chat(model='exaone3.5:7.8b', messages=[{'role':'user','content':'안녕'}]); print(r.message.content[:50])"
```
- [ ] 정상 출력 → A-1 해당 없음
- [ ] `AttributeError` → **A-1 확정**. 여기서 `requirements.txt` 고치고 진행

### Step 3 — Whisper 모델 먼저 받기 (A-3 회피)

```powershell
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8'); print('모델 준비 완료')"
```
3GB 다운로드. `main.py`와 분리해서 받아두면 `_Tee` 문제와 로그 폭증을 피할 수 있습니다.
- [ ] 다운로드 완료  - [ ] int8 로딩 성공 (ARM64에서 int8 미지원이면 여기서 에러)

### Step 4 — 업무시간 게이트 우회

현재 시각이 평일 09:00~18:00이 아니면 아무것도 안 합니다. `main.py:32-34`를 임시 수정:
```python
WORK_START = dtime(0, 0)
WORK_END = dtime(23, 59)
WORK_DAYS = frozenset({0,1,2,3,4,5,6})
```
**Step 9에서 원복 필수.**

### Step 5 — 엔드투엔드 (실제 한국어 음성)

1. `python main.py` 실행 (pythonw 아님 — 화면으로 확인)
2. 다른 창에서 `%USERPROFILE%\상담\recordings\`에 한국어 샘플 복사
3. `[감지] → [STT] → [요약] → [완료]` 순서 확인

- [ ] `[감지]` 뜸 (안 뜨면 A-7 확장자 또는 watchdog 문제)
- [ ] 파일 안정화 통과 (최소 6초 대기 후 STT 시작)
- [ ] `results\샘플.txt` 생성 + 한글 정상
- [ ] `results\샘플_요약.txt` 생성 (실패하면 A-1)
- [ ] 요약 내용이 **실제 발화 범위 내**인지 (창작 없는지) 눈으로 확인
- [ ] `processed.json`에 파일명 기록
- [ ] `log.txt`에 타임스탬프와 함께 동일 내용 기록

### Step 6 — 예외 경로

- [ ] **6-1 무음**: `test_silent.m4a` 투입 → `[건너뜀] ... STT 결과 없음` 뜨고 `_요약.txt` **미생성**, processed에는 기록
- [ ] **6-2 중복 방지**: 처리 끝난 파일 다시 복사 → 재처리 안 됨
- [ ] **6-3 Ollama 중단**: Ollama 종료 후 투입 → `[오류] 요약 실패`, `.txt`는 남고 프로그램은 계속 실행
- [ ] **6-4 직렬 처리**: `test_a` + `test_b` 동시 투입 → `[STT] 변환 중`이 **겹치지 않고 순차** 출력, 메모리 사용량 확인 (구 T1 회귀 확인)
- [ ] **6-5 Ctrl+C**: 처리 중 Ctrl+C → 남은 큐 경고 후 종료. 종료 후 `Get-Content ~/상담/processed.json`이 **유효한 JSON**인지 확인
- [ ] **6-6 경계 중복 (A-5)**: `WORK_END`를 2분 뒤로 설정 → `test_large.m4a` 투입 → 처리 중 경계 통과.
      `업무 시간 종료` 로그 이후에도 처리가 **완료되는지**(유실 없음), 재시작 후 **재처리되지 않는지**(중복 없음) 확인

### Step 7 — processed.json 손상 (A-2)

각 케이스마다 손상시킨 뒤 `python main.py` 실행:

```powershell
'["test_a.m4a"' | Set-Content ~/상담/processed.json -NoNewline   # A 불완전
'' | Set-Content ~/상담/processed.json -NoNewline                 # B 빈 파일
'{"file": "test.m4a"}' | Set-Content ~/상담/processed.json        # C 객체
'[1, 2, 3]' | Set-Content ~/상담/processed.json                   # D 숫자
```

현재 코드 기준 예상: A·B는 `JSONDecodeError`로 **시작 불가**, C·D는 시작되지만 **이력 유실 → 전체 재처리**.

- [ ] A: 크래시 확인  - [ ] B: 크래시 확인  - [ ] C: 이력 유실 확인  - [ ] D: 이력 유실 확인

수정 후 기대: 4개 모두 경고 메시지 출력 후 빈 이력으로 정상 시작.

### Step 8 — 로그

- [ ] `log.txt` 5MB 초과시킨 뒤 재실행 → `log.old.txt`로 로테이션
- [ ] `pythonw`로 실행해도 `log.txt`에 기록되는지 (**A-3가 여기서 드러날 수 있음**)

### Step 9 — 실사용 조건 복귀

- [ ] Step 4/6-6에서 바꾼 `WORK_START`/`WORK_END`/`WORK_DAYS` **원복**
- [ ] 작업 스케줄러 등록 후 재부팅 테스트
- [ ] 덮개 닫고도 처리 계속되는지

### Step 10 — 부하 테스트

- [ ] 30분 분량 녹음 1건 → 10~15분 내 완료, 메모리 여유 확인

---

## D. 수정 우선순위

| 순위 | 항목 | 코드 위치 | 검증 |
|---|---|---|---|
| 1 | A-1 ollama 버전 하한 상향 | `requirements.txt` | Step 2 |
| 2 | A-2 `load_processed` 예외/타입 처리 + `save_processed` 원자적 쓰기 | `main.py:113-122` | Step 7 |
| 3 | A-3 `_Tee`에 `isatty`/`encoding`/`fileno` 추가 | `main.py:45-76` | Step 8 |
| 4 | A-4 `scan_existing` 예외 격리 | `main.py:280-290` | — |
| 5 | A-5 `_in_progress` 전역화 (중복 처리) | `main.py:245-268`, `314` | Step 6-6 |
| 6 | A-6 실패 파일 재시도 정책 | `main.py:186-204` | — |

수정 후 `README.md`의 "변경 내역"에 항목 추가.

---

## E. 결과 기록

```
Step 0 환경:        [ ] PASS  [ ] FAIL      비고:
Step 1 import:      [ ] PASS  [ ] FAIL      비고:
Step 2 ollama(A-1): [ ] PASS  [ ] FAIL      비고:
Step 3 모델 로딩:    [ ] PASS  [ ] FAIL      비고:
Step 5 엔드투엔드:   [ ] PASS  [ ] FAIL      비고:
Step 6 예외 경로:    6-1[ ] 6-2[ ] 6-3[ ] 6-4[ ] 6-5[ ] 6-6[ ]
Step 7 JSON손상(A-2): A[ ] B[ ] C[ ] D[ ]
Step 8 로그(A-3):    [ ] PASS  [ ] FAIL      비고:
Step 9 실사용 복귀:  [ ] PASS  [ ] FAIL      비고:
Step 10 부하:       [ ] PASS  [ ] FAIL      비고:
```
