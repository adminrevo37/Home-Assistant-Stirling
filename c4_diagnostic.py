#!/usr/bin/env python3
"""
Control4 Director diagnostic - finds DS3 intercom and checks door code variables.
Trigger from HA: Developer Tools -> Actions -> shell_command.c4_diagnostic
Read output:     cat /config/c4_output.txt
"""
import asyncio
import json

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

    host                 = c4_entry['data'].get('host')
    username             = c4_entry['data'].get('username')
    password             = c4_entry['data'].get('password')
    controller_unique_id = c4_entry['data'].get('controller_unique_id')

    print(f"Director host          : {host}")
    print(f"Controller unique ID   : {controller_unique_id}")
    print()

    # ----------------------------------------------------------------
    # Step 2: Authenticate and get Director bearer token
    # ----------------------------------------------------------------
    try:
        import aiohttp
        from pyControl4.account import C4Account
        from pyControl4.director import C4Director
    except ImportError as e:
        print(f"ERROR: {e}")
        return

    print("Authenticating with Control4 cloud...")
    try:
        async with aiohttp.ClientSession() as session:
            account = C4Account(username, password, session)
            await account.get_account_bearer_token()

            token_dict = await account.get_director_bearer_token(controller_unique_id)
            token = token_dict['token']
            print("Authentication OK")
            print()

            director = C4Director(host, token, session)

            # ----------------------------------------------------------------
            # Step 3: Get all items - search for DS3 / intercom / keypad
            # ----------------------------------------------------------------
            print("Fetching all Director items...")
            all_items = await director.get_all_item_info()
            print(f"Total items: {len(all_items)}")
            print()

            keywords = ['ds3', 'ds2', 'intercom', 'doorbell', 'keypad',
                        'door', 'entry', 'access', 'pier', 'lock', 'relay', 'spare']

            matched = []
            for item in all_items:
                name  = str(item.get('name',  '')).lower()
                type_ = str(item.get('type',  '')).lower()
                if any(k in name or k in type_ for k in keywords):
                    matched.append(item)

            if not matched:
                print("No door/intercom items matched. Printing ALL items:")
                for item in all_items:
                    print(f"  ID:{item.get('id'):>5}  "
                          f"Type:{str(item.get('type','')):<30}  "
                          f"Name:{str(item.get('name','')):<30}  "
                          f"Room:{item.get('roomName','')}")
            else:
                print(f"Found {len(matched)} relevant item(s):\n")
                for item in matched:
                    iid   = item.get('id')
                    iname = item.get('name')
                    itype = item.get('type')
                    iroom = item.get('roomName')
                    print(f"=== {iname} ===")
                    print(f"  ID   : {iid}")
                    print(f"  Type : {itype}")
                    print(f"  Room : {iroom}")

                    # Variables
                    try:
                        variables = await director.get_item_variables(iid)
                        if variables:
                            print("  Variables:")
                            for v in variables:
                                print(f"    {str(v.get('varName','')):<40} = {v.get('value','')}")
                        else:
                            print("  Variables: (none)")
                    except Exception as ve:
                        print(f"  Variables error: {ve}")

                    # Commands (best-effort - method name may vary)
                    for cmd_method in ['get_item_commands', 'getItemCommands']:
                        m = getattr(director, cmd_method, None)
                        if m:
                            try:
                                commands = await m(iid)
                                if commands:
                                    print("  Commands:")
                                    for c in commands:
                                        print(f"    {c}")
                            except Exception:
                                pass
                            break

                    print()

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
