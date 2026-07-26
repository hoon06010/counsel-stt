# 상담 녹음 자동 STT + 요약 시스템

Galaxy Tab에서 녹음한 상담 음성을 Galaxy Book4 Edge가 자동으로 텍스트 변환·요약하여 태블릿으로 돌려보내는 완전 로컬 시스템입니다.  
모든 데이터는 두 기기 안에서만 처리됩니다.

---

## 폴더 구조

```
C:\Users\{사용자명}\상담\
├── recordings\    ← 태블릿 녹음 파일(m4a) 수신 (Syncthing 동기화)
├── results\       ← 변환 텍스트 + 요약 파일 저장 (Syncthing 동기화)
├── processed.json ← 처리 완료 파일 기록
└── log.txt        ← 실행 로그 (5MB 초과 시 log.old.txt로 보관)
```

스크립트를 처음 실행하면 폴더가 자동으로 생성됩니다.

---

## 설치 순서

### 1. Ollama 설치 및 EXAONE 모델 다운로드

1. [https://ollama.com/download](https://ollama.com/download) 에서 **Windows** 설치 파일을 받아 실행합니다.
2. 설치 후 터미널(PowerShell)을 열고 EXAONE 모델을 다운로드합니다.

```powershell
ollama pull exaone3.5:7.8b
```

3. 다운로드 완료 후 Ollama가 백그라운드에서 자동 실행됩니다.  
   시스템 트레이에 Ollama 아이콘이 표시되면 정상입니다.

---

### 2. Syncthing 설치 및 폴더 연결

Syncthing은 두 기기(Galaxy Tab, Galaxy Book4 Edge)에 각각 설치해야 합니다.

#### Galaxy Book4 Edge (Windows)

관리자 권한이 없으므로 **무설치(portable) 방식**으로 사용자 폴더에 설치합니다.

1. [Syncthing 릴리스](https://github.com/syncthing/syncthing/releases/latest)에서
   `syncthing-windows-arm64-<버전>.zip` 을 받아 압축을 풀고,
   내용을 `%LOCALAPPDATA%\Programs\Syncthing` 에 복사합니다. (ARM64 네이티브 빌드)
2. 첫 실행 전에 설정과 장치 키를 만듭니다.
   ```powershell
   & "$env:LOCALAPPDATA\Programs\Syncthing\syncthing.exe" generate
   ```
3. `%LOCALAPPDATA%\Syncthing\config.xml` 에서 외부 통신을 끕니다 (상담 음성이 외부 서버를 경유하지 않도록).

   | 항목 | 값 | 이유 |
   |---|---|---|
   | `globalAnnounceEnabled` | `false` | 외부 디스커버리 서버 미사용 |
   | `relaysEnabled` | `false` | 외부 릴리스 경유 차단 |
   | `natEnabled` | `false` | 공유기 포트 개방 안 함 |
   | `crashReportingEnabled` | `false` | 크래시 리포트 전송 안 함 |
   | `urAccepted` | `-1` | 사용 통계 전송 거부 |
   | `startBrowser` | `false` | 자동 실행 시 브라우저 안 뜨게 |
   | `localAnnounceEnabled` | `true` (유지) | 같은 WiFi에서 태블릿 탐색 |

   이 설정에서는 **두 기기가 같은 WiFi에 있을 때만** 동기화됩니다.
4. 로그인 시 자동 실행되도록 시작프로그램 폴더
   (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`)에 바로가기를 만듭니다.
   - 대상: `%LOCALAPPDATA%\Programs\Syncthing\syncthing.exe`
   - 인수: `serve --no-console --no-browser --log-file="%LOCALAPPDATA%\Syncthing\syncthing.log"`
5. 브라우저에서 `http://127.0.0.1:8384` 에 접속하면 Syncthing 관리 화면이 열립니다.
6. 오른쪽 상단 **장치 ID**를 메모해 둡니다.

#### Galaxy Tab (Android)

1. Play 스토어에서 **Syncthing** 앱을 설치합니다.
2. 앱을 열고 오른쪽 상단 **장치 ID**를 확인합니다.
3. **기기 추가** → Galaxy Book4 Edge의 장치 ID를 입력해 연결 요청을 보냅니다.
4. Galaxy Book4 Edge의 Syncthing 화면에서 연결 수락 알림을 승인합니다.

#### 폴더 공유 설정

| 폴더 | 태블릿 역할 | 노트북 역할 |
|------|------------|------------|
| `recordings` | **보내기 전용** | **받기 전용** |
| `results` | **받기 전용** | **보내기 전용** |

1. Galaxy Book4 Edge Syncthing에서 **폴더 추가**를 클릭합니다.
2. `recordings` 폴더 경로를 `C:\Users\{사용자명}\상담\recordings`로 설정하고, 역할을 **받기 전용**으로 설정합니다.
3. 같은 방법으로 `results` 폴더를 추가하고 역할을 **보내기 전용**으로 설정합니다.
4. 태블릿 Syncthing에서 각 폴더를 수락하고 역할을 반대로 설정합니다.

---

### 3. 덮개를 닫아도 동작하도록 설정 (Windows 전원 옵션)

노트북 덮개를 닫은 상태에서도 STT 처리가 계속 실행되도록 설정합니다.

> **Galaxy Book4 Edge는 제어판에 이 항목이 없습니다.**
> 최신 대기 모드(Modern Standby) 기기라 `덮개를 닫을 때 설정` 페이지 자체가 숨겨져 있습니다
> (레지스트리 `LIDACTION` 의 `Attributes=1`). 아래 명령으로 직접 설정합니다.
> **관리자 권한은 필요 없습니다.**

```powershell
# 덮개를 닫을 때: 아무 것도 하지 않음 (전원 연결 시 / 배터리 사용 시 모두)
powercfg /setacvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0
powercfg /setdcvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0

# 절전 모드로 전환: 안 함
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0

# 무인 절전 시간 제한: 안 함 (절전에서 깨어난 뒤 2분 만에 다시 자는 것 방지)
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP 7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP 7bc4a2f9-d8fc-4469-b07b-33eb785aaca0 0

# 변경 사항 적용
powercfg /setactive SCHEME_CURRENT
```

원래대로 되돌리려면 마지막 인자 `0`을 `1`(절전)로 바꿔 다시 실행합니다.

확인:

```powershell
powercfg /q SCHEME_CURRENT SUB_SLEEP
```

`STANDBYIDLE`의 AC/DC 설정 색인이 모두 `0x00000000`이면 정상입니다.

> **작업 스케줄러 작업도 함께 확인하세요.**
> 작업 속성 → **조건** 탭에서 `컴퓨터의 AC 전원이 켜져 있는 경우에만 작업 시작`과
> `컴퓨터가 배터리 전원으로 전환되면 중지`가 **모두 해제**되어 있어야
> 배터리 상태에서 덮개를 닫아도 처리가 계속됩니다.

> **덮개를 닫은 채로 돌릴 때는 전원 어댑터를 연결해 두세요.**
> large-v3 STT는 CPU를 계속 100% 가까이 쓰기 때문에 배터리 소모가 매우 빠릅니다.
> 또한 배터리 상태로 3일간 방치하면 최대 절전 모드로 들어갑니다(`HIBERNATEIDLE` 기본값).

---

### 4. Python 설치 및 패키지 설치

1. [https://www.python.org/downloads/](https://www.python.org/downloads/) 에서 Python 3.12 이상을 설치합니다.  
   설치 시 **"Add Python to PATH"** 옵션을 반드시 체크합니다.

2. 이 프로젝트 폴더로 이동한 뒤 패키지를 설치합니다.

```powershell
cd C:\Users\{사용자명}\Downloads\counsel-stt
```

> **Galaxy Book4 Edge (ARM64) 전용 설치 순서**  
> `pip install -r requirements.txt`를 바로 실행하면 `ctranslate2` 빌드 오류가 납니다.  
> 아래 두 줄을 순서대로 실행해야 합니다.

```powershell
pip install ctranslate2 --only-binary :all:
pip install faster-whisper watchdog ollama
```

`--only-binary :all:` 옵션이 소스 빌드를 차단하고 ARM64용 사전 빌드 wheel을 사용합니다.

---

### 5. 실행

```powershell
python main.py
```

터미널에 아래와 같이 출력되면 정상입니다.

```
처리 완료 기록: 0개
감시 폴더: C:\Users\{사용자명}\상담\recordings
로그 파일: C:\Users\{사용자명}\상담\log.txt
업무 시간: 평일 09:00~18:00 자동 실행
종료하려면 Ctrl+C
```

화면에 나오는 모든 메시지는 `상담\log.txt`에도 시간과 함께 기록됩니다.

**업무 시간(평일 09:00~18:00) 동안만 파일 감시가 활성화됩니다.**  
업무 시간 외에는 아래처럼 대기 상태로 유지되다가 다음 날 자동으로 재시작합니다.

```
[대기] 05/23 18:00 업무 시간 종료 — 내일 09:00에 재시작
[시작] 05/26 09:00 업무 시간 — 감시 시작
```

이후 태블릿에서 녹음을 저장하면 업무 시간 내에 자동으로 처리됩니다.

```
[감지] 상담_2025-01-15.m4a
[STT] 변환 중... (상담_2025-01-15.m4a)
[STT] 완료 (상담_2025-01-15.m4a)
[요약] 요약 중... (상담_2025-01-15.m4a)
[요약] 완료 (상담_2025-01-15.m4a)
[완료] 상담_2025-01-15.txt / 상담_2025-01-15_요약.txt 저장됨
```

---

### 6. Windows 시작 시 자동 실행 등록

로그인할 때마다 `main.py`가 자동으로 실행되도록 작업 스케줄러에 등록합니다.
PowerShell 창을 열고 아래를 그대로 붙여넣습니다. **관리자 권한이 필요하지 않습니다.**

```powershell
$action = New-ScheduledTaskAction -Execute "C:\counsel-stt-main\.venv\Scripts\pythonw.exe" `
    -Argument "main.py" -WorkingDirectory "C:\counsel-stt-main"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = "PT1M"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "counsel-stt" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Description "Counsel STT watcher (pythonw, no console)" -Force
```

각 설정의 이유입니다.

| 설정 | 이유 |
|---|---|
| `.venv\Scripts\pythonw.exe` **전체 경로** | `pythonw`만 쓰면 PATH의 ARM64 파이썬이 잡혀 `ctranslate2` 임포트에서 죽습니다 |
| 트리거 = **로그온 시** | "컴퓨터 시작 시"는 관리자 권한이 필요합니다 |
| `Delay = PT1M` | Syncthing이 먼저 뜨도록 1분 늦춥니다 |
| `MultipleInstances IgnoreNew` | 중복 실행을 막습니다 — STT 직렬 처리 원칙 |
| `ExecutionTimeLimit 0` | 기본값(3일)에 걸려 종료되지 않게 합니다 |
| `AllowStartIfOnBatteries` | 배터리 상태에서도 실행·유지합니다 |

등록 확인과 즉시 실행:

```powershell
Get-ScheduledTask -TaskName "counsel-stt" | Select-Object TaskName, State
Start-ScheduledTask -TaskName "counsel-stt"
```

> `pythonw`는 터미널 창 없이 백그라운드로 실행합니다.  
> 터미널 창이 없어도 모든 로그는 `상담\log.txt`에 기록되므로, 문제가 생기면 이 파일을 확인하세요.  
> 실행 화면을 직접 보고 싶을 때만 `.venv\Scripts\python.exe main.py`로 실행하면 됩니다.

> **업무 시간 밖에 실행하면 로그에 시작 배너만 찍히고 조용합니다.** 정상입니다.
> 평일 09:00 전에는 감시를 시작하지 않으며(`main.py:495`), 이때는 `[대기]` 메시지도 남기지 않습니다.

---

## 처리 상태 메시지

| 메시지 | 의미 |
|--------|------|
| `[감지] 파일명.m4a` | 새 녹음 파일 발견 |
| `[STT] 변환 중...` | Whisper 모델이 텍스트 변환 중 |
| `[STT] 완료` | 텍스트 변환 완료 |
| `[요약] 요약 중...` | EXAONE이 요약 생성 중 |
| `[요약] 완료` | 요약 완료 |
| `[완료] ...` | 결과 파일 저장 완료 |
| `[오류] ...` | 오류 발생 (해당 파일 건너뜀, 시스템은 계속 실행) |

---

## 처리 속도 안내

Galaxy Book4 Edge (Snapdragon X Plus, CPU 전용 기준):

| 녹음 길이 | STT 예상 시간 |
|-----------|--------------|
| 30분 | 약 10~15분 |
| 50분 | 약 15~25분 |

Whisper large-v3 모델은 처음 실행 시 모델 파일을 다운로드합니다 (약 3GB).

---

## 문제 해결

**무엇이 잘못됐는지 모를 때**  
`C:\Users\{사용자명}\상담\log.txt`를 메모장으로 엽니다.  
자동 실행(`pythonw`) 중 발생한 오류도 이 파일에 그대로 기록됩니다.

**Ollama 연결 오류가 발생할 때**  
시스템 트레이에서 Ollama가 실행 중인지 확인합니다. 실행 중이지 않으면 시작 메뉴에서 Ollama를 실행합니다.

**파일이 감지되지 않을 때**  
Syncthing이 실행 중인지, 두 기기가 연결되어 있는지 확인합니다.

**처리 완료 파일을 다시 처리하고 싶을 때**  
`C:\Users\{사용자명}\상담\processed.json`에서 해당 파일명을 삭제하고 스크립트를 재시작합니다.

---

## 변경 내역

### 2026-07-25 (2차 — 윈도우 테스트 반영)

**처리 이력 파일이 손상돼도 시작되도록 수정**
`processed.json`이 깨져 있으면 프로그램이 아무 메시지 없이 즉시 종료되던 문제를 수정했습니다.
이제 경고를 남기고 빈 이력으로 시작합니다. 저장할 때도 임시 파일에 먼저 쓴 뒤 교체해,
저장 도중 컴퓨터가 꺼져도 기존 이력이 남습니다.

**모델 다운로드 중 종료되던 문제 수정**
Whisper 모델을 처음 받을 때 진행률 표시 때문에 오류가 나거나 `log.txt`가 급격히 커지던 문제를 수정했습니다.

**녹음 폴더 주기적 재확인**
10분마다 폴더를 다시 확인해, 변환에 실패했거나 감지되지 않고 지나간 파일을 자동으로 재시도합니다.
같은 파일이 3회 연속 실패하면 더 이상 시도하지 않고 로그에 남깁니다.

**업무 시간 경계에서 같은 파일이 두 번 처리되던 문제 수정**
18:00을 넘겨 처리가 이어질 때, 다음 날 같은 파일이 다시 변환될 수 있던 문제를 수정했습니다.

**폴더 확인 중 파일이 사라져도 계속 실행**
Syncthing이 동기화 중 파일을 옮기면 프로그램 전체가 종료되던 문제를 수정했습니다.

**필요 패키지 버전 정정**
`ollama` 최소 버전을 0.4.0으로 올렸습니다. 이보다 낮은 버전에서는 요약이 항상 실패합니다.

### 2026-07-25

**녹음 파일 직렬 처리**  
미처리 파일이 여러 개 쌓여 있을 때 모두 동시에 변환을 시작해 메모리가 부족해지던 문제를 수정했습니다.  
이제 작업 큐를 통해 한 번에 한 파일씩(오래된 녹음부터) 순차 처리합니다.

**로그 파일 기록**  
모든 실행 메시지와 오류를 `상담\log.txt`에 시간과 함께 남깁니다.  
작업 스케줄러에서 `pythonw`로 자동 실행할 때도 로그를 확인할 수 있습니다.

**빈 결과 처리 보강**  
STT 결과가 비어 있으면(무음·녹음 실패) 건너뛰고, 요약 응답이 비어 있으면 오류로 처리해  
빈 파일이 태블릿으로 전송되지 않도록 했습니다.

### 2026-05-24

**ARM64 설치 오류 수정**  
`pip install -r requirements.txt`로 설치 시 `ctranslate2` 빌드 오류가 발생하는 문제를 수정했습니다.  
ARM64 전용 설치 명령을 사용해야 합니다 (설치 순서 4번 참고).

**업무 시간 자동 실행**  
평일 09:00~18:00에만 파일 감시가 활성화되도록 변경했습니다.  
업무 시간 외에는 대기 상태로 유지되며, 다음 업무 시간이 되면 자동으로 재시작합니다.  
Windows 시작 시 자동 실행(작업 스케줄러)과 함께 설정하면 별도 조작 없이 운영할 수 있습니다.
