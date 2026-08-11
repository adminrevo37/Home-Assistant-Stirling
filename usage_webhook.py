#!/usr/bin/env python3
import sys, json, hmac, hashlib, base64, urllib.request, urllib.error
cfg = json.load(open("/config/.usage_webhook_keys.json"))
raw = base64.b64decode(sys.argv[1])
sig = hmac.new(cfg["sign_key"].encode(), raw, hashlib.sha256).hexdigest()
req = urllib.request.Request(cfg["url"], data=raw, method="POST",
    headers={"Content-Type": "application/json", "X-HA-Signature": "sha256=" + sig})
try:
    print(urllib.request.urlopen(req, timeout=10).status)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read(200))
