#!/usr/bin/env python3
"""Stack the two front-door camera snapshots into ONE image for iOS push
notifications (iOS renders only a single attachment per notification).

Inputs  (written by camera.snapshot in the stuck-at-keypad alert):
  /config/www/keypad_alert_intercom.jpg   (front_intercom - face level)
  /config/www/keypad_alert_outside.jpg    (outside_entry CCTV - wide)
Output:
  /config/www/keypad_alert_snapshot.jpg   (vertical stack, equal width)

Called by shell_command.combine_door_snapshots. Degrades gracefully: if one
input is missing/corrupt the other is used alone; if both are missing the
output is left untouched (the notification then shows the previous image
rather than nothing). Pillow ships in the HA core image.
"""
from PIL import Image
import os

WWW = "/config/www"
INPUTS = [
    os.path.join(WWW, "keypad_alert_intercom.jpg"),
    os.path.join(WWW, "keypad_alert_outside.jpg"),
]
OUT = os.path.join(WWW, "keypad_alert_snapshot.jpg")
TARGET_W = 1280

imgs = []
for p in INPUTS:
    try:
        im = Image.open(p)
        im.load()
        if im.width != TARGET_W:
            im = im.resize((TARGET_W, int(im.height * TARGET_W / im.width)))
        imgs.append(im.convert("RGB"))
    except Exception as e:  # missing/corrupt input -> skip it
        print(f"skip {p}: {e}")

if not imgs:
    print("no inputs - output untouched")
    raise SystemExit(0)

if len(imgs) == 1:
    imgs[0].save(OUT, "JPEG", quality=80)
else:
    combined = Image.new("RGB", (TARGET_W, imgs[0].height + imgs[1].height))
    combined.paste(imgs[0], (0, 0))
    combined.paste(imgs[1], (0, imgs[0].height))
    combined.save(OUT, "JPEG", quality=80)
print(f"wrote {OUT}")
