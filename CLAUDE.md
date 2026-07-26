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
| 설정 상수 | 15-46 | 경로, 모델명, 파일 안정화 파라미터, 업무시간, 재스캔 주기, 시스템 프롬프트 |
| `_Tee` / `setup_logging` | 49-131 | stdout/stderr를 터미널+로그파일로 이중 출력, 파일 객체 인터페이스 포함 |
| `ALWAYS_ON` / `is_work_hours` | 134-143 | 업무시간 판정 (`COUNSEL_STT_ALWAYS_ON=1`로 테스트 시 우회) |
| `get_whisper_model` | 149-160 | Whisper 지연 로딩 (싱글턴) |
| `load_processed` / `save_processed` | 163-203 | 처리 이력 JSON (손상 내성 읽기 + 원자적 쓰기) |
| `wait_for_stable` | 206-229 | Syncthing 쓰기 완료 대기 (크기 3회 연속 동일) |
| `run_stt` / `run_summary` | 232-254 | STT / 요약 |
| `_record_failure` | 257-269 | 실패 횟수 기록, `MAX_RETRIES` 초과 시 재시도 중단 |
| `process_file` | 272-321 | 한 파일의 전체 처리 |
| `worker_loop` | 327-345 | **직렬** 처리 워커 (단일 스레드) |
| `_in_progress` / `_failures` | 348-352 | 진행 중·실패 목록 (**전역** — handler 수명과 분리) |
| `RecordingHandler` | 355-393 | watchdog 이벤트 → 큐 투입, 중복 방지 |
| `scan_existing` | 396-426 | 미처리 파일 수집 (시작 시 + `RESCAN_INTERVAL` 주기) |
| `main` | 429-473 | 업무시간 루프, observer 생명주기, 주기 재스캔 |

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

`PLAN-windows-test.md`의 A-1~A-6은 **2026-07-25 모두 수정 완료**했습니다.
검증 결과와 남은 항목은 `PLAN-windows-test.md`를 참고하세요.

남은 확인 항목:

| 이슈 | 내용 |
|---|---|
| 실행 환경 | ARM64 네이티브 Python으로는 `ctranslate2`/`av` 휠이 없어 **x64 Python venv(`.venv`)** 를 써야 합니다. 에뮬레이션이라 STT가 느립니다 |
| 처리 속도 | `README.md`의 "30분 → 10~15분" 수치는 실측과 다를 수 있습니다 (아래 참고) |
| 콘솔 인코딩 | 윈도우 기본 cp949 — 실행 시 `PYTHONUTF8=1` 권장. `log.txt`는 UTF-8로 정상 기록됨 |
| 덮개 닫힘 동작 | Modern Standby 기기라 제어판에 `덮개를 닫을 때 설정`이 없습니다. `powercfg`로 설정(관리자 권한 불필요, `README.md` 3장). 2026-07-26 설정 적용 완료, **덮개 닫고 실제 처리되는지는 미검증** |

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
