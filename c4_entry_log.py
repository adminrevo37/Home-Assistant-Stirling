#!/usr/bin/env python3
"""
Control4 DS3 Entry Log Poller
Detects when someone uses a code on the DS3 and appends to entry log CSV.
Runs every 1 minute via HA automation -> shell_command.c4_entry_log

Log file: /config/www/entry_log.csv
State file: /config/c4_entry_log_state.json  (tracks last seen code ID + timestamp)

Slot -> Bay mapping:
  11-13 -> Bay 1
  14-16 -> Bay 2
  17-19 -> Bay 3
  20-22 -> Bay 4
  23-25 -> Bay 5
  1-10  -> Staff

Fixes applied 2026-05-29:
  Bug 1: Same-slot repeat now detected via timestamp (REPEAT_ENTRY_MIN_MINUTES)
  Bug 2: Staff slots 1-10 return 'staff-protected' instead of blank
  Bug 3: get_slot_code() tries HA REST API first (requires /config/ha_token.txt),
         falls back to core.restore_state
  Bug 4: CODE:Name format stripped — returns just the 6-digit code
"""
import asyncio
import json
import csv
import os
import sys
import traceback
from datetime import datetime

import c4_auth  # shared, expiry-aware token management

DS3_ITEM_ID = 39
LOG_FILE = '/config/www/entry_log.csv'
STATE_FILE = '/config/c4_entry_log_state.json'
HA_TOKEN_FILE = '/config/ha_token.txt'
HA_API = 'http://localhost:8123/api'

# Minimum minutes between repeated same-slot entries — prevents duplicate logging
# during a single session while still catching genuine re-entries
REPEAT_ENTRY_MIN_MINUTES = 15

SLOT_TO_BAY = {}
for bay, start in enumerate([11, 14, 17, 20, 23], 1):
    for offset in range(3):
        SLOT_TO_BAY[start + offset] = f"Bay {bay}"
for slot in range(1, 11):
    SLOT_TO_BAY[slot] = "Staff"


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_code_id": None, "last_name": None, "last_logged_at": None}


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def ensure_log_header():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp", "Bay", "Slot", "Customer Name", "Code Used"
            ])


def append_log_entry(slot, name):
    ensure_log_header()
    bay = SLOT_TO_BAY.get(slot, f"Unknown (slot {slot})")
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    code = get_slot_code(slot)
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([ts, bay, slot, name, code])
    print(f"Logged: {ts} | {bay} | slot {slot} | {name} | code {code}")


def get_ha_token():
    """Read HA long-lived token from file, return None if unavailable."""
    try:
        with open(HA_TOKEN_FILE) as f:
            return f.read().strip()
    except Exception:
        return None


def get_slot_code(slot):
    """
    Return the 6-digit code stored for this slot.
    Staff slots (1-10): returns 'staff-protected'.
    Bay slots (11-25): tries HA REST API first (requires /config/ha_token.txt),
                       falls back to core.restore_state if no token.
    Strips any trailing ':Name' suffix from stored value.
    """
    # Staff slots — codes are permanent and not stored in input_text helpers
    if 1 <= slot <= 10:
        return 'staff-protected'

    # Map slot number to input_text entity_id
    entity_id = None
    for bay_num, start in enumerate([11, 14, 17, 20, 23], 1):
        for i, offset in enumerate(range(3)):
            if start + offset == slot:
                letter = ['a', 'b', 'c'][i]
                entity_id = f"input_text.bay{bay_num}_code_slot_{letter}"
                break
        if entity_id:
            break

    if not entity_id:
        return ''

    # Try HA REST API for live state (most accurate)
    token = get_ha_token()
    if token:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{HA_API}/states/{entity_id}",
                headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read())
                raw = data.get('state', '')
                return raw.split(':')[0].strip() if raw else ''
        except Exception:
            pass

    # Fallback: core.restore_state (stale — only updated on HA restart)
    # Create /config/ha_token.txt with a long-lived HA token to use the live API
    try:
        with open('/config/.storage/core.restore_state') as f:
            states = json.load(f)
        for s in states.get('data', {}).get('states', []):
            if s.get('entity_id') == entity_id:
                raw = s.get('state', '')
                return raw.split(':')[0].strip() if raw else ''
    except Exception:
        pass

    return ''


async def poll():
    import aiohttp

    # Expiry-aware token (re-auths automatically if cache is missing/expired)
    host, token = await c4_auth.get_valid_token_and_host()

    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://{host}/api/v1/items/{DS3_ITEM_ID}/variables"

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        async with s.get(url, headers=headers,
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            variables = await r.json(content_type=None)

    # Extract Last Known User Code ID and Name
    current_code_id = None
    current_name = None
    for v in variables:
        if v.get('varName') == 'Last Known User Code ID':
            current_code_id = v.get('value')
        elif v.get('varName') == 'Last Known User Name':
            current_name = v.get('value', 'Unknown')

    if current_code_id is None:
        print("Could not read Last Known User Code ID")
        return

    current_code_id = int(current_code_id) if current_code_id != '' else None

    # Compare with last seen state
    state = load_state()
    last_id = state.get('last_code_id')
    last_logged_at = state.get('last_logged_at')

    # Determine if this is a new loggable entry:
    # - Different slot → always new
    # - Same slot → only log again if REPEAT_ENTRY_MIN_MINUTES has elapsed
    is_new_entry = False
    if current_code_id is not None:
        if current_code_id != last_id:
            is_new_entry = True
        elif last_logged_at is not None:
            try:
                last_dt = datetime.fromisoformat(last_logged_at)
                elapsed = (datetime.now() - last_dt).total_seconds() / 60
                if elapsed >= REPEAT_ENTRY_MIN_MINUTES:
                    is_new_entry = True
                    print(f"Same slot {current_code_id} re-entry after {elapsed:.1f} min")
            except Exception:
                is_new_entry = True  # Unparseable timestamp — log to be safe

    if is_new_entry:
        print(f"New entry detected: slot={current_code_id} name={current_name}")
        append_log_entry(current_code_id, current_name or 'Unknown')
        state['last_code_id'] = current_code_id
        state['last_name'] = current_name
        state['last_logged_at'] = datetime.now().isoformat()
        save_state(state)
    else:
        print(f"No new entry. Last slot={last_id}")


if __name__ == '__main__':
    try:
        asyncio.run(poll())
    except Exception as e:
        # Log one clean line instead of crashing with exit 1 every minute
        # (the previous behaviour flooded home-assistant.log). A persistent
        # failure is still visible here in /config/www/entry_log_debug.txt.
        print(f"{datetime.now().isoformat()} poll() failed: {e}")
        traceback.print_exc()
        # Exit 0 so HA's shell_command doesn't flag a recurring ERROR; the
        # message above remains in the debug log for diagnosis.
        sys.exit(0)
