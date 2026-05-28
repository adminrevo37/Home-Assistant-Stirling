#!/usr/bin/env python3
"""
Roller Door Visual State Sensor  (adaptive baseline edition)
============================================================
Uses PIL (Pillow) to measure average luma in the door ROI.

Output:  prints "open" or "closed" to stdout (no trailing newline)
         diagnostic line written to stderr
Exit:    always 0  (errors default to "closed" for safety)

Call flow (driven by HA automation every 15 seconds):
  1. camera.snapshot  →  /config/www/door_check_latest.jpg
  2. shell_command.c4_door_visual_check  →  runs this script
  3. automation reads response_variable.stdout → sets input_boolean.roller_door_visual_open

ROI (tuned from live test 2026-05-29 06:19):
  x=55%, y=10%, w=17%, h=32% of image  (targets the roller door face)

Adaptive baseline design
────────────────────────
Because ambient lighting changes (skylights, high-bay lights, night vs day),
a fixed baseline triggers false positives.  This version keeps a rolling
baseline that adapts only while the door is CLOSED:

  • Status = OPEN   if abs(avg − baseline) > OPEN_THRESHOLD
  • Status = CLOSED otherwise
  • When CLOSED:  baseline = (1-ALPHA)*baseline + ALPHA*avg   (slow EMA)
  • When OPEN:    baseline is frozen  (outdoor brightness can't corrupt it)
  • Baseline persists across restarts in STATE_FILE.

Calibration (initial night-vision values, 2026-05-29):
  Closed (B&W IR)  : avg ≈ 96.4   (door slats visible)
  Open (dawn)      : avg ≈ 127.4  Δ = +31 (exterior brick visible)
  OPEN_THRESHOLD   : 20  — tuned after multi-scenario testing below

Scenarios requiring calibration (to be measured and noted here):
  □ Daytime, indoor lights OFF  (skylights above door)
  □ Night,   indoor lights ON   (high-bays illuminate door face from inside)
  □ Night,   indoor lights OFF  ← current calibration above

  NOTE: night/lights-ON is expected to have a NEGATIVE delta when door opens
  (interior bright → exterior dark).  abs(delta) handles both directions.
  Run a test in each scenario and check the stderr diagnostic output to
  confirm the baseline and delta are sensible before relying on this sensor.
"""

import sys
import json
import os

SNAPSHOT        = "/config/www/door_check_latest.jpg"
STATE_FILE      = "/config/c4_door_visual_state.json"

# Tuning parameters
CLOSED_BASELINE_DEFAULT = 96.4   # initial value if state file doesn't exist
OPEN_THRESHOLD          = 20.0   # abs(delta) > this → OPEN
ALPHA                   = 0.05   # EMA weight for baseline adaptation (~20 cycles to 64%)


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        return float(s.get("baseline", CLOSED_BASELINE_DEFAULT)), s.get("last_status", "closed")
    except Exception:
        return CLOSED_BASELINE_DEFAULT, "closed"


def save_state(baseline, status):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"baseline": round(baseline, 2), "last_status": status}, f)
    except Exception:
        pass


try:
    from PIL import Image

    baseline, last_status = load_state()

    img  = Image.open(SNAPSHOT).convert("L")   # grayscale
    W, H = img.size
    x, y = int(W * 0.55), int(H * 0.10)
    w, h = int(W * 0.17), int(H * 0.32)

    crop   = img.crop((x, y, x + w, y + h))
    # tobytes() returns raw pixel bytes for 'L' mode — each byte = one pixel value
    raw    = crop.tobytes()
    avg    = sum(raw) / len(raw)
    delta  = avg - baseline
    status = "open" if abs(delta) > OPEN_THRESHOLD else "closed"

    # Adapt baseline only while door is closed; freeze when open
    new_baseline = baseline
    if status == "closed":
        new_baseline = (1.0 - ALPHA) * baseline + ALPHA * avg

    save_state(new_baseline, status)

    print(
        f"img={W}x{H} roi=({x},{y},{w},{h}) avg={avg:.1f} "
        f"baseline={baseline:.1f} delta={delta:+.1f} => {status}  "
        f"(new_baseline={new_baseline:.1f})",
        file=sys.stderr,
    )
    print(status, end="")
    sys.exit(0)

except Exception as e:
    print(f"c4_door_visual error: {e}", file=sys.stderr)
    print("closed", end="")   # fail-safe: never falsely trigger auto-close
    sys.exit(0)
