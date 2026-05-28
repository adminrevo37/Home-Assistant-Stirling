#!/usr/bin/env python3
"""
Control4 DS3 Entry Log Poller
Detects when someone uses a code on the DS3 and appends to entry log CSV.
Runs every 1 minute via HA automation -> shell_command.c4_entry_log

Log file: /config/www/entry_log.csv
State file: /config/c4_entry_log_state.json  (tracks last seen code ID)

Slot -> Bay mapping:
  11-13 -> Bay 1
  14-16 -> Bay 2
  17-19 -> Bay 3
  20-22 -> Bay 4
  23-25 -> Bay 5
  1-10  -> Staff
"""
import asyncio
import json
import csv
import os
from datetime import datetime

DS3_ITEM_ID = 39
LOG_FILE = '/config/www/entry_log.csv'
STATE_FILE = '/config/c4_entry_log_state.json'

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
        return {"last_code_id": None, "last_name": None}


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
    # Read slot helpers to get the code for this slot
    code = get_slot_code(slot)
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([ts, bay, slot, name, code])
    print(f"Logged: {ts} | {bay} | slot {slot} | {name} | code {code}")


def get_slot_code(slot):
    """Read the code stored in the input_text helper for this slot."""
    try:
        # Map slot to bay and helper name
        for bay_num, start in enumerate([11, 14, 17, 20, 23], 1):
            for i, offset in enumerate(range(3)):
                if start + offset == slot:
                    letter = ['a', 'b', 'c'][i]
                    helper_path = f'/config/.storage/core.restore_state'
                    # Try reading from HA states file
                    with open(helper_path) as f:
                        states = json.load(f)
                    for s in states.get('data', {}).get('states', []):
                        entity_id = f"input_text.bay{bay_num}_code_slot_{letter}"
                        if s.get('entity_id') == entity_id:
                            return s.get('state', '')
    except Exception:
        pass
    return ''


async def poll():
    import aiohttp

    # Load credentials
    with open('/config/.storage/core.config_entries') as f:
        config = json.load(f)
    c4 = next(e for e in config['data']['entries'] if e['domain'] == 'control4')
    host = c4['data']['host']

    with open('/config/c4_token_cache.txt') as f:
        token = f.read().strip()

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

    if current_code_id is not None and current_code_id != last_id:
        print(f"New entry detected: slot={current_code_id} name={current_name}")
        append_log_entry(current_code_id, current_name or 'Unknown')
        state['last_code_id'] = current_code_id
        state['last_name'] = current_name
        save_state(state)
    else:
        print(f"No new entry. Last slot={last_id}")


asyncio.run(poll())
