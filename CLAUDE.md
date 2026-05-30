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
