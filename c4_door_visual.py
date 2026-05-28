#!/usr/bin/env python3
"""
Roller Door Visual State Sensor
================================
Reads the latest camera snapshot and measures brightness in the door ROI
to determine whether the roller door is open or closed.

Output:  prints "open" or "closed" to stdout (no trailing newline)
         diagnostic line is written to stderr
Exit:    always 0  (errors default to "closed" for safety)

Call flow (driven by HA automation every 15 seconds):
  1. camera.snapshot  →  /config/www/door_check_latest.jpg
  2. shell_command.c4_door_visual_check  →  runs this script
  3. automation reads response_variable.stdout → sets input_boolean.roller_door_visual_open

ROI (tuned from live test 2026-05-29 06:19):
  x=55%, y=10%, w=17%, h=32% of image  (targets the roller door face)

Calibration:
  Closed baseline : avg ≈ 96.4  (B&W night-vision, door slats visible)
  Open (dawn)     : avg ≈ 127.4  Δ = +31  (colour mode, exterior brickwork visible)
  Threshold       : |avg − baseline| > 15  →  OPEN

The ±0.2 closed variance means false-trigger risk is negligible.
"""

import sys

SNAPSHOT        = "/config/www/door_check_latest.jpg"
CLOSED_BASELINE = 96.4
OPEN_THRESHOLD  = 15.0   # any deviation > this = door open

# ROI fractions  (tuned 2026-05-29)
ROI_X = 0.55
ROI_Y = 0.10
ROI_W = 0.17
ROI_H = 0.32

try:
    from PIL import Image

    img  = Image.open(SNAPSHOT).convert("L")   # grayscale
    W, H = img.size
    x, y = int(W * ROI_X), int(H * ROI_Y)
    w, h = int(W * ROI_W), int(H * ROI_H)

    crop   = img.crop((x, y, x + w, y + h))
    pixels = list(crop.getdata())
    avg    = sum(pixels) / len(pixels)
    delta  = avg - CLOSED_BASELINE
    status = "open" if abs(delta) > OPEN_THRESHOLD else "closed"

    print(f"img={W}x{H} roi=({x},{y},{w},{h}) avg={avg:.1f} delta={delta:+.1f} => {status}",
          file=sys.stderr)
    print(status, end="")
    sys.exit(0)

except Exception as e:
    print(f"c4_door_visual error: {e}", file=sys.stderr)
    print("closed", end="")   # fail-safe: never falsely trigger auto-close
    sys.exit(0)
