#!/bin/bash
# notify_stop.sh — Claude Code Stop hook: push assistant text to Telegram.
# Collects ALL assistant text blocks since the last real user prompt
# (not tool_result), concatenates in order, sends via tg_notify.sh.

INPUT=$(cat)
LOG=~/.claude/channels/telegram/notify_stop.log

TRANSCRIPT=$(printf '%s' "$INPUT" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('transcript_path',''))
except: pass" 2>/dev/null)

[ -z "$TRANSCRIPT" ] && exit 0
[ ! -f "$TRANSCRIPT" ] && exit 0

TEXT=$(python3 - "$TRANSCRIPT" <<'PY'
import json, sys, time
path = sys.argv[1]

def is_real_user_prompt(e):
    if e.get("type") != "user":
        return False
    msg = e.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result":
                return False
        return True
    if isinstance(content, str):
        return True
    return False

def extract():
    try:
        with open(path) as f:
            entries = [json.loads(l) for l in f if l.strip()]
    except Exception:
        return ""
    boundary = -1
    for i in range(len(entries) - 1, -1, -1):
        if is_real_user_prompt(entries[i]):
            boundary = i
            break
    texts = []
    for e in entries[boundary + 1:]:
        if e.get("type") != "assistant":
            continue
        msg = e.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    t = c.get("text", "")
                    if t.strip():
                        texts.append(t)
        elif isinstance(content, str) and content.strip():
            texts.append(content)
    return "\n\n".join(texts)

# Poll for transcript stability. Stop hook can fire before the final
# assistant text is flushed; an early-exit retry would miss later text
# blocks. Wait until extract() returns the same text twice in a row
# (≥600ms unchanged) or hit the 5s ceiling.
merged = ""
last = ""
stable = 0
deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    current = extract()
    if current and current == last:
        stable += 1
        if stable >= 2:
            merged = current
            break
    else:
        stable = 0
        last = current
        merged = current
    time.sleep(0.3)

if len(merged) > 3900:
    merged = merged[:3900] + "\n\n…(잘림)"
print(merged)
PY
)

# Debug log: timestamp, text length, first 40 chars
{
    printf '[%s] len=%d preview=%q\n' "$(date '+%F %T')" "${#TEXT}" "${TEXT:0:40}"
} >> "$LOG" 2>/dev/null

[ -z "$TEXT" ] && exit 0

/home/chan/.claude/channels/telegram/tg_notify_team1.sh "$TEXT" >/dev/null 2>&1 || true
exit 0
