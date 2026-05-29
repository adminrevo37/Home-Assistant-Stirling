#!/usr/bin/env python3
"""
Roller Door Timing Test  ─  multi-cycle edition
================================================
Fully automated: opens the door, measures open time, closes it, measures
close time, repeats N times.  Produces live luma trace + ASCII charts +
statistics table + HA parameter recommendations.

Usage (run on HA server via SSH):

  # Full automated test — 3 open+close cycles:
  python3 /config/c4_door_timing_test.py

  # Override number of runs:
  python3 /config/c4_door_timing_test.py --runs 5

  # Watch-only (no door commands — you open/close manually):
  python3 /config/c4_door_timing_test.py --watch

  # Override RTSP URL if auto-detection fails:
  python3 /config/c4_door_timing_test.py --rtsp "rtsp://user:pass@ip:554/path"

  # Save raw timing data to JSON for later analysis:
  python3 /config/c4_door_timing_test.py --save /config/www/door_timing.json

Output:
  • Live luma readings with sparkline chart during each phase
  • ASCII timeline per cycle (UNLOCK → motor → OPEN, LOCK → motor → CLOSED)
  • Final summary table: min / mean / max for open and close times
  • Recommended values for door_close_attempt1_secs / 2 / alert_secs
"""

import sys
import io
import json
import math
import time
import argparse
import subprocess
import statistics
from pathlib import Path
from datetime import datetime

# ── ROI constants (must match c4_door_visual.py exactly) ─────────────────────
ROI_X_PCT       = 0.52
ROI_Y_PCT       = 0.29
ROI_W_PCT       = 0.15
ROI_H_PCT       = 0.08
OPEN_THRESHOLD  = 20.0   # abs(avg − baseline) > this → OPEN

# ── HA server paths ───────────────────────────────────────────────────────────
STATE_FILE      = "/config/c4_door_visual_state.json"
ENTITY_REGISTRY = "/config/.storage/core.entity_registry"
CONFIG_ENTRIES  = "/config/.storage/core.config_entries"
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


def get_rtsp_url() -> str | None:
    """Auto-detect RTSP URL for camera.pier_facing_entry from HA storage."""
    try:
        with open(ENTITY_REGISTRY) as f:
            er = json.load(f)
        pier_entry_id = None
        for ent in er.get("data", {}).get("entities", []):
            if ent.get("entity_id") == "camera.pier_facing_entry":
                pier_entry_id = ent.get("config_entry_id")
                break
        if not pier_entry_id:
            for ent in er.get("data", {}).get("entities", []):
                if "pier" in ent.get("entity_id", "").lower() and \
                   ent.get("entity_id", "").startswith("camera."):
                    pier_entry_id = ent.get("config_entry_id")
                    break
        if not pier_entry_id:
            return None
        with open(CONFIG_ENTRIES) as f:
            ce = json.load(f)
        for entry in ce.get("data", {}).get("entries", []):
            if entry.get("entry_id") == pier_entry_id:
                opts = entry.get("options", {})
                data = entry.get("data", {})
                return opts.get("stream_source") or data.get("stream_source")
    except Exception as e:
        print(f"  [auto-detect] {e}", file=sys.stderr)
    return None


def send_door_command(command: str) -> bool:
    """Send LOCK or UNLOCK.  Returns True on success."""
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


def read_jpeg_frames(proc):
    """Generator: yield raw JPEG bytes from ffmpeg pipe."""
    buf = b""
    while True:
        chunk = proc.stdout.read(8192)
        if not chunk:
            break
        buf += chunk
        while True:
            soi = buf.find(b"\xff\xd8")
            if soi < 0:
                break
            eoi = buf.find(b"\xff\xd9", soi + 2)
            if eoi < 0:
                break
            yield buf[soi : eoi + 2]
            buf = buf[eoi + 2 :]


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

def sparkline(values: list[float], width: int = 50) -> str:
    """Return a sparkline string for a list of luma values."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = max(hi - lo, 1.0)
    chars = []
    # Downsample if needed
    step = max(1, len(values) // width)
    sampled = [values[i] for i in range(0, len(values), step)][:width]
    for v in sampled:
        idx = int((v - lo) / span * (len(BLOCKS) - 1))
        chars.append(BLOCKS[idx])
    return "".join(chars)


def draw_luma_chart(samples: list[tuple], baseline: float,
                    phase: str, transition_t: float | None,
                    chart_width: int = 60) -> None:
    """
    Draw a vertical luma chart.
    samples: list of (t_rel, avg, status)
    """
    if not samples:
        return

    times  = [s[0] for s in samples]
    lumas  = [s[1] for s in samples]
    t_min, t_max = times[0], max(times[-1], times[0] + 0.1)
    l_min  = max(0, min(lumas) - 10)
    l_max  = max(lumas) + 10
    rows   = 8
    cols   = min(chart_width, len(samples))

    # Downsample
    step    = max(1, len(samples) // cols)
    ds      = [samples[i] for i in range(0, len(samples), step)][:cols]
    ds_t    = [s[0] for s in ds]
    ds_l    = [s[1] for s in ds]

    threshold_high = baseline + OPEN_THRESHOLD
    threshold_low  = baseline - OPEN_THRESHOLD

    print(f"\n  {'─'*62}")
    print(f"  Phase: {phase}")
    print(f"  Baseline={baseline:.1f}  Open threshold: Δ>{OPEN_THRESHOLD:.0f}")
    print(f"  {'─'*62}")

    # Y axis labels and chart rows
    print(f"  {'luma':>5} ┤")
    for row in range(rows, -1, -1):
        luma_at_row = l_min + (l_max - l_min) * row / rows
        label = f"{luma_at_row:5.0f} ┤"
        bar_chars = []
        for l in ds_l:
            col_hi = l_min + (l_max - l_min) * (row + 1) / rows
            col_lo = l_min + (l_max - l_min) * row / rows
            if l >= col_hi:
                bar_chars.append("█")
            elif l >= col_lo:
                # Partial block
                frac = (l - col_lo) / (col_hi - col_lo)
                bar_chars.append(BLOCKS[int(frac * (len(BLOCKS)-1))])
            else:
                # Mark threshold lines
                if abs(luma_at_row - baseline) < (l_max - l_min) / rows:
                    bar_chars.append("─")   # baseline
                elif abs(luma_at_row - threshold_high) < (l_max - l_min) / rows:
                    bar_chars.append("·")   # open threshold
                else:
                    bar_chars.append(" ")
        print(f"  {label}{''.join(bar_chars)}")

    # X axis
    t_span_str = f"{t_min:.0f}s{'─'*max(0, cols-8)}{t_max:.0f}s"
    print(f"  {'':>7}└{'─'*cols}")
    print(f"  {'time':>7} {t_span_str[:cols]}")

    # Transition marker
    if transition_t is not None:
        # Find column position of transition
        t_span = t_max - t_min
        if t_span > 0:
            pos = int((transition_t - t_min) / t_span * cols)
            pos = max(0, min(pos, cols - 1))
            print(f"  {'':>8}{'':>{pos}}↑")
            if "OPEN" in phase:
                print(f"  {'':>8}{'':>{pos}}OPEN detected  +{transition_t:.1f}s")
            else:
                print(f"  {'':>8}{'':>{pos}}CLOSED detected  +{transition_t:.1f}s")
    print()


def draw_timeline_bar(label: str, duration: float, max_duration: float,
                      width: int = 40) -> None:
    """Draw a single horizontal bar: label |████░░░░| 12.3s"""
    filled = int(width * duration / max(max_duration, 0.1))
    filled = min(filled, width)
    empty  = width - filled
    bar    = "█" * filled + "░" * empty
    print(f"  {label:10s} │{bar}│ {duration:.1f}s")


def draw_summary_table(open_times: list[float], close_times: list[float]) -> None:
    """Print the statistics table and HA parameter recommendations."""
    n = len(open_times)

    def stats(vals):
        if not vals:
            return 0, 0, 0, 0
        return min(vals), statistics.mean(vals), max(vals), \
               (statistics.stdev(vals) if len(vals) > 1 else 0)

    o_min, o_mean, o_max, o_sd = stats(open_times)
    c_min, c_mean, c_max, c_sd = stats(close_times)

    W = 64
    sep = "─" * W

    print(f"\n  ┌{sep}┐")
    print(f"  │{'DOOR TIMING SUMMARY  (' + str(n) + ' run' + ('s' if n!=1 else '') + ')':^{W}}│")
    print(f"  ├{'─'*12}┬{'─'*10}┬{'─'*10}┬{'─'*10}┬{'─'*19}┤")
    print(f"  │{'Metric':^12}│{'  Min':^10}│{' Mean':^10}│{'  Max':^10}│{'  Mean ± SD':^19}│")
    print(f"  ├{'─'*12}┼{'─'*10}┼{'─'*10}┼{'─'*10}┼{'─'*19}┤")
    print(f"  │{'Open time':^12}│{o_min:>8.1f}s │{o_mean:>8.1f}s │{o_max:>8.1f}s │"
          f"  {o_mean:.1f}s ± {o_sd:.1f}s{' ':^6}│")
    print(f"  │{'Close time':^12}│{c_min:>8.1f}s │{c_mean:>8.1f}s │{c_max:>8.1f}s │"
          f"  {c_mean:.1f}s ± {c_sd:.1f}s{' ':^6}│")
    print(f"  └{'─'*12}┴{'─'*10}┴{'─'*10}┴{'─'*10}┴{'─'*19}┘")

    # Bar charts
    max_t = max(o_max, c_max, 1.0)
    print()
    print(f"  {'─'*56}")
    print(f"  OPEN  vs  CLOSE  (bar = mean time, each tick ≈ {max_t/40:.1f}s)")
    print(f"  {'─'*56}")
    draw_timeline_bar("Open  time", o_mean, max_t)
    draw_timeline_bar("Close time", c_mean, max_t)

    # Per-run breakdown
    print()
    print(f"  {'─'*56}")
    print(f"  PER-RUN BREAKDOWN")
    print(f"  {'─'*56}")
    print(f"  {'Run':>5}  {'Open (s)':>9}  {'Close (s)':>10}  {'Δ open-close':>13}")
    print(f"  {'───':>5}  {'────────':>9}  {'─────────':>10}  {'────────────':>13}")
    for i, (o, c) in enumerate(zip(open_times, close_times), 1):
        diff = o - c
        print(f"  {i:>5}  {o:>9.2f}  {c:>10.2f}  {diff:>+13.2f}")

    # Recommendations
    CUSTOMER_ENTRY_MARGIN = 40   # seconds of dwell time after door opens
    CLOSE_ATTEMPT_BUFFER  = 8    # seconds after close_time for attempt2
    ALERT_SECS            = 20

    attempt1 = math.ceil(o_mean) + CUSTOMER_ENTRY_MARGIN
    attempt1 = max(attempt1 // 5, 1) * 5   # round up to nearest 5s
    attempt2 = math.ceil(c_mean) + CLOSE_ATTEMPT_BUFFER
    attempt2 = max(attempt2 // 5, 1) * 5

    print(f"\n  ┌{sep}┐")
    print(f"  │{'RECOMMENDED HA PARAMETERS':^{W}}│")
    print(f"  ├{sep}┤")
    print(f"  │  door_close_attempt1_secs  =  {attempt1:<5}"
          f"  [open≈{o_mean:.0f}s + {CUSTOMER_ENTRY_MARGIN}s customer entry time]{'':<{W-62}}│")
    print(f"  │  door_close_attempt2_secs  =  {attempt2:<5}"
          f"  [close≈{c_mean:.0f}s + {CLOSE_ATTEMPT_BUFFER}s buffer]{'':<{W-56}}│")
    print(f"  │  door_close_alert_secs     =  {ALERT_SECS:<5}"
          f"  [standard — adjust if needed]{'':<{W-52}}│")
    print(f"  │{'':{W}}│")
    print(f"  │  Total auto-close window:  {attempt1+attempt2+ALERT_SECS}s after door opens{'':<{W-42}}│")
    print(f"  └{sep}┘")
    print()
    print(f"  Set these in HA:  Settings → Helpers → search 'door_close'")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Core: record one phase (open or close)
# ─────────────────────────────────────────────────────────────────────────────

def record_phase(frame_iter, baseline: float, target_status: str,
                 max_secs: int = 60) -> tuple[float | None, list]:
    """
    Read frames until target_status is detected.
    Returns (transition_time_or_None, samples)
    samples: list of (t_rel, avg, status)
    """
    samples         = []
    transition_t    = None
    last_status     = None
    t0              = time.monotonic()
    consecutive_tgt = 0          # require 2 consecutive frames to confirm

    for jpeg in frame_iter:
        t_rel  = time.monotonic() - t0
        avg    = roi_luma(jpeg)
        delta  = avg - baseline
        status = status_from_delta(delta)
        samples.append((t_rel, avg, status))

        # Sparkline-style live output
        bar_w   = 30
        bar_pos = min(int(abs(delta) / (OPEN_THRESHOLD * 2) * bar_w), bar_w)
        bar     = "█" * bar_pos + "░" * (bar_w - bar_pos)
        marker  = " ◄◄◄" if status == target_status else ""
        print(f"  {t_rel:6.1f}s │{bar}│ avg={avg:5.1f}  Δ={delta:+5.1f}  {status}{marker}",
              flush=True)

        # Require 2 back-to-back frames in target state to avoid glitches
        if status == target_status:
            consecutive_tgt += 1
            if consecutive_tgt >= 2 and transition_t is None:
                # First full confirmation — use time of FIRST of the 2 frames
                transition_t = samples[-2][0] if len(samples) >= 2 else t_rel
        else:
            consecutive_tgt = 0

        if transition_t is not None and t_rel > transition_t + 2.0:
            break   # a little extra recording after transition

        if t_rel > max_secs:
            print(f"  ⚠️  Timeout — {target_status} not detected in {max_secs}s")
            break

        last_status = status

    return transition_t, samples


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Roller door multi-cycle timing test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--runs",     type=int,   default=3,
                        help="Number of open+close cycles (default 3)")
    parser.add_argument("--fps",      type=float, default=2.0,
                        help="Frames per second for analysis (default 2)")
    parser.add_argument("--watch",    action="store_true",
                        help="Watch mode — no door commands, you trigger manually")
    parser.add_argument("--rtsp",     help="RTSP URL (auto-detected if omitted)")
    parser.add_argument("--baseline", type=float,
                        help="Override baseline luma (loaded from state file if omitted)")
    parser.add_argument("--save",     help="Path to save raw timing JSON (optional)")
    parser.add_argument("--pause",    type=int, default=10,
                        help="Seconds to pause between cycles (default 10)")
    args = parser.parse_args()

    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║          ROLLER DOOR TIMING TEST — multi-cycle          ║")
    print(f"  ║  {datetime.now().strftime('%Y-%m-%d  %H:%M:%S'):^56}  ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print()

    # ── RTSP URL ─────────────────────────────────────────────────────────────
    rtsp_url = args.rtsp
    if not rtsp_url:
        print("  Detecting camera URL ...", end=" ", flush=True)
        rtsp_url = get_rtsp_url()
        if rtsp_url:
            print("OK")
        else:
            print("FAILED\n  Pass --rtsp URL", file=sys.stderr)
            sys.exit(1)

    # ── Baseline ─────────────────────────────────────────────────────────────
    baseline = args.baseline
    if baseline is None:
        baseline = load_baseline()
        print(f"  Baseline loaded from state file: {baseline:.1f}")

    print(f"  OPEN_THRESHOLD : ±{OPEN_THRESHOLD}")
    print(f"  Runs           : {args.runs}")
    print(f"  Frame rate     : {args.fps} fps")
    print(f"  Watch only     : {args.watch}")
    print()

    # ── Start ffmpeg stream ───────────────────────────────────────────────────
    ffmpeg_cmd = [
        "ffmpeg", "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-vf", f"fps={args.fps}",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-"
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    frame_iter = read_jpeg_frames(proc)

    # Consume first frame to confirm stream is live
    try:
        first = next(frame_iter)
        avg0  = roi_luma(first)
        init_status = status_from_delta(avg0 - baseline)
        print(f"  Stream live  ✓   initial luma={avg0:.1f}  status={init_status}")
    except StopIteration:
        print("  ERROR: no frames from camera — check RTSP URL")
        proc.terminate()
        sys.exit(1)

    # ── Collect data ──────────────────────────────────────────────────────────
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
                    print(f"  WARNING: UNLOCK may have failed")
            else:
                print(f"  Waiting — open the door now ...")

            t_open, open_samples = record_phase(frame_iter, baseline,
                                                target_status="OPEN",
                                                max_secs=45)

            if t_open is not None:
                open_times.append(t_open)
                print(f"\n  ✅ OPEN detected at T+{t_open:.2f}s")
                draw_luma_chart(open_samples, baseline,
                                f"Run {run} — OPEN phase", t_open)
            else:
                print(f"\n  ⚠️  OPEN not detected — skipping close phase for this run")
                all_raw.append({"run": run, "open_time": None, "close_time": None,
                                "open_samples": open_samples, "close_samples": []})
                if run < args.runs:
                    print(f"  Pausing {args.pause}s before next run ...")
                    time.sleep(args.pause)
                continue

            # Brief settle after full open
            settle = 3
            print(f"  Settling {settle}s ...")
            time.sleep(settle)

            # ── CLOSE phase ───────────────────────────────────────────────────
            print()
            print(f"  ── CLOSE phase ─────────────────────────────────────────")
            print(f"  {'time':>8}  {'delta bar (0 → 2× threshold)':^32}  reading")
            print(f"  {'─'*8}  {'─'*32}  {'─'*25}")

            if not args.watch:
                print(f"  {'T0':>8}  Sending LOCK ...")
                ok = send_door_command("LOCK")
                if not ok:
                    print(f"  WARNING: LOCK may have failed")
            else:
                print(f"  Waiting — close the door now ...")

            t_close, close_samples = record_phase(frame_iter, baseline,
                                                  target_status="CLOSED",
                                                  max_secs=45)

            if t_close is not None:
                close_times.append(t_close)
                print(f"\n  ✅ CLOSED detected at T+{t_close:.2f}s")
                draw_luma_chart(close_samples, baseline,
                                f"Run {run} — CLOSE phase", t_close)
            else:
                print(f"\n  ⚠️  CLOSED not detected this run")

            all_raw.append({
                "run": run,
                "open_time":  t_open,
                "close_time": t_close,
                "open_samples":  [(t, a, s) for t, a, s in open_samples],
                "close_samples": [(t, a, s) for t, a, s in (close_samples or [])],
            })

            if run < args.runs:
                print(f"  Pausing {args.pause}s before Run {run+1} ...")
                time.sleep(args.pause)

    except KeyboardInterrupt:
        print("\n\n  Stopped by user (Ctrl+C)")

    finally:
        proc.terminate()
        proc.wait()

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if open_times or close_times:
        draw_summary_table(open_times, close_times)
    else:
        print("  No timing data collected.")

    # ── Save raw data ─────────────────────────────────────────────────────────
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
                "open_mean":    statistics.mean(open_times) if open_times else None,
                "close_mean":   statistics.mean(close_times) if close_times else None,
            }
            Path(args.save).write_text(json.dumps(out, indent=2))
            print(f"  Raw data saved to: {args.save}")
        except Exception as e:
            print(f"  Could not save: {e}")


if __name__ == "__main__":
    main()
