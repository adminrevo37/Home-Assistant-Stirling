#!/usr/bin/env python3
"""
entry_webhook.py  -- Cricket Revolution / Stirling
Push a door-keypad ENTRY EVENT from Home Assistant to the Krickora (Convex) backend.

Design (see cricket/SPEC_DOOR_ENTRY_LOG_WEBHOOK.md):
  - The raw door code NEVER leaves this box. We send only a *keyed* hash:
        code_hash = HMAC-SHA256(CODE_HMAC_KEY, raw_code)
    HMAC (not a plain hash) because a 4-6 digit code is trivially brute-forced
    from a plain SHA-256. The key makes it un-reversible; it stays deterministic
    so Krickora can later match code_hash -> booking by hashing its stored codes
    with the SAME key.
  - Transport is HTTPS (TLS) to *.convex.site.
  - The request body is signed: X-HA-Signature = HMAC-SHA256(SIGN_KEY, body).
    Convex verifies it so only this box can write entry events.
  - v1 payload carries NO PII (no name/email) -- just {ts, bay, code_hash, result}.
    Krickora derives the customer from the hash match later.
  - This script MUST NEVER block the door flow: any error -> log to stderr, exit 0.

Usage (from shell_command.entry_webhook):
    python3 /config/entry_webhook.py "<raw_code_digits>" "<valid|invalid>" "<bay_or_blank>"

Secrets/config: read from /config/.entry_webhook_keys.json (gitignored, NOT in repo),
or overridden by env vars. JSON shape:
    { "code_hmac_key": "<64-hex>", "sign_key": "<64-hex>",
      "url": "https://artful-boar-748.convex.site/ha/entry" }
Generate the two keys once (256-bit each):
    python3 -c "import secrets; print(secrets.token_hex(32))"
and set the SAME values on the Convex side (env ENTRY_CODE_HMAC_KEY / ENTRY_SIGN_KEY).
"""
import sys
import os
import json
import time
import hmac
import hashlib
import urllib.request

KEYS_FILE = "/config/.entry_webhook_keys.json"


def load_config():
    cfg = {}
    try:
        with open(KEYS_FILE, "r") as f:
            cfg = json.load(f)
    except Exception:
        pass
    return {
        "code_hmac_key": os.environ.get("ENTRY_CODE_HMAC_KEY", cfg.get("code_hmac_key", "")),
        "sign_key": os.environ.get("ENTRY_SIGN_KEY", cfg.get("sign_key", "")),
        "url": os.environ.get("ENTRY_WEBHOOK_URL", cfg.get("url", "")),
    }


def main():
    raw_code = sys.argv[1] if len(sys.argv) > 1 else ""
    result = sys.argv[2] if len(sys.argv) > 2 else ""
    bay = sys.argv[3] if len(sys.argv) > 3 else ""

    cfg = load_config()
    if not cfg["url"] or not cfg["sign_key"] or not cfg["code_hmac_key"]:
        print("entry_webhook: keys/url not configured -- skipping (inert)", file=sys.stderr)
        sys.exit(0)

    code_hmac_key = cfg["code_hmac_key"].encode()
    sign_key = cfg["sign_key"].encode()

    code_hash = ""
    if raw_code:
        code_hash = hmac.new(code_hmac_key, raw_code.encode(), hashlib.sha256).hexdigest()

    payload = {
        "ts": int(time.time()),
        "bay": bay,
        "code_hash": code_hash,
        "result": result if result in ("valid", "invalid") else "unknown",
        "source": "keypad",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(sign_key, body, hashlib.sha256).hexdigest()

    req = urllib.request.Request(cfg["url"], data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-HA-Signature", "sha256=" + sig)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            print("entry_webhook: %s" % r.status)
    except Exception as e:
        # Never block the door flow on a logging failure.
        print("entry_webhook error: %s" % e, file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
