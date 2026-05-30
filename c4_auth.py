#!/usr/bin/env python3
"""
Control4 director token — single source of truth for both
c4_manage_codes.py (door-code programming) and c4_entry_log.py (entry logging).

WHY THIS EXISTS
---------------
Control4 director bearer tokens are short-lived JWTs (~24h). The previous code
only re-authenticated when the cache FILE was missing — never when the cached
token had EXPIRED. So ~24h after each fresh auth the whole Control4 chain
silently died: c4_entry_log.py crashed every minute (exit 1) and the next
booking's c4_set_code failed, leaving the customer locked out.

This module decodes the JWT `exp` claim and re-authenticates proactively when
the token is missing, unreadable, expired, or within EXPIRY_SKEW_SECS of expiry.
Standard library only for the decode (no PyJWT dependency).
"""
import base64
import json
import time

CONFIG_ENTRIES = '/config/.storage/core.config_entries'
TOKEN_CACHE = '/config/c4_token_cache.txt'
# Re-auth this many seconds before the JWT's exp (covers clock skew + call latency)
EXPIRY_SKEW_SECS = 300


def _load_c4_config():
    """Return the Control4 config-entry data dict (host, username, password, ...)."""
    with open(CONFIG_ENTRIES) as f:
        config = json.load(f)
    c4 = next(e for e in config['data']['entries'] if e['domain'] == 'control4')
    return c4['data']


def _token_expired(token):
    """True if the JWT is missing, unparseable, expired, or near expiry."""
    if not token:
        return True
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += '=' * (-len(payload_b64) % 4)  # restore base64 padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = int(payload.get('exp', 0))
    except Exception:
        return True  # can't read expiry → treat as expired and re-auth
    return time.time() >= (exp - EXPIRY_SKEW_SECS)


async def _fresh_token(data):
    """Authenticate against the Control4 cloud and cache a new director token."""
    import aiohttp
    from pyControl4.account import C4Account
    async with aiohttp.ClientSession() as cloud:
        account = C4Account(data['username'], data['password'], cloud)
        await account.get_account_bearer_token()
        result = await account.get_director_bearer_token(data['controller_unique_id'])
    token = result['token']
    with open(TOKEN_CACHE, 'w') as f:
        f.write(token)
    return token


async def get_valid_token_and_host(force_refresh=False):
    """
    Return (host, token), re-authenticating if the cached token is missing,
    expired, near expiry, or force_refresh=True (use after a 401/failure).
    """
    data = _load_c4_config()
    host = data['host']

    token = None
    if not force_refresh:
        try:
            with open(TOKEN_CACHE) as f:
                token = f.read().strip()
        except FileNotFoundError:
            token = None

    if force_refresh or _token_expired(token):
        token = await _fresh_token(data)

    return host, token
