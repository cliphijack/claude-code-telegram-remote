#!/usr/bin/env python3
"""
tg_poll.py — Telegram Bot long-polling 데몬

Claude Code와 완전 독립적으로 작동. launchd로 상주 실행.
메시지를 받으면 inbox.jsonl에 한 줄씩 append.
last_update_id는 state.json에 저장해서 재시작해도 중복 수신 방지.

실행:
  python3 tg_poll.py   # 무한 루프 (launchd가 호출)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
STATE_FILE = BASE_DIR / "state.json"
INBOX_FILE = BASE_DIR / "inbox.jsonl"
LOG_FILE = BASE_DIR / "poll.log"

TG_NOTIFY = BASE_DIR / "tg_notify.sh"

# Resolve tmux binary across platforms: macOS Homebrew (/opt/homebrew/bin),
# Intel Homebrew (/usr/local/bin), Linux distros (/usr/bin).
# `shutil.which` uses PATH; fall back to common install locations if unset.
def _resolve_tmux() -> str:
    found = shutil.which("tmux")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"):
        if Path(candidate).exists():
            return candidate
    return "tmux"  # last resort — let subprocess raise a clear error


TMUX_BIN = _resolve_tmux()


# Platform-specific screenshot tool detection. macOS uses `screencapture`;
# Linux has several options depending on display server (X11 vs Wayland).
def _resolve_screenshot_cmd(out_path: Path) -> list[str] | None:
    if sys.platform == "darwin":
        if Path("/usr/sbin/screencapture").exists():
            return ["/usr/sbin/screencapture", "-x", str(out_path)]
        return None
    # Linux / BSD: try common tools in order of preference.
    # grim (Wayland), gnome-screenshot (GNOME), scrot (X11 lightweight), maim (X11).
    for tool, args in (
        ("grim", [str(out_path)]),
        ("gnome-screenshot", ["-f", str(out_path)]),
        ("scrot", [str(out_path)]),
        ("maim", [str(out_path)]),
    ):
        path = shutil.which(tool)
        if path:
            return [path, *args]
    return None

LONG_POLL_TIMEOUT = 30  # seconds — Telegram 서버에서 최대 대기
HTTP_TIMEOUT = LONG_POLL_TIMEOUT + 10

sys.path.insert(0, str(BASE_DIR))
from tg_commands import dispatch, CommandResult  # noqa: E402


LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB per file
LOG_KEEP_ROTATIONS = 3            # keep .1, .2, .3
PHOTO_MAX_AGE_SECONDS = 7 * 24 * 3600  # screenshots older than 7 days


def rotate_if_large(path: Path, max_bytes: int = LOG_MAX_BYTES,
                    keep: int = LOG_KEEP_ROTATIONS) -> None:
    """Rotate a log file when it crosses the size threshold.

    Renames path.N → path.(N+1), dropping the oldest beyond `keep`,
    then moves the current path → path.1. Cheap enough to call anywhere;
    no-op if the file is under threshold or missing.
    """
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        # Shift existing rotations: .(keep-1) → .keep, ..., .1 → .2
        oldest = path.with_suffix(path.suffix + f".{keep}")
        if oldest.exists():
            oldest.unlink()
        for i in range(keep - 1, 0, -1):
            src = path.with_suffix(path.suffix + f".{i}")
            dst = path.with_suffix(path.suffix + f".{i + 1}")
            if src.exists():
                src.rename(dst)
        path.rename(path.with_suffix(path.suffix + ".1"))
    except Exception:
        # Rotation is best-effort — do not crash the poller over log plumbing.
        pass


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    rotate_if_large(LOG_FILE)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


def cleanup_old_photos(max_age: int = PHOTO_MAX_AGE_SECONDS) -> int:
    """Delete screenshots older than `max_age` seconds. Returns count removed."""
    photos_dir = BASE_DIR / "photos"
    if not photos_dir.exists():
        return 0
    cutoff = time.time() - max_age
    removed = 0
    try:
        for p in photos_dir.iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
    except Exception:
        pass
    return removed


def load_env() -> dict:
    if not ENV_FILE.exists():
        log(f"❌ .env not found at {ENV_FILE}")
        sys.exit(1)
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    if "TELEGRAM_BOT_TOKEN" not in env:
        log("❌ TELEGRAM_BOT_TOKEN not found in .env")
        sys.exit(1)
    return env


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
        return set()  # empty = accept-all sentinel, honoured only if ALLOW_ANY=1
    return {s.strip() for s in raw.split(",") if s.strip()}


def user_id_of(update: dict) -> str:
    msg = update.get("message") or update.get("edited_message") or {}
    return str((msg.get("from") or {}).get("id", ""))


def inject_to_claude(tmux_target: str, text: str) -> bool:
    """tmux send-keys로 Claude Code 세션에 사용자 입력 주입."""
    if not tmux_target:
        return False
    # 개행·캐리지리턴 제거 → 한 줄로 (여러 줄 입력 방지)
    clean = text.replace("\r", " ").replace("\n", " ").strip()
    if not clean:
        return False
    # 너무 길면 truncate (Claude context 절약)
    if len(clean) > 4000:
        clean = clean[:4000] + " ...(잘림)"
    # 프롬프트 인젝션 방지용 prefix — 이 메시지는 텔레그램에서 왔다는 명확한 표시
    prompt = f"[텔레그램 수신 메시지] {clean}"
    try:
        # literal 모드로 텍스트 입력 후 Enter
        subprocess.run(
            ["/opt/homebrew/bin/tmux", "send-keys", "-t", tmux_target, "-l", prompt],
            check=True,
            timeout=5,
        )
        subprocess.run(
            ["/opt/homebrew/bin/tmux", "send-keys", "-t", tmux_target, "Enter"],
            check=True,
            timeout=5,
        )
        log(f"➡️  injected to tmux {tmux_target}")
        return True
    except subprocess.CalledProcessError as e:
        log(f"⚠️ tmux send-keys failed: {e}")
        return False
    except subprocess.TimeoutExpired:
        log("⚠️ tmux send-keys timeout")
        return False
    except FileNotFoundError:
        log("⚠️ tmux not found in PATH")
        return False


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [TMUX_BIN, *args],
        check=True, timeout=5, capture_output=True, text=True,
    )


def send_raw_slash(tmux_target: str, text: str) -> bool:
    try:
        _tmux("send-keys", "-t", tmux_target, "-l", text)
        _tmux("send-keys", "-t", tmux_target, "Enter")
        log(f"🔑 raw_inject → {text}")
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        log(f"⚠️ raw_inject failed (exit={e.returncode}): {stderr}")
        return False
    except Exception as e:
        log(f"⚠️ raw_inject failed: {e}")
        return False


def send_keys_seq(tmux_target: str, keys: tuple[str, ...]) -> bool:
    try:
        for k in keys:
            if k.startswith("sleep:"):
                time.sleep(float(k.split(":", 1)[1]))
                continue
            _tmux("send-keys", "-t", tmux_target, k)
        log(f"⌨️  key_inject → {keys}")
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        log(f"⚠️ key_inject failed (exit={e.returncode}): {stderr}")
        return False
    except Exception as e:
        log(f"⚠️ key_inject failed: {e}")
        return False


def tg_reply(text: str) -> None:
    try:
        subprocess.run([str(TG_NOTIFY), text], check=True, timeout=15)
    except Exception as e:
        log(f"⚠️ tg_reply failed: {e}")


_RESTART_BIN_ALLOWLIST = {"claude", "claudex"}


def _claude_tui_running(tmux_target: str) -> bool:
    """Return True if a `claude`/`claudex` process is attached to the tmux pane's TTY.

    Source-of-truth check: does not rely on scrollback text (which keeps stale
    `❯` glyphs from shell prompts / past TUI sessions and produces false positives).
    """
    try:
        tty_res = subprocess.run(
            [TMUX_BIN, "display-message", "-p", "-t", tmux_target, "#{pane_tty}"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        pane_tty_full = tty_res.stdout.strip()
        if not pane_tty_full:
            return False
        pane_tty = pane_tty_full.replace("/dev/", "", 1)
        ps_res = subprocess.run(
            ["ps", "-t", pane_tty, "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        for line in ps_res.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            first_token = stripped.split()[0]
            basename = os.path.basename(first_token)
            if basename in _RESTART_BIN_ALLOWLIST:
                return True
        return False
    except Exception as e:
        log(f"⚠️ _claude_tui_running probe failed: {e}")
        return False


def handle_restart_claude(tmux_target: str, binary: str) -> None:
    """Launch `claude` (or aliased variant) in the pane if the TUI is not running."""
    if binary not in _RESTART_BIN_ALLOWLIST:
        tg_reply(f"❌ 허용되지 않은 binary: {binary}")
        log(f"🚫 /restart refused — unknown binary '{binary}'")
        return
    try:
        if _claude_tui_running(tmux_target):
            tg_reply("❌ Claude Code 실행 중. 먼저 /quit 로 종료해.")
            log("🚫 /restart refused — TUI still alive (process check)")
            return
        _tmux("send-keys", "-t", tmux_target, "-l", binary)
        _tmux("send-keys", "-t", tmux_target, "Enter")
        tg_reply(f"✅ {binary} 재기동 요청 전송")
        log(f"🔄 /restart → {binary} + Enter")
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        log(f"⚠️ restart failed (exit={e.returncode}): {stderr}")
        tg_reply(f"❌ restart 실패: {stderr or e}")
    except Exception as e:
        log(f"⚠️ restart failed: {e}")
        tg_reply(f"❌ restart 실패: {e}")


_SEP_CHARS = set("─━═-━╌╍╴╶╸╺")


def _is_separator(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 5:
        return False
    return all(c in _SEP_CHARS for c in stripped)


_SEPARATOR_REPLACEMENT = "─" * 20  # short enough to not wrap on a phone screen


def _collapse_tui_noise(lines: list[str]) -> list[str]:
    """Collapse consecutive separator and blank lines, shorten separators.

    Claude Code renders full-width `─` frame dividers (often 150+ chars)
    plus padding blanks to fill the pane vertically. Telegram on a phone
    wraps a single 150-char divider into 8-9 visible rows, so even one
    divider looks like a wall. Replace each run of separators with a
    short fixed-length one (20 chars), and collapse blank runs to one.
    """
    out: list[str] = []
    prev_blank = False
    prev_sep = False
    for line in lines:
        if _is_separator(line):
            if prev_sep:
                continue
            prev_sep = True
            prev_blank = False
            out.append(_SEPARATOR_REPLACEMENT)
        elif not line.strip():
            if prev_blank:
                continue
            prev_blank = True
            prev_sep = False
            out.append("")
        else:
            prev_blank = False
            prev_sep = False
            out.append(line)
    # Trim leading/trailing blanks (but keep separators — they're boundaries).
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def _split_body_chrome(all_lines: list[str]) -> tuple[list[str], list[str]]:
    """Split pane into (body, chrome) using the last ❯ input prompt as boundary.

    Returns (all_lines, []) when no prompt found (raw fallback).
    Chrome starts at the separator line above the prompt if present, else at the prompt line.
    """
    chrome_start = None
    for i in range(len(all_lines) - 1, -1, -1):
        stripped = all_lines[i].lstrip()
        if stripped.startswith("❯"):
            chrome_start = i - 1 if i > 0 and _is_separator(all_lines[i - 1]) else i
            break
    if chrome_start is None:
        return all_lines, []
    return all_lines[:chrome_start], all_lines[chrome_start:]


def send_screen_text(tmux_target: str, lines: int) -> None:
    try:
        # Capture N body lines + chrome (~6 lines) + padding for scrollback
        proc = subprocess.run(
            [TMUX_BIN, "capture-pane", "-p", "-t", tmux_target, "-S", f"-{lines + 50}"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        raw = proc.stdout or ""
        all_lines = raw.rstrip("\n").split("\n") if raw else []
        body, chrome = _split_body_chrome(all_lines)
        if chrome:
            # Strip TUI noise (separator lines, duplicated blanks) from both
            # halves before counting — otherwise body N reflects chrome
            # padding instead of real content.
            body = _collapse_tui_noise(body)
            chrome = _collapse_tui_noise(chrome)
            # Count only non-blank lines toward `lines`; blank lines come along for free.
            non_blank = 0
            start_idx = 0
            for idx in range(len(body) - 1, -1, -1):
                if body[idx].strip():
                    non_blank += 1
                    if non_blank >= lines:
                        start_idx = idx
                        break
            else:
                start_idx = 0
            body = body[start_idx:]
            combined = body + ([""] if chrome else []) + chrome
            text = "\n".join(combined) if combined else "(empty pane)"
            non_blank_count = sum(1 for l in body if l.strip())
            header = f"📺 screen (body {non_blank_count}/{lines} + chrome):"
        else:
            # Fallback: no prompt detected, raw tail
            all_lines = _collapse_tui_noise(all_lines)
            if len(all_lines) > lines:
                all_lines = all_lines[-lines:]
            text = "\n".join(all_lines) if all_lines else "(empty pane)"
            header = f"📺 screen (last {lines} lines, raw):"
        if len(text) > 3800:
            text = "…(잘림)\n" + text[-3800:]
        tg_reply(f"{header}\n{text}")
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        log(f"⚠️ screen_text failed (exit={e.returncode}): {stderr}")
        tg_reply(f"❌ screen 실패: {stderr or e}")
    except Exception as e:
        log(f"⚠️ screen_text failed: {e}")
        tg_reply(f"❌ screen 실패: {e}")


def _pane_text(tmux_target: str) -> str:
    """Capture current visible pane content (best-effort; empty string on failure)."""
    try:
        proc = subprocess.run(
            [TMUX_BIN, "capture-pane", "-p", "-t", tmux_target],
            capture_output=True, text=True, check=True, timeout=5,
        )
        return proc.stdout or ""
    except Exception:
        return ""


def _is_compacting(pane: str) -> bool:
    """Heuristic: Claude Code shows 'Compacting' / 'compact' while the work runs."""
    lower = pane.lower()
    return ("compacting" in lower) or ("compact…" in lower) or ("compact..." in lower)


def watch_compact(tmux_target: str,
                  max_wait_s: int = 180,
                  poll_s: float = 2.0,
                  grace_s: int = 15) -> None:
    """Background watcher: poll tmux pane, notify Telegram on /compact completion.

    State machine:
      1. Wait up to grace_s for 'Compacting' text to appear.
      2. Once seen, wait for it to disappear → send ✅ notification.
      3. If grace elapses without seeing it → send ⚠️ (CLI may have ignored).
      4. If max_wait elapses mid-compact → send ⏱️ timeout notification.
    """
    start = time.time()
    seen_compacting = False

    while time.time() - start < max_wait_s:
        time.sleep(poll_s)
        pane = _pane_text(tmux_target)
        compacting_now = _is_compacting(pane)

        if compacting_now:
            seen_compacting = True
            continue

        if seen_compacting:
            elapsed = int(time.time() - start)
            tg_reply(f"✅ /compact 완료 ({elapsed}초)")
            return

        if (time.time() - start) > grace_s:
            tg_reply("⚠️ /compact 감지 실패 — CLI가 명령을 인식 못 했을 수 있음. /screen으로 확인.")
            return

    tg_reply(f"⏱️ /compact 타임아웃 ({max_wait_s}초) — 여전히 진행 중일 수 있음.")


WATCHERS = {
    "compact": watch_compact,
}


# --- Prompt watcher (always-on) ---
#
# Detects active AskUserQuestion modals and permission prompts so the user
# gets notified on Telegram when Claude Code is waiting for input. Without
# this, an idle remote user has no way of knowing a prompt appeared without
# polling /screen manually.

import hashlib  # noqa: E402
import re       # noqa: E402

# Cursor char Claude Code uses to mark the currently-selected option.
# Permission prompts render "❯ 1. Yes" while AskUserQuestion renders
# "❯ 1. A) ..." — both start with `❯ ` followed by a digit+period.
_PROMPT_OPTION_RE = re.compile(r"^\s*[❯>]\s*(\d+)\.\s")
_PROMPT_CONT_RE = re.compile(r"^\s*(\d+)\.\s")
# Lines that are mostly box-drawing / divider chars — stop context scan here
# so TUI frame lines don't pollute the notification preview.
_DIVIDER_CHARS = set("─━═╌╍╴╶╸╺│┃┆┇┊┋┌┍┎┏┐┑┒┓└┕┖┗┘┙┚┛━╸╺")


def _is_divider(line: str) -> bool:
    """Only a line made mostly of box-drawing characters counts as a divider.

    Blank lines are NOT dividers — Claude Code renders an empty line between
    the question and the option block, and treating that as a stop would
    clip the question from the notification preview.
    """
    stripped = line.strip()
    if not stripped:
        return False
    non_space = [c for c in stripped if not c.isspace()]
    if not non_space:
        return False
    divider_count = sum(1 for c in non_space if c in _DIVIDER_CHARS)
    return divider_count / len(non_space) >= 0.8


def detect_prompt(pane: str) -> tuple[str, list[str]] | None:
    """Look for an active numbered-option prompt in the pane.

    Returns (hash, preview_lines) if a prompt is detected, else None.
    `hash` is stable for the same visible prompt so repeat polls don't
    re-notify. `preview_lines` is the context + options block to send.
    """
    lines = pane.splitlines()
    # Scan from the bottom up. Active prompts sit near the bottom of the pane.
    cursor_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if _PROMPT_OPTION_RE.match(lines[i]):
            cursor_idx = i
            break
    if cursor_idx is None:
        return None

    # Collect the full option block. AskUserQuestion often renders an indented
    # description after each `  N. label` line, so we can't require option
    # lines to be strictly consecutive. Allow a small gap of non-divider,
    # non-option lines (the description) between entries.
    MAX_GAP = 3

    def _is_block_line(idx: int) -> bool:
        return bool(
            _PROMPT_CONT_RE.match(lines[idx])
            or _PROMPT_OPTION_RE.match(lines[idx])
        )

    block_start = cursor_idx
    j = cursor_idx - 1
    gap = 0
    while j >= 0 and gap <= MAX_GAP:
        if _is_block_line(j):
            block_start = j
            gap = 0
        elif _is_divider(lines[j]):
            break
        elif lines[j].strip():
            gap += 1
        j -= 1

    block_end = cursor_idx
    k = cursor_idx + 1
    gap = 0
    while k < len(lines) and gap <= MAX_GAP:
        if _is_block_line(k):
            block_end = k
            gap = 0
        elif _is_divider(lines[k]):
            break
        elif lines[k].strip():
            gap += 1
        k += 1

    # Include any description lines trailing the last matched option — these
    # sit between the last numbered line and the next blank/divider boundary.
    k = block_end + 1
    while k < len(lines):
        line = lines[k]
        if _is_divider(line) or _is_block_line(k) or not line.strip():
            break
        block_end = k
        k += 1

    # Context = the question text above the option block. Blank lines are
    # skipped (Claude Code renders an empty line between question and
    # options), but real box-drawing dividers hard-stop the scan so that
    # unrelated TUI chrome / prior output never leaks in. Bounded search
    # window so the scan can't run up the entire pane.
    MAX_CTX_SCAN = 15
    ctx_lines: list[str] = []
    j = block_start - 1
    stop_at = max(-1, block_start - 1 - MAX_CTX_SCAN)
    while j > stop_at and len(ctx_lines) < 4:
        line = lines[j].rstrip()
        if _is_divider(line):
            break
        if line.strip():
            ctx_lines.insert(0, line)
        j -= 1

    options = [lines[k].rstrip() for k in range(block_start, block_end + 1)]
    preview = ctx_lines + options

    # Hash the option block only (not context). Context can flicker across
    # re-renders; options are the stable identity of a prompt.
    digest_input = "\n".join(l.strip() for l in options).encode("utf-8")
    prompt_hash = hashlib.sha256(digest_input).hexdigest()[:12]
    return prompt_hash, preview


def watch_prompts(tmux_target: str, interval_s: float = 3.0) -> None:
    """Continuously watch for new prompts and push a Telegram notification.

    Runs as a daemon thread for the lifetime of the poller. Same prompt is
    never notified twice; a prompt that disappears and reappears with a
    different option set is treated as new.
    """
    last_hash: str | None = None
    log("👀 prompt watcher started")
    while True:
        try:
            time.sleep(interval_s)
            pane = _pane_text(tmux_target)
            if not pane:
                continue
            result = detect_prompt(pane)
            if result is None:
                last_hash = None  # Prompt cleared; arm for next appearance.
                continue
            prompt_hash, preview = result
            if prompt_hash == last_hash:
                continue
            last_hash = prompt_hash
            body = "\n".join(preview)
            # Cap at ~1500 chars to stay well under Telegram's 4096 limit.
            if len(body) > 1500:
                body = body[:1500] + "\n…(잘림)"
            tg_reply(
                f"🔔 Claude Code 응답 대기\n\n{body}\n\n"
                f"응답: /1 /2 /3 … 또는 /yes /no /esc"
            )
        except Exception as e:
            # Never let this thread die; just log and keep going.
            log(f"⚠️ prompt watcher error: {e}")
            time.sleep(interval_s)


def start_watcher(name: str, tmux_target: str) -> None:
    fn = WATCHERS.get(name)
    if not fn:
        log(f"⚠️ unknown watcher: {name}")
        return
    t = threading.Thread(target=fn, args=(tmux_target,), daemon=True)
    t.start()
    log(f"👀 watcher started: {name}")


def send_screen_png() -> None:
    out = BASE_DIR / "photos" / f"screen-{int(time.time())}.png"
    out.parent.mkdir(exist_ok=True)
    cmd = _resolve_screenshot_cmd(out)
    if cmd is None:
        msg = (
            "❌ screenshot 도구를 찾을 수 없음. "
            "macOS는 screencapture, Linux는 grim/gnome-screenshot/scrot/maim 중 하나 필요"
        )
        log(f"⚠️ {msg}")
        tg_reply(msg)
        return
    try:
        subprocess.run(cmd, check=True, timeout=10)
        subprocess.run([str(TG_NOTIFY), "📸 screenshot", "--photo", str(out)],
                       check=True, timeout=20)
    except Exception as e:
        log(f"⚠️ screen_png failed: {e}")
        tg_reply(f"❌ screenshot 실패: {e}")


def download_photo(token: str, file_id: str) -> str | None:
    """Telegram getFile API로 사진 다운로드, 로컬 경로 반환."""
    photos_dir = BASE_DIR / "photos"
    photos_dir.mkdir(exist_ok=True)
    try:
        url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("ok"):
            return None
        file_path = data["result"]["file_path"]
        dl_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        ext = Path(file_path).suffix or ".jpg"
        local_name = f"{file_id[:20]}{ext}"
        local_path = photos_dir / local_name
        urllib.request.urlretrieve(dl_url, str(local_path))
        log(f"📷 photo saved: {local_path}")
        return str(local_path)
    except Exception as e:
        log(f"⚠️ photo download failed: {e}")
        return None


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_update_id": 0}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def append_inbox(update: dict) -> None:
    rotate_if_large(INBOX_FILE)
    with INBOX_FILE.open("a") as f:
        f.write(json.dumps(update, ensure_ascii=False) + "\n")


class TelegramRateLimited(Exception):
    """Raised when Telegram returns 429. `retry_after` is seconds to wait."""
    def __init__(self, retry_after: int, message: str = "") -> None:
        super().__init__(message or f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class TelegramAuthError(Exception):
    """401 (bad token) or 404 (malformed URL). Not retryable without human fix."""


class TelegramConflict(Exception):
    """409 — another poller holds getUpdates. Backoff hard to avoid duel."""


def get_updates(token: str, offset: int) -> list[dict]:
    params = {
        "offset": offset,
        "timeout": LONG_POLL_TIMEOUT,
        "allowed_updates": json.dumps(["message", "edited_message", "callback_query"]),
    }
    url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Telegram returns structured JSON even on error responses.
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {}
        code = e.code
        desc = body.get("description", "")
        retry_after = int(body.get("parameters", {}).get("retry_after", 0) or 0)
        if code == 429:
            # Prefer the server's Retry-After; fall back to header, then 1s.
            header_ra = int(e.headers.get("Retry-After", "0") or 0)
            wait = max(retry_after, header_ra, 1)
            raise TelegramRateLimited(wait, desc) from e
        if code in (401, 404):
            raise TelegramAuthError(f"{code} {desc}") from e
        if code == 409:
            raise TelegramConflict(desc) from e
        raise RuntimeError(f"Telegram HTTP {code}: {desc}") from e
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data.get("result", [])


def extract_summary(update: dict) -> str:
    msg = update.get("message") or update.get("edited_message") or {}
    frm = msg.get("from", {}) or {}
    who = frm.get("first_name") or frm.get("username") or f"id={frm.get('id')}"
    text = msg.get("text") or msg.get("caption") or "(non-text)"
    return f"{who}: {text[:80]}"


def main() -> None:
    env = load_env()
    token = env["TELEGRAM_BOT_TOKEN"]
    tmux_target = env.get("TMUX_TARGET", "")
    allowed_ids = parse_allowed_ids(env)
    state = load_state()
    log(f"🚀 tg_poll started (last_update_id={state['last_update_id']}, "
        f"tmux={tmux_target or 'OFF'}, allowlist={len(allowed_ids)} ids)")
    removed = cleanup_old_photos()
    if removed:
        log(f"🧹 cleaned {removed} stale screenshot(s) older than 7 days")

    # Always-on prompt watcher — notifies Telegram when Claude Code is
    # waiting on an AskUserQuestion or permission prompt.
    if tmux_target:
        threading.Thread(
            target=watch_prompts, args=(tmux_target,), daemon=True
        ).start()

    backoff = 1.0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 100  # launchd/systemd가 재기동
    while True:
        try:
            offset = state["last_update_id"] + 1
            updates = get_updates(token, offset)
            backoff = 1.0  # 성공하면 리셋
            consecutive_failures = 0
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
                    if tmux_target and send_raw_slash(tmux_target, result.payload):
                        watcher_name = result.metadata.get("watch")
                        if watcher_name:
                            start_watcher(watcher_name, tmux_target)
                elif result.action == "key_inject":
                    if tmux_target:
                        send_keys_seq(tmux_target, result.keys)
                elif result.action == "screen_text":
                    if tmux_target:
                        send_screen_text(tmux_target, int(result.payload))
                elif result.action == "screen_png":
                    send_screen_png()
                elif result.action == "restart_claude":
                    if tmux_target:
                        handle_restart_claude(tmux_target, result.payload or "claude")
                elif result.action in ("confirm_required", "status_reply"):
                    tg_reply(result.reply_text)
                else:
                    log(f"⚠️ unknown action: {result.action}")
            if updates:
                save_state(state)
        except TelegramRateLimited as e:
            # 429 — honor server's retry_after, do NOT exponential backoff on top.
            wait = e.retry_after
            log(f"⏳ Telegram 429 rate limited — sleeping {wait}s (server request)")
            time.sleep(wait)
            # Do not bump consecutive_failures: rate limit is benign, not an outage.
        except TelegramAuthError as e:
            log(f"🔑 Auth error: {e}. Check TELEGRAM_BOT_TOKEN in .env. "
                f"Sleeping 5min before retry.")
            time.sleep(300)
            consecutive_failures += 1
        except TelegramConflict as e:
            # Another getUpdates poller is active. Sleep long to avoid a duel.
            log(f"⚔️ Polling conflict: {e}. Sleeping 30s.")
            time.sleep(30)
            consecutive_failures += 1
        except urllib.error.URLError as e:
            log(f"⚠️ Network error: {e} — retry in {backoff:.1f}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            consecutive_failures += 1
        except Exception as e:
            log(f"❌ Unexpected error: {e} — retry in {backoff:.1f}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            consecutive_failures += 1
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            log(f"💀 {consecutive_failures} consecutive failures — exiting so "
                f"launchd/systemd can restart cleanly")
            sys.exit(1)


if __name__ == "__main__":
    main()
