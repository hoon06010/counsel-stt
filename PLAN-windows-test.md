# PLAN — counsel-stt 윈도우 테스트 점검

대상: Galaxy Book4 Edge (Windows on ARM / Snapdragon X) — 윈도우 PC의 Claude Code로 진행
작성일: 2026-07-25
기준 코드: `main.py` (341줄, 문법 오류 없음 — `python -m py_compile main.py` 통과)

---

## A. 코드 점검 결과 (실행 전 확인 필요)

문법 오류는 없습니다. 아래는 **런타임에 터질 수 있는 지점**이며, 심각도 순입니다.

### A-1. `ollama` 버전 불일치 (터질 확률 높음)

`main.py:169`
```python
summary = (response.message.content or "").strip()
```
`response.message` 속성 접근은 **ollama 0.4.0 이상**에서만 동작합니다.
`requirements.txt`는 `ollama>=0.3.0`이라 0.3.x가 깔리면 `ollama.chat()`이 **dict**를 반환해
`AttributeError: 'dict' object has no attribute 'message'`로 요약 단계가 매번 실패합니다.

- 확인: `pip show ollama` → Version이 0.4.0 미만이면 문제
- 조치안: `requirements.txt`를 `ollama>=0.4.0`으로 수정 (또는 `response["message"]["content"]` 호환 처리)

### A-2. `_Tee`에 파일 객체 인터페이스가 부족 (첫 실행에서 터질 수 있음)

`main.py:45-76`의 `_Tee`는 `write`/`flush`만 구현합니다.
`sys.stdout`/`sys.stderr`를 이걸로 갈아끼우는데, Whisper large-v3 최초 다운로드(약 3GB) 시
`huggingface_hub`의 tqdm 진행바가 `sys.stderr.isatty()` / `.encoding` / `.fileno()`를 호출하면
`AttributeError`가 납니다.

- 부작용 하나 더: 진행바가 `\r`을 대량 출력 → `log.txt`가 수십 MB로 폭증 (5MB 로테이션이 즉시 돌아감)
- 조치안: `_Tee`에 `isatty() -> False`, `encoding`, `errors`, `fileno()` 추가.
  또는 **모델을 먼저 미리 받아두고**(A-2 회피) 테스트 시작

### A-3. `scan_existing`의 예외가 프로그램을 죽임

`main.py:280-290`
```python
key=lambda f: f.stat().st_mtime,
```
`glob` 직후 Syncthing이 파일을 지우거나 이름을 바꾸면 `f.stat()`에서 `FileNotFoundError`.
`main()`의 `try`는 `KeyboardInterrupt`만 잡으므로 **전체 프로세스가 종료**됩니다.
`pythonw`로 자동 실행 중이면 조용히 죽습니다.

- 조치안: `scan_existing` 전체를 `try/except Exception`으로 감싸기

### A-4. 업무시간 경계에서 중복 처리 가능

`main.py:314`에서 매 업무일마다 `RecordingHandler`를 **새로 생성**합니다.
새 handler의 `_in_progress`는 비어 있는데, 큐에 남아 있는 파일은 아직 `processed`에도 없습니다.
→ 18:00 넘어 큐가 남은 상태에서 다음 날 09:00에 같은 파일 이벤트가 오면 **두 번 처리**될 수 있습니다.

- 조치안: `_in_progress`를 모듈 전역으로 올리거나, handler를 한 번만 만들고 observer만 재생성

### A-5. 실패한 파일이 재시작 전까지 재시도되지 않음

STT 실패(`main.py:189`)나 텍스트 저장 실패(`main.py:203`) 시 `processed`에 추가되지 않고
`_in_progress`에서만 해제됩니다. 새 파일 이벤트가 없으니 **재시작 전까지 방치**됩니다.
(재시작하면 `scan_existing`이 다시 잡음 — 동작상 치명적이진 않으나 알고 있어야 함)

### A-6. `.m4a` 확장자만 감지 — 테스트 시 걸림돌

`main.py:252` `if path.suffix.lower() != ".m4a"`.
테스트용 wav/mp3를 넣으면 **아무 반응이 없어** 고장으로 오인하기 쉽습니다.
→ 테스트 샘플은 반드시 `.m4a`로 준비하거나, 테스트 동안만 확장자 조건을 넓히기

### A-7. 사소한 항목

- `setup_logging()`이 연 로그 핸들을 닫지 않음 (프로세스 종료 시 OS가 정리 — 무해)
- 콘솔 인코딩: 윈도우 기본 cp949에서 한글 출력 시 깨질 수 있음 → `set PYTHONUTF8=1` 권장
- `_lock` 하나로 `processed`와 `_in_progress`를 함께 보호 — 중첩 획득 없어 데드락은 없음 (문제 없음)

---

## B. 설치 단계 리스크 (ARM64 고유)

- `ctranslate2` — README대로 `pip install ctranslate2 --only-binary :all:` 먼저. win-arm64 wheel이 없으면 여기서 막힘
- `av` (PyAV) — `faster-whisper`가 오디오 디코딩에 사용. **win-arm64 wheel 미제공 가능성**이 있음.
  실패하면 대안: x64 Python을 에뮬레이션으로 설치하거나, ffmpeg로 m4a→wav 선변환
- Python은 **3.10 이상 필수** (`WhisperModel | None` 문법). README대로 3.12+ 권장

---

## C. 테스트 절차 (윈도우에서 순서대로)

### Step 0 — 환경 확인
```powershell
python --version          # 3.12+ 인지
python -c "import platform; print(platform.machine())"   # ARM64 확인
pip show ollama ctranslate2 faster-whisper av
ollama list               # exaone3.5:7.8b 있는지
```
체크: [ ] Python 3.12+ [ ] ollama 0.4.0+ [ ] av 설치됨 [ ] exaone 모델 있음

### Step 1 — import만 검증 (STT 없이)
```powershell
python -c "from faster_whisper import WhisperModel; import watchdog, ollama; print('OK')"
```
여기서 실패하면 B의 설치 리스크. 다음 단계 무의미.

### Step 2 — Ollama 단독 검증
```powershell
python -c "import ollama; r=ollama.chat(model='exaone3.5:7.8b', messages=[{'role':'user','content':'안녕'}]); print(r.message.content[:50])"
```
`AttributeError`면 → **A-1 확정**. 여기서 잡고 가기.

### Step 3 — Whisper 모델 다운로드를 먼저 (A-2 회피)
```powershell
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8'); print('모델 준비 완료')"
```
3GB 다운로드. `main.py`와 분리해서 받아두면 로그 폭증/`_Tee` 문제를 피할 수 있음.
체크: [ ] 다운로드 완료 [ ] int8 로딩 성공 (ARM64에서 int8 미지원이면 여기서 에러)

### Step 4 — 업무시간 게이트 우회
현재 시각이 평일 09:00~18:00이 아니면 아무것도 안 합니다.
테스트 시에는 `main.py:32-34`를 임시로 넓히기:
```python
WORK_START = dtime(0, 0)
WORK_END = dtime(23, 59)
WORK_DAYS = frozenset({0,1,2,3,4,5,6})
```
**테스트 후 원복 필수.**

### Step 5 — 짧은 샘플로 엔드투엔드
1. 30초~1분짜리 한국어 `.m4a` 준비 (스마트폰 녹음 등)
2. `python main.py` 실행 (pythonw 아님 — 화면으로 봐야 함)
3. 다른 창에서 `%USERPROFILE%\상담\recordings\`에 샘플 복사
4. `[감지] → [STT] → [요약] → [완료]` 순서로 뜨는지 확인

체크:
- [ ] `[감지]` 뜸 (안 뜨면 A-6 확장자 또는 watchdog 문제)
- [ ] 파일 안정화 통과 (최소 6초 대기 후 STT 시작)
- [ ] `results\샘플.txt` 생성 + 한글 정상
- [ ] `results\샘플_요약.txt` 생성 (실패하면 A-1)
- [ ] `processed.json`에 파일명 기록됨
- [ ] `log.txt`에 타임스탬프와 함께 동일 내용 기록됨

### Step 6 — 예외 경로 확인
- [ ] **무음 파일**: 무음 m4a 넣기 → `[건너뜀] ... STT 결과 없음`, processed에 기록
- [ ] **중복 방지**: 처리 끝난 파일을 다시 복사 → 재처리 안 됨
- [ ] **Ollama 끄고**: `[오류] 요약 실패` 뜨고 `.txt`는 남고 프로그램은 계속 실행
- [ ] **여러 개 동시 투입**: 3개 한꺼번에 넣고 **순차** 처리되는지(직렬 큐 동작), 메모리 사용량 확인
- [ ] **Ctrl+C**: 남은 큐 경고 후 정상 종료

### Step 7 — 로그 로테이션
- [ ] `log.txt`를 5MB 넘게 만든 뒤 재실행 → `log.old.txt`로 이동되는지

### Step 8 — 실사용 조건 복귀
- [ ] Step 4에서 바꾼 업무시간 원복
- [ ] `pythonw`로 실행해도 `log.txt`에 기록되는지 (A-2가 여기서 드러날 수 있음)
- [ ] 작업 스케줄러 등록 후 재부팅 테스트
- [ ] 덮개 닫고도 처리 계속되는지

### Step 9 — 실제 길이 부하 테스트
- [ ] 30분 분량 녹음 1건 → 예상 10~15분 내 완료되는지, 메모리 여유 있는지

---

## D. 수정 우선순위 (테스트 중 문제 발견 시)

| 순위 | 항목 | 수정 위치 |
|---|---|---|
| 1 | ollama 버전 하한 상향 | `requirements.txt` |
| 2 | `_Tee`에 `isatty`/`encoding`/`fileno` 추가 | `main.py:45-76` |
| 3 | `scan_existing` 예외 격리 | `main.py:280-290` |
| 4 | `_in_progress` 전역화 (중복 처리) | `main.py:245-268`, `314` |
| 5 | 실패 파일 재시도 정책 | `main.py:186-204` |

수정 후 `README.md`의 "변경 내역"에 항목 추가.

---

## E. 참고

- 이 저장소에는 `CLAUDE.md`가 없음. `llm-wiki/sources/counsel-stt/`에 7/20자 스냅샷(CLAUDE.md 포함)이 있으나 현재 코드와 다름 — 참고 시 주의
- 결과 기록은 이 파일 하단에 체크 표시로 남기기
