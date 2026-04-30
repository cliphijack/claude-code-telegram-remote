#!/bin/bash
set -euo pipefail

HOOK_INPUT=$(cat)

ENV_FILE="/c/Users/chan/.claude/channels/telegram/.env"
# WSL 경로 fallback
if [[ ! -f "$ENV_FILE" ]]; then
  ENV_FILE="//wsl.localhost/Ubuntu/home/chan/.claude/channels/telegram/.env"
fi
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true)
CHAT_ID=$(grep '^TELEGRAM_CHAT_ID=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || true)

[[ -z "$TOKEN" || -z "$CHAT_ID" ]] && exit 0

PYTHON="/c/Users/chan/AppData/Local/Programs/Python/Python312/python.exe"

TRANSCRIPT_PATH=$(echo "$HOOK_INPUT" | "$PYTHON" -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('transcript_path', ''))
except:
    pass
" 2>/dev/null || true)

[[ -z "$TRANSCRIPT_PATH" || ! -f "$TRANSCRIPT_PATH" ]] && exit 0

PAYLOAD=$("$PYTHON" - "$TRANSCRIPT_PATH" "$CHAT_ID" <<'PY'
import json, sys

path = sys.argv[1]
chat_id = sys.argv[2]
try:
    with open(path, encoding='utf-8') as f:
        entries = [json.loads(l) for l in f if l.strip()]
except Exception:
    sys.exit(0)

texts = []
for e in reversed(entries):
    if e.get('type') == 'assistant':
        msg = e.get('message') or {}
        content = msg.get('content', [])
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'text':
                    t = c.get('text', '').strip()
                    if t:
                        texts.insert(0, t)
        elif isinstance(content, str) and content.strip():
            texts.insert(0, content.strip())
    elif e.get('type') == 'user':
        msg = e.get('message') or {}
        content = msg.get('content', [])
        # stop at real user message (not tool_result)
        if isinstance(content, str):
            break
        if isinstance(content, list) and any(
            isinstance(c, dict) and c.get('type') != 'tool_result'
            for c in content
        ):
            break

if not texts:
    sys.exit(0)

result = '\n\n'.join(texts)[:4000]
print(json.dumps({"chat_id": chat_id, "text": result}))
PY
2>/dev/null || true)

[[ -z "$PAYLOAD" ]] && exit 0

curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  > /dev/null 2>&1 || true

exit 0
