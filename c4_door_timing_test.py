#!/usr/bin/env python3
"""
Roller Door Timing Test  (c4_door_timing_test.py)
==================================================
Measures exact time for the roller door to open and close using the
pier-facing camera — same ROI + luma logic as c4_door_visual.py.

Usage (run on the HA server over SSH):

  # Measure OPEN time (script sends UNLOCK, times until visual detects open):
  python3 /config/c4_door_timing_test.py --open

  # Measure CLOSE time (script sends LOCK, times until visual detects closed):
  python3 /config/c4_door_timing_test.py --close

  # Just watch — no door command sent (manual open/close):
  python3 /config/c4_door_timing_test.py --watch

  # Override RTSP URL if auto-detection fails:
  python3 /config/c4_door_timing_test.py --open --rtsp "rtsp://user:pass@ip:554/stream"

  # Override baseline luma if state file is stale:
  python3 /config/c4_door_timing_test.py --watch --baseline 89.3

Output:
  Live luma per frame, transition markers, and a timing summary.
  Use Ctrl+C to stop early.

Notes:
  - At 2 fps the timing resolution is ±0.5 s. This is fine for setting
    the auto-close delay parameters (which are in 5 s increments).
  - The door command is sent AFTER the camera feed is confirmed open,
    so T0 is accurate.
  - Run 3× and average for reliable numbers.

Auto-close parameters to set based on measurements:
  door_close_attempt1_secs  = round_up(OPEN_time) + 30–45 s safety margin
  door_close_attempt2_secs  = 15–20 s (≥ CLOSE_time + 5 s buffer)
  door_close_alert_secs     = 15–20 s
"""

import sys
import io
import json
import time
import argparse
import subprocess
from pathlib import Path

# ── ROI constants (must match c4_door_visual.py) ──────────────────────────────
ROI_X_PCT       = 0.52
ROI_Y_PCT       = 0.29
ROI_W_PCT       = 0.15
ROI_H_PCT       = 0.08
OPEN_THRESHOLD  = 20.0   # abs(avg − baseline) > this → OPEN

# ── HA server paths ────────────────────────────────────────────────────────────
STATE_FILE          = "/config/c4_door_visual_state.json"
ENTITY_REGISTRY     = "/config/.storage/core.entity_registry"
CONFIG_ENTRIES      = "/config/.storage/core.config_entries"
TOKEN_CACHE         = "/config/c4_token_cache.txt"
C4_HOST             = "https://192.168.1.112"
C4_DOOR_ITEM        = "93"   # DS2 Lock Front

# ── Lazy PIL import ────────────────────────────────────────────────────────────
try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow not installed.  pip3 install Pillow", file=sys.stderr)
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# Helper: load adaptive baseline from state file
# ──────────────────────────────────────────────────────────────────────────────
def load_baseline() -> float:
    try:
        with open(STATE_FILE) as f:
            return float(json.load(f).get("baseline", 99.8))
    except Exception:
        return 99.8


# ──────────────────────────────────────────────────────────────────────────────
# Helper: auto-detect RTSP URL for camera.pier_facing_entry
# ──────────────────────────────────────────────────────────────────────────────
def get_rtsp_url() -> str | None:
    """Parse HA storage to find the RTSP stream URL for pier_facing_entry."""
    try:
        # Step 1: entity registry → find config_entry_id for pier_facing_entry
        with open(ENTITY_REGISTRY) as f:
            er = json.load(f)
        pier_entry_id = None
        for ent in er.get("data", {}).get("entities", []):
            if ent.get("entity_id") == "camera.pier_facing_entry":
                pier_entry_id = ent.get("config_entry_id")
                break

        if not pier_entry_id:
            # Fallback: check unique_id or platform
            for ent in er.get("data", {}).get("entities", []):
                if ent.get("entity_id", "").startswith("camera.") and "pier" in ent.get("entity_id", "").lower():
                    pier_entry_id = ent.get("config_entry_id")
                    break

        if not pier_entry_id:
            print("[auto-detect] entity camera.pier_facing_entry not found in registry", file=sys.stderr)
            return None

        # Step 2: config entries → find RTSP URL for that entry_id
        with open(CONFIG_ENTRIES) as f:
            ce = json.load(f)
        for entry in ce.get("data", {}).get("entries", []):
            if entry.get("entry_id") == pier_entry_id:
                opts = entry.get("options", {})
                data = entry.get("data", {})
                url  = opts.get("stream_source") or data.get("stream_source", "")
                if url:
                    return url
                print(f"[auto-detect] Found entry but no stream_source.  keys={list(opts.keys())}", file=sys.stderr)
                return None

        print(f"[auto-detect] config entry {pier_entry_id} not found", file=sys.stderr)
    except Exception as e:
        print(f"[auto-detect] {e}", file=sys.stderr)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Helper: send door command via Control4 API
# ──────────────────────────────────────────────────────────────────────────────
def send_door_command(command: str) -> bool:
    """Send LOCK or UNLOCK to Control4 DS2 item 93.  Returns True on success."""
    try:
        token = Path(TOKEN_CACHE).read_text().strip()
        payload = json.dumps({"command": command.upper(), "async": False})
        url = f"{C4_HOST}/api/v1/items/{C4_DOOR_ITEM}/commands"
        result = subprocess.run(
            ["curl", "-sk", "-X", "POST", url,
             "-H", f"Authorization: Bearer {token}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=10
        )
        resp = json.loads(result.stdout)
        ok   = resp.get("result") == 1
        if not ok:
            print(f"[door-cmd] {command} response: {result.stdout}", file=sys.stderr)
        return ok
    except Exception as e:
        print(f"[door-cmd] ERROR: {e}", file=sys.stderr)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Helper: read JPEG frames from an ffmpeg MJPEG pipe
# ──────────────────────────────────────────────────────────────────────────────
def read_jpeg_frames(proc):
    """Generator: yield JPEG bytes from ffmpeg stdout."""
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


# ──────────────────────────────────────────────────────────────────────────────
# Helper: compute average luma in door ROI
# ──────────────────────────────────────────────────────────────────────────────
def roi_luma(jpeg_bytes: bytes) -> float:
    img  = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    W, H = img.size
    x    = int(W * ROI_X_PCT)
    y    = int(H * ROI_Y_PCT)
    w    = int(W * ROI_W_PCT)
    h    = int(H * ROI_H_PCT)
    raw  = img.crop((x, y, x + w, y + h)).tobytes()
    return sum(raw) / len(raw)


# ──────────────────────────────────────────────────────────────────────────────
# Main: start ffmpeg, watch for transitions, report timing
# ──────────────────────────────────────────────────────────────────────────────
def run_timing_test(rtsp_url: str, baseline: float, fps: float,
                    max_seconds: int, door_command: str | None):
    """
    door_command: 'UNLOCK' (measure open time), 'LOCK' (measure close time),
                  or None (watch only).
    """
    print(f"  Baseline : {baseline:.1f}")
    print(f"  Threshold: ±{OPEN_THRESHOLD}")
    print(f"  FPS      : {fps}")
    if door_command:
        print(f"  Command  : {door_command} (sent at T+0)")
    print()

    ffmpeg_cmd = [
        "ffmpeg",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-vf", f"fps={fps}",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-"
    ]

    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    # Wait for first frame to confirm stream is live before sending command
    first_frame = None
    frame_iter  = read_jpeg_frames(proc)
    try:
        first_frame = next(frame_iter)
    except StopIteration:
        print("ERROR: no frames received — check RTSP URL", file=sys.stderr)
        proc.terminate()
        return

    print(f"{'  Time':>8}  {'Avg':>6}  {'Delta':>7}  Status")
    print("  " + "-" * 34)

    # T0 = when we send the command (or when we start watching)
    t0 = time.monotonic()
    if door_command:
        print(f"  {'T0':>6}           Sending {door_command}...")
        ok = send_door_command(door_command)
        if not ok:
            print("  WARNING: door command may have failed — continuing anyway")
        t0 = time.monotonic()   # reset after command sent

    transitions = []   # list of (t_rel, new_status)
    last_status  = None
    n_frames     = 0

    def process_frame(jpeg: bytes):
        nonlocal last_status, n_frames
        n_frames += 1
        t_rel  = time.monotonic() - t0
        avg    = roi_luma(jpeg)
        delta  = avg - baseline
        status = "OPEN" if abs(delta) > OPEN_THRESHOLD else "CLOSED"

        marker = ""
        if status != last_status:
            transitions.append((t_rel, status))
            marker = " ◄◄◄"
        last_status = status

        print(f"  {t_rel:7.1f}s  {avg:6.1f}  {delta:+7.1f}  {status}{marker}")
        return t_rel

    try:
        # Process the pre-fetched first frame
        t_rel = process_frame(first_frame)

        for jpeg in frame_iter:
            t_rel = process_frame(jpeg)
            if t_rel >= max_seconds:
                break

    except KeyboardInterrupt:
        print("\n  (stopped by user)")
    finally:
        proc.terminate()
        proc.wait()

    # ── Summary ────────────────────────────────────────────────────────────────
    print()
    print("  " + "=" * 36)
    print("  TIMING SUMMARY")
    print("  " + "=" * 36)

    if not transitions:
        print("  No transitions detected — door may not have moved")
        return

    for i, (t, st) in enumerate(transitions):
        if i == 0 and door_command:
            print(f"  T+{t:5.1f}s → {st}  (door command → visual detection lag)")
        elif i == 0:
            print(f"  T+{t:5.1f}s → {st}")
        else:
            prev_t = transitions[i-1][0]
            print(f"  T+{t:5.1f}s → {st}  ({t - prev_t:.1f}s after previous)")

    print()

    # Key metric: time from T0 to first transition
    t_first, st_first = transitions[0]
    if door_command == "UNLOCK" and st_first == "OPEN":
        print(f"  ✅ Door OPEN detected {t_first:.1f}s after UNLOCK command")
        print()
        print(f"  Suggested parameter values:")
        headroom = 30   # safety margin
        print(f"    door_close_attempt1_secs = {int(t_first) + headroom}  "
              f"({t_first:.0f}s open + {headroom}s safety margin)")
    elif door_command == "LOCK" and st_first == "CLOSED":
        print(f"  ✅ Door CLOSED detected {t_first:.1f}s after LOCK command")
        print()
        print(f"  Suggested parameter values:")
        buffer = 8
        print(f"    door_close_attempt2_secs  ≥ {int(t_first) + buffer}  "
              f"({t_first:.0f}s close time + {buffer}s buffer)")
        print(f"    door_close_alert_secs     = 15–20")
    else:
        print(f"  First transition ({st_first}) at T+{t_first:.1f}s")

    print()
    print(f"  Frames analysed: {n_frames}")
    print(f"  Frame rate:      {n_frames / max(t_rel, 1):.1f} fps (actual)")


# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Roller door open/close timing test using camera luma",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--open",  action="store_true", help="Send UNLOCK, measure open time")
    mode.add_argument("--close", action="store_true", help="Send LOCK,   measure close time")
    mode.add_argument("--watch", action="store_true", help="Watch only — no door command")

    parser.add_argument("--rtsp",     help="RTSP URL (auto-detected if omitted)")
    parser.add_argument("--fps",      type=float, default=2.0,
                        help="Frames per second to analyse (default 2)")
    parser.add_argument("--duration", type=int,   default=60,
                        help="Max seconds to record (default 60)")
    parser.add_argument("--baseline", type=float,
                        help="Override baseline luma (loaded from state file if omitted)")

    args = parser.parse_args()

    print()
    print("Roller Door Timing Test")
    print("─" * 40)

    # RTSP URL
    rtsp_url = args.rtsp
    if not rtsp_url:
        print("  Auto-detecting camera RTSP URL...", end=" ", flush=True)
        rtsp_url = get_rtsp_url()
        if rtsp_url:
            print("OK")
        else:
            print("FAILED")
            print("  Pass --rtsp \"rtsp://user:pass@ip:port/path\"", file=sys.stderr)
            sys.exit(1)

    # Baseline
    baseline = args.baseline
    if baseline is None:
        baseline = load_baseline()
        print(f"  Loaded baseline from state file: {baseline:.1f}")

    # Mode
    if args.open:
        command = "UNLOCK"
    elif args.close:
        command = "LOCK"
    else:
        command = None

    print()
    run_timing_test(
        rtsp_url=rtsp_url,
        baseline=baseline,
        fps=args.fps,
        max_seconds=args.duration,
        door_command=command,
    )


if __name__ == "__main__":
    main()
