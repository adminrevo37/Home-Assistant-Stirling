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

    # --- Door-code variable probe (read-only) -------------------------------
    # Dumps live variables for the items that could hold the DS3 keypad's
    # user-code table, to find WHERE codes must be written. Context: codes
    # pushed via SET_USER_CODE to the DS3 station (39) return result=1 but the
    # keypad rejects them. Candidates: Access agent (87), DS2 Lock proxy (93),
    # door-lock relay (94). GET-only — never writes a code.
    PROBE_IDS = [39, 87, 93, 94]
    async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False)) as s2:
        for pid in PROBE_IDS:
            print(f"\n=== ITEM {pid} VARIABLES ===")
            try:
                async with s2.get(
                        f"https://{host}/api/v1/items/{pid}/variables",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=aiohttp.ClientTimeout(total=8)) as r:
                    body = await r.json(content_type=None)
                if isinstance(body, list):
                    if not body:
                        print("  (no variables)")
                    for v in body:
                        print(f"  varName={v.get('varName')!r}  value={v.get('value')!r}")
                else:
                    print(f"  status={r.status} body={body}")
            except Exception as e:
                print(f"  <error: {e}>")


asyncio.run(main())
