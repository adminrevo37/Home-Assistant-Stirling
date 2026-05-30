#!/usr/bin/env python3
"""
Control4 DS3 Door Code Manager
Runs inside HA container (has access to aiohttp + pyControl4).

Usage:
  python3 /config/c4_manage_codes.py set   <slot> <code> <name>
  python3 /config/c4_manage_codes.py clear <slot>

Slot assignments:
  Bay 1: slots 11, 12, 13
  Bay 2: slots 14, 15, 16
  Bay 3: slots 17, 18, 19
  Bay 4: slots 20, 21, 22
  Bay 5: slots 23, 24, 25
  Slots 1-10: reserved for permanent staff codes (never touched)

Token is read from /config/c4_token_cache.txt
"""
import asyncio
import json
import sys
import aiohttp
from datetime import datetime

import c4_auth  # shared, expiry-aware token management

DS3_ITEM_ID = 39
DIRECTOR_HOST = None  # loaded from config

# Token loading + expiry-aware refresh lives in c4_auth.get_valid_token_and_host().
# (Previously a local get_token_and_host() re-authed only on a MISSING cache file,
#  never on an EXPIRED token — see c4_auth.py for the full explanation.)


async def set_code(host, token, slot, code, name):
    """Program a user code into a DS3 slot."""
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"https://{host}/api/v1/items/{DS3_ITEM_ID}/commands"
        payload = {
            "command": "SET_USER_CODE",
            "params": {
                "CODE_ID": int(slot),
                "CODE": str(code),
                "NAME": str(name)
            },
            "async": False
        }
        async with s.post(url, headers=headers, json=payload,
                          timeout=aiohttp.ClientTimeout(total=10)) as r:
            body = await r.json(content_type=None)
            result = body.get('result', -1)
            print(f"SET slot={slot} code={code} name={name} => status={r.status} result={result}")
            return r.status == 200


async def clear_code(host, token, slot):
    """Remove a user code from a DS3 slot."""
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"https://{host}/api/v1/items/{DS3_ITEM_ID}/commands"

        # Try DELETE_USER_CODE first (preferred if supported)
        for cmd in ["DELETE_USER_CODE", "SET_USER_CODE"]:
            if cmd == "DELETE_USER_CODE":
                payload = {
                    "command": cmd,
                    "params": {"CODE_ID": int(slot)},
                    "async": False
                }
            else:
                # Fallback: overwrite with empty code
                payload = {
                    "command": cmd,
                    "params": {"CODE_ID": int(slot), "CODE": "", "NAME": ""},
                    "async": False
                }
            async with s.post(url, headers=headers, json=payload,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                body = await r.json(content_type=None)
                result = body.get('result', -1)
                print(f"CLEAR slot={slot} cmd={cmd} => status={r.status} result={result}")
                if r.status == 200:
                    return True
        return False


async def main():
    if len(sys.argv) < 3:
        print("Usage: c4_manage_codes.py set <slot> <code> <name>")
        print("       c4_manage_codes.py clear <slot>")
        sys.exit(1)

    action = sys.argv[1].lower()
    slot = sys.argv[2]

    host, token = await c4_auth.get_valid_token_and_host()

    if action == 'set':
        if len(sys.argv) < 5:
            print("Usage: c4_manage_codes.py set <slot> <code> <name>")
            sys.exit(1)
        code = sys.argv[3].replace(' ', '')  # strip spaces from code
        name = ' '.join(sys.argv[4:])        # name may have spaces
        success = await set_code(host, token, slot, code, name)
        if not success:
            # Token may have been revoked/invalidated server-side — force a
            # fresh token and retry once before giving up.
            host, token = await c4_auth.get_valid_token_and_host(force_refresh=True)
            success = await set_code(host, token, slot, code, name)
        sys.exit(0 if success else 1)

    elif action == 'clear':
        success = await clear_code(host, token, slot)
        if not success:
            host, token = await c4_auth.get_valid_token_and_host(force_refresh=True)
            success = await clear_code(host, token, slot)
        sys.exit(0 if success else 1)

    else:
        print(f"Unknown action: {action}")
        sys.exit(1)


asyncio.run(main())
