#!/bin/bash
INPUT=$(cat)

# transcript_path에서 team1 여부 판단
IS_TEAM1=$(printf '%s' "$INPUT" | python3 -c "
import json, sys
try:
    path = json.load(sys.stdin).get('transcript_path', '')
    print('yes' if 'team1' in path else 'no')
except:
    print('no')
" 2>/dev/null)

echo "$(date) transcript_team1=$IS_TEAM1" >> /home/chan/.claude/channels/telegram/notify_stop_main.log

if [[ "$IS_TEAM1" == 'yes' ]]; then
    printf '%s' "$INPUT" | exec /home/chan/.claude/channels/telegram/notify_stop_team1.sh
else
    printf '%s' "$INPUT" | exec /home/chan/.claude/channels/telegram/notify_stop.sh
fi
