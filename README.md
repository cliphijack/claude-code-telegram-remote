# Claude Code 텔레그램 리모컨

**[English](README.en.md)** | 한국어

외출 중에도 스마트폰 텔레그램에서 Mac/Linux에 돌아가는 Claude Code CLI를 직접 제어하는 경량 봇.

- 슬래시 명령(`/ship`, `/compact`, `/cost` 등) 네이티브 주입
- 화면 캡처 텍스트 + PNG 전송 (`/screen`, `/tail N`, `/screenshot`)
- 권한 프롬프트 원격 응답 (`/yes`, `/no`, `/1`, `/2`, ...)
- 위험 명령(`/clear`, `/kill`, `/quit`)은 60초 TTL 확인 토큰으로 보호
- 설치된 모든 스킬(플러그인 포함 `/superpowers:write-plan` 같은 형식도) 자동 지원
- **🆕 Claude 응답 자동 푸시** — 매 턴이 끝나면 응답이 텔레그램으로 자동 전송됨 (`/screen`·`/tail` 안 쳐도 됨)

---

## 이 fork에서 달라진 점 (vs [업스트림](https://github.com/etinpres/claude-code-telegram-remote))

이 fork는 [etinpres/claude-code-telegram-remote](https://github.com/etinpres/claude-code-telegram-remote)에서 분기했고, 다음 세 가지가 추가/변경됐어:

| | 업스트림 | 이 fork |
|---|---|---|
| Linux에서 자유 텍스트 주입 | ❌ `tmux not found in PATH`로 실패 | ✅ `TMUX_BIN` 변수 사용 |
| systemd 서비스가 tmux 소켓 접근 | ❌ `PrivateTmp=true`라 막힘 | ✅ `PrivateTmp=false`로 풀어둠 |
| Claude 응답 → 텔레그램 자동 푸시 | ❌ `/screen`/`/tail`로 풀(pull)만 | ✅ Stop hook으로 자동 푸시 |
| 멀티 팀원 구조 | ❌ 단일 봇만 | ✅ 팀원별 봇 + `CLAUDE.md`로 성격 정의 |

자세한 의도는 각 커밋 메시지 참고. 업스트림에 PR로 보낼 가치도 있는 변경사항들.

---

## 응답 자동 푸시 (`notify_stop.sh`)

`install.sh`가 자동으로 Claude Code의 Stop hook으로 등록함. 동작:

1. Claude Code가 응답 끝낸 시점에 Stop hook 발동
2. `notify_stop.sh`가 transcript JSONL을 읽고 마지막 사용자 prompt 이후의 모든 assistant text 블록을 합침
3. JSONL flush race를 피하려고 결과가 600ms 동안 안 변할 때까지 0.3초 간격으로 폴링 (최대 5초)
4. `tg_notify.sh`로 텔레그램에 전송 (3900자에서 자름)

디버깅 로그: `~/.claude/channels/telegram/notify_stop.log`

> ⚠️ `~/.claude/settings.json`의 hook은 **세션 시작 시점에만** 로드됨. install 직후 활성 세션에서는 `/hooks` 슬래시 명령을 한 번 열었다 닫아야 reload됨.

---

## 요구사항

- macOS (launchd) 또는 Linux with systemd
- **Windows: 네이티브 미지원. [WSL2](#windows-wsl2-안내) 안에서 Linux 경로로 사용**
- `python3`, `curl`, **`tmux` (필수)**
- 텔레그램 계정

> **⚠️ tmux는 이 도구의 핵심 전제조건.** 봇은 tmux의 `send-keys`로 Claude Code가 돌아가는 pane에 키 시퀀스와 슬래시 명령을 주입함. tmux 없이 실행 중인 Claude Code는 제어 불가능 — 네이티브 터미널 창은 외부 프로세스가 키를 꽂을 방법이 없음.

---

## Windows (WSL2) 안내

네이티브 Windows는 미지원 — tmux / launchd / systemd 셋 다 없기 때문. 대신 **WSL2(Windows Subsystem for Linux 2)** 안에서 리눅스용 설치 경로를 그대로 따라가면 됨.

### WSL2 = 뭐?

Microsoft 공식 기능. Windows 안에서 진짜 리눅스(기본 Ubuntu)를 돌리는 가상 환경. 설치 한 줄, 무료.

### WSL2 설치 (PowerShell 관리자 권한)

```powershell
wsl --install
```

- Windows 10 (빌드 19041+) 또는 Windows 11 필요
- 재부팅 한 번 후 Ubuntu 터미널 자동 실행 → 사용자명/비밀번호 설정
- 이후 시작 메뉴에서 "Ubuntu" 실행하면 리눅스 셸 진입

### WSL2 안에서 우리 리모컨 설치

Ubuntu 터미널 안에서 **리눅스 경로 그대로** 실행:

```bash
sudo apt update && sudo apt install -y python3 curl tmux git
git clone https://github.com/etinpres/claude-code-telegram-remote.git \
  ~/.claude/channels/telegram
cd ~/.claude/channels/telegram
./install.sh
```

install.sh가 자동으로 systemd --user 서비스로 등록함.

### Claude Code도 WSL2 안에서 실행

Windows CMD / PowerShell이 아니라 **Ubuntu 터미널 → tmux 세션 → claude** 순서로 실행해야 봇이 제어 가능.

```bash
tmux new -s cc
claude   # WSL2 Ubuntu 안에서
```

### 주의사항

- WSL2는 기본적으로 Windows 로그아웃하면 꺼짐. `loginctl enable-linger`로 유지 가능(install.sh가 시도하지만 WSL2에선 효과 제한적) — **사실상 Windows 로그인 유지 + Ubuntu 터미널 하나 띄워놓는 게 가장 확실**.
- `/screenshot`은 WSL2에선 Linux 도구(`grim` 등) 설치해도 WSL2엔 디스플레이 서버가 없어서 캡처 불가. `/screen`/`/tail`(텍스트)은 정상 작동.
- 성능 팁: 코드 저장소는 WSL2 파일시스템(`~/`)에 두고 작업. Windows 드라이브(`/mnt/c/...`)는 I/O 느림.

---

## tmux 설치 + 세션 세팅

### tmux 설치

| OS | 명령 |
|----|------|
| macOS (Homebrew) | `brew install tmux` |
| Ubuntu / Debian / WSL2 | `sudo apt update && sudo apt install -y tmux` |
| Fedora / RHEL | `sudo dnf install -y tmux` |
| Arch | `sudo pacman -S tmux` |

설치 확인: `tmux -V` → `tmux 3.x` 출력.

### Claude Code를 tmux 안에서 실행

네이티브 터미널에서 바로 `claude` 하지 말고 **항상 tmux 세션 안에서** 실행:

```bash
tmux new -s cc     # "cc" 이름의 새 세션 생성, 그 안에 들어감
claude             # 이제 Claude Code가 tmux pane에서 돌아감
```

세션 분리(나가되 계속 실행): `Ctrl-b` → `d`
재접속: `tmux attach -t cc`

### TMUX_TARGET 값 찾기

`.env`에 적을 `TMUX_TARGET` 값은 `session:window.pane` 형식:

```bash
tmux list-panes -a
```

출력 예:
```
cc:0.0: [80x24] [history 0/2000, 0 bytes] %2 (active)
```

→ 복사할 값: `cc:0.0`

세션/윈도우/페인 하나만 있으면 보통 `0:0.0` 같은 단순한 값이 나옴.

> **팁**: tmux 창을 닫거나 세션을 종료하면 `TMUX_TARGET`이 사라져 봇 명령이 전부 실패함. 데몬은 매번 `tmux send-keys`에 대상을 명시하기 때문에 tmux 세션이 살아있어야 함. macOS에선 로그아웃해도 tmux는 유지되고, Linux는 `loginctl enable-linger`를 install.sh가 자동 처리.

---

## 설치 (3분)

### 1. Telegram 봇 생성

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)와 대화 시작
2. `/newbot` → 이름/username 입력 → **봇 토큰** 받기 (예: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
3. 생성된 봇에게 아무 메시지나 먼저 전송 (안 하면 봇이 답장 못 함)

### 2. Chat ID 확인

텔레그램에서 [@userinfobot](https://t.me/userinfobot)에게 메시지 → 응답에 `Id: 123456789` 형태의 **숫자**. 이게 본인 chat ID이자 user ID.

### 3. 저장소 클론 + 설치

```bash
git clone https://github.com/etinpres/claude-code-telegram-remote.git \
  ~/.claude/channels/telegram
cd ~/.claude/channels/telegram
./install.sh
```

첫 실행에서 `.env` 템플릿이 생성되고 스크립트가 멈춤. `.env`를 열어서:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...    # 1번에서 받은 토큰
TELEGRAM_CHAT_ID=123456789                    # 2번에서 확인한 숫자
TMUX_TARGET=0:0.0                             # Claude Code가 돌아가는 tmux 세션
ALLOWED_USER_IDS=123456789                    # 본인 user ID (콤마로 여러 명)
```

`TMUX_TARGET` 찾는 법은 위 **"tmux 설치 + 세션 세팅"** 섹션 참고.

다시 `./install.sh` 실행 → 서비스 등록 + 자동 기동.

### 4. 동작 확인

텔레그램에서 본인 봇에게 `/help` 전송. 명령어 목록이 답장으로 오면 성공.

---

## 주요 명령어

| 명령 | 설명 |
|------|------|
| `/help` | 명령어 목록 |
| `/screen`, `/tail N` | 현재 tmux 화면을 텍스트로 전송 |
| `/screenshot` | 화면을 PNG로 전송 ([플랫폼별 지원 범위 ↓](#screenshot-플랫폼별-지원)) |
| `/cancel`, `/esc`, `/enter` | 키 이벤트 주입 |
| `/yes`, `/no`, `/1`~`/9` | 옵션 프롬프트 응답 |
| `/compact`, `/cost`, `/agents`, `/model <n>` | Claude Code 네이티브 슬래시 |
| `/ship`, `/review`, `/assemble` 등 | 사용자 스킬 슬래시 (자동 인식) |
| `/clear`, `/kill`, `/quit` | 위험 — `/confirm <token>`으로 60초 내 승인 필요 |
| `/restart [x]` | 종료된 pane에서 Claude Code 재기동 (`x` = `--dangerously-skip-permissions` bypass 모드) |
| 그 외 `/...` | Claude Code TUI로 그대로 전달, 모르는 명령은 "Unknown command" |

---

## `/screenshot` 플랫폼별 지원

| 플랫폼 | 지원 범위 | 비고 |
|--------|-----------|------|
| **macOS** | ✅ 풀 지원 | 창 단위 캡처 가능 (아래 macOS 설정 참고) |
| **Linux 데스크탑 (X11/Wayland)** | ⚠️ 전체 화면만 | `grim`·`scrot`·`gnome-screenshot`·`maim` 중 하나 필요 |
| **WSL2** | ❌ 불가 | 디스플레이 서버 없음 — `/screen`·`/tail`로 대체 |
| **헤드리스 서버** | ❌ 불가 | 같은 이유 |

Linux에서 도구 설치:
```bash
# Ubuntu/Debian (Wayland)
sudo apt install -y grim

# 또는 X11
sudo apt install -y scrot
```

봇이 자동으로 PATH에서 `grim → gnome-screenshot → scrot → maim` 순으로 찾음.

---

## macOS 추가 설정

### 1. 화면 녹화 권한 (필수)

`/screenshot`이 동작하려면 launchd가 실행하는 `python3` 바이너리에 **화면 녹화** 권한이 있어야 함.

1. **시스템 설정 → 개인정보 보호 및 보안 → 화면 녹화**
2. `+` 버튼 → `/usr/bin/python3` 추가 (또는 `install.sh`가 plist에 박은 파이썬 경로)
3. 토글 ON
4. 권한 부여 후 데몬 재기동:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.$(whoami).tg-poll
   ```

> **주의**: `/Library/Frameworks/Python.framework/Versions/3.x/bin/python3`는 심볼릭 링크라 TCC가 인식 못 함. 실제 번들 경로(`.../Resources/Python.app`)를 추가해야 하는 경우가 있음. 가장 확실한 건 `/usr/bin/python3` 사용 (install.sh 기본값).

### 2. 창 단위 캡처용 pyobjc (선택)

`.env`에 `TERMINAL_APP`을 지정하면 **그 앱의 창만** 캡처함 (데스크탑 전체가 아니라). Quartz(CoreGraphics)로 창 ID를 찾는 과정에서 pyobjc가 필요:

```bash
pip3 install --user pyobjc-framework-Quartz
```

설치 안 돼 있으면 경고 없이 **전체 화면 캡처로 폴백** — 선택 사항.

`.env` 예시:
```
TERMINAL_APP=Termius     # 또는 iTerm, Terminal, Warp, Ghostty, kitty, Alacritty, WezTerm
```

대소문자 무관, 부분 일치. 해당 앱의 가장 큰 on-screen 창 하나만 찍음.

### 3. Homebrew python3 사용 시

install.sh가 `command -v python3` 결과를 plist에 박기 때문에 Homebrew 파이썬(`/opt/homebrew/bin/python3`)이 먼저 걸리면 그 경로로 등록됨. 이 경우:

- **화면 녹화 권한**은 Homebrew python3 경로에 추가해야 함
- 또는 plist를 수정해서 `/usr/bin/python3`로 바꾸고 kickstart

권한은 **plist가 실제로 실행하는 바이너리**에 붙이는 게 원칙.

---

## 보안

- `ALLOWED_USER_IDS`에 등록되지 않은 chat은 **무응답 fail-secure**
- `.env`는 `chmod 600`, git `.gitignore`에 포함
- 위험 명령은 UUID 토큰 + 60초 TTL로 실수 방지
- macOS `/screenshot`은 화면 녹화 권한 필요 — [macOS 추가 설정](#macos-추가-설정) 참고

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 봇이 답장 안 함 | `ALLOWED_USER_IDS` 누락 | `.env` 확인 후 서비스 재기동 |
| `/screenshot` 실패 | 스크린 녹화 권한 (macOS) / 도구 없음 (Linux) | macOS 권한 부여 / `sudo apt install grim` 등 |
| 서비스 죽어있음 (macOS) | launchd 기동 실패 | `launchctl kickstart -k gui/$(id -u)/com.$(whoami).tg-poll`, 로그는 `launchd.err.log` |
| 서비스 죽어있음 (Linux) | systemd 기동 실패 | `systemctl --user status tg-poll`, `journalctl --user -u tg-poll` |
| tmux 명령이 안 들어감 | `TMUX_TARGET` 세션 없음 | `tmux list-panes -a`로 재확인 |

---

## 제거

```bash
./install.sh --uninstall
```

`.env`와 로그는 남음. 완전히 지우려면 `rm -rf ~/.claude/channels/telegram`.

---

## 멀티 팀원 구조 (Multi-Agent)

Claude 세션 하나 = 팀원 하나. 각 팀원이 독립된 텔레그램 봇과 tmux 세션을 가진다.

```
텔레그램 그룹방
  ├── 메인 봇 (나) ────→ tmux cc:0 (Claude Code 메인)
  ├── team1 봇 ─────→ tmux team1:0 (역할별 Claude)
  └── team2 봇 ─────→ tmux team2:0 (역할별 Claude)
```

### 팀원 추가 방법

**1. 봇 생성**
BotFather에서 새 봇 토큰 발급.

**2. 팀원 디렉토리 구조**
```
~/team1/
  ├── CLAUDE.md          # 팀원 성격·역할 정의
  ├── start.sh           # 원클릭 세션 시작
  └── telegram/
       ├── .env          # 팀원 봇 토큰 + TMUX_TARGET
       ├── tg_poll.py    # 이 repo에서 복사
       └── tg_notify.sh  # 이 repo에서 복사
```

**3. `.env` 설정**
```bash
TELEGRAM_BOT_TOKEN=<팀원 봇 토큰>
TELEGRAM_CHAT_ID=<채팅 ID>
TMUX_TARGET=team1:0.0
ALLOWED_USER_IDS=<허용할 유저 ID>
```

**4. `CLAUDE.md` 작성 — 팀원 성격 정의**
```markdown
# Team1 — 긍정적인 해결사
어떤 문제가 와도 "일단 해보자"가 출발점이다.
막히면 우회로를 찾고, 안 되면 다른 방법을 제안한다.

## 역할
- 막힌 문제 뚫기
- 대안 제시
```

**5. `start.sh` — 원클릭 세션 시작**
```bash
#!/bin/bash
# 미수신 메시지 스킵 (재시작 시 자동 주입 방지)
python3 -c "
import urllib.request, json, pathlib
token = [l.split('=',1)[1].strip() for l in open('telegram/.env').read().splitlines() if l.startswith('TELEGRAM_BOT_TOKEN')][0]
data = json.loads(urllib.request.urlopen(f'https://api.telegram.org/bot{token}/getUpdates?offset=-1&limit=1', timeout=5).read())
updates = data.get('result', [])
if updates:
    state = {'last_update_id': updates[-1]['update_id']}
    pathlib.Path('telegram/state.json').write_text(json.dumps(state))
"

tmux kill-session -t team1 2>/dev/null
tmux new-session -d -s team1 -c ~/team1
tmux send-keys -t team1:0 'claude' Enter
tmux new-window -t team1 -c ~/team1/telegram
tmux send-keys -t team1:1 'python3 tg_poll.py' Enter
tmux select-window -t team1:0
tmux attach -t team1
```

**6. 자동 응답 라우팅 (Stop hook)**

`notify_stop.sh`를 팀원 수만큼 복사하고, `transcript_path`로 분기:

```bash
# notify_stop_main.sh (라우터)
TPATH=$(printf '%s' "$INPUT" | grep -o '"transcript_path":"[^"]*"' || true)
if [[ "$TPATH" == *-team1* ]]; then
    exec ~/team1/telegram/notify_stop.sh
else
    exec ~/.claude/channels/telegram/notify_stop.sh
fi
```

각 팀원의 `notify_stop.sh`는 해당 팀원의 `tg_notify.sh`를 호출하도록 경로만 수정.

---

## 아키텍처

```
Telegram (long-polling)
    ↓
tg_poll.py (launchd/systemd 상주)
    ├── tg_commands.dispatch() → action 분류
    │    ├── RAW_SLASH / SKILL_SLASH (allowlist)
    │    ├── Pattern passthrough (모든 /well-formed 슬래시)
    │    ├── KEY_COMMANDS (ctrl-c, esc, enter, 숫자키 …)
    │    ├── DANGEROUS + /confirm (TTL 60s)
    │    └── fallback_prefix (평문 메시지)
    ↓
tmux send-keys → Claude Code TUI (별도 tmux 세션)
```

순수 함수 디스패치 + 데몬 I/O 분리라 테스트 커버리지 확보(38 단위 테스트)가 쉽고 확장 용이.

---

## 라이선스

MIT
