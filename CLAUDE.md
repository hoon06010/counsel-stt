# counsel-stt 테스트 계획

이 파일은 `main.py`에서 발견된 잠재적 버그를 검증하기 위한 테스트 절차서입니다.
각 테스트는 독립적으로 실행할 수 있습니다. 순서대로 진행을 권장하지만 필수는 아닙니다.

---

## 프로젝트 개요

- **역할**: Galaxy Tab 녹음(m4a) → faster-whisper STT → EXAONE 요약 → 결과 저장
- **실행 환경**: Galaxy Book4 Edge (Windows ARM64, CPU 전용)
- **핵심 파일**: `main.py` (단일 파일 구성)
- **데이터 경로**: `~/상담/recordings/` (입력), `~/상담/results/` (출력), `~/상담/processed.json` (처리 이력)

---

## 공통 사전 준비

모든 테스트 전에 다음을 확인합니다.

```powershell
# 1. 의존성 확인
python -c "import faster_whisper, watchdog, ollama; print('OK')"

# 2. Ollama 실행 확인
ollama list   # exaone3.5:7.8b 가 목록에 있어야 함

# 3. 테스트용 디렉터리 초기화 (매 테스트 시작 전 실행)
Remove-Item -Recurse -Force ~/상담 -ErrorAction SilentlyContinue
python -c "
from pathlib import Path
(Path.home() / '상담/recordings').mkdir(parents=True, exist_ok=True)
(Path.home() / '상담/results').mkdir(parents=True, exist_ok=True)
print('디렉터리 생성 완료')
"
```

### 테스트용 오디오 파일 생성

ffmpeg가 없으면 먼저 설치합니다.

```powershell
winget install Gyan.FFmpeg
# 또는 https://ffmpeg.org/download.html 에서 Windows 빌드 수동 설치 후 PATH 등록
```

```powershell
# 10초짜리 440Hz 사인파 (파일 A - 유효한 오디오)
ffmpeg -f lavfi -i "sine=frequency=440:duration=10" -c:a aac -b:a 128k test_a.m4a

# 10초짜리 880Hz 사인파 (파일 B - A와 다른 주파수)
ffmpeg -f lavfi -i "sine=frequency=880:duration=10" -c:a aac -b:a 128k test_b.m4a

# 5초짜리 완전 무음 (파일 S)
ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 5 -c:a aac -b:a 128k test_silent.m4a

# 60초짜리 사인파 (파일 L - Ctrl+C 타이밍 테스트용, STT에 30~60초 소요)
ffmpeg -f lavfi -i "sine=frequency=440:duration=60" -c:a aac -b:a 128k test_large.m4a
```

생성 확인:
```powershell
ls test_a.m4a, test_b.m4a, test_silent.m4a, test_large.m4a
# 각각 수십 KB ~ 수백 KB 크기여야 함. 0 bytes면 ffmpeg 재설치 필요
```

---

## T1. 동시 파일 처리 — Whisper 스레드 안전성 (CRITICAL)

### 검증하려는 버그

`main.py:105` `get_whisper_model()`은 초기화만 락으로 보호합니다.
두 스레드가 같은 `WhisperModel` 인스턴스에서 `transcribe()`를 동시에 호출하면
CTranslate2 내부에서 크래시가 나거나 한 파일의 세그먼트가 다른 파일 결과에 섞일 수 있습니다.

```python
# main.py:51-62 — _model_lock은 if _whisper_model is None 블록만 보호
def get_whisper_model() -> WhisperModel:
    with _model_lock:
        if _whisper_model is None:
            _whisper_model = WhisperModel(...)
        return _whisper_model   # 여러 스레드가 같은 객체 사용

# main.py:103-109 — 락 없이 transcribe() 호출
def run_stt(audio_path: Path) -> str:
    model = get_whisper_model()
    segments, _ = model.transcribe(...)  # ← 동시 호출 가능
```

### 테스트 절차

**터미널 1** — main.py 실행:
```powershell
cd ~/Documents/Claude/counsel-stt-main

# 업무 시간 외에도 실행하도록 WORK_START/END를 임시 우회
# main.py 상단의 is_work_hours()가 항상 True를 반환하도록 수정하거나,
# 아래처럼 현재 시각 기준 ±1시간으로 임시 변경:
# WORK_START = dtime(0, 0), WORK_END = dtime(23, 59) 로 수정 후 실행

python main.py
# "[시작] ... 업무 시간 — 감시 시작" 메시지가 나올 때까지 대기
```

**터미널 2** — 두 파일 동시 복사:
```powershell
# 아래 두 명령을 가능한 한 동시에 실행 (한 줄씩 빠르게 Enter)
Copy-Item test_a.m4a ~/상담/recordings/test_a.m4a
Copy-Item test_b.m4a ~/상담/recordings/test_b.m4a

# 또는 PowerShell 백그라운드 잡으로 진짜 동시 실행:
Start-Job { Copy-Item "C:\경로\test_a.m4a" "$HOME\상담\recordings\test_a.m4a" }
Start-Job { Copy-Item "C:\경로\test_b.m4a" "$HOME\상담\recordings\test_b.m4a" }
```

`test_a.m4a`, `test_b.m4a`의 실제 경로는 앞서 ffmpeg로 생성한 위치로 교체합니다.

### 대기 및 확인

두 파일 처리가 모두 완료되었다는 로그가 나올 때까지 대기합니다 (CPU 기준 약 5~15분).

```
[완료] test_a.txt 저장됨
[완료] test_b.txt 저장됨
```

처리 완료 후 검증:
```powershell
# 결과 파일 존재 확인
ls ~/상담/results/

# processed.json에 두 파일 모두 기록됐는지 확인
Get-Content ~/상담/processed.json
# ["test_a.m4a", "test_b.m4a"] 형태여야 함

# 결과 파일 내용 확인 (사인파라 한국어 인식은 안 되므로 빈 파일이나 잡음이 정상)
Get-Content ~/상담/results/test_a.txt
Get-Content ~/상담/results/test_b.txt
```

### 성공 기준

- `[오류]` 또는 Python 예외(traceback)가 터미널 1에 출력되지 않음
- `~/상담/results/test_a.txt`, `test_b.txt` 두 파일 모두 존재
- `processed.json`에 두 파일명 모두 기록됨

### 실패 기준 (버그 확인됨)

다음 중 하나라도 해당하면 버그입니다.

- Python traceback 출력 (특히 CTranslate2 관련 C++ 예외)
- 프로세스 강제 종료 (exit code 비정상)
- 한 쪽 파일의 결과만 생성되고 나머지 없음
- `processed.json`에 한 파일만 기록됨

### 정리

```powershell
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse ~/상담
```

---

## T2. 처리 중 Ctrl+C 후 재시작 — processed.json 비원자적 쓰기

### 검증하려는 버그

`main.py:72-74` `save_processed()`는 `open(..., "w")`로 파일을 먼저 비우고(truncate) 씁니다.
Ctrl+C가 쓰기 도중 발생하면 파일이 `{` 또는 `[` 등 불완전한 JSON으로 남습니다.
이후 `main.py:65-69` `load_processed()`는 에러 처리 없이 `json.load()`를 호출하므로
다음 시작 시 `json.JSONDecodeError`로 앱이 시작 불가 상태가 됩니다.

```python
# main.py:72-74
def save_processed(processed: set[str]) -> None:
    with open(PROCESSED_FILE, "w", encoding="utf-8") as f:
        json.dump(...)   # ← 이 도중 Ctrl+C → 파일 손상

# main.py:65-69
def load_processed() -> set[str]:
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, encoding="utf-8") as f:
            return set(json.load(f))   # ← JSONDecodeError → 앱 전체 크래시
```

### 테스트 절차

**STEP 1 — 정상 처리 1회 완료로 processed.json 생성:**

```powershell
python main.py &
# "[시작] ... 감시 시작" 확인 후:
Copy-Item test_a.m4a ~/상담/recordings/test_a.m4a
# "[완료] test_a.txt 저장됨" 까지 대기
# Ctrl+C로 종료
```

```powershell
Get-Content ~/상담/processed.json
# ["test_a.m4a"] 가 나와야 정상 상태 확인
```

**STEP 2 — 큰 파일 처리 중 Ctrl+C:**

```powershell
Copy-Item test_large.m4a ~/상담/recordings/test_large.m4a
python main.py
```

`[STT] 변환 중... (test_large.m4a)` 가 출력된 후 약 **5~10초** 뒤에 **Ctrl+C**를 누릅니다.
(STT가 내부적으로 finally 블록에서 save_processed를 호출하는 시점을 노립니다.)

타이밍이 어려우면 대신 아래 방법으로 강제 손상 재현:

```powershell
# processed.json을 직접 손상
'["test_a.m4a"' | Set-Content ~/상담/processed.json -NoNewline
# (닫는 ] 없는 불완전한 JSON)
```

**STEP 3 — 재시작:**

```powershell
python main.py
```

### 성공 기준 (현재 코드 기준 — 버그 있음이 정상)

현재 코드는 이 버그를 수정하지 않았으므로 아래가 재현되면 버그 확인입니다.

```
Traceback (most recent call last):
  ...
json.decoder.JSONDecodeError: ...
```

또는 아무 출력 없이 프로세스가 즉시 종료됩니다.

### 향후 수정 후 검증 기준

수정 후 재시작 시 다음 중 하나여야 합니다.
- `[경고] processed.json 읽기 실패, 초기화합니다.` 같은 메시지 출력 후 정상 시작
- 빈 처리 이력으로 정상 시작

### 정리

```powershell
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse ~/상담
```

---

## T3. 손상된 processed.json으로 시작 — 에러 핸들링 부재

### 검증하려는 버그

T2와 연결된 버그입니다. T2에서 파일이 손상된 상황을 전제로,
`load_processed()`의 에러 핸들링 부재를 독립적으로 검증합니다.

### 테스트 절차

```powershell
# 1. 경로 확인
python -c "from pathlib import Path; print(Path.home() / '상담' / 'processed.json')"
# 출력된 경로를 PROCESSED_PATH로 메모

# 2. 기본 디렉터리 준비
python -c "
from pathlib import Path
(Path.home() / '상담/recordings').mkdir(parents=True, exist_ok=True)
(Path.home() / '상담/results').mkdir(parents=True, exist_ok=True)
"

# 3. 손상된 JSON 4가지 케이스 순서대로 테스트
```

**케이스 A — 불완전한 배열:**
```powershell
'["test_a.m4a"' | Set-Content ~/상담/processed.json -NoNewline
python main.py
# 즉시 Ctrl+C로 종료 후 다음 케이스로
```

**케이스 B — 완전히 빈 파일:**
```powershell
'' | Set-Content ~/상담/processed.json -NoNewline
python main.py
```

**케이스 C — JSON 객체 (배열 아닌 타입):**
```powershell
'{"file": "test.m4a"}' | Set-Content ~/상담/processed.json
python main.py
# json.load()는 성공하나 set(dict)는 key들의 set이 됨 → 논리 오류 가능
```

**케이스 D — 배열 안에 숫자 (문자열 아닌 타입):**
```powershell
'[1, 2, 3]' | Set-Content ~/상담/processed.json
python main.py
# set()에 숫자가 들어가 name in processed 비교가 항상 False → 재처리 발생
```

### 성공 기준 (현재 코드 기준 — 버그 있음이 정상)

케이스 A, B: `json.JSONDecodeError` 또는 `ValueError`로 앱이 종료됩니다.
케이스 C, D: 앱은 시작되지만 처리 이력이 잘못 로드됩니다.

### 향후 수정 후 검증 기준

4가지 케이스 모두에서 경고 메시지 출력 후 빈 set으로 정상 시작합니다.

### 정리

```powershell
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse ~/상담
```

---

## T4. 무음 파일 처리 — 빈 STT 결과를 LLM에 전달

### 검증하려는 버그

`main.py:107` STT 결과가 빈 문자열일 때 `run_summary("")`가 그대로 호출됩니다.
LLM이 빈 텍스트를 받으면 상담 내용을 창작하거나 의미 없는 요약을 생성할 수 있습니다.
이는 의료/상담 맥락에서 심각한 오작동입니다.

```python
# main.py:103-109
def run_stt(audio_path: Path) -> str:
    segments, _ = model.transcribe(str(audio_path), language=LANGUAGE)
    text = "\n".join(seg.text.strip() for seg in segments if seg.text.strip())
    return text  # ← "" 반환 가능

# main.py:126-165 process_file()
transcript = run_stt(audio_path)   # "" 일 수 있음
txt_path.write_text(transcript, ...)  # 빈 파일 저장
summary = run_summary(transcript, name)  # run_summary("", ...) 호출됨 ← 버그
```

### 테스트 절차

```powershell
# 디렉터리 준비 (공통 준비 섹션 참고)
python main.py &
# "[시작] ... 감시 시작" 확인 후:
Copy-Item test_silent.m4a ~/상담/recordings/test_silent.m4a
```

처리 완료까지 대기 (무음 파일이라 STT는 매우 빠름, 30초~2분).

### 확인

```powershell
# 1. 터미널 로그 확인 — run_summary가 호출됐는지 확인
# "[요약] 요약 중..." 메시지가 출력됐으면 버그 재현됨

# 2. 결과 파일 확인
Get-Content ~/상담/results/test_silent.txt
# 빈 파일이어야 함

Get-Content ~/상담/results/test_silent_요약.txt
# 이 파일이 존재하면 버그 — 빈 텍스트로 요약이 생성됨
# 파일 내용이 상담 내용을 창작한 텍스트이면 심각한 버그
```

### 성공 기준 (현재 코드 기준 — 버그 있음이 정상)

`test_silent_요약.txt`가 생성되고, 내용이 LLM이 만들어낸 임의의 텍스트이면 버그 확인.

### 향후 수정 후 검증 기준

- `[경고] 변환된 텍스트 없음, 요약 건너뜀 (test_silent.m4a)` 로그 출력
- `test_silent_요약.txt` 미생성
- `test_silent.txt`는 생성되지만 비어 있음 (빈 오디오임을 기록)

### 정리

```powershell
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse ~/상담
```

---

## T5. 업무 시간 경계 처리 — 처리 중 업무 시간 종료

### 검증하려는 버그

`main.py:233-251` 업무 시간이 끝나면 `observer.stop()`을 호출하지만,
이미 스레드에서 실행 중인 `process_file()`은 데몬 스레드로 계속 돌아갑니다.
`observer = None`, `handler = None`으로 재설정되지만, 처리 중인 스레드는
원래 handler 객체 참조를 클로저로 유지하므로 완료 후 `processed.add()`와
`save_processed()`가 정상 호출됩니다.

검증 목표: 업무 시간 경계에서 처리가 정상 완료되는지, 또는 결과가 유실되는지 확인.

### 테스트 절차

**STEP 1 — WORK_END를 2분 후로 임시 수정:**

`main.py` 상단의 설정값을 수정합니다.

```python
# 원본
WORK_START = dtime(9, 0)
WORK_END = dtime(18, 0)

# 테스트용 임시 변경 — 현재 시각 기준 2분 후로 설정
# 예: 현재 시각이 14:30이면:
WORK_START = dtime(0, 0)
WORK_END = dtime(14, 32)  # 약 2분 후
```

현재 시각 확인:
```powershell
Get-Date -Format "HH:mm"
```

**STEP 2 — 실행 및 큰 파일 투입:**

```powershell
python main.py
# "[시작] ... 감시 시작" 확인 후 즉시:
Copy-Item test_large.m4a ~/상담/recordings/test_large.m4a
# "[STT] 변환 중... (test_large.m4a)" 확인
```

**STEP 3 — WORK_END 도달 후 관찰:**

WORK_END 시각이 지나면 다음 로그가 출력됩니다:
```
[대기] HH:MM 업무 시간 종료 — 내일 00:00에 재시작
```

이 메시지 이후에도 STT 처리가 계속 진행되는지 확인합니다.

### 확인

STT 완료 후 (추가로 수~10분 대기):
```powershell
# 결과 파일 확인
ls ~/상담/results/
Get-Content ~/상담/processed.json
```

### 성공 기준

- `업무 시간 종료` 로그 이후에도 `[STT] 완료` 또는 `[완료]` 로그 출력
- `~/상담/results/test_large.txt` 존재
- `processed.json`에 `test_large.m4a` 기록됨

### 실패 기준 (버그 확인됨)

- `업무 시간 종료` 로그 이후 처리 완료 로그 없음
- 결과 파일 미생성
- `processed.json`에 기록 없음 (다음 날 재처리 발생)

### 테스트 후 코드 원복

수정했던 `WORK_START`, `WORK_END`를 원래 값으로 복구합니다.

```python
WORK_START = dtime(9, 0)
WORK_END = dtime(18, 0)
```

### 정리

```powershell
Stop-Process -Name python -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse ~/상담
```

---

## 테스트 결과 기록 양식

각 테스트 실행 후 아래 양식으로 결과를 기록합니다.

```
T1 동시 처리:     [ ] PASS  [ ] FAIL  [ ] 미실시
  - 실패 증상:

T2 Ctrl+C 손상:   [ ] PASS  [ ] FAIL  [ ] 미실시
  - 실패 증상:

T3 손상 JSON 로드: [ ] PASS  [ ] FAIL  [ ] 미실시
  - 케이스 A: [ ] B: [ ] C: [ ] D: [ ]

T4 무음 파일:     [ ] PASS  [ ] FAIL  [ ] 미실시
  - 요약 파일 생성됨: [ ] Y  [ ] N
  - 요약 내용 (짧게):

T5 경계 처리:     [ ] PASS  [ ] FAIL  [ ] 미실시
  - 실패 증상:
```

---

## 이슈 우선순위 요약

| 순위 | 테스트 | 버그 설명 | 코드 위치 |
|------|--------|-----------|-----------|
| 1 | T1 | Whisper 동시 추론 — 크래시 또는 결과 오염 | `main.py:105`, `run_stt()` |
| 2 | T2/T3 | processed.json 손상 → 재시작 불가 | `main.py:72`, `main.py:67` |
| 3 | T4 | 빈 STT 결과 → LLM 창작 요약 | `main.py:151`, `process_file()` |
| 4 | T5 | 업무 시간 경계 처리 유실 가능성 | `main.py:245` |
