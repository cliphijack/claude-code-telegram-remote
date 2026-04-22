"""Unit tests for the prompt-watcher detect function.

Run:  python3 -m pytest test_prompt_watcher.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tg_poll import detect_prompt


def test_detect_permission_prompt():
    pane = (
        "Tool call pending\n"
        "\n"
        "Do you want to make this edit to foo.py?\n"
        "❯ 1. Yes\n"
        "  2. Yes, and allow Claude to edit its own settings for this session\n"
        "  3. No\n"
    )
    result = detect_prompt(pane)
    assert result is not None
    prompt_hash, preview = result
    assert len(prompt_hash) == 12
    joined = "\n".join(preview)
    assert "Do you want to make this edit" in joined
    assert "❯ 1. Yes" in joined
    assert "3. No" in joined


def test_detect_ask_user_question():
    pane = (
        "Choose a plan review mode.\n"
        "\n"
        "❯ 1. A) Full review\n"
        "  2. B) Skip review\n"
        "  3. C) Delegate to codex\n"
    )
    result = detect_prompt(pane)
    assert result is not None
    _, preview = result
    assert "A) Full review" in "\n".join(preview)


def test_no_prompt_returns_none():
    pane = "Just output\nwith no prompt\nand no cursor glyph\n"
    assert detect_prompt(pane) is None


def test_numbered_list_without_cursor_ignored():
    """Plain numbered output (e.g. /help) must not trigger the watcher."""
    pane = "1. first\n2. second\n3. third\n"
    assert detect_prompt(pane) is None


def test_same_prompt_produces_stable_hash():
    pane1 = "Q?\n❯ 1. Yes\n  2. No\n"
    pane2 = "different context above\n\nQ?\n❯ 1. Yes\n  2. No\n"
    h1, _ = detect_prompt(pane1)
    h2, _ = detect_prompt(pane2)
    assert h1 == h2, "hash must depend only on the option block"


def test_different_options_produce_different_hash():
    pane1 = "❯ 1. Yes\n  2. No\n"
    pane2 = "❯ 1. Accept\n  2. Reject\n"
    h1, _ = detect_prompt(pane1)
    h2, _ = detect_prompt(pane2)
    assert h1 != h2


def test_caret_greater_cursor_also_detected():
    """Some terminals render the cursor as `>` rather than `❯`."""
    pane = "Q?\n> 1. A\n  2. B\n"
    result = detect_prompt(pane)
    assert result is not None


def test_divider_stops_context_scan():
    """Lines above a divider (TUI frame, previous output) must not leak in."""
    pane = (
        "Previous bash call output here\n"
        "More unrelated output\n"
        "─────────────────────────────\n"
        "The actual question text?\n"
        "❯ 1. Option A\n"
        "  2. Option B\n"
    )
    result = detect_prompt(pane)
    assert result is not None
    _, preview = result
    joined = "\n".join(preview)
    assert "The actual question text?" in joined
    assert "Previous bash call output" not in joined, (
        "divider did not stop context scan — unrelated output leaked"
    )
    assert "─────" not in joined, "divider line itself must not be included"


def test_blank_line_alone_does_not_stop_context():
    """Empty lines inside the question block are OK — only dividers stop."""
    # Actually with current design we want blank lines to be skipped too,
    # since the question and options sit tight. Dividers are the hard boundary.
    pane = (
        "Question line 1\n"
        "Question line 2\n"
        "❯ 1. A\n"
        "  2. B\n"
    )
    result = detect_prompt(pane)
    assert result is not None
    _, preview = result
    joined = "\n".join(preview)
    assert "Question line 1" in joined
    assert "Question line 2" in joined


def test_context_capped_at_four_lines():
    pane = (
        "L6\nL5\nL4\nL3\nL2\nL1\n"
        "❯ 1. A\n  2. B\n"
    )
    result = detect_prompt(pane)
    assert result is not None
    _, preview = result
    joined = "\n".join(preview)
    assert "L1" in joined and "L4" in joined
    assert "L5" not in joined and "L6" not in joined


def test_blank_line_between_question_and_options_captured():
    """Claude Code TUI inserts a blank line between the question and the
    option block. That blank must not stop context collection."""
    pane = (
        "What do you want to do?\n"
        "\n"
        "❯ 1. Option A\n"
        "  2. Option B\n"
    )
    result = detect_prompt(pane)
    assert result is not None
    _, preview = result
    joined = "\n".join(preview)
    assert "What do you want to do?" in joined


def test_options_with_descriptions_all_captured():
    """AskUserQuestion inserts description lines between `  N. label` rows;
    block extension must tolerate that gap."""
    pane = (
        "Question?\n"
        "❯ 1. Option A\n"
        "    details about A span a line\n"
        "  2. Option B\n"
        "    details about B\n"
        "  3. Option C\n"
        "    details about C\n"
    )
    result = detect_prompt(pane)
    assert result is not None
    _, preview = result
    joined = "\n".join(preview)
    for marker in ("Option A", "Option B", "Option C",
                   "details about A", "details about B", "details about C"):
        assert marker in joined, f"{marker!r} missing from preview"
