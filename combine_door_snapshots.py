#!/usr/bin/env python3
"""Stack the three front-door camera snapshots into ONE image for the iOS push
notification (iOS renders only a single attachment per notification).

The three individual snapshots ALSO stay on disk and are shown, unstacked, on
the Alert Cameras dashboard - so that page is a still record of exactly what
the cameras saw at the moment of the alert (only useful when an alert has
captured photos).

Inputs  (written by camera.snapshot in the stuck-at-keypad alert):
  /config/www/keypad_alert_intercom.jpg   (front_intercom - face at the door)
  /config/www/keypad_alert_outside.jpg     (outside_entry CCTV - wide street/approach)
  /config/www/keypad_alert_inside.jpg      (pier_facing_entry - inside the roller door)
Output:
  /config/www/keypad_alert_snapshot.jpg    (vertical stack, equal width - push attachment)

Called by shell_command.combine_door_snapshots. Degrades gracefully: any
missing/corrupt input is skipped; if all are missing the output is left
untouched. Pillow ships in the HA core image.
"""
from PIL import Image
import os

WWW = "/config/www"
INPUTS = [
    os.path.join(WWW, "keypad_alert_intercom.jpg"),
    os.path.join(WWW, "keypad_alert_outside.jpg"),
    os.path.join(WWW, "keypad_alert_inside.jpg"),
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
    total_h = sum(im.height for im in imgs)
    combined = Image.new("RGB", (TARGET_W, total_h))
    y = 0
    for im in imgs:
        combined.paste(im, (0, y))
        y += im.height
    combined.save(OUT, "JPEG", quality=80)
print(f"wrote {OUT} from {len(imgs)} input(s)")
