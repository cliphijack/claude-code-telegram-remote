# Claude Code Telegram Remote

English | **[한국어](README.md)**

A lightweight bot that lets you drive the Claude Code CLI on your Mac/Linux box directly from Telegram on your phone — even when you're away from your desk.

- Native slash command injection (`/ship`, `/compact`, `/cost`, etc.)
- Screen capture as text + PNG (`/screen`, `/tail N`, `/screenshot`)
- Remote answers to permission prompts (`/yes`, `/no`, `/1`, `/2`, ...)
- Dangerous commands (`/clear`, `/kill`, `/quit`) are guarded by a 60-second TTL confirm token
- Automatic support for every installed skill (including plugin-style names like `/superpowers:write-plan`)

---

## Requirements

- macOS (launchd) or Linux with systemd
- **Windows: not supported natively. Use [WSL2](#windows-wsl2-guide) and follow the Linux path**
- `python3`, `curl`, **`tmux` (required)**
- A Telegram account

> **⚠️ tmux is the core prerequisite of this tool.** The bot injects key sequences and slash commands into the pane running Claude Code via tmux's `send-keys`. Claude Code running outside tmux cannot be controlled — there's no way for an external process to inject keys into a native terminal window.

---

## Windows (WSL2) Guide

Native Windows isn't supported — no tmux, launchd, or systemd. Instead, run inside **WSL2 (Windows Subsystem for Linux 2)** and follow the Linux installation path verbatim.

### What's WSL2?

An official Microsoft feature. A virtualized environment that runs a real Linux distribution (Ubuntu by default) inside Windows. One-line install, free.

### Install WSL2 (PowerShell as Administrator)

```powershell
wsl --install
```

- Requires Windows 10 (build 19041+) or Windows 11
- After a single reboot, the Ubuntu terminal opens automatically → set username/password
- Launch "Ubuntu" from the Start menu to enter the Linux shell anytime

### Install the remote inside WSL2

From the Ubuntu terminal, follow the Linux path as-is:

```bash
sudo apt update && sudo apt install -y python3 curl tmux git
git clone https://github.com/etinpres/claude-code-telegram-remote.git \
  ~/.claude/channels/telegram
cd ~/.claude/channels/telegram
./install.sh
```

`install.sh` registers a systemd `--user` service automatically.

### Run Claude Code inside WSL2 too

Not from Windows CMD or PowerShell — launch in this order: **Ubuntu terminal → tmux session → claude**. The bot can only control Claude Code that's running inside that tmux session.

```bash
tmux new -s cc
claude   # inside WSL2 Ubuntu
```

### Caveats

- By default, WSL2 shuts down when you log out of Windows. `loginctl enable-linger` helps (install.sh tries it, but its effect is limited inside WSL2) — in practice, **keeping your Windows session logged in with one Ubuntu terminal open is the most reliable**.
- `/screenshot` doesn't work in WSL2 even with Linux tools installed, because WSL2 has no display server. Use `/screen` / `/tail` (text) instead.
- Performance tip: keep your repo on the WSL2 filesystem (`~/`). Windows drives (`/mnt/c/...`) are slow for I/O.

---

## tmux Install + Session Setup

### Install tmux

| OS | Command |
|----|---------|
| macOS (Homebrew) | `brew install tmux` |
| Ubuntu / Debian / WSL2 | `sudo apt update && sudo apt install -y tmux` |
| Fedora / RHEL | `sudo dnf install -y tmux` |
| Arch | `sudo pacman -S tmux` |

Verify: `tmux -V` → prints `tmux 3.x`.

### Run Claude Code inside tmux

Don't invoke `claude` directly in a native terminal. **Always launch it inside a tmux session**:

```bash
tmux new -s cc     # create and enter a new session named "cc"
claude             # Claude Code now runs inside a tmux pane
```

Detach (leave it running in the background): `Ctrl-b` → `d`
Reattach: `tmux attach -t cc`

### Find your TMUX_TARGET value

The `TMUX_TARGET` value in `.env` uses `session:window.pane` format:

```bash
tmux list-panes -a
```

Example output:
```
cc:0.0: [80x24] [history 0/2000, 0 bytes] %2 (active)
```

→ Value to copy: `cc:0.0`

With a single session/window/pane, it's usually something simple like `0:0.0`.

> **Tip**: Closing the tmux window or killing the session invalidates `TMUX_TARGET` and makes every bot command fail. The daemon re-targets on every `tmux send-keys`, so the session must stay alive. On macOS, tmux survives logout; on Linux, install.sh runs `loginctl enable-linger` automatically.

---

## Install (3 minutes)

### 1. Create a Telegram bot

1. In Telegram, start a chat with [@BotFather](https://t.me/BotFather)
2. `/newbot` → enter name/username → receive the **bot token** (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
3. Send any message to your new bot first (otherwise the bot can't reply to you)

### 2. Get your Chat ID

Message [@userinfobot](https://t.me/userinfobot) in Telegram → the reply contains a number like `Id: 123456789`. That's your Chat ID and user ID (they're the same for personal bots).

### 3. Clone + install

```bash
git clone https://github.com/etinpres/claude-code-telegram-remote.git \
  ~/.claude/channels/telegram
cd ~/.claude/channels/telegram
./install.sh
```

On first run, an `.env` template is created and the script stops. Open `.env` and fill in:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...    # the token from step 1
TELEGRAM_CHAT_ID=123456789                    # the number from step 2
TMUX_TARGET=0:0.0                             # the tmux session running Claude Code
ALLOWED_USER_IDS=123456789                    # your user ID (comma-separated for multiple)
```

See the **"tmux Install + Session Setup"** section above for finding `TMUX_TARGET`.

Re-run `./install.sh` → it registers the service and starts it automatically.

### 4. Verify

From Telegram, send `/help` to your bot. If the command list comes back as a reply, you're good.

---

## Commands

| Command | Description |
|---------|-------------|
| `/help` | List available commands |
| `/screen`, `/tail N` | Send the current tmux screen as text |
| `/screenshot` | Send the screen as a PNG ([platform support ↓](#screenshot-platform-support)) |
| `/cancel`, `/esc`, `/enter` | Inject key events |
| `/yes`, `/no`, `/1`–`/9` | Respond to option prompts |
| `/compact`, `/cost`, `/agents`, `/model <n>` | Claude Code native slashes |
| `/ship`, `/review`, `/assemble`, ... | User skill slashes (auto-detected) |
| `/clear`, `/kill`, `/quit` | Dangerous — requires `/confirm <token>` within 60 seconds |
| `/restart [x]` | Relaunch Claude Code in an exited pane (`x` = bypass mode) |
| Any other `/...` | Forwarded verbatim to the Claude Code TUI; unknown commands return "Unknown command" |

---

## `/screenshot` Platform Support

| Platform | Support | Notes |
|----------|---------|-------|
| **macOS** | ✅ Full | Per-window capture supported (see macOS setup below) |
| **Linux desktop (X11/Wayland)** | ⚠️ Full-screen only | Requires one of `grim`, `scrot`, `gnome-screenshot`, `maim` |
| **WSL2** | ❌ Not available | No display server — fall back to `/screen` / `/tail` |
| **Headless server** | ❌ Not available | Same reason |

Install a Linux tool:
```bash
# Ubuntu/Debian (Wayland)
sudo apt install -y grim

# or X11
sudo apt install -y scrot
```

The bot probes `PATH` in order: `grim → gnome-screenshot → scrot → maim`.

---

## macOS Additional Setup

### 1. Screen Recording permission (required)

For `/screenshot` to work, the `python3` binary that launchd executes must have **Screen Recording** permission.

1. **System Settings → Privacy & Security → Screen Recording**
2. `+` → add `/usr/bin/python3` (or whatever Python path install.sh baked into the plist)
3. Toggle ON
4. Restart the daemon after granting permission:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.$(whoami).tg-poll
   ```

> **Warning**: `/Library/Frameworks/Python.framework/Versions/3.x/bin/python3` is a symlink and TCC won't recognize it. You may need to add the real bundle path (`.../Resources/Python.app`). The most reliable option is `/usr/bin/python3` (install.sh's default).

### 2. pyobjc for window-scoped capture (optional)

Set `TERMINAL_APP` in `.env` to capture **only that app's window**, not the whole desktop. Resolving the window ID via Quartz (CoreGraphics) requires pyobjc:

```bash
pip3 install --user pyobjc-framework-Quartz
```

Without it, the bot **silently falls back to full-screen capture** — strictly optional.

Example `.env`:
```
TERMINAL_APP=Termius     # or iTerm, Terminal, Warp, Ghostty, kitty, Alacritty, WezTerm
```

Case-insensitive substring match. Captures the largest on-screen window of that app.

### 3. When using Homebrew python3

`install.sh` bakes the output of `command -v python3` into the plist, so if Homebrew's `/opt/homebrew/bin/python3` is picked up first, that path gets registered. In that case:

- **Screen Recording permission** must be granted to the Homebrew python3 path
- Or edit the plist to use `/usr/bin/python3` and kickstart

The rule: grant permission to **the binary the plist actually executes**.

---

## Security

- Chats not listed in `ALLOWED_USER_IDS` get **no reply — fail-secure**
- `.env` is `chmod 600` and included in `.gitignore`
- Dangerous commands are gated by a UUID token with a 60-second TTL to prevent misfires
- `/screenshot` on macOS requires Screen Recording permission — see [macOS Additional Setup](#macos-additional-setup)

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Bot doesn't reply | `ALLOWED_USER_IDS` missing | Check `.env` and restart the service |
| `/screenshot` fails | Screen Recording permission (macOS) / missing tool (Linux) | Grant macOS permission / `sudo apt install grim` etc. |
| Service is down (macOS) | launchd failed to start | `launchctl kickstart -k gui/$(id -u)/com.$(whoami).tg-poll`; logs at `launchd.err.log` |
| Service is down (Linux) | systemd failed to start | `systemctl --user status tg-poll`, `journalctl --user -u tg-poll` |
| tmux commands aren't landing | No session matching `TMUX_TARGET` | Recheck with `tmux list-panes -a` |

---

## Uninstall

```bash
./install.sh --uninstall
```

`.env` and logs are kept. To wipe completely: `rm -rf ~/.claude/channels/telegram`.

---

## Architecture

```
Telegram (long-polling)
    ↓
tg_poll.py (launchd/systemd resident)
    ├── tg_commands.dispatch() → action classification
    │    ├── RAW_SLASH / SKILL_SLASH (allowlist)
    │    ├── Pattern passthrough (every well-formed /slash)
    │    ├── KEY_COMMANDS (ctrl-c, esc, enter, digits …)
    │    ├── DANGEROUS + /confirm (60s TTL)
    │    └── fallback_prefix (plain text)
    ↓
tmux send-keys → Claude Code TUI (separate tmux session)
```

Pure-function dispatch plus isolated daemon I/O makes test coverage easy (38 unit tests) and keeps the codebase easy to extend.

---

## License

MIT
