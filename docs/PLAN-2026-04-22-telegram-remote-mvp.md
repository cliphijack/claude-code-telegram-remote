# Telegram Remote Control MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `~/.claude/channels/telegram/tg_poll.py` so Telegram becomes a real remote control for Claude Code — routing native slash commands, raw key sequences, and screen-capture requests through tmux while preserving prompt-injection safety.

**Architecture:** Extract command routing into a new pure-functional module (`tg_commands.py`) that returns a `CommandResult` describing the action. The poller stays thin and delegates I/O only (tmux send-keys, screencapture, Telegram outbound). A persistent confirmation state file guards dangerous commands with a 60-second TTL. A chat/user allowlist gate runs before any dispatch. Plain-text messages continue to use the existing prefixed-inject path — nothing about the current chat behaviour regresses.

**Tech Stack:** Python 3 stdlib only (no new deps). tmux `send-keys` + `capture-pane` for CLI control. macOS `screencapture` for PNG. Existing `tg_notify.sh` for outbound messages.

---

## Pre-Task: Environment Baseline

**Files:**
- Modify: `~/.claude/channels/telegram/.env`

- [ ] **Step P.1: Add allowlist env var**

Append to `.env`:

```
ALLOWED_USER_IDS=7703208804
```

(Comma-separated numeric Telegram user ids. `7703208804` is the form's id, already used as `CHAT_ID` in `tg_notify.sh`.)

- [ ] **Step P.2: Verify tmux target is set**

```bash
grep TMUX_TARGET ~/.claude/channels/telegram/.env || echo "MISSING"
```

Expected: a line like `TMUX_TARGET=0` (tmux session name). If `MISSING`, append `TMUX_TARGET=0`.

- [ ] **Step P.3: Confirm current daemon is running**

```bash
launchctl list | grep com.yonghaekim.tg
```

Expected: a line with PID. Note the service label for later restart.

---

## Task 1: Scaffold `tg_commands.py` + Test Harness

**Files:**
- Create: `~/.claude/channels/telegram/tg_commands.py`
- Create: `~/.claude/channels/telegram/test_tg_commands.py`

Establish the module with the data types, empty dispatch, and the two cheapest tests (empty text, plain text). This locks the public contract before any branches exist.

- [ ] **Step 1.1: Write failing tests for fallback behaviour**

Create `~/.claude/channels/telegram/test_tg_commands.py`:

```python
"""Unit tests for tg_commands dispatch.

Run:  python3 -m pytest test_tg_commands.py -v
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_commands as tc
from tg_commands import dispatch


def test_empty_text_falls_back():
    r = dispatch("")
    assert r.action == "fallback_prefix"


def test_plain_text_falls_back():
    r = dispatch("hello there")
    assert r.action == "fallback_prefix"
    assert r.payload == "hello there"
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `ModuleNotFoundError: No module named 'tg_commands'`.

- [ ] **Step 1.3: Create `tg_commands.py` skeleton**

Create `~/.claude/channels/telegram/tg_commands.py`:

```python
"""Command dispatch for Telegram → Claude Code remote control.

Pure functions only. No I/O. The poller (tg_poll.py) is responsible for
actually executing the CommandResult.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent
PENDING_FILE = BASE_DIR / "pending.json"
CONFIRM_TTL_SECONDS = 60

Action = Literal[
    "raw_inject",
    "key_inject",
    "screen_text",
    "screen_png",
    "confirm_required",
    "status_reply",
    "fallback_prefix",
]


@dataclass
class CommandResult:
    action: Action
    payload: str = ""
    keys: tuple[str, ...] = ()
    reply_text: str = ""
    pending_token: str = ""
    metadata: dict = field(default_factory=dict)


def dispatch(text: str, now: float | None = None) -> CommandResult:
    """Classify inbound Telegram text into an action for the poller."""
    text = (text or "").strip()
    if not text or not text.startswith("/"):
        return CommandResult(action="fallback_prefix", payload=text)

    # Unknown slash commands fall through to the prefixed path (safe default).
    return CommandResult(action="fallback_prefix", payload=text)
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `2 passed`.

- [ ] **Step 1.5: Commit**

```bash
cd ~/.claude/channels/telegram
git init -q 2>/dev/null || true   # directory is not tracked in main repo
git add tg_commands.py test_tg_commands.py
git commit -m "feat(tg): scaffold command dispatch module" 2>/dev/null || true
```

Note: `~/.claude/channels/telegram/` isn't part of a repo by default; the `git init` line is best-effort so steps still work if the user adopted versioning. Skip the commit cleanly if git isn't initialised.

---

## Task 2: Raw Slash Passthrough (Tier 1)

**Files:**
- Modify: `~/.claude/channels/telegram/tg_commands.py`
- Modify: `~/.claude/channels/telegram/test_tg_commands.py`

Recognise known Claude Code native and skill slash commands, return `raw_inject` so the poller sends them verbatim to tmux (slash parser sees them as real commands).

- [ ] **Step 2.1: Write failing tests**

Append to `test_tg_commands.py`:

```python
def test_compact_is_raw_inject():
    r = dispatch("/compact")
    assert r.action == "raw_inject"
    assert r.payload == "/compact"


def test_fast_is_raw_inject():
    r = dispatch("/fast")
    assert r.action == "raw_inject"


def test_model_with_arg_is_raw_inject():
    r = dispatch("/model claude-sonnet-4-6")
    assert r.action == "raw_inject"
    assert r.payload == "/model claude-sonnet-4-6"


def test_skill_slash_is_raw_inject():
    r = dispatch("/recap")
    assert r.action == "raw_inject"


def test_skill_slash_with_args():
    r = dispatch("/qa taxi-ledger")
    assert r.action == "raw_inject"
    assert r.payload == "/qa taxi-ledger"


def test_unknown_slash_falls_back():
    r = dispatch("/totallyunknownxyz foo")
    assert r.action == "fallback_prefix"
    assert r.payload == "/totallyunknownxyz foo"
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `/compact` → fallback, etc. — 4 new failures, 1 still passing.

- [ ] **Step 2.3: Implement RAW_SLASH + argument-aware variants**

In `tg_commands.py`, between the `CommandResult` dataclass and `dispatch()`, add:

```python
# Claude Code native slash commands (no args) safe to pass through verbatim.
# NOTE: /help is intentionally NOT listed here — it has a dedicated branch
# in dispatch() that returns a status_reply (see Task 6).
RAW_SLASH = {
    "/compact", "/fast", "/cost", "/agents", "/config", "/status",
    "/ide", "/mcp", "/privacy", "/terminal-setup", "/bug", "/login",
    "/logout", "/doctor", "/memory", "/pr_comments",
}

# User skill slash commands (sourced from project/user skills directory).
SKILL_SLASH = {
    "/browse", "/qa", "/qa-only", "/ship", "/recap", "/youtube",
    "/review", "/investigate", "/land-and-deploy", "/canary",
    "/benchmark", "/codex", "/assemble", "/design-review",
    "/plan-ceo-review", "/plan-eng-review", "/plan-design-review",
    "/autoplan", "/office-hours", "/cso", "/retro", "/document-release",
    "/setup-browser-cookies", "/setup-deploy", "/health",
    "/gstack-upgrade", "/design-consultation", "/memory_review",
    "/freeze", "/unfreeze", "/careful", "/guard",
}

# Commands where the first token is fixed but arguments follow.
RAW_SLASH_WITH_ARGS = {"/model", "/config", "/codex", "/browse", "/qa", "/review"}
```

Then replace the body of `dispatch()`:

```python
def dispatch(text: str, now: float | None = None) -> CommandResult:
    text = (text or "").strip()
    if not text or not text.startswith("/"):
        return CommandResult(action="fallback_prefix", payload=text)

    parts = text.split(maxsplit=1)
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # No-arg raw slash
    if not rest and (cmd in RAW_SLASH or cmd in SKILL_SLASH):
        return CommandResult(action="raw_inject", payload=cmd)

    # Slash with args
    if rest and cmd in RAW_SLASH_WITH_ARGS:
        return CommandResult(action="raw_inject", payload=f"{cmd} {rest}".strip())

    # Skill slash with args (same treatment)
    if rest and cmd in SKILL_SLASH:
        return CommandResult(action="raw_inject", payload=f"{cmd} {rest}".strip())

    return CommandResult(action="fallback_prefix", payload=text)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `8 passed`.

- [ ] **Step 2.5: Commit**

```bash
cd ~/.claude/channels/telegram
git add tg_commands.py test_tg_commands.py 2>/dev/null || true
git commit -m "feat(tg): tier-1 raw slash passthrough" 2>/dev/null || true
```

---

## Task 3: Key-Sequence Injection (Tier 2)

**Files:**
- Modify: `~/.claude/channels/telegram/tg_commands.py`
- Modify: `~/.claude/channels/telegram/test_tg_commands.py`

Map short slash aliases to tmux key names for control keys (Ctrl+C, Escape, arrow keys, menu choices). No Enter after these — the poller sends the keys literally and lets the TUI decide.

- [ ] **Step 3.1: Write failing tests**

Append to `test_tg_commands.py`:

```python
def test_cancel_maps_to_ctrl_c():
    r = dispatch("/cancel")
    assert r.action == "key_inject"
    assert r.keys == ("C-c",)


def test_esc_maps_to_escape():
    r = dispatch("/esc")
    assert r.action == "key_inject"
    assert r.keys == ("Escape",)


def test_yes_is_choice_one_then_enter():
    r = dispatch("/yes")
    assert r.action == "key_inject"
    assert r.keys == ("1", "Enter")


def test_no_is_choice_two_then_enter():
    r = dispatch("/no")
    assert r.keys == ("2", "Enter")


def test_arrow_up():
    r = dispatch("/up")
    assert r.keys == ("Up",)


def test_tab():
    r = dispatch("/tab")
    assert r.keys == ("Tab",)
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: 6 new failures.

- [ ] **Step 3.3: Add KEY_COMMANDS map + branch**

Add to `tg_commands.py` below the slash sets:

```python
# Slash aliases → tmux key names. No Enter suffix unless in tuple.
KEY_COMMANDS: dict[str, tuple[str, ...]] = {
    "/cancel":  ("C-c",),
    "/stop":    ("C-c",),
    "/esc":     ("Escape",),
    "/escape":  ("Escape",),
    "/enter":   ("Enter",),
    "/tab":     ("Tab",),
    "/up":      ("Up",),
    "/down":    ("Down",),
    "/yes":     ("1", "Enter"),
    "/no":      ("2", "Enter"),
    "/opt3":    ("3", "Enter"),
    "/opt4":    ("4", "Enter"),
}
```

In `dispatch()`, insert this branch **before** the `RAW_SLASH` checks (key commands take priority):

```python
    if cmd in KEY_COMMANDS:
        return CommandResult(action="key_inject", keys=KEY_COMMANDS[cmd])
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `14 passed`.

- [ ] **Step 3.5: Commit**

```bash
cd ~/.claude/channels/telegram
git add tg_commands.py test_tg_commands.py 2>/dev/null || true
git commit -m "feat(tg): tier-2 key-sequence injection" 2>/dev/null || true
```

---

## Task 4: Screen Capture (Tier 3)

**Files:**
- Modify: `~/.claude/channels/telegram/tg_commands.py`
- Modify: `~/.claude/channels/telegram/test_tg_commands.py`

Support three screen-view commands. `/screen` returns the last 50 lines of the tmux pane; `/tail N` is a variable-length version; `/screenshot` sends a PNG via `screencapture`.

- [ ] **Step 4.1: Write failing tests**

Append to `test_tg_commands.py`:

```python
def test_screen_default_lines():
    r = dispatch("/screen")
    assert r.action == "screen_text"
    assert r.payload == "50"


def test_tail_with_count():
    r = dispatch("/tail 200")
    assert r.action == "screen_text"
    assert r.payload == "200"


def test_tail_default_when_no_arg():
    r = dispatch("/tail")
    assert r.action == "screen_text"
    assert r.payload == "50"


def test_tail_invalid_arg_defaults():
    r = dispatch("/tail banana")
    assert r.action == "screen_text"
    assert r.payload == "50"


def test_tail_clamps_negative():
    r = dispatch("/tail -1")
    assert r.action == "screen_text"
    assert r.payload == "50"  # negative is invalid, falls back to default


def test_tail_clamps_too_large():
    r = dispatch("/tail 99999")
    assert r.action == "screen_text"
    assert int(r.payload) <= 500


def test_screenshot():
    r = dispatch("/screenshot")
    assert r.action == "screen_png"
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: 5 new failures.

- [ ] **Step 4.3: Implement screen branch**

Add the screen constants to `tg_commands.py`:

```python
DEFAULT_TAIL_LINES = 50
```

In `dispatch()`, add before the final `fallback_prefix` return:

```python
    if cmd == "/screen":
        return CommandResult(action="screen_text", payload=str(DEFAULT_TAIL_LINES))

    if cmd == "/tail":
        n_str = rest.strip()
        try:
            n = max(1, min(int(n_str), 500))  # clamp 1..500
        except (ValueError, TypeError):
            n = DEFAULT_TAIL_LINES
        return CommandResult(action="screen_text", payload=str(n))

    if cmd == "/screenshot":
        return CommandResult(action="screen_png")
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `21 passed`.

- [ ] **Step 4.5: Commit**

```bash
cd ~/.claude/channels/telegram
git add tg_commands.py test_tg_commands.py 2>/dev/null || true
git commit -m "feat(tg): tier-3 screen capture commands" 2>/dev/null || true
```

---

## Task 5: Dangerous-Command Confirmation Flow

**Files:**
- Modify: `~/.claude/channels/telegram/tg_commands.py`
- Modify: `~/.claude/channels/telegram/test_tg_commands.py`

`/clear` and `/kill` are destructive. First call stores a pending token and asks the user to echo `/confirm <token>` within 60s. Expired or unknown tokens return a `status_reply` explaining the situation. This is the only stateful part of `tg_commands.py` — state lives in `pending.json` next to the poller.

- [ ] **Step 5.1: Write failing tests**

Append to `test_tg_commands.py`:

```python
def test_confirm_with_empty_arg_shows_usage():
    r = dispatch("/confirm")
    assert r.action == "status_reply"
    assert "사용법" in r.reply_text or "usage" in r.reply_text.lower()


def test_clear_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    r = dispatch("/clear", now=1_700_000_000.0)
    assert r.action == "confirm_required"
    assert r.pending_token.startswith("clear-")
    assert "/confirm" in r.reply_text


def test_confirm_with_valid_token_executes(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    r1 = dispatch("/clear", now=1_700_000_000.0)
    r2 = dispatch(f"/confirm {r1.pending_token}")
    assert r2.action == "raw_inject"
    assert r2.payload == "/clear"


def test_confirm_with_unknown_token_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    r = dispatch("/confirm nonsense-1234")
    assert r.action == "status_reply"
    assert "만료" in r.reply_text or "알 수 없" in r.reply_text


def test_confirm_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(tc, "CONFIRM_TTL_SECONDS", 0.05)
    r1 = dispatch("/clear", now=time.time())
    time.sleep(0.15)
    r2 = dispatch(f"/confirm {r1.pending_token}")
    assert r2.action == "status_reply"


def test_confirm_is_single_use(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    r1 = dispatch("/clear", now=time.time())
    r2 = dispatch(f"/confirm {r1.pending_token}")
    assert r2.action == "raw_inject"
    r3 = dispatch(f"/confirm {r1.pending_token}")
    assert r3.action == "status_reply"  # token already consumed
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: 5 new failures.

- [ ] **Step 5.3: Add pending-state helpers + dispatch branches**

Add to `tg_commands.py` below the key-command map:

```python
DANGEROUS = {"/clear", "/kill"}


def _load_pending() -> dict:
    if not PENDING_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_FILE.read_text())
    except Exception:
        return {}


def _save_pending(data: dict) -> None:
    # Atomic write: write to sibling tmp, then rename. Survives mid-write
    # daemon restarts and guards against a manual `python3 tg_poll.py`
    # accidentally racing the launchd instance.
    import os
    tmp = PENDING_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, PENDING_FILE)


def _prune(data: dict, now: float) -> dict:
    return {k: v for k, v in data.items() if v.get("expires_at", 0) > now}


def _store_pending(token: str, command: str, now: float) -> None:
    data = _prune(_load_pending(), now)
    data[token] = {"command": command, "expires_at": now + CONFIRM_TTL_SECONDS}
    _save_pending(data)


def _consume_pending(token: str, now: float) -> str | None:
    data = _prune(_load_pending(), now)
    entry = data.pop(token, None)
    _save_pending(data)
    return entry["command"] if entry else None
```

In `dispatch()`, add these branches **before** any other matching (confirmation must override even key commands):

```python
    ts = now if now is not None else time.time()

    if cmd == "/confirm":
        token = rest.strip()
        if not token:
            return CommandResult(
                action="status_reply",
                reply_text="❌ 사용법: /confirm <token>",
            )
        stored = _consume_pending(token, ts)
        if stored is None:
            return CommandResult(
                action="status_reply",
                reply_text=f"❌ 알 수 없거나 만료된 토큰: {token}",
            )
        stored_cmd = stored.split(maxsplit=1)[0]
        return CommandResult(action="raw_inject", payload=stored_cmd)

    if cmd in DANGEROUS:
        token = f"{cmd[1:]}-{uuid.uuid4().hex[:6]}"
        _store_pending(token, text, ts)
        return CommandResult(
            action="confirm_required",
            pending_token=token,
            reply_text=(
                f"⚠️ 위험 명령: {cmd}\n"
                f"60초 내 승인: /confirm {token}"
            ),
        )
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `27 passed`.

- [ ] **Step 5.5: Commit**

```bash
cd ~/.claude/channels/telegram
git add tg_commands.py test_tg_commands.py 2>/dev/null || true
git commit -m "feat(tg): dangerous-command confirmation with token TTL" 2>/dev/null || true
```

---

## Task 6: `/help` Listing

**Files:**
- Modify: `~/.claude/channels/telegram/tg_commands.py`
- Modify: `~/.claude/channels/telegram/test_tg_commands.py`

Single status-reply branch so the form can see the whole command surface without consulting the plan doc.

- [ ] **Step 6.1: Write failing test**

Append to `test_tg_commands.py`:

```python
def test_help_returns_status_reply():
    r = dispatch("/help")
    assert r.action == "status_reply"
    assert "/compact" in r.reply_text
    assert "/screen" in r.reply_text
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: 1 new failure (`/help` is not in RAW_SLASH so it'd fall back).

- [ ] **Step 6.3: Implement `/help` branch**

Add constant near `DANGEROUS`:

```python
HELP_TEXT = """Claude Code 리모컨 명령어

[슬래시 - CLI에 그대로 전달]
/compact  /fast  /cost  /agents  /status  /config
/model <name>
/browse  /qa  /ship  /recap  /review  /codex  /assemble
(그 외 사용자 스킬 슬래시도 대부분 지원)

[키 시퀀스]
/cancel /stop   → Ctrl+C
/esc /escape    → Escape
/enter /tab     → Enter / Tab
/up /down       → 화살표
/yes /no /opt3  → permission 프롬프트 응답

[화면]
/screen         → 최근 50줄
/tail <N>       → 최근 N줄 (1-500)
/screenshot     → 맥 스크린샷 PNG

[위험 명령 (confirm 필요)]
/clear  /kill
승인: /confirm <token>   (60초 TTL)

[도움말]
/help"""
```

Add this branch in `dispatch()` immediately after the `/confirm` branch:

```python
    if cmd == "/help":
        return CommandResult(action="status_reply", reply_text=HELP_TEXT)
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
cd ~/.claude/channels/telegram && python3 -m pytest test_tg_commands.py -v
```

Expected: `28 passed`.

- [ ] **Step 6.5: Commit**

```bash
cd ~/.claude/channels/telegram
git add tg_commands.py test_tg_commands.py 2>/dev/null || true
git commit -m "feat(tg): /help listing" 2>/dev/null || true
```

---

## Task 7: Integrate Dispatcher into `tg_poll.py`

**Files:**
- Modify: `~/.claude/channels/telegram/tg_poll.py`

Add chat/user allowlist gate, I/O executor functions for each `CommandResult` action, and route inbound messages through `dispatch()`. Keep the existing `inject_to_claude` behaviour for `fallback_prefix` — no regression for plain chat.

- [ ] **Step 7.1: Read the current poller**

```bash
cat ~/.claude/channels/telegram/tg_poll.py | head -220
```

Expected: confirms the main loop at lines ~171-210 and `inject_to_claude` at lines ~60-95.

- [ ] **Step 7.2: Add imports + helpers**

At the top of `tg_poll.py`, below the existing `from pathlib import Path` line, add:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tg_commands import dispatch, CommandResult  # noqa: E402
```

After `LOG_FILE = BASE_DIR / "poll.log"` add:

```python
TG_NOTIFY = BASE_DIR / "tg_notify.sh"
TMUX_BIN = "/opt/homebrew/bin/tmux"
```

- [ ] **Step 7.3: Add allowlist parser**

After `load_env()` add:

```python
def parse_allowed_ids(env: dict) -> set[str]:
    """Parse ALLOWED_USER_IDS. Fatal if missing unless ALLOW_ANY=1."""
    raw = env.get("ALLOWED_USER_IDS", "").strip()
    allow_any = env.get("ALLOW_ANY", "").strip() == "1"
    if not raw and not allow_any:
        log("❌ ALLOWED_USER_IDS not set and ALLOW_ANY!=1 — aborting. "
            "Set ALLOWED_USER_IDS=<your_telegram_user_id> in .env, "
            "or set ALLOW_ANY=1 to explicitly accept any sender.")
        sys.exit(2)
    if not raw and allow_any:
        log("⚠️ ALLOW_ANY=1 — all Telegram senders accepted (dev mode).")
        return set()  # empty = accept-all sentinel, only honoured if ALLOW_ANY=1
    return {s.strip() for s in raw.split(",") if s.strip()}


def user_id_of(update: dict) -> str:
    msg = update.get("message") or update.get("edited_message") or {}
    return str((msg.get("from") or {}).get("id", ""))
```

- [ ] **Step 7.4: Add action executors**

After `inject_to_claude` add:

```python
def _tmux(*args: str) -> None:
    subprocess.run([TMUX_BIN, *args], check=True, timeout=5)


def send_raw_slash(tmux_target: str, text: str) -> bool:
    try:
        _tmux("send-keys", "-t", tmux_target, "-l", text)
        _tmux("send-keys", "-t", tmux_target, "Enter")
        log(f"🔑 raw_inject → {text}")
        return True
    except Exception as e:
        log(f"⚠️ raw_inject failed: {e}")
        return False


def send_keys_seq(tmux_target: str, keys: tuple[str, ...]) -> bool:
    try:
        for k in keys:
            _tmux("send-keys", "-t", tmux_target, k)
        log(f"⌨️  key_inject → {keys}")
        return True
    except Exception as e:
        log(f"⚠️ key_inject failed: {e}")
        return False


def tg_reply(text: str) -> None:
    try:
        subprocess.run([str(TG_NOTIFY), text], check=True, timeout=15)
    except Exception as e:
        log(f"⚠️ tg_reply failed: {e}")


def send_screen_text(tmux_target: str, lines: int) -> None:
    try:
        proc = subprocess.run(
            [TMUX_BIN, "capture-pane", "-p", "-t", tmux_target, "-S", f"-{lines}"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        text = proc.stdout or "(empty pane)"
        # Telegram message cap is 4096 — trim from the top and keep the most
        # recent lines so the user always sees the latest output.
        if len(text) > 3800:
            text = "…(잘림)\n" + text[-3800:]
        tg_reply(f"📺 screen (last {lines} lines):\n{text}")
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        log(f"⚠️ screen_text failed (exit={e.returncode}): {stderr}")
        tg_reply(f"❌ screen 실패: {stderr or e}")
    except Exception as e:
        log(f"⚠️ screen_text failed: {e}")
        tg_reply(f"❌ screen 실패: {e}")


def send_screen_png() -> None:
    out = BASE_DIR / "photos" / f"screen-{int(time.time())}.png"
    out.parent.mkdir(exist_ok=True)
    try:
        subprocess.run(["/usr/sbin/screencapture", "-x", str(out)],
                       check=True, timeout=10)
        subprocess.run([str(TG_NOTIFY), "📸 screenshot", "--photo", str(out)],
                       check=True, timeout=20)
    except Exception as e:
        log(f"⚠️ screen_png failed: {e}")
        tg_reply(f"❌ screenshot 실패: {e}")
```

- [ ] **Step 7.5: Route each inbound message through dispatch**

Replace the message-handling block inside `main()` (roughly lines 176-200 of the current file — the block starting with `for u in updates:` and ending before the `if updates: save_state(state)` line) with:

```python
            for u in updates:
                append_inbox(u)
                state["last_update_id"] = max(state["last_update_id"], u["update_id"])
                log(f"📥 update_id={u['update_id']} — {extract_summary(u)}")

                # Allowlist gate. Empty `allowed_ids` only reaches here if
                # ALLOW_ANY=1 was set during env parsing (dev mode).
                if allowed_ids and user_id_of(u) not in allowed_ids:
                    log(f"🚫 blocked user_id={user_id_of(u)!r}")
                    continue

                msg = u.get("message") or u.get("edited_message") or {}
                text = msg.get("text") or msg.get("caption") or ""
                photos = msg.get("photo")

                # Photos still flow through the legacy prefixed path.
                photo_paths: list[str] = []
                if photos and tmux_target:
                    best = photos[-1]
                    local = download_photo(token, best["file_id"])
                    if local:
                        photo_paths.append(local)

                if photo_paths and tmux_target:
                    parts = [f"[이미지: {p}]" for p in photo_paths]
                    if text:
                        parts.append(text)
                    inject_to_claude(tmux_target, " ".join(parts))
                    continue

                if not text:
                    continue

                result: CommandResult = dispatch(text)

                if result.action == "fallback_prefix":
                    if tmux_target:
                        inject_to_claude(tmux_target, result.payload)
                elif result.action == "raw_inject":
                    if tmux_target:
                        send_raw_slash(tmux_target, result.payload)
                elif result.action == "key_inject":
                    if tmux_target:
                        send_keys_seq(tmux_target, result.keys)
                elif result.action == "screen_text":
                    if tmux_target:
                        send_screen_text(tmux_target, int(result.payload))
                elif result.action == "screen_png":
                    send_screen_png()
                elif result.action in {"confirm_required", "status_reply"}:
                    tg_reply(result.reply_text)
                else:
                    log(f"⚠️ unknown action: {result.action}")
```

Then, update the `main()` preamble to load the allowlist once:

```python
def main() -> None:
    env = load_env()
    token = env["TELEGRAM_BOT_TOKEN"]
    tmux_target = env.get("TMUX_TARGET", "")
    allowed_ids = parse_allowed_ids(env)
    state = load_state()
    log(f"🚀 tg_poll started (last_update_id={state['last_update_id']}, "
        f"tmux={tmux_target or 'OFF'}, allowlist={len(allowed_ids)} ids)")
```

- [ ] **Step 7.6: Sanity-run the poller (no Telegram traffic yet)**

```bash
cd ~/.claude/channels/telegram && python3 -c "
import tg_poll  # module import should not raise
print('IMPORT_OK')
"
```

Expected: `IMPORT_OK`.

- [ ] **Step 7.7: Commit**

```bash
cd ~/.claude/channels/telegram
git add tg_poll.py 2>/dev/null || true
git commit -m "feat(tg): route inbound through command dispatch, add allowlist" 2>/dev/null || true
```

---

## Task 8: Manual Smoke Test + launchd Restart

**Files:** none modified — operational only.

Restart the poll daemon and run each command category once from the form's actual Telegram client, confirming observed behaviour. This is the only integration checkpoint; we do not try to simulate Telegram end-to-end in unit tests.

- [ ] **Step 8.1: Find the launchd service label**

```bash
launchctl list | grep -i tg || launchctl list | grep -i telegram
```

Expected: a label like `com.yonghaekim.tg-poll` (substitute real value below).

- [ ] **Step 8.2: Restart the daemon**

```bash
launchctl kickstart -k gui/$(id -u)/com.yonghaekim.tg-poll
```

Check the log:

```bash
tail -n 20 ~/.claude/channels/telegram/poll.log
```

Expected: `🚀 tg_poll started ... allowlist=1 ids`.

- [ ] **Step 8.3: Smoke-test each tier from the user's Telegram chat**

Send these messages from Telegram, one at a time, and verify the listed outcome. Each row is a tick in the checklist:

- [ ] `/help` → bot reply with command listing
- [ ] `/cost` → CLI replies with cost summary (appears on the Mac terminal)
- [ ] `/fast` → CLI toggles fast mode; Telegram will see the TUI banner via `/screen`
- [ ] `/screen` → bot reply with last 50 lines of the pane
- [ ] `/tail 20` → bot reply with last 20 lines
- [ ] `/screenshot` → bot sends PNG
- [ ] `/up` then `/enter` → prompt history replayed in the TUI
- [ ] `/cancel` during a long task → running task interrupted (verify on Mac)
- [ ] `/clear` → bot replies with confirmation request + token
- [ ] `/confirm <token>` → CLI `/clear` fires
- [ ] `hello there` (plain text) → still arrives in CLI as `[텔레그램 수신 메시지] hello there` (no regression)
- [ ] Message from an unrelated Telegram user id (if available) → silently dropped, `🚫 blocked` entry in `poll.log`

- [ ] **Step 8.4: Final commit + record success**

```bash
cd ~/.claude/channels/telegram
echo "smoke test passed $(date +%F)" >> docs/SMOKE.log
git add docs/SMOKE.log 2>/dev/null || true
git commit -m "chore(tg): smoke-test MVP pass" 2>/dev/null || true
```

---

## Self-Review Checklist (run before execution)

**Spec coverage:**
- Tier 1 slash bypass → Tasks 2, 5, 6
- Tier 2 key sequences → Task 3
- Tier 3 screen capture → Task 4
- Whitelist / chat_id gate → Tasks 1, 7, Pre-Task
- Dangerous-command confirmation → Task 5
- No custom macros / voice input → absent by design ✓

**Type consistency:**
- `CommandResult` fields used identically in Tasks 1-6 and in Task 7's dispatcher
- `Action` literal names match between dispatch return values and poller routing branches
- tmux key names (`"C-c"`, `"Escape"`, `"Up"`, etc.) match tmux's documented `send-keys` identifiers

**Placeholder scan:** none remaining — all code blocks contain the actual implementation.

---

## Execution Handoff

Plan saved to `~/.claude/channels/telegram/docs/PLAN-2026-04-22-telegram-remote-mvp.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with `superpowers:executing-plans`, batch checkpoints.

Which approach?

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 3 decisions approved (security default, atomic pending, metadata field), 3 test gaps closed, 2 code-quality nits fixed |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | DevEx gaps | 0 | — | — |

**VERDICT:** ENG CLEARED — ready to execute.

### Changes applied post-review

- **[A2 security]** `parse_allowed_ids`: fatal-error at startup when `ALLOWED_USER_IDS` empty unless `ALLOW_ANY=1` is explicitly set.
- **[A3 atomicity]** `_save_pending`: write to `pending.json.tmp` + `os.replace`.
- **[A4 extensibility]** `CommandResult` gains `metadata: dict = field(default_factory=dict)` for future custom-macro expansion.
- **[Q2]** `/help` dead entry removed from `RAW_SLASH` (dedicated branch handles it).
- **[Q3]** `send_screen_text` catches `CalledProcessError` separately to log stderr.
- **Tests +4**: `test_confirm_with_empty_arg_shows_usage`, `test_skill_slash_with_args`, `test_tail_clamps_negative`, `test_tail_clamps_too_large`. New total: 28 tests.

### Deferred (not in scope)

- Custom macros (`/build`, `/deploy`, `/health`) — MVP exclusion per request.
- Voice input — MVP exclusion per request.
- Inline-keyboard confirmation UI — token-based confirmation is sufficient for MVP.
- Screenshot cropping to TUI window only — full-screen intended; flag in follow-up if sensitivity becomes a concern.
- Observability/metrics for dispatcher usage — log-line presence is the current signal.

