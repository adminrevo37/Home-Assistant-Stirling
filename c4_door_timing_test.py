#!/usr/bin/env python3
"""
Roller Door Timing Test  ─  multi-cycle edition
================================================
Fully automated: opens the door, measures open time, closes it, measures
close time, repeats N times.  Produces live luma trace + ASCII charts +
statistics table + HA parameter recommendations.

Frame source: Frigate HTTP API  (camera.pier_facing_entry)
  http://ccab4aaf-frigate:5000/api/pier_facing_entry/latest.jpg

Usage (run on HA server via SSH):

  # Full automated 3-cycle test (default):
  python3 /config/c4_door_timing_test.py

  # Override number of runs:
  python3 /config/c4_door_timing_test.py --runs 5

  # Watch-only — no door commands, you open/close manually:
  python3 /config/c4_door_timing_test.py --watch

  # Override Frigate URL if detection fails:
  python3 /config/c4_door_timing_test.py --frigate "http://host:5000/api/camera_name/latest.jpg"

  # Save raw timing JSON:
  python3 /config/c4_door_timing_test.py --save /config/www/door_timing.json

Output:
  • Live delta bar per frame  (width = 0 → 2× threshold)
  • ASCII luma chart per phase with transition marker
  • Summary table: min / mean / max / SD for open and close times
  • Per-run breakdown table
  • Recommended door_close_attempt1_secs / 2 / alert_secs values
"""

import sys
import io
import json
import math
import time
import argparse
import subprocess
import statistics
import urllib.request
from pathlib import Path
from datetime import datetime

# ── ROI constants (must match c4_door_visual.py exactly) ─────────────────────
ROI_X_PCT       = 0.52
ROI_Y_PCT       = 0.29
ROI_W_PCT       = 0.15
ROI_H_PCT       = 0.08
OPEN_THRESHOLD  = 20.0   # abs(avg − baseline) > this → OPEN

# ── Default Frigate camera URL ────────────────────────────────────────────────
FRIGATE_BASE    = "http://ccab4aaf-frigate:5000"
FRIGATE_CAMERA  = "pier_facing_entry"
FRIGATE_URL     = f"{FRIGATE_BASE}/api/{FRIGATE_CAMERA}/latest.jpg"

# ── HA server paths ───────────────────────────────────────────────────────────
STATE_FILE      = "/config/c4_door_visual_state.json"
TOKEN_CACHE     = "/config/c4_token_cache.txt"
C4_HOST         = "https://192.168.1.112"
C4_DOOR_ITEM    = "93"

# ── Pillow ────────────────────────────────────────────────────────────────────
try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow not installed.  pip3 install Pillow", file=sys.stderr)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# I/O helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_baseline() -> float:
    try:
        with open(STATE_FILE) as f:
            return float(json.load(f).get("baseline", 99.8))
    except Exception:
        return 99.8


def send_door_command(command: str) -> bool:
    """Send LOCK or UNLOCK to Control4.  Returns True on success."""
    try:
        token = Path(TOKEN_CACHE).read_text().strip()
        payload = json.dumps({"command": command.upper(), "async": False})
        url = f"{C4_HOST}/api/v1/items/{C4_DOOR_ITEM}/commands"
        r = subprocess.run(
            ["curl", "-sk", "-X", "POST", url,
             "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=10
        )
        resp = json.loads(r.stdout)
        return resp.get("result") == 1
    except Exception as e:
        print(f"  [door-cmd] {e}", file=sys.stderr)
        return False


def fetch_frame(frigate_url: str, timeout: int = 3) -> bytes | None:
    """Fetch the latest JPEG frame from Frigate.  Returns None on error."""
    try:
        with urllib.request.urlopen(frigate_url, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print(f"  [frame] {e}", file=sys.stderr)
        return None


def roi_luma(jpeg_bytes: bytes) -> float:
    img  = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    W, H = img.size
    x, y = int(W * ROI_X_PCT), int(H * ROI_Y_PCT)
    w, h = int(W * ROI_W_PCT), int(H * ROI_H_PCT)
    return sum(img.crop((x, y, x+w, y+h)).tobytes()) / (w * h)


def status_from_delta(delta: float) -> str:
    return "OPEN" if abs(delta) > OPEN_THRESHOLD else "CLOSED"


# ─────────────────────────────────────────────────────────────────────────────
# Chart helpers
# ─────────────────────────────────────────────────────────────────────────────

BLOCKS = " ▁▂▃▄▅▆▇█"


def draw_luma_chart(samples: list[tuple], baseline: float,
                    phase: str, transition_t: float | None,
                    chart_width: int = 58) -> None:
    """
    Vertical ASCII luma chart.
    samples: list of (t_rel, avg, status)
    """
    if not samples:
        return

    times = [s[0] for s in samples]
    lumas = [s[1] for s in samples]
    t_min, t_max = times[0], max(times[-1], times[0] + 0.1)
    l_min = max(0,  min(lumas) - 10)
    l_max =         max(lumas) + 10
    rows  = 8
    cols  = min(chart_width, len(samples))

    step = max(1, len(samples) // cols)
    ds   = [samples[i] for i in range(0, len(samples), step)][:cols]
    ds_l = [s[1] for s in ds]

    threshold_high = baseline + OPEN_THRESHOLD

    print(f"\n  {'─'*62}")
    print(f"  Phase: {phase}")
    print(f"  Baseline={baseline:.1f}  Open threshold: Δ>{OPEN_THRESHOLD:.0f}")
    print(f"  {'─'*62}")

    for row in range(rows, -1, -1):
        luma_at_row = l_min + (l_max - l_min) * row / rows
        label = f"{luma_at_row:5.0f} ┤"
        bar_chars = []
        for l in ds_l:
            col_hi = l_min + (l_max - l_min) * (row + 1) / rows
            col_lo = l_min + (l_max - l_min) * row       / rows
            if l >= col_hi:
                bar_chars.append("█")
            elif l >= col_lo:
                frac = (l - col_lo) / max(col_hi - col_lo, 0.001)
                bar_chars.append(BLOCKS[int(frac * (len(BLOCKS)-1))])
            else:
                if abs(luma_at_row - baseline) < (l_max - l_min) / rows:
                    bar_chars.append("─")
                elif abs(luma_at_row - threshold_high) < (l_max - l_min) / rows:
                    bar_chars.append("·")
                else:
                    bar_chars.append(" ")
        print(f"  {label}{''.join(bar_chars)}")

    t_span_str = f"{t_min:.0f}s{'─'*max(0, cols-8)}{t_max:.0f}s"
    print(f"  {'':>7}└{'─'*cols}")
    print(f"  {'time':>7} {t_span_str[:cols]}")

    if transition_t is not None:
        t_span = t_max - t_min
        pos = int((transition_t - t_min) / t_span * cols) if t_span > 0 else 0
        pos = max(0, min(pos, cols - 1))
        print(f"  {'':>8}{'':>{pos}}↑")
        label = "OPEN" if "OPEN" in phase else "CLOSED"
        print(f"  {'':>8}{'':>{pos}}{label} detected  +{transition_t:.1f}s")
    print()


def draw_timeline_bar(label: str, duration: float, max_duration: float,
                      width: int = 40) -> None:
    filled = int(width * duration / max(max_duration, 0.1))
    filled = min(filled, width)
    bar    = "█" * filled + "░" * (width - filled)
    print(f"  {label:10s} │{bar}│ {duration:.1f}s")


def draw_summary_table(open_times: list[float], close_times: list[float]) -> None:
    n = len(open_times)

    def stats(vals):
        if not vals:
            return 0, 0, 0, 0
        mn, mx, me = min(vals), max(vals), statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0
        return mn, me, mx, sd

    o_min, o_mean, o_max, o_sd = stats(open_times)
    c_min, c_mean, c_max, c_sd = stats(close_times)

    W = 64
    print(f"\n  ┌{'─'*W}┐")
    print(f"  │{'DOOR TIMING SUMMARY  (' + str(n) + ' run' + ('s' if n!=1 else '') + ')':^{W}}│")
    print(f"  ├{'─'*12}┬{'─'*10}┬{'─'*10}┬{'─'*10}┬{'─'*19}┤")
    print(f"  │{'Metric':^12}│{'  Min':^10}│{' Mean':^10}│{'  Max':^10}│{'  Mean ± SD':^19}│")
    print(f"  ├{'─'*12}┼{'─'*10}┼{'─'*10}┼{'─'*10}┼{'─'*19}┤")
    print(f"  │{'Open time':^12}│{o_min:>8.1f}s │{o_mean:>8.1f}s │{o_max:>8.1f}s │"
          f"  {o_mean:.1f}s ± {o_sd:.1f}s{' ':^6}│")
    print(f"  │{'Close time':^12}│{c_min:>8.1f}s │{c_mean:>8.1f}s │{c_max:>8.1f}s │"
          f"  {c_mean:.1f}s ± {c_sd:.1f}s{' ':^6}│")
    print(f"  └{'─'*12}┴{'─'*10}┴{'─'*10}┴{'─'*10}┴{'─'*19}┘")

    max_t = max(o_max, c_max, 1.0)
    print()
    print(f"  {'─'*56}")
    print(f"  OPEN vs CLOSE  (bar = mean, each tick ≈ {max_t/40:.1f}s)")
    print(f"  {'─'*56}")
    draw_timeline_bar("Open  time", o_mean, max_t)
    draw_timeline_bar("Close time", c_mean, max_t)

    print()
    print(f"  {'─'*56}")
    print(f"  PER-RUN BREAKDOWN")
    print(f"  {'─'*56}")
    print(f"  {'Run':>5}  {'Open (s)':>9}  {'Close (s)':>10}  {'Δ open−close':>13}")
    print(f"  {'───':>5}  {'────────':>9}  {'─────────':>10}  {'────────────':>13}")
    for i, (o, c) in enumerate(zip(open_times, close_times), 1):
        print(f"  {i:>5}  {o:>9.2f}  {c:>10.2f}  {o-c:>+13.2f}")

    ENTRY_MARGIN = 40
    CLOSE_BUFFER = 8
    ALERT_SECS   = 20
    attempt1 = max(math.ceil(o_mean) + ENTRY_MARGIN, 10)
    attempt1 = (attempt1 + 4) // 5 * 5          # round up to nearest 5s
    attempt2 = max(math.ceil(c_mean) + CLOSE_BUFFER, 10)
    attempt2 = (attempt2 + 4) // 5 * 5

    print(f"\n  ┌{'─'*W}┐")
    print(f"  │{'RECOMMENDED HA PARAMETERS':^{W}}│")
    print(f"  ├{'─'*W}┤")
    print(f"  │  door_close_attempt1_secs  =  {attempt1:<5}"
          f"  [open≈{o_mean:.0f}s + {ENTRY_MARGIN}s customer entry time]"
          f"{'':<{W - 57}}│")
    print(f"  │  door_close_attempt2_secs  =  {attempt2:<5}"
          f"  [close≈{c_mean:.0f}s + {CLOSE_BUFFER}s buffer]"
          f"{'':<{W - 49}}│")
    print(f"  │  door_close_alert_secs     =  {ALERT_SECS:<5}"
          f"  [standard — adjust if needed]"
          f"{'':<{W - 51}}│")
    print(f"  │  Total window from open:   {attempt1+attempt2+ALERT_SECS}s"
          f"{'':<{W - 32}}│")
    print(f"  └{'─'*W}┘")
    print()
    print(f"  Set in HA:  Settings → Helpers → search 'door_close'")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Core: record one phase (OPEN or CLOSED) by polling Frigate
# ─────────────────────────────────────────────────────────────────────────────

def record_phase(frigate_url: str, baseline: float, target_status: str,
                 interval: float = 0.5, max_secs: int = 60
                 ) -> tuple[float | None, list]:
    """
    Poll Frigate at `interval` seconds until `target_status` is detected.
    Returns (transition_time_or_None, samples).
    samples: list of (t_rel, avg, status)
    """
    samples         = []
    transition_t    = None
    consecutive_tgt = 0       # require 2 back-to-back frames to confirm
    t0              = time.monotonic()

    while True:
        t_rel = time.monotonic() - t0

        jpeg = fetch_frame(frigate_url)
        if jpeg is None:
            time.sleep(interval)
            continue

        avg    = roi_luma(jpeg)
        delta  = avg - baseline
        status = status_from_delta(delta)
        samples.append((t_rel, avg, status))

        bar_w   = 30
        bar_pos = min(int(abs(delta) / (OPEN_THRESHOLD * 2) * bar_w), bar_w)
        bar     = "█" * bar_pos + "░" * (bar_w - bar_pos)
        marker  = " ◄◄◄" if status == target_status else ""
        print(f"  {t_rel:6.1f}s │{bar}│ avg={avg:5.1f}  Δ={delta:+5.1f}  {status}{marker}",
              flush=True)

        if status == target_status:
            consecutive_tgt += 1
            if consecutive_tgt >= 2 and transition_t is None:
                transition_t = samples[-2][0] if len(samples) >= 2 else t_rel
        else:
            consecutive_tgt = 0

        if transition_t is not None and t_rel > transition_t + 2.0:
            break
        if t_rel > max_secs:
            print(f"  ⚠️  Timeout — {target_status} not detected in {max_secs}s")
            break

        # Sleep for remainder of interval (accounting for fetch time)
        elapsed = time.monotonic() - t0 - t_rel
        sleep_t = max(0.0, interval - elapsed)
        if sleep_t > 0:
            time.sleep(sleep_t)

    return transition_t, samples


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Roller door multi-cycle timing test (Frigate HTTP source)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--runs",     type=int,   default=3,
                        help="Open+close cycles (default 3)")
    parser.add_argument("--fps",      type=float, default=2.0,
                        help="Frames per second — Frigate poll rate (default 2)")
    parser.add_argument("--watch",    action="store_true",
                        help="Watch only — no door commands")
    parser.add_argument("--frigate",  default=FRIGATE_URL,
                        help=f"Frigate snapshot URL (default: {FRIGATE_URL})")
    parser.add_argument("--baseline", type=float,
                        help="Override baseline luma (loads from state file if omitted)")
    parser.add_argument("--save",     help="Path to write raw timing JSON")
    parser.add_argument("--pause",    type=int, default=10,
                        help="Seconds between cycles (default 10)")
    args = parser.parse_args()

    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║          ROLLER DOOR TIMING TEST — multi-cycle          ║")
    print(f"  ║  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S'):^56}  ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print()

    # ── Verify Frigate is reachable ───────────────────────────────────────────
    print(f"  Checking Frigate feed ...", end=" ", flush=True)
    test = fetch_frame(args.frigate)
    if test:
        try:
            img = Image.open(io.BytesIO(test))
            print(f"OK  ({img.size[0]}×{img.size[1]} JPEG)")
        except Exception:
            print("OK (unreadable JPEG — PIL error)")
    else:
        print(f"FAILED\n  URL: {args.frigate}")
        sys.exit(1)

    # ── Baseline ──────────────────────────────────────────────────────────────
    baseline = args.baseline
    if baseline is None:
        baseline = load_baseline()
        print(f"  Baseline from state file : {baseline:.1f}")

    interval = 1.0 / args.fps
    print(f"  OPEN_THRESHOLD : ±{OPEN_THRESHOLD}")
    print(f"  Runs           : {args.runs}")
    print(f"  Poll interval  : {interval:.2f}s  ({args.fps} fps)")
    print(f"  Watch only     : {args.watch}")
    print()

    open_times  : list[float] = []
    close_times : list[float] = []
    all_raw     : list[dict]  = []

    try:
        for run in range(1, args.runs + 1):
            print()
            print(f"  {'═'*60}")
            print(f"  RUN {run} of {args.runs}")
            print(f"  {'═'*60}")

            # ── OPEN phase ────────────────────────────────────────────────────
            print()
            print(f"  ── OPEN phase ──────────────────────────────────────────")
            print(f"  {'time':>8}  {'delta bar (0 → 2× threshold)':^32}  reading")
            print(f"  {'─'*8}  {'─'*32}  {'─'*25}")

            if not args.watch:
                print(f"  {'T0':>8}  Sending UNLOCK ...")
                ok = send_door_command("UNLOCK")
                if not ok:
                    print("  WARNING: UNLOCK may have failed")
            else:
                print("  Waiting — open the door now ...")

            t_open, open_samples = record_phase(
                args.frigate, baseline, "OPEN",
                interval=interval, max_secs=45
            )

            if t_open is not None:
                open_times.append(t_open)
                print(f"\n  ✅ OPEN detected at T+{t_open:.2f}s")
                draw_luma_chart(open_samples, baseline,
                                f"Run {run} — OPEN phase", t_open)
            else:
                print(f"\n  ⚠️  OPEN not detected — skipping close for this run")
                all_raw.append({"run": run, "open_time": None, "close_time": None,
                                "open_samples": open_samples, "close_samples": []})
                if run < args.runs:
                    print(f"  Pausing {args.pause}s ...")
                    time.sleep(args.pause)
                continue

            # ── Wait for motor to fully stop (luma stabilisation) ────────────
            # Visual OPEN fires when luma first crosses threshold (~2–3 s),
            # but the motor keeps running for ~10–12 s total.  We poll until
            # the ROI luma stops changing before sending LOCK — otherwise the
            # motor ignores the command (hardware anti-reversal protection).
            _STAB_TOL     = 2.0   # luma units — change < this = "stable"
            _STAB_FRAMES  = 3     # consecutive stable frames required
            _STAB_TIMEOUT = 25    # max wait (s) before proceeding anyway
            _STAB_BUFFER  = 3     # extra seconds after luma is stable
            print(f"  Waiting for motor to stop (luma stabilisation ±{_STAB_TOL}) ...")
            _stable_count = 0
            _last_luma    = None
            _stab_t0      = time.monotonic()
            while True:
                _elapsed = time.monotonic() - _stab_t0
                if _elapsed > _STAB_TIMEOUT:
                    print(f"  ⚠️  Stabilisation timeout {_STAB_TIMEOUT}s — proceeding")
                    break
                _jpeg = fetch_frame(args.frigate)
                if _jpeg is None:
                    time.sleep(interval)
                    continue
                _luma = roi_luma(_jpeg)
                if _last_luma is not None:
                    _diff = abs(_luma - _last_luma)
                    print(f"  stab {_elapsed:5.1f}s  luma={_luma:.1f}  Δ={_diff:+.1f}"
                          f"  stable={_stable_count}", flush=True)
                    if _diff < _STAB_TOL:
                        _stable_count += 1
                        if _stable_count >= _STAB_FRAMES:
                            print(f"  ✅ Motor stopped — buffering {_STAB_BUFFER}s")
                            time.sleep(_STAB_BUFFER)
                            break
                    else:
                        _stable_count = 0
                else:
                    print(f"  stab {_elapsed:5.1f}s  luma={_luma:.1f}  (first sample)",
                          flush=True)
                _last_luma = _luma
                time.sleep(interval)

            # ── CLOSE phase ───────────────────────────────────────────────────
            print()
            print(f"  ── CLOSE phase ─────────────────────────────────────────")
            print(f"  {'time':>8}  {'delta bar (0 → 2× threshold)':^32}  reading")
            print(f"  {'─'*8}  {'─'*32}  {'─'*25}")

            if not args.watch:
                print(f"  {'T0':>8}  Sending LOCK ...")
                ok = send_door_command("LOCK")
                if not ok:
                    print("  WARNING: LOCK may have failed")
            else:
                print("  Waiting — close the door now ...")

            t_close, close_samples = record_phase(
                args.frigate, baseline, "CLOSED",
                interval=interval, max_secs=45
            )

            if t_close is not None:
                close_times.append(t_close)
                print(f"\n  ✅ CLOSED detected at T+{t_close:.2f}s")
                draw_luma_chart(close_samples, baseline,
                                f"Run {run} — CLOSE phase", t_close)
            else:
                print(f"\n  ⚠️  CLOSED not detected this run")

            all_raw.append({
                "run": run,
                "open_time":     t_open,
                "close_time":    t_close,
                "open_samples":  [(t, a, s) for t, a, s in open_samples],
                "close_samples": [(t, a, s) for t, a, s in (close_samples or [])],
            })

            if run < args.runs:
                print(f"  Pausing {args.pause}s before Run {run+1} ...")
                time.sleep(args.pause)

    except KeyboardInterrupt:
        print("\n\n  Stopped by user (Ctrl+C)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if open_times or close_times:
        draw_summary_table(open_times, close_times)
    else:
        print("  No timing data collected.")

    # ── Save raw JSON ─────────────────────────────────────────────────────────
    if args.save and all_raw:
        try:
            out = {
                "timestamp":    datetime.now().isoformat(),
                "baseline":     baseline,
                "open_threshold": OPEN_THRESHOLD,
                "fps":          args.fps,
                "runs":         all_raw,
                "open_times":   open_times,
                "close_times":  close_times,
                "open_mean":    statistics.mean(open_times)  if open_times  else None,
                "close_mean":   statistics.mean(close_times) if close_times else None,
            }
            Path(args.save).write_text(json.dumps(out, indent=2))
            print(f"  Raw data → {args.save}")
            print(f"  View at : http://192.168.1.5:8123/local/door_timing.json")
        except Exception as e:
            print(f"  Could not save: {e}")


if __name__ == "__main__":
    main()
