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


# --- Task 1: fallback baseline ---

def test_empty_text_falls_back():
    r = dispatch("")
    assert r.action == "fallback_prefix"


def test_plain_text_falls_back():
    r = dispatch("hello there")
    assert r.action == "fallback_prefix"
    assert r.payload == "hello there"


# --- Task 2: raw slash passthrough ---

def test_compact_is_raw_inject():
    r = dispatch("/compact")
    assert r.action == "raw_inject"
    assert r.payload == "/compact"
    # /compact is long-running → dispatcher tags it for background watcher
    assert r.metadata.get("watch") == "compact"


def test_fast_has_no_watcher():
    r = dispatch("/fast")
    assert r.action == "raw_inject"
    # non-compact raw_slash shouldn't trigger the watcher
    assert r.metadata.get("watch") is None


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


def test_unknown_slash_is_injected_raw():
    # Unknown but well-formed slashes pass through — Claude Code TUI handles them.
    r = dispatch("/totallyunknownxyz foo")
    assert r.action == "raw_inject"
    assert r.payload == "/totallyunknownxyz foo"


def test_plugin_namespaced_slash_injected_raw():
    # plugin:skill form (e.g. /superpowers:write-plan) must pass through.
    r = dispatch("/superpowers:write-plan")
    assert r.action == "raw_inject"
    assert r.payload == "/superpowers:write-plan"


def test_plugin_namespaced_slash_with_args():
    r = dispatch("/vercel-plugin:deploy prod")
    assert r.action == "raw_inject"
    assert r.payload == "/vercel-plugin:deploy prod"


def test_malformed_slash_falls_back():
    # Spaces/weird chars in the command itself → fallback_prefix.
    r = dispatch("/ hello world")
    assert r.action == "fallback_prefix"


# --- Task 3: key sequence injection ---

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


# --- Task 4: screen capture ---

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


# --- Task 5: dangerous-command confirmation ---

def test_confirm_with_no_pending_shows_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    r = dispatch("/confirm")
    assert r.action == "status_reply"
    assert "pending" in r.reply_text.lower() or "없" in r.reply_text


def test_confirm_without_token_auto_selects_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    t = 1_700_000_000.0
    dispatch("/clear", now=t)
    r = dispatch("/confirm", now=t + 5)
    assert r.action == "raw_inject"
    assert r.payload == "/clear"


def test_quit_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    r = dispatch("/quit", now=1_700_000_000.0)
    assert r.action == "confirm_required"
    assert r.pending_token.startswith("quit-")


def test_quit_confirm_emits_double_ctrl_c(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    t = 1_700_000_000.0
    dispatch("/quit", now=t)
    r = dispatch("/confirm", now=t + 5)
    assert r.action == "key_inject"
    assert r.keys[0] == "C-c"
    assert r.keys[-1] == "C-c"
    assert any(k.startswith("sleep:") for k in r.keys)


def test_restart_default_uses_claude():
    r = dispatch("/restart")
    assert r.action == "restart_claude"
    assert r.payload == "claude"


def test_restart_x_uses_claudex():
    r = dispatch("/restart x")
    assert r.action == "restart_claude"
    assert r.payload == "claudex"


def test_restart_unknown_arg_falls_back_to_claude():
    r = dispatch("/restart bogus")
    assert r.action == "restart_claude"
    assert r.payload == "claude"


def test_clear_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    r = dispatch("/clear", now=1_700_000_000.0)
    assert r.action == "confirm_required"
    assert r.pending_token.startswith("clear-")
    assert "/confirm" in r.reply_text


def test_confirm_with_valid_token_executes(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PENDING_FILE", tmp_path / "pending.json")
    t = 1_700_000_000.0
    r1 = dispatch("/clear", now=t)
    r2 = dispatch(f"/confirm {r1.pending_token}", now=t + 5)
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


# --- Task 6: /help ---

def test_help_returns_status_reply():
    r = dispatch("/help")
    assert r.action == "status_reply"
    assert "/compact" in r.reply_text
    assert "/screen" in r.reply_text
