#!/usr/bin/env python3
"""
Control4 Director item dump — lists ALL items (id / type / name / room) to STDOUT.

Purpose: surface Control4 devices that the HA Control4 integration does NOT expose
(it only exposes lights/locks/relays — never inputs/contacts/buttons). Used to locate
the roller-door exit button / contact inputs and confirm the door operator item (93).

Run via shell_command.c4_item_dump and read the returned stdout (NOT redirected).
"""
import asyncio
import json
import c4_auth  # shared, expiry-aware token


KEYWORDS = ['relay', 'contact', 'button', 'keypad', 'door', 'exit', 'push',
            'release', 'sensor', 'input', 'io', 'switch', 'intercom', 'ds3',
            'access', 'spare', 'gate', 'motor', 'opener']


def _norm(items):
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return []
    return items if isinstance(items, list) else []


async def main():
    host, token = await c4_auth.get_valid_token_and_host()
    import aiohttp
    from pyControl4.director import C4Director
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        director = C4Director(host, token, s)
        items = _norm(await director.get_all_item_info())

    print(f"TOTAL ITEMS: {len(items)}")

    def line(it):
        return (f"id={it.get('id')} type={it.get('type')} "
                f"ctl={it.get('control','')} room={it.get('roomName','')} "
                f"name={it.get('name','')}")

    print("\n=== LIKELY-RELEVANT ITEMS (door/relay/contact/button/keypad/io/etc.) ===")
    for it in items:
        blob = f"{it.get('name','')} {it.get('type','')} {it.get('control','')}".lower()
        if any(k in blob for k in KEYWORDS):
            print(line(it))

    print("\n=== ALL ITEMS ===")
    for it in sorted(items, key=lambda x: str(x.get('type', ''))):
        print(line(it))

    # NOTE (2026-05-31): a deeper per-item API probe (detail/commands/bindings/
    # variables for the access-relevant items 87/40/42/39/92/93) was run from
    # here during the door-code-keypad-reject investigation. It proved the
    # Director REST API exposes NO add-access-code command (DS3 commands =
    # Restart/Send-Snapshot only; Access agent 87 = empty; lock 93 = LOCK/
    # UNLOCK/TOGGLE only). Findings recorded in
    # cricket/home-assistant/DIAG_DOOR_CODE_KEYPAD_REJECT_2026-05-31.md.
    # Probe code removed afterwards (96KB output); recover from git history
    # (commit 7149309) if it needs re-running.


asyncio.run(main())
