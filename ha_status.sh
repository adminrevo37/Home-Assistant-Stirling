#!/bin/bash
# Revolution Sports — HA Status Check
# Usage: bash /config/ha_status.sh
# Requires: /config/ha_token.txt (HA long-lived token)
# Created: 2026-05-30

TOKEN_FILE="/config/ha_token.txt"
HA="http://localhost:8123/api"

# Check token exists
if [ ! -f "$TOKEN_FILE" ]; then
  echo "ERROR: $TOKEN_FILE not found."
  echo "Create via HA Profile > Security > Long-lived access tokens"
  exit 1
fi

TOKEN=$(cat "$TOKEN_FILE")

get_state() {
  curl -s -H "Authorization: Bearer $TOKEN" "$HA/states/$1" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('state','?'))" 2>/dev/null
}

get_attr() {
  curl -s -H "Authorization: Bearer $TOKEN" "$HA/states/$1" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('attributes',{}).get('$2','?'))" 2>/dev/null
}

echo "========================================"
echo "  REVO STIRLING STATUS — $(date '+%Y-%m-%d %H:%M %Z')"
echo "========================================"

# Bay helpers + highbay lights + power
echo ""
echo "--- BAYS ---"
for N in 1 2 3 4 5; do
  HELPER=$(get_state "input_boolean.bay${N}_helper")
  LIGHT=$(get_state "light.highbay_${N}")
  POWER=$(get_attr "light.highbay_${N}" "CURRENT_POWER")
  BLOCKED=$(get_state "input_boolean.bay${N}_blocked")
  BLOCK_LABEL=""
  if [ "$BLOCKED" = "on" ]; then BLOCK_LABEL=" [BLOCKED]"; fi
  printf "Bay %s: %-3s | Highbay: %-3s (%sW)%s\n" "$N" "$HELPER" "$LIGHT" "$POWER" "$BLOCK_LABEL"
done

# Door status
echo ""
echo "--- FRONT DOOR ---"
DOOR_LOCK=$(get_state "lock.front_door_lock")
DOOR_SENSOR=$(get_state "binary_sensor.entry_door")
VISUAL=$(get_state "input_boolean.roller_door_visual_open")
SERVICE=$(get_state "input_boolean.door_service_mode")
echo "Lock: $DOOR_LOCK | Door sensor: $DOOR_SENSOR | Visual open: $VISUAL | Service mode: $SERVICE"

# Common lights
echo ""
echo "--- COMMON LIGHTS ---"
WALL=$(get_state "light.inside_wall_lights")
FLURO=$(get_state "light.fluro_x_4")
MEZZ=$(get_state "light.mezzanine_wall_lights")
OUTSIDE=$(get_state "light.outside_wall_lights")
echo "Wall: $WALL | Fluro: $FLURO | Mezzanine: $MEZZ | Outside: $OUTSIDE"

# Occupancy
echo ""
echo "--- OCCUPANCY ---"
OCC=$(get_state "binary_sensor.main_occupancy_status")
OCC_PROB=$(get_state "sensor.main_occupancy_probability_2")
echo "Status: $OCC | Probability: $OCC_PROB%"

# Energy today (if available)
echo ""
echo "--- ENERGY TODAY ---"
for N in 1 2 3 4 5; do
  ENERGY=$(get_attr "light.highbay_${N}" "ENERGY_USED_TODAY")
  printf "Highbay %s: %s Wh\n" "$N" "$ENERGY"
done

# Last entry log
echo ""
echo "--- LAST ENTRY LOG ---"
if [ -f "/config/www/entry_log.csv" ]; then
  tail -3 /config/www/entry_log.csv
else
  echo "No entry log found"
fi

echo ""
echo "========================================"
