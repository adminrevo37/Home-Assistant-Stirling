#!/usr/bin/env python3
"""
Control4 Director diagnostic - finds DS3 intercom and checks door code variables.
Run from HA terminal:
    docker exec homeassistant python3 /config/c4_diagnostic.py
"""
import asyncio
import json
import sys

async def main():
    # ----------------------------------------------------------------
    # Step 1: Load Control4 credentials from HA config entries
    # ----------------------------------------------------------------
    try:
        with open('/config/.storage/core.config_entries', 'r') as f:
            config = json.load(f)
    except FileNotFoundError:
        print("ERROR: Cannot find /config/.storage/core.config_entries")
        return

    c4_entry = None
    for entry in config['data']['entries']:
        if entry['domain'] == 'control4':
            c4_entry = entry
            break

    if not c4_entry:
        print("ERROR: Control4 integration not found in HA config entries")
        return

    host     = c4_entry['data'].get('host')
    username = c4_entry['data'].get('username')
    password = c4_entry['data'].get('password')

    if not all([host, username, password]):
        print("ERROR: Missing host/username/password in Control4 config entry")
        print(f"Keys found: {list(c4_entry['data'].keys())}")
        return

    print(f"Director host : {host}")
    print(f"Account user  : {username}")
    print()

    # ----------------------------------------------------------------
    # Step 2: Authenticate with Control4 cloud to get Director token
    # ----------------------------------------------------------------
    try:
        import aiohttp
        from pyControl4.account import C4Account
        from pyControl4.director import C4Director
    except ImportError as e:
        print(f"ERROR: {e}")
        print("pyControl4 is not available in this Python environment.")
        print("Run this script inside the HA container:")
        print("  docker exec homeassistant python3 /config/c4_diagnostic.py")
        return

    print("Authenticating with Control4 cloud...")
    try:
        async with aiohttp.ClientSession() as session:
            account = C4Account(username, password, session)
            await account.getSessionToken()
            token_dict = await account.getDirectorBearerToken(host)
            token = token_dict['token']
            print("Authentication OK")
            print()

            director = C4Director(host, token, session)

            # ----------------------------------------------------------------
            # Step 3: List all items - look for DS3 / intercom / keypad
            # ----------------------------------------------------------------
            print("Fetching all Director items...")
            all_items = await director.getAllItemInfo()
            print(f"Total items found: {len(all_items)}")
            print()

            # Keywords that suggest a door/intercom/keypad device
            keywords = ['ds3', 'ds2', 'intercom', 'doorbell', 'keypad',
                        'door', 'entry', 'access', 'pier', 'front', 'lock']

            matched = []
            for item in all_items:
                name  = str(item.get('name',  '')).lower()
                type_ = str(item.get('type',  '')).lower()
                if any(k in name or k in type_ for k in keywords):
                    matched.append(item)

            if not matched:
                print("No door/intercom items matched by keyword.")
                print("Printing ALL items so you can identify the DS3 manually:")
                for item in all_items:
                    print(f"  ID:{item.get('id'):>5}  Type:{item.get('type',''):<30}  "
                          f"Name:{item.get('name',''):<30}  Room:{item.get('roomName','')}")
            else:
                print(f"Found {len(matched)} potentially relevant item(s):\n")
                for item in matched:
                    iid   = item.get('id')
                    iname = item.get('name')
                    itype = item.get('type')
                    iroom = item.get('roomName')
                    print(f"  ID:{iid}  |  {iname}  |  {itype}  |  {iroom}")
                    try:
                        variables = await director.getItemVariables(iid)
                        if variables:
                            print("  Variables:")
                            for v in variables:
                                print(f"    {v.get('varName',''):<35} = {v.get('value','')}")
                        else:
                            print("  Variables: (none)")
                    except Exception as ve:
                        print(f"  Variables: ERROR - {ve}")
                    print()

            # ----------------------------------------------------------------
            # Step 4: Check the Front Door Lock and Office Door Lock item IDs
            # ----------------------------------------------------------------
            print("=== Lock relay items ===")
            lock_keywords = ['lock', 'relay', 'spare']
            for item in all_items:
                name = str(item.get('name', '')).lower()
                if any(k in name for k in lock_keywords):
                    iid = item.get('id')
                    print(f"  ID:{iid}  |  {item.get('name')}  |  {item.get('type')}  |  {item.get('roomName')}")
                    try:
                        variables = await director.getItemVariables(iid)
                        if variables:
                            for v in variables:
                                print(f"    {v.get('varName',''):<35} = {v.get('value','')}")
                    except Exception:
                        pass
                    print()

    except Exception as e:
        print(f"ERROR during Director query: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
