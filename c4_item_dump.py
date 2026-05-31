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

    # --- Access-agent / door-code API probe (read-only) ---------------------
    # On-site test 2026-05-31 ruled out the DS3 station (39) AND the lock driver
    # (93) as the code authority — a code accepted by SET_USER_CODE on either is
    # still rejected at the keypad, while a known staff code (managed in the
    # Control4 app) works. The keypad validates against the Access agent (87).
    # This dumps the API surface (detail/commands/bindings/variables) of the
    # access-relevant items so we can find the real add-access-code command.
    # GET-only — never writes a code or fires a command.
    PROBE_IDS = [87, 40, 42, 39, 92, 93]
    ENDPOINTS = ['', '/commands', '/bindings', '/variables']
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False)) as s2:
        for pid in PROBE_IDS:
            print(f"\n{'='*60}\n=== ITEM {pid} API PROBE ===\n{'='*60}")
            for ep in ENDPOINTS:
                url = f"https://{host}/api/v1/items/{pid}{ep}"
                print(f"\n--- GET /items/{pid}{ep or ' (detail)'} ---")
                try:
                    async with s2.get(url, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=8)) as r:
                        body = await r.json(content_type=None)
                    text = json.dumps(body, indent=2, default=str)
                    if len(text) > 4000:
                        text = text[:4000] + "\n  ...[truncated]"
                    print(f"  status={r.status}\n{text}")
                except Exception as e:
                    print(f"  <error: {e}>")


asyncio.run(main())
