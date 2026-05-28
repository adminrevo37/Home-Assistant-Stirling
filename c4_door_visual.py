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

ROI (tuned from live test 2026-05-29 06:19):
  x=55%, y=10%, w=17%, h=32% of image  (targets the roller door face)

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
  │ Daytime, lights OFF, OPEN       │ 106.1 │   TBD    │  TBD   │ TBD    │
  │ Daytime, lights OFF, CLOSED     │  TBD  │   TBD    │  TBD   │ TBD    │
  └─────────────────────────────────┴───────┴──────────┴────────┴────────┘

  Key findings (night scenarios):
  • Door-open Δ is consistently ~+31 regardless of lighting — robust signal
  • Lights ON/OFF shifts baseline by ~14 (96.4↔82.6) — BELOW threshold of 20
    → No false "open" trigger when lights switch while door is closed ✅
  • Adaptive EMA baseline adjusts to lighting changes within ~5 min
  • OPEN_THRESHOLD = 20 confirmed correct for all tested scenarios

  Daytime + lights OFF findings (2026-05-29):
  • Baseline drifted due to lights-off transition falling near grey zone.
  • Root cause: door OPEN avg ≈ 106 was close enough to old baseline (82.6)
    that some reads fell under threshold → baseline crept up incorrectly.
  • Fix: consecutive-reads guard (BASELINE_ADAPT_AFTER = 3) prevents
    isolated or transitional "closed" misreads from corrupting the baseline.
  • Full daytime calibration pending (door-closed reading not yet captured).
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
    x, y = int(W * 0.55), int(H * 0.10)
    w, h = int(W * 0.17), int(H * 0.32)

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
