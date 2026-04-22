# Claude Code 텔레그램 리모컨

외출 중에도 스마트폰 텔레그램에서 Mac/Linux에 돌아가는 Claude Code CLI를 직접 제어하는 경량 봇.

- 슬래시 명령(`/ship`, `/compact`, `/cost` 등) 네이티브 주입
- 화면 캡처 텍스트 + PNG 전송 (`/screen`, `/tail N`, `/screenshot`)
- 권한 프롬프트 원격 응답 (`/yes`, `/no`, `/1`, `/2`, ...)
- 위험 명령(`/clear`, `/kill`, `/quit`)은 60초 TTL 확인 토큰으로 보호
- 설치된 모든 스킬(플러그인 포함 `/superpowers:write-plan` 같은 형식도) 자동 지원

---

## 요구사항

- macOS (launchd) 또는 Linux with systemd (WSL2도 Linux 경로로 작동)
- `python3`, `curl`, `tmux` 설치
- 텔레그램 계정

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
git clone https://github.com/YOUR_ACCOUNT/claude-code-telegram-remote.git \
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

`TMUX_TARGET` 찾는 법: Claude Code를 tmux 안에서 실행 중인 상태에서 `tmux list-panes -a` → `session:window.pane` 형식 복사.

다시 `./install.sh` 실행 → 서비스 등록 + 자동 기동.

### 4. 동작 확인

텔레그램에서 본인 봇에게 `/help` 전송. 명령어 목록이 답장으로 오면 성공.

---

## 주요 명령어

| 명령 | 설명 |
|------|------|
| `/help` | 명령어 목록 |
| `/screen`, `/tail N` | 현재 tmux 화면을 텍스트로 전송 |
| `/screenshot` | 화면을 PNG로 전송 (macOS: `screencapture` / Linux: `grim`·`scrot`·`gnome-screenshot`·`maim` 중 하나 필요) |
| `/cancel`, `/esc`, `/enter` | 키 이벤트 주입 |
| `/yes`, `/no`, `/1`~`/9` | 옵션 프롬프트 응답 |
| `/compact`, `/cost`, `/agents`, `/model <n>` | Claude Code 네이티브 슬래시 |
| `/ship`, `/review`, `/assemble` 등 | 사용자 스킬 슬래시 (자동 인식) |
| `/clear`, `/kill`, `/quit` | 위험 — `/confirm <token>`으로 60초 내 승인 필요 |
| `/restart [x]` | 종료된 pane에서 Claude Code 재기동 (`x`는 bypass 모드) |
| 그 외 `/...` | Claude Code TUI로 그대로 전달, 모르는 명령은 "Unknown command" |

---

## 보안

- `ALLOWED_USER_IDS`에 등록되지 않은 chat은 **무응답 fail-secure**
- `.env`는 `chmod 600`, git `.gitignore`에 포함
- 위험 명령은 UUID 토큰 + 60초 TTL로 실수 방지
- macOS 시스템 설정 → 개인정보보호 → **화면 녹화**에 `/usr/bin/python3` 허용해야 `/screenshot` 동작

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
