"""Command dispatch for Telegram → Claude Code remote control.

Pure functions only. No I/O beyond the confirmation-token state file.
The poller (tg_poll.py) is responsible for actually executing the
CommandResult it receives.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent
PENDING_FILE = BASE_DIR / "pending.json"
CONFIRM_TTL_SECONDS = 60
DEFAULT_TAIL_LINES = 50

# Pattern-based slash passthrough: matches any /foo, /foo:bar, /foo-bar_baz.
# Applied AFTER allowlist and special handlers, BEFORE fallback_prefix.
# Rationale: supports plugin:skill form (e.g. /superpowers:write-plan) and
# user-installed skills without hard-coding every name.
SLASH_PATTERN = re.compile(r"^/[a-z0-9][a-z0-9:_-]*$", re.IGNORECASE)

Action = Literal[
    "raw_inject",
    "key_inject",
    "screen_text",
    "screen_png",
    "confirm_required",
    "status_reply",
    "fallback_prefix",
    "restart_claude",
]


@dataclass
class CommandResult:
    action: Action
    payload: str = ""
    keys: tuple[str, ...] = ()
    reply_text: str = ""
    pending_token: str = ""
    metadata: dict = field(default_factory=dict)


# --- Whitelists ---

# Claude Code native slash commands (no args) safe to pass through verbatim.
# NOTE: /help is intentionally NOT listed here — it has a dedicated branch
# in dispatch() that returns a status_reply.
RAW_SLASH = {
    "/compact", "/fast", "/cost", "/agents", "/config", "/status",
    "/ide", "/mcp", "/privacy", "/terminal-setup", "/bug", "/login",
    "/logout", "/doctor", "/memory", "/pr_comments",
}

# User skill slash commands.
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

# Slash aliases → tmux key names. Keys are sent literally (no Enter suffix
# unless the tuple includes "Enter").
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

# Dangerous — requires /confirm <token> within TTL.
DANGEROUS = {"/clear", "/kill", "/quit"}

# Dangerous commands that emit a key sequence instead of text raw_inject.
DANGEROUS_KEY_SEQS: dict[str, tuple[str, ...]] = {
    "/quit": ("C-c", "sleep:0.3", "C-c"),
}

HELP_TEXT = """Claude Code 리모컨 명령어

[슬래시 - CLI에 그대로 전달]
/compact  /fast  /cost  /agents  /status  /config
/model <name>
/browse  /qa  /ship  /recap  /review  /codex  /assemble
(그 외 사용자 스킬 슬래시도 대부분 지원)

[키 시퀀스]
/cancel /stop   → Ctrl+C (작업 인터럽트)
/esc /escape    → Escape (다이얼로그/모달 dismiss · /fast 승인창 포함)
/enter /tab     → Enter / Tab
/up /down       → 화살표
/yes /no /opt3  → permission 프롬프트 숫자 응답 (1/2/3)

[화면]
/screen         → 최근 50줄
/tail <N>       → 최근 N줄 (1-500)
/screenshot     → 맥 스크린샷 PNG

[위험 명령 (confirm 필요)]
/clear  /kill   → Claude Code에 슬래시 입력
/quit           → 세션 완전 종료 (Ctrl+C × 2)
승인: /confirm   (60초 TTL, 토큰 생략 가능)

[세션 재시작]
/restart        → claude 재기동 (TUI 실행 중이면 거부)
/restart x      → claudex 재기동 (bypass-on 모드)

[도움말]
/help"""


# --- Pending confirmation state (atomic write) ---

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


# --- Dispatch ---

def dispatch(text: str, now: float | None = None) -> CommandResult:
    """Classify inbound Telegram text into an action for the poller."""
    text = (text or "").strip()
    if not text or not text.startswith("/"):
        return CommandResult(action="fallback_prefix", payload=text)

    parts = text.split(maxsplit=1)
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    ts = now if now is not None else time.time()

    # 1. /confirm [token] — consume pending dangerous command.
    #    인자 없으면 가장 최근 등록된 pending 하나를 자동 선택.
    if cmd == "/confirm":
        token = rest.strip()
        if not token:
            data = _prune(_load_pending(), ts)
            if not data:
                return CommandResult(
                    action="status_reply",
                    reply_text="❌ 확인할 pending 명령 없음",
                )
            token = max(data, key=lambda k: data[k].get("expires_at", 0))
        stored = _consume_pending(token, ts)
        if stored is None:
            return CommandResult(
                action="status_reply",
                reply_text=f"❌ 알 수 없거나 만료된 토큰: {token}",
            )
        stored_cmd = stored.split(maxsplit=1)[0]
        if stored_cmd in DANGEROUS_KEY_SEQS:
            return CommandResult(
                action="key_inject",
                keys=DANGEROUS_KEY_SEQS[stored_cmd],
                reply_text=f"✅ {stored_cmd} 실행",
            )
        return CommandResult(
            action="raw_inject",
            payload=stored_cmd,
            reply_text=f"✅ {stored_cmd} 실행",
        )

    # 2. Dangerous commands — require /confirm.
    if cmd in DANGEROUS:
        token = f"{cmd[1:]}-{uuid.uuid4().hex[:6]}"
        _store_pending(token, text, ts)
        return CommandResult(
            action="confirm_required",
            pending_token=token,
            reply_text=(
                f"⚠️ 위험 명령: {cmd}\n"
                f"60초 내 승인: /confirm  (또는 /confirm {token})"
            ),
        )

    # 3. /help — built-in status reply.
    if cmd == "/help":
        return CommandResult(action="status_reply", reply_text=HELP_TEXT)

    # 3b. /restart [x] — re-launch Claude Code in shell (poller guards against live TUI).
    #   /restart    → claude
    #   /restart x  → claudex (bypass-on alias)
    if cmd == "/restart":
        variant = rest.strip().lower()
        binary = "claudex" if variant == "x" else "claude"
        return CommandResult(action="restart_claude", payload=binary)

    # 4. Key sequence aliases.
    if cmd in KEY_COMMANDS:
        return CommandResult(action="key_inject", keys=KEY_COMMANDS[cmd])

    # 5. Screen capture.
    if cmd == "/screen":
        return CommandResult(action="screen_text", payload=str(DEFAULT_TAIL_LINES))

    if cmd == "/tail":
        n_str = rest.strip()
        try:
            n = int(n_str)
            if n < 1:
                n = DEFAULT_TAIL_LINES
            else:
                n = min(n, 500)  # clamp upper bound
        except (ValueError, TypeError):
            n = DEFAULT_TAIL_LINES
        return CommandResult(action="screen_text", payload=str(n))

    if cmd == "/screenshot":
        return CommandResult(action="screen_png")

    # 6. Raw slash pass-through.
    if not rest and (cmd in RAW_SLASH or cmd in SKILL_SLASH):
        meta = {"watch": "compact"} if cmd == "/compact" else {}
        return CommandResult(action="raw_inject", payload=cmd, metadata=meta)

    if rest and (cmd in RAW_SLASH_WITH_ARGS or cmd in SKILL_SLASH):
        return CommandResult(action="raw_inject", payload=f"{cmd} {rest}".strip())

    # 7. Pattern-based passthrough — any well-formed slash (incl. plugin:skill)
    #    gets injected raw. Claude Code TUI handles unknowns itself.
    if SLASH_PATTERN.match(cmd):
        payload = f"{cmd} {rest}".strip() if rest else cmd
        return CommandResult(action="raw_inject", payload=payload)

    # 8. Default: malformed slash → fall back to the prefixed injection path.
    return CommandResult(action="fallback_prefix", payload=text)
