#!/bin/bash
# Control4 Roller Door Control
# Usage: bash /config/c4_door_control.sh lock|unlock
#
# Sends LOCK or UNLOCK to DS2 Lock Front (item 93) via Control4 Director API.
# Token is read from /config/c4_token_cache.txt

COMMAND="${1:-lock}"
TOKEN=$(cat /config/c4_token_cache.txt)
H="https://192.168.1.112"
URL="$H/api/v1/items/93/commands"

case "$COMMAND" in
  lock|LOCK)
    PAYLOAD='{"command":"LOCK","async":false}'
    LABEL="LOCK"
    ;;
  unlock|UNLOCK)
    PAYLOAD='{"command":"UNLOCK","async":false}'
    LABEL="UNLOCK"
    ;;
  *)
    echo "ERROR: Unknown command '$COMMAND'. Use: lock or unlock"
    exit 1
    ;;
esac

RESPONSE=$(curl -sk -X POST "$URL" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

RESULT=$(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result','?'))" 2>/dev/null || echo "?")
echo "DOOR $LABEL => result=$RESULT"
echo "$RESPONSE"
