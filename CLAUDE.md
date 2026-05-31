# Home Assistant — Stirling Cricket Facility (repo guide)

**This repo IS `/config` on the HA server** (192.168.1.5, HA OS). GitHub:
`adminrevo37/Home-Assistant-Stirling`. Branch: **`main`** (single source of truth).
Master context: `../CLAUDE.md` (the Claude folder). Pull only this repo for HA work.

> Created 2026-05-30 alongside the deploy-pipeline fix + Control4 token-refresh fix.
> Keep updated after significant changes (standing rule).

---

## Deploy pipeline (READ FIRST — this is how changes reach the box)

**Model: GitHub `main` is the source of truth. HA pulls from it.** One branch only.

- **Push a fix:** edit here → commit → `git push origin main`.
- **Deploy to HA:** trigger a pull. Two ways:
  - Over MCP (no webhook/exposure needed): `ha_call_service(shell_command, git_pull)`
    then `homeassistant.reload_all` if YAML changed. `git_pull.sh` does
    `git fetch origin main && git reset --hard origin/main`.
  - Webhook: `automation.deploy_from_github` (webhook `7k2p9x4m3n8q1r5f`) runs
    git_pull → reload_all. **Note: the GitHub webhook has never been confirmed
    delivering** (HA is LAN-only); MCP-triggered pull is the reliable path.
- **Shell/Python scripts** (`*.py`, `*.sh`) are read fresh on each run — no reload
  needed, the next invocation uses the new file.
- **Nightly backup:** `automation.auto_push_config_to_github` runs `git_push.sh`
  at 02:00 — commits live `/config` (incl. MCP/UI automation edits) and pushes to
  `main`. Hardened 2026-05-30 with `pull --rebase` before push so it can't fail on
  a non-fast-forward. A failed push fires a persistent notification.

**Gotcha:** `git reset --hard` on pull discards uncommitted working-tree changes on
the box (runtime artifacts in `www/` regenerate — fine). Don't trigger a pull if
there are un-pushed *config* edits made directly on the box you want to keep.

**History note (2026-05-30):** the pipeline was previously broken — a `main`/`master`
split where HA pushed to `main` but pulled from `master`; the pull never ran. Both
branches were consolidated to `main`; `master` retired. Safety tag:
`backup-master-20260530`.

---

## Control4 door codes (token auto-refresh)

> **⚠️ 2026-05-31 — DOOR-CODE PROGRAMMING DOES NOT WORK AND CANNOT VIA THIS API.**
> On-site test + live API probe proved the Control4 **local Director REST API exposes no
> add-access-code command**. `SET_USER_CODE` to the DS3 (item 39) returns `result=1` but is a
> no-op; the keypad rejects every API-written code (a known app-managed staff code works fine).
> Access agent (87) is empty over REST; lock (93) only does LOCK/UNLOCK/TOGGLE. Codes are managed
> only via the Control4 app/cloud. **`c4_manage_codes.py` is effectively dead** until the access
> strategy changes (HA-unlock-on-booking, a HA-programmable smart lock, or the C4 cloud API). Full
> analysis + options: `../cricket/home-assistant/DIAG_DOOR_CODE_KEYPAD_REJECT_2026-05-31.md`.
> The token/auth path below is still healthy and is shared with the entry logger.

The front-door Control4 DS3 (item 39, `192.168.1.107`, self-signed cert) is reached
only from this box via `pyControl4`. Director bearer tokens are **~24h JWTs**.

- **`c4_auth.py`** — shared, expiry-aware token loader (decodes JWT `exp`, re-auths
  when missing/expired/near-expiry, caches to `c4_token_cache.txt`). **Both** the
  code-setter and entry-logger use it. Added 2026-05-30 to fix a silent 24h
  time-bomb (tokens only refreshed on a *missing* cache file before, never on
  expiry → door programming + entry logging died daily).
- **`c4_manage_codes.py set|clear <slot> [code] [name]`** → `shell_command.c4_set_code`
  / `c4_clear_code`. Forces a token refresh + retries once on failure.
  Returns `status=200 result=1` on success. Output NOT redirected (visible to MCP).
- **`c4_entry_log.py`** → `shell_command.c4_entry_log`, runs every minute, logs DS3
  code usage to `www/entry_log.csv`. Wrapped so a transient failure logs one line
  instead of crashing exit-1. Output redirected to `www/entry_log_debug.txt`.

**Slot → bay:** Staff 1–10 (permanent, never touched) · Bay1 11–13 · Bay2 14–16 ·
Bay3 17–19 · Bay4 20–22 · Bay5 23–25 (a/b/c per bay). Booking codes tracked in
`input_text.bay{N}_code_slot_{a/b/c}` as `CODE:Name`.

**Door-code flow:** Krickora → Google Calendar event description (`DOOR CODE: NNNNNN`,
`Customer: Name`) → `bay{N}_code_activate` (T-15min) regex-extracts, picks a free
slot, calls `c4_set_code` → `bay{N}_code_deactivate` (end+15min) finds the slot by
code and calls `c4_clear_code`. **Cap: 3 concurrent codes per bay** — a 4th
activate aborts silently (no alert). Worth adding an admin alert on slot exhaustion.

---

## Lighting automations (rebuilt 2026-05-30, commit `9ce0b24`)

Full rebuild per `../cricket/home-assistant/SPEC_HA_LIGHTING_AUTOMATIONS.md` (that doc is the
deployment record). Shape:

- **Bay highbays:** `bay{1-5}_booking_start` turns on the bay highbay + helper (T−7m, if not
  blocked); `bay{1-5}_booking_end` turns off neighbour-aware (T+5m), using native
  `condition: sun after sunset offset -30m` for the night check. **Highbay 3 is reserved at
  night** (never turned off by booking_end) — it's the residual.
- **Common lights** (`inside_wall_lights`, `fluro_x_4`, `mezzanine_wall_lights`): ON via front-
  door unlock OR occupancy>threshold; OFF when occupancy ≤threshold AND no active booking
  (re-checking: debounced threshold-cross + `/5` time-pattern, so the already-empty case is
  caught). Booking-active guard prevents mid-session darkness.
- **Night residual:** "Night - All Off + Highbay 3 Residual" (reworked `night_staggered_shutdown`)
  drops hb1/2/4/5 + turns hb3 ON when all helpers off after dark; "Night - Highbay 3 Residual
  Off" turns hb3 off once occupancy <threshold (re-checking).
- **End-of-day:** dynamic hard-off (configurable delay after last booking) + absolute 23:00
  catch-all. **Office:** off-sweep at 18/20/22. **Exterior:** on −12m before booking (dark only)
  + `/15` manage (stay-on/off).
- **Tunable values** live in helpers (Settings → Helpers, no redeploy):
  `input_number.lighting_occupancy_threshold` / `_common_off_debounce_min` / `_endofday_delay_min`,
  `input_datetime.lighting_hard_off_catchall` / `office_off_1..3`. Calendar-trigger offsets stay
  static (HA can't reference helpers in trigger offsets).
- **On-site calibration still pending** (see the spec): 40% occupancy threshold, the door-open→
  common-lights assumption (`lock.front_door_lock`→unlocked on a code entry), exterior timing,
  residual handover on a real night booking.

---

## Zigbee (Zigbee2MQTT)

Zigbee runs on **Zigbee2MQTT** (bridge v2.10.1, USB antenna coordinator `0xd878f0fffe6815f9`), via
the MQTT integration. Pair from HA by toggling `switch.zigbee2mqtt_bridge_permit_join` on, putting the
device in pairing mode, then toggling it off. Rename via MQTT: publish to
`zigbee2mqtt/bridge/request/device/rename` with `{"from":"<ieee>","to":"<name>"}` (updates the Z2M
friendly_name + MQTT topic; pre-existing HA entity_ids keep their join-time ieee slug).

Paired devices (2026-05-31):
- **roller_door_contact** — Aqara door/window sensor T1 (`0x54ef4410014ae72a`) → `binary_sensor.roller_door_contact` (roller-door spec).
- **Plug 1** — Tuya smart plug w/ power monitoring (`0xa4c138074803a9a9`) → `switch.0xa4c138074803a9a9` + power/current/voltage/energy sensors. Registered + named only; **not yet assigned a purpose or automation** (parked 2026-05-31). Mains-powered → also a Zigbee router.
- **Plug 2** — Tuya smart plug w/ power monitoring (`0xa4c138ba345696ae`) → `switch.0xa4c138ba345696ae` + same sensors. Same parked status. Entity_ids still ieee-based (tidy to `plug_2` later if wanted).

---

## Key files

| File | Purpose |
|------|---------|
| `automations.yaml` | All automations (bay lighting, door codes, deploy, backup) |
| `configuration.yaml` | shell_commands, sensors, input helpers, door-close timing |
| `c4_auth.py` | Shared Control4 token (expiry-aware) |
| `c4_manage_codes.py` | Program/clear DS3 door codes |
| `c4_entry_log.py` | Poll DS3 for code usage → CSV |
| `c4_door_visual.py` | Roller-door visual state sensor (PIL) — *to be retired, see roller-door spec* |
| `c4_item_dump.py` | List all Control4 Director items to stdout (`shell_command.c4_item_dump`) — diagnostic |
| `git_pull.sh` / `git_push.sh` | Deploy from / backup to GitHub `main` |
| `c4_token_cache.txt` | Cached director JWT (auto-managed, **gitignored**; do not edit/commit) |

## Gotchas
- `.env.local`/dev: N/A here (that warning is Krickora). This repo runs live on the box.
- Windows clone: `core.fileMode false` is set so exec-bit noise doesn't pollute diffs.
  Scripts run via `bash`/`python3 <path>`, so the exec bit is irrelevant.
- **Runtime state + the token are gitignored** (`c4_token_cache.txt`, `c4_door_visual_state.json`,
  `c4_entry_log_state.json`, `www/` artifacts). Reason: `git_pull` does `reset --hard`, which would
  otherwise overwrite the LIVE token/baselines with stale committed copies each deploy. They
  self-heal (missing token → clean re-auth; state files → defaults). Don't re-add them to git.
- `ha_token.txt` (HA long-lived token) still not created — `c4_entry_log.py`'s
  "Code Used" column falls back to `core.restore_state` until it exists.
- HA changes via MCP edit `automations.yaml` on the box directly; they reach GitHub
  only via the 2am backup push (or a manual push). Commit them if you want them
  before then.
