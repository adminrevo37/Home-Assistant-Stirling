#!/usr/bin/env python3
"""
Roller Door Visual State Sensor  (adaptive baseline edition v2)
===============================================================
Uses PIL (Pillow) to measure average luma in the door ROI.

Output:  prints "open" or "closed" to stdout (no trailing newline)
         diagnostic line written to stderr
Exit:    always 0  (errors default to "closed" for safety)

Call flow (driven by HA automation every 15 seconds):
  1. camera.snapshot  →  /config/www/door_check_latest.jpg
  2. shell_command.c4_door_visual_check  →  runs this script
  3. automation reads response_variable.stdout → sets input_boolean.roller_door_visual_open

ROI (tuned from live test 2026-05-29):
  x=52%, y=29%, w=15%, h=8% of image  (targets the black floor mat at door threshold)

Adaptive baseline design
────────────────────────
Because ambient lighting changes (skylights, high-bay lights, night vs day),
a fixed baseline triggers false positives.  This version keeps a rolling
baseline that adapts only while the door is CLOSED:

  • Status = OPEN   if abs(avg − baseline) > OPEN_THRESHOLD
  • Status = CLOSED otherwise
  • When CLOSED (for ≥ BASELINE_ADAPT_AFTER consecutive reads):
                  baseline = (1-ALPHA)*baseline + ALPHA*avg   (slow EMA)
  • When OPEN (or CLOSED but not yet stable):  baseline is frozen
  • Baseline persists across restarts in STATE_FILE.

The consecutive-reads guard prevents a lighting transition that happens to
land just under the threshold from corrupting the baseline.  The door must
read CLOSED for at least BASELINE_ADAPT_AFTER consecutive polls (~45 s at
15 s poll interval) before the EMA starts moving.

Calibration (2026-05-29):
  ┌─────────────────────────────────┬───────┬──────────┬────────┬────────┐
  │ Scenario                        │  avg  │ baseline │   Δ    │ result │
  ├─────────────────────────────────┼───────┼──────────┼────────┼────────┤
  │ Night, lights OFF, door CLOSED  │  96.4 │   96.4   │   0    │ closed │
  │ Night, lights OFF, door OPEN    │ 127.4 │   96.4   │ +31.0  │ open   │
  │ Day/Night, lights ON, CLOSED    │  82.6 │   82.6   │   0    │ closed │
  │ Day/Night, lights ON, OPEN      │ 114.0 │   82.6   │ +31.4  │ open   │
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ OLD ROI (door face x=55%,y=10%,w=17%,h=32%) — superseded               │
  │ Night, lights OFF, CLOSED  │  96.4 │  96.4 │   0   │ closed            │
  │ Night, lights OFF, OPEN    │ 127.4 │  96.4 │ +31.0 │ open              │
  │ Night, lights ON,  CLOSED  │  82.6 │  82.6 │   0   │ closed            │
  │ Night, lights ON,  OPEN    │ 114.0 │  82.6 │ +31.4 │ open              │
  │ Day,   lights OFF, OPEN    │ 106.1 │  82.6 │ +23.5 │ open (borderline) │
  │ Day,   lights OFF, CLOSED  │ 110.1 │  96.4 │ +13.7 │ WRONG — inverted  │
  │ → ROI moved to floor mat: door face reflected daylight causing inversion │
  └──────────────────────────────────────────────────────────────────────────┘

  NEW ROI calibration (floor mat x=52%,y=29%,w=15%,h=8%) — 2026-05-29:
  ┌─────────────────────────────────┬───────┬──────────┬────────┬────────┐
  │ Scenario                        │  avg  │ baseline │   Δ    │ result │
  ├─────────────────────────────────┼───────┼──────────┼────────┼────────┤
  │ Day, lights ON, CLOSED          │  99.8 │   99.8   │   0    │ closed │
  │ Day, lights ON, OPEN            │  TBD  │   TBD    │  TBD   │ TBD    │
  │ Day, lights OFF, CLOSED         │  TBD  │   TBD    │  TBD   │ TBD    │
  │ Day, lights OFF, OPEN           │  TBD  │   TBD    │  TBD   │ TBD    │
  │ Night, lights OFF, CLOSED       │  TBD  │   TBD    │  TBD   │ TBD    │
  │ Night, lights OFF, OPEN         │  TBD  │   TBD    │  TBD   │ TBD    │
  └─────────────────────────────────┴───────┴──────────┴────────┴────────┘

  Key finding (new ROI):
  • Floor mat is black → low luma when door closed (indoor light only)
  • When door opens, daylight or exterior light hits mat → avg increases
  • Signal should be consistently POSITIVE delta, all lighting conditions
"""

import sys
import json
import os

SNAPSHOT        = "/config/www/door_check_latest.jpg"
STATE_FILE      = "/config/c4_door_visual_state.json"

# Tuning parameters
CLOSED_BASELINE_DEFAULT = 99.8   # initial value if state file doesn't exist (day/lights-on closed reading)
OPEN_THRESHOLD          = 20.0   # abs(delta) > this → OPEN
ALPHA                   = 0.05   # EMA weight for baseline adaptation (~20 cycles to 64%)
BASELINE_ADAPT_AFTER    = 3      # consecutive "closed" reads required before EMA moves


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        return (
            float(s.get("baseline", CLOSED_BASELINE_DEFAULT)),
            s.get("last_status", "closed"),
            int(s.get("consecutive_closed", 0)),
        )
    except Exception:
        return CLOSED_BASELINE_DEFAULT, "closed", 0


def save_state(baseline, status, consecutive_closed):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(
                {
                    "baseline": round(baseline, 2),
                    "last_status": status,
                    "consecutive_closed": consecutive_closed,
                },
                f,
            )
    except Exception:
        pass


try:
    from PIL import Image

    baseline, last_status, consecutive_closed = load_state()

    img  = Image.open(SNAPSHOT).convert("L")   # grayscale
    W, H = img.size
    x, y = int(W * 0.52), int(H * 0.29)   # floor mat at door threshold
    w, h = int(W * 0.15), int(H * 0.08)

    crop   = img.crop((x, y, x + w, y + h))
    # tobytes() returns raw pixel bytes for 'L' mode — each byte = one pixel value
    raw    = crop.tobytes()
    avg    = sum(raw) / len(raw)
    delta  = avg - baseline
    status = "open" if abs(delta) > OPEN_THRESHOLD else "closed"

    # Track consecutive closed reads for stable baseline adaptation
    new_consecutive = (consecutive_closed + 1) if status == "closed" else 0

    # Adapt baseline only after BASELINE_ADAPT_AFTER consecutive closed reads;
    # freeze when door is open OR when closed count hasn't yet reached threshold.
    new_baseline = baseline
    if status == "closed" and new_consecutive >= BASELINE_ADAPT_AFTER:
        new_baseline = (1.0 - ALPHA) * baseline + ALPHA * avg

    save_state(new_baseline, status, new_consecutive)

    print(
        f"img={W}x{H} roi=({x},{y},{w},{h}) avg={avg:.1f} "
        f"baseline={baseline:.1f} delta={delta:+.1f} => {status}  "
        f"(new_baseline={new_baseline:.1f} consec_closed={new_consecutive})",
        file=sys.stderr,
    )
    print(status, end="")
    sys.exit(0)

except Exception as e:
    print(f"c4_door_visual error: {e}", file=sys.stderr)
    print("closed", end="")   # fail-safe: never falsely trigger auto-close
    sys.exit(0)
