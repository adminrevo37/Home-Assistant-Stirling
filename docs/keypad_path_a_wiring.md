# Customer Entry Keypad — Path A (wiring + bench notes)

**Status: BENCH-TESTING, NOT YET WORKING ON THE BOX (as of 2026-06-03).**

Path A = the keypad is a *dumb Wiegand reader*. It reads the typed PIN and fires
`esphome.keypad_code` to HA; **HA is the sole code authority** and unlocks the
roller door. The device never validates or opens anything itself. This is the
workaround for the Control4 dead-end (DS3 REST API rejects all API-written codes —
see the ⚠ warning in the Control4 section of `CLAUDE.md`).

- **Device config:** `esphome/cricket-keypad.yaml` (handles BOTH Wiegand modes —
  4-bit-per-key `on_key` and whole-PIN 26-bit `on_tag` — automatically).
- **HA validator:** `automations.yaml` → `keypad_validate_entry`
  (alias *Customer Keypad - Validate Entry & Open Door*). Matches the typed PIN
  against the active-booking `input_text.bay{N}_code_slot_{a/b/c}` (`CODE:Name`),
  unlocks `lock.front_door_lock` on a hit, logs the attempt either way.
- **Master build doc:** `../cricket/home-assistant/BUILD_KEYPAD_PATH_A_JAYCAR.md`
  (lives in the Claude folder, NOT cloned into this repo).
- **Wiring diagram:** `docs/keypad_path_a_wiring.svg` (open in a browser / print).

---

## Hardware

| Part | Notes |
|------|-------|
| Olimex **ESP32-POE-ISO** | Powered by **PoE only** — no Wi-Fi/BT (no wireless attack surface) |
| PoE switch / injector | Sole power for the Olimex, over the Ethernet cable |
| **12 V DC** PSU | Powers the keypad only |
| Jaycar **LA5353** (Sebury W1-C) | Wiegand output keypad |
| **BSS138 4-ch** logic level shifter | 5 V Wiegand → 3.3 V GPIO (2 channels used) |
| **12 V→5 V buck** (LM2596 / Mini-360) | Makes the shifter's HV (5 V) reference |

## Power & ground — the key idea

- **Keypad** runs off 12 V (Red = +12 V, Black + Pink = GND).
- **Olimex** runs off **PoE** — it draws NOTHING from the 12 V supply.
- **All grounds meet at ONE star point** tied to the 12 V negative: PSU(−),
  keypad Black/Pink, buck IN−/OUT−, shifter GND (both pins), Olimex GND. The
  Olimex GND joins purely as a shared **signal reference**, not for power.
- The "ISO" isolation on the Olimex is on the **Ethernet/PoE side** — bonding the
  board GND to the 12 V ground is correct and does not defeat it.

## Connection table

**Power**

| From | To |
|------|----|
| 12 V PSU **+** | Keypad **Red** |
| 12 V PSU **+** | Buck **IN+** |
| Buck **OUT+** (set **5.0 V**) | Shifter **HV** |
| Olimex **3V3** | Shifter **LV** |
| PoE switch/injector | Olimex **RJ45** (only power for the Olimex) |

**Data (Wiegand → shift → GPIO)**

| Keypad | Shifter HV | Shifter LV | Olimex |
|--------|-----------|-----------|--------|
| **Green = D0** | HV1 | LV1 | **GPIO4** |
| **White = D1** | HV2 | LV2 | **GPIO5** |

**Ground:** every GND above → single star point on 12 V (−).

## Build & test order

1. Wire **all grounds** to the star point first (flaky GND = garbage frames).
2. Set the buck to **exactly 5.0 V** (measure) *before* connecting it to HV.
3. Power the keypad; confirm **Red→Black ≈ 12 V**.
4. Wire the shifter (HV=5 V, LV=3.3 V, GNDs, the two data channels).
5. Probe **HV1/HV2 → GND**: should idle **~5 V** (only meter check worth doing).
6. Connect data to GPIO4/GPIO5 **last**, plug in Ethernet, watch the ESPHome
   `on_raw` log. Type a known code e.g. `482193#` to confirm + read the bit count.

## Gotchas / hard-won notes

- **ESP32 GPIOs are NOT 5 V tolerant** — BOTH D0 and D1 must pass through the
  shifter. Never run a bare 5 V Wiegand line into a GPIO.
- **GPIO5 is a strapping pin** (must be high at boot). Wiegand idles high, so it's
  fine — just don't hold a key down during power-up.
- **Wiegand D0/D1 are open-collector.** A bare line (no pull-up) reads ~0 V and is
  meaningless. It only idles at 5 V once a pull-up to 5 V is present — the BSS138
  board provides built-in 10 kΩ pull-ups to HV, so HV=5 V is what makes the lines
  idle high. (Or a temporary 4.7 k–10 kΩ resistor to 5 V for bench testing.)
- **A multimeter CANNOT see Wiegand pulses** — each is ~50 µs, a full code ~25 ms;
  the meter only averages. It can confirm idle-high (5 V) and nothing more. The
  `on_raw` ESPHome log is the only thing that actually decodes the frames.

## Bench-test progress (2026-06-03)

- Keypad powered from 12 V. Measured the Wiegand data line **bare (no pull-up)**:
  idle ~0 mV, and it **twitches up to ~0.15 V when `#` is pressed** → confirms the
  **keypad IS transmitting on that wire** (positive — right wire, keypad alive).
- That 0.15 V is expected/meaningless without a pull-up (open-collector + meter too
  slow). **Not a fault.**
- **NEXT:** wire the shifter with HV=5 V so the lines idle ~5 V, confirm with the
  meter, then connect to GPIO4/5 and read the `on_raw` log to (a) prove decode and
  (b) determine which path is live (4-bit `on_key` vs 26-bit `on_tag`). Bench
  calibration of the Wiegand mode is the remaining open item before trusting it.
