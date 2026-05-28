#!/bin/bash
# Control4 Phase 3 diagnostic - tests all code management commands via curl
# Run: bash /config/c4_diagnostic3.sh > /config/www/c4_output3.txt 2>&1
TOKEN=$(cat /config/c4_token_cache.txt)
H="https://192.168.1.112"
URL="$H/api/v1/items/39/commands"
AUTH="Authorization: Bearer $TOKEN"
CT="Content-Type: application/json"

send() {
  local label="$1"
  local data="$2"
  echo "--- $label ---"
  curl -sk -X POST "$URL" -H "$AUTH" -H "$CT" -d "$data"
  echo ""
  echo ""
}

echo "=============================="
echo " Control4 Phase 3 Diagnostic"
echo "=============================="
echo ""

echo "=== TEST 1: SET_USER_CODE slot 11 WITH NAME ==="
send "SET CODE_ID=11 CODE=972707 NAME=Athlete Julian" \
  '{"command":"SET_USER_CODE","params":{"CODE_ID":11,"CODE":"972707","NAME":"Athlete Julian"},"async":false}'

echo "=== TEST 2: READ variables (check Last Known values) ==="
curl -sk "$H/api/v1/items/39/variables" -H "$AUTH" | python3 -c "
import json,sys
for v in json.load(sys.stdin):
    print(f'  {v[\"varName\"]:<40} = {v[\"value\"]}')
"
echo ""

echo "=== TEST 3: DELETE_USER_CODE slot 11 ==="
send "DELETE CODE_ID=11" \
  '{"command":"DELETE_USER_CODE","params":{"CODE_ID":11},"async":false}'

echo "=== TEST 4: REMOVE_USER_CODE slot 11 ==="
send "REMOVE CODE_ID=11" \
  '{"command":"REMOVE_USER_CODE","params":{"CODE_ID":11},"async":false}'

echo "=== TEST 5: CLEAR_USER_CODE slot 11 ==="
send "CLEAR CODE_ID=11" \
  '{"command":"CLEAR_USER_CODE","params":{"CODE_ID":11},"async":false}'

echo "=== TEST 6: SET empty string to clear slot 11 ==="
send "SET CODE_ID=11 CODE=empty" \
  '{"command":"SET_USER_CODE","params":{"CODE_ID":11,"CODE":"","NAME":""},"async":false}'

echo "=== TEST 7: Slot boundary - slot 0 ==="
send "SET CODE_ID=0" \
  '{"command":"SET_USER_CODE","params":{"CODE_ID":0,"CODE":"000001","NAME":"test"},"async":false}'

echo "=== TEST 8: Slot boundary - slot 100 ==="
send "SET CODE_ID=100" \
  '{"command":"SET_USER_CODE","params":{"CODE_ID":100,"CODE":"100100","NAME":"test"},"async":false}'

echo "=== TEST 9: Slot boundary - slot 250 ==="
send "SET CODE_ID=250" \
  '{"command":"SET_USER_CODE","params":{"CODE_ID":250,"CODE":"250250","NAME":"test"},"async":false}'

echo "=== TEST 10: Re-set slot 11 with real booking code ==="
send "SET CODE_ID=11 CODE=972707 NAME=Athlete Julian" \
  '{"command":"SET_USER_CODE","params":{"CODE_ID":11,"CODE":"972707","NAME":"Athlete Julian"},"async":false}'

echo "=== TEST 11: All available commands on DS3 item 39 ==="
curl -sk "$H/api/v1/items/39/commands" -H "$AUTH"
echo ""

echo "=== Done ==="
