#!/usr/bin/env python3
"""
Control4 Director - Phase 2 diagnostic.
Queries agent endpoints and tries to find user/code management.
Trigger: Developer Tools -> Actions -> shell_command.c4_diagnostic2
Output:  cat /config/c4_output2.txt
"""
import asyncio
import json

async def main():
    # Load credentials
    with open('/config/.storage/core.config_entries', 'r') as f:
        config = json.load(f)

    c4_entry = None
    for entry in config['data']['entries']:
        if entry['domain'] == 'control4':
            c4_entry = entry
            break

    host                 = c4_entry['data']['host']
    username             = c4_entry['data']['username']
    password             = c4_entry['data']['password']
    controller_unique_id = c4_entry['data']['controller_unique_id']

    import aiohttp
    from pyControl4.account import C4Account
    from pyControl4.director import C4Director

    print(f"Director: {host}")
    print()

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession() as cloud_session, \
               aiohttp.ClientSession(connector=connector) as dir_session:

        # Try to reuse cached token from first diagnostic run
        token = None
        try:
            with open('/config/c4_token_cache.txt', 'r') as f:
                token = f.read().strip()
            print("Using cached Director token")
        except FileNotFoundError:
            pass

        if not token:
            print("Authenticating with Control4 cloud...")
            for attempt in range(3):
                try:
                    account = C4Account(username, password, cloud_session)
                    await account.get_account_bearer_token()
                    token = (await account.get_director_bearer_token(controller_unique_id))['token']
                    # Cache for next run
                    with open('/config/c4_token_cache.txt', 'w') as f:
                        f.write(token)
                    break
                except Exception as e:
                    print(f"  Auth attempt {attempt+1} failed: {e}")
                    if attempt < 2:
                        await asyncio.sleep(3)
                    else:
                        raise
        print("Auth OK")

        director = C4Director(host, token, dir_session)
        headers  = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        base     = f"https://{host}"

        # ----------------------------------------------------------------
        # 1. Probe known Director REST endpoints for user/code management
        # ----------------------------------------------------------------
        probe_paths = [
            "/api/v1/agents",
            "/api/v1/agents/access",
            "/api/v1/agents/access/users",
            "/api/v1/agents/access/codes",
            "/api/v1/agents/userManager",
            "/api/v1/agents/userManager/users",
            "/api/v1/users",
            "/api/v1/codes",
            "/api/v1/items/87",
            "/api/v1/items/87/variables",
            "/api/v1/items/87/commands",
            "/api/v1/items/39/variables",
            "/api/v1/items/39/commands",
            "/api/v1/items/93/variables",
            "/api/v1/items/93/commands",
        ]

        print("\n=== Probing Director REST endpoints ===\n")
        for path in probe_paths:
            url = base + path
            try:
                async with dir_session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    status = resp.status
                    try:
                        body = await resp.json(content_type=None)
                        body_str = json.dumps(body, indent=2)[:800]
                    except Exception:
                        body_str = (await resp.text())[:400]
                    print(f"GET {path}")
                    print(f"  Status : {status}")
                    print(f"  Body   : {body_str}")
                    print()
            except Exception as e:
                print(f"GET {path}")
                print(f"  ERROR  : {e}")
                print()

        # ----------------------------------------------------------------
        # 2. Try set_item_variable on DS3 (ID 39) with user code params
        # ----------------------------------------------------------------
        print("\n=== Testing set_item_variable on DS3 (ID 39) ===\n")
        test_vars = [
            ("USER_CODE_1", "1234"),
            ("USERCODE1", "1234"),
            ("Code1", "1234"),
            ("UserCode", "1234"),
        ]
        for var_name, val in test_vars:
            try:
                result = await director.set_item_variable(39, var_name, val)
                print(f"  set_item_variable({var_name}={val}) => {result}")
            except Exception as e:
                print(f"  set_item_variable({var_name}={val}) => ERROR: {e}")

        # ----------------------------------------------------------------
        # 3. Try sending commands directly via Director API
        # ----------------------------------------------------------------
        print("\n=== Testing commands on DS3 (ID 39) ===\n")
        test_commands = [
            ("SET_USER_CODE", {"CODE_ID": 10, "CODE": "9999"}),
            ("ADD_USER_CODE", {"CODE": "9999"}),
            ("SET_ACCESS_CODE", {"SLOT": 10, "CODE": "9999"}),
        ]
        set_cmd_url = f"{base}/api/v1/items/39/commands"
        for cmd, params in test_commands:
            payload = {"command": cmd, "params": params, "async": False}
            try:
                async with dir_session.post(
                    set_cmd_url, headers={**headers, "Content-Type": "application/json"},
                    json=payload, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    body = await resp.text()
                    print(f"  POST command={cmd}: status={resp.status} body={body[:300]}")
            except Exception as e:
                print(f"  POST command={cmd}: ERROR: {e}")

        print("\n=== Done ===")

asyncio.run(main())
