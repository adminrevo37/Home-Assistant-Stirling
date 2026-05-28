#!/usr/bin/env python3
"""
Control4 Director - Phase 3 diagnostic.
Tests SET_USER_CODE with NAME param, DELETE commands, slot probing.
Trigger: shell_command.c4_diagnostic3
Output:  /config/www/c4_output3.txt
"""
import asyncio, json, aiohttp

async def main():
    with open('/config/.storage/core.config_entries') as f:
        config = json.load(f)
    c4 = next(e for e in config['data']['entries'] if e['domain'] == 'control4')
    host = c4['data']['host']
    
    with open('/config/c4_token_cache.txt') as f:
        token = f.read().strip()
    print(f"Director: {host}  Token cached: OK\n")

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as s:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        base = f"https://{host}"
        url = f"{base}/api/v1/items/39/commands"

        async def send(cmd, params):
            payload = {"command": cmd, "params": params, "async": False}
            async with s.post(url, headers=headers, json=payload,
                              timeout=aiohttp.ClientTimeout(total=8)) as r:
                body = await r.text()
                print(f"  {cmd} {params}")
                print(f"    status={r.status}  body={body[:200]}\n")

        # 1. SET with NAME param (slot 11, Bay 1 test)
        print("=== TEST 1: SET_USER_CODE with NAME ===")
        await send("SET_USER_CODE", {"CODE_ID": 11, "CODE": "972707", "NAME": "Athlete Julian"})

        # 2. Verify by reading variables
        print("=== TEST 2: Read DS3 variables after set ===")
        async with s.get(f"{base}/api/v1/items/39/variables", headers=headers,
                         timeout=aiohttp.ClientTimeout(total=5)) as r:
            body = await r.json(content_type=None)
            for v in body:
                print(f"  {v.get('varName',''):<40} = {v.get('value','')}")
        print()

        # 3. Try delete commands
        print("=== TEST 3: DELETE command variants ===")
        for cmd in ["DELETE_USER_CODE", "REMOVE_USER_CODE", "CLEAR_USER_CODE"]:
            await send(cmd, {"CODE_ID": 11})

        # 4. SET with empty code to clear
        print("=== TEST 4: SET with empty CODE to clear slot ===")
        await send("SET_USER_CODE", {"CODE_ID": 11, "CODE": "", "NAME": ""})

        # 5. Probe GET commands list for any code-reading commands
        print("=== TEST 5: All available commands on DS3 item 39 ===")
        async with s.get(f"{base}/api/v1/items/39/commands", headers=headers,
                         timeout=aiohttp.ClientTimeout(total=5)) as r:
            body = await r.json(content_type=None)
            for c in body:
                print(f"  {c}")
        print()

        # 6. Try slot 0 (test boundary)
        print("=== TEST 6: Slot boundary test ===")
        await send("SET_USER_CODE", {"CODE_ID": 0, "CODE": "000000", "NAME": "test"})
        await send("SET_USER_CODE", {"CODE_ID": 100, "CODE": "100100", "NAME": "test"})
        await send("SET_USER_CODE", {"CODE_ID": 250, "CODE": "250250", "NAME": "test"})

        # 7. Re-set slot 11 cleanly for real booking
        print("=== TEST 7: Re-set slot 11 to real booking code ===")
        await send("SET_USER_CODE", {"CODE_ID": 11, "CODE": "972707", "NAME": "Athlete Julian"})

    print("=== Done ===")

asyncio.run(main())
