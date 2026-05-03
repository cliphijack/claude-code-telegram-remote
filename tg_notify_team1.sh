#!/bin/bash
# tg_notify.sh — Claude Code가 형한테 메시지/파일 보내는 헬퍼
# 사용법:
#   tg_notify.sh "메시지 텍스트"
#   tg_notify.sh "메시지" --photo /path/to/image.png
#   tg_notify.sh "메시지" --doc /path/to/file.mp4
#   tg_notify.sh "메시지" --reply_to <message_id>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.env"

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set in .env}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID not set in .env}"

CHAT_ID="${TELEGRAM_CHAT_ID}"
API="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}"

if [ $# -eq 0 ]; then
  echo "Usage: $0 \"message text\" [--photo path | --doc path] [--reply_to id]" >&2
  exit 1
fi

TEXT="$1"
shift

PHOTO=""
DOC=""
REPLY_TO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --photo) PHOTO="$2"; shift 2 ;;
    --doc)   DOC="$2";   shift 2 ;;
    --reply_to) REPLY_TO="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

PARSE_RESULT='python3 -c "import json,sys; d=json.load(sys.stdin); print(\"✅\" if d.get(\"ok\") else \"❌\", d.get(\"result\",{}).get(\"message_id\") or d.get(\"description\",\"\"))"'

if [ -n "$PHOTO" ]; then
  # sendPhoto는 multipart (-F)
  ARGS=(-F "chat_id=${CHAT_ID}" -F "photo=@${PHOTO}" -F "caption=${TEXT}")
  [ -n "$REPLY_TO" ] && ARGS+=(-F "reply_to_message_id=${REPLY_TO}")
  curl -sS -X POST "${API}/sendPhoto" "${ARGS[@]}" | eval "$PARSE_RESULT"
elif [ -n "$DOC" ]; then
  # sendDocument는 multipart (-F)
  ARGS=(-F "chat_id=${CHAT_ID}" -F "document=@${DOC}" -F "caption=${TEXT}")
  [ -n "$REPLY_TO" ] && ARGS+=(-F "reply_to_message_id=${REPLY_TO}")
  curl -sS -X POST "${API}/sendDocument" "${ARGS[@]}" | eval "$PARSE_RESULT"
else
  # sendMessage는 form-urlencoded (-d)
  ARGS=(-d "chat_id=${CHAT_ID}" --data-urlencode "text=${TEXT}")
  [ -n "$REPLY_TO" ] && ARGS+=(-d "reply_to_message_id=${REPLY_TO}")
  curl -sS -X POST "${API}/sendMessage" "${ARGS[@]}" | eval "$PARSE_RESULT"
fi
