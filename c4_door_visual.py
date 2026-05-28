#!/usr/bin/env python3
"""
Roller Door Visual State Sensor
================================
Uses ffprobe (built into HA) to measure average Y-luminance in the door ROI.
No PIL/Pillow dependency needed.

Output:  prints "open" or "closed" to stdout (no trailing newline)
         diagnostic line written to stderr
Exit:    always 0  (errors default to "closed" for safety)

Call flow (driven by HA automation every 15 seconds):
  1. camera.snapshot  →  /config/www/door_check_latest.jpg
  2. shell_command.c4_door_visual_check  →  runs this script
  3. automation reads response_variable.stdout → sets input_boolean.roller_door_visual_open

ROI (tuned from live test 2026-05-29 06:19):
  x=55%, y=10%, w=17%, h=32% of image  (targets the roller door face)

Calibration:
  Closed baseline : avg ≈ 96.4  (B&W night-vision, door slats visible)
  Open (dawn)     : avg ≈ 127.4  Δ = +31  (colour mode, exterior brick visible)
  Threshold       : abs(avg − baseline) > 15  →  OPEN

  Closed variance was ±0.2 across 25 frames — false trigger risk negligible.
"""

import sys
import subprocess
import re

SNAPSHOT        = "/config/www/door_check_latest.jpg"
CLOSED_BASELINE = 96.4
OPEN_THRESHOLD  = 15.0

# ROI as fractions of image dimensions (ffprobe crop filter: w:h:x:y)
ROI_X = 0.55
ROI_Y = 0.10
ROI_W = 0.17
ROI_H = 0.32


def get_brightness():
    """
    Crop the door ROI and return average Y (luma) via ffprobe signalstats.
    YAVG is 0-255 scale for full-range JPEG input.
    """
    crop = f"crop=iw*{ROI_W}:ih*{ROI_H}:iw*{ROI_X}:ih*{ROI_Y},signalstats"
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
        "-vf", crop,
        "-of", "default=noprint_wrappers=1",
        SNAPSHOT
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    m = re.search(r"YAVG=(\d+\.?\d*)", r.stdout)
    if not m:
        raise RuntimeError(
            f"ffprobe returned no YAVG\n"
            f"  stdout: {r.stdout!r}\n"
            f"  stderr: {r.stderr!r}"
        )
    return float(m.group(1))


try:
    avg    = get_brightness()
    delta  = avg - CLOSED_BASELINE
    status = "open" if abs(delta) > OPEN_THRESHOLD else "closed"
    print(f"avg={avg:.1f} delta={delta:+.1f} threshold={OPEN_THRESHOLD} => {status}",
          file=sys.stderr)
    print(status, end="")
    sys.exit(0)

except Exception as e:
    print(f"c4_door_visual error: {e}", file=sys.stderr)
    print("closed", end="")   # fail-safe: never falsely trigger auto-close
    sys.exit(0)
