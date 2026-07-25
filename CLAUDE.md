# CLAUDE.md

Claude Code가 이 저장소에서 작업할 때 참고하는 지침입니다.

## 프로젝트 개요

Galaxy Tab에서 녹음한 상담 음성을 Galaxy Book4 Edge가 자동으로 텍스트 변환·요약해
태블릿으로 돌려보내는 **완전 로컬** 시스템입니다. 외부로 데이터가 나가지 않습니다.

- **파이프라인**: Syncthing 수신 → watchdog 감지 → faster-whisper STT → EXAONE 요약 → results 저장 → Syncthing 송신
- **실행 환경**: Windows on ARM (Snapdragon X Plus), **CPU 전용**
- **구성**: `main.py` 단일 파일 (341줄)

## 데이터 경로

```
~/상담/
├── recordings/     입력 (Syncthing 받기 전용)
├── results/        출력 (Syncthing 보내기 전용)
├── processed.json  처리 이력
└── log.txt         실행 로그 (5MB 초과 시 log.old.txt로 로테이션)
```

## 실행

```powershell
python main.py       # 화면 확인용
pythonw main.py      # 백그라운드 (작업 스케줄러 등록용, 로그는 log.txt에만)
```

설치 순서와 Syncthing/전원 옵션/작업 스케줄러 설정은 `README.md` 참고.
ARM64에서는 `pip install -r requirements.txt`를 바로 실행하면 `ctranslate2` 빌드 오류가 납니다.

## 코드 구조 (`main.py`)

| 영역 | 줄 | 설명 |
|---|---|---|
| 설정 상수 | 14-41 | 경로, 모델명, 파일 안정화 파라미터, 업무시간, 시스템 프롬프트 |
| `_Tee` / `setup_logging` | 45-86 | stdout/stderr를 터미널+로그파일로 이중 출력 |
| `get_whisper_model` | 99-110 | Whisper 지연 로딩 (싱글턴) |
| `load_processed` / `save_processed` | 113-122 | 처리 이력 JSON |
| `wait_for_stable` | 125-148 | Syncthing 쓰기 완료 대기 (크기 3회 연속 동일) |
| `run_stt` / `run_summary` | 151-173 | STT / 요약 |
| `process_file` | 176-222 | 한 파일의 전체 처리 |
| `worker_loop` | 228-242 | **직렬** 처리 워커 (단일 스레드) |
| `RecordingHandler` | 245-277 | watchdog 이벤트 → 큐 투입, 중복 방지 |
| `scan_existing` | 280-290 | 시작 시 미처리 파일 수집 |
| `main` | 293-337 | 업무시간 루프, observer 생명주기 |

## 설계상 지켜야 할 것

- **STT는 반드시 직렬.** `WhisperModel` 인스턴스는 동시 `transcribe()` 호출에 안전하지 않고,
  large-v3는 CPU/메모리를 모두 점유합니다. `_work_queue` + 단일 워커 구조를 깨지 마세요.
- **빈 결과를 LLM에 넘기지 말 것.** 무음/녹음 실패로 STT 결과가 비면 요약을 건너뜁니다.
  빈 텍스트를 요약시키면 상담 내용을 창작합니다 — 의료 맥락에서 심각한 오작동입니다.
- **요약 프롬프트는 보수적으로.** `SYSTEM_PROMPT`는 "텍스트에 없는 내용 추가·추론 금지"를 명시합니다.
  이 제약을 완화하지 마세요.
- **오류는 삼키되 죽지 말 것.** 한 파일이 실패해도 시스템 전체는 계속 실행되어야 합니다.
- **모든 출력은 `log.txt`에도 남아야 함.** `pythonw` 실행 시 터미널이 없어 로그가 유일한 진단 수단입니다.

## 알려진 이슈

윈도우 테스트 전 확인이 필요한 항목이 있습니다. 상세 분석·검증 절차·수정 우선순위는
**`PLAN-windows-test.md`** 를 참고하세요.

우선순위 요약:

| 순위 | 이슈 | 위치 |
|---|---|---|
| 1 | `requirements.txt`의 `ollama>=0.3.0`과 `response.message` 접근 방식 불일치 → 요약 항상 실패 | `main.py:169` |
| 2 | `processed.json` 손상 시 예외 처리 없이 시작 즉시 크래시 | `main.py:113-122`, `300` |
| 3 | `_Tee`에 `isatty`/`encoding`/`fileno` 미구현 → tqdm 진행바 사용 시 AttributeError | `main.py:45-76` |
| 4 | `scan_existing`의 `f.stat()` 예외가 프로세스를 종료시킴 | `main.py:284` |
| 5 | 업무시간 경계에서 handler 재생성 → 큐 잔여 파일 중복 처리 가능 | `main.py:314` |
| 6 | STT/저장 실패 파일이 재시작 전까지 재시도되지 않음 | `main.py:186-204` |

## 테스트

로컬(Linux)에서는 문법 검사만 가능합니다.

```bash
python -m py_compile main.py
```

실제 동작 검증은 윈도우에서 `PLAN-windows-test.md`의 Step 0~10을 순서대로 진행합니다.
테스트 시 `WORK_START`/`WORK_END`/`WORK_DAYS`(`main.py:32-34`)를 임시로 넓혀야 하며,
**Step 9에서 반드시 원복**해야 합니다.

## 문서 갱신 규칙

코드를 수정하면 함께 갱신합니다.

- `README.md` — 사용자용. 설치/운영 절차와 하단 "변경 내역"
- `CLAUDE.md` — 이 파일. 코드 구조 표의 줄 번호와 "알려진 이슈" 표
- `PLAN-windows-test.md` — 해결된 이슈는 0장으로 옮기고 해당 Step을 회귀 확인용으로 정리
