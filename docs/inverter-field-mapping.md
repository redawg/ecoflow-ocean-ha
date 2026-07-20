# Inverter raw field → sensor mapping

Working document for correlating raw MQTT wire fields from the CDO Ocean Pro
inverter (product type `88`, `HR51…` serials) against what they actually mean,
so we can confidently wire up new Home Assistant sensors and the dashboard.

## Workflow

1. **Capture.** Run the raw capture tool while you deliberately change device
   state (force a discharge, flip work mode, disconnect solar, etc.):

   ```powershell
   python scripts/inverter_raw_capture.py
   ```

   - Type a short note + Enter any time you change something (e.g.
     `started forced discharge`, `switched to self-use`, `unplugged string 2`).
     It gets timestamped into `annotations.csv` so we can line it up with the
     data afterward.
   - Let it run for the whole test — a few minutes per scenario is plenty.
   - Press `Ctrl+C` (or type `q` + Enter) to stop. Files land under
     `captures/inverter_raw/<timestamp>/`:
     - `raw_capture.csv` — one row per MQTT message, every raw wire field
       (`raw.<dotted.path>`) plus every field the integration already decodes
       (`mapped.<key>`).
     - `annotations.csv` — your timestamped notes.
     - `field_catalog.csv` — per-column min/max/first/last/distinct-count
       summary, so you can sort by which columns actually *moved* during the
       test window instead of scrolling the full CSV.
     - `run_summary.json` — run metadata (duration, message count, etc.).

2. **Correlate.** Open `field_catalog.csv`, filter to `kind = raw`, and sort
   by `delta` (last − first) or `distinct` to find which raw fields changed
   during your scenario. Cross-reference the timestamp in `annotations.csv`
   against `raw_capture.csv`'s `capture_ts` / `t_offset_s` to see the before/
   after values.

3. **Update this table.** Add a row (or fill in the `Observed` / `Confidence`
   columns of an existing row) for anything you confirm. Once a field is
   confirmed, it's a candidate to promote from "decoded only" to a real
   `SensorDefinition` in `sensor.py`.

## Known / already-decoded fields

Seeded from `inverter_decoder.py`, `models.py`, and `sensor.py` as of
2026-07-19. `Exposure` reflects what's true today; update `Observed` as you
verify behavior during test scenarios.

| Raw source | Data point | Unit | Exposure today | Observed during test | Confidence |
|---|---|---|---|---|---|
| `bpSoc` | Battery SOC | % | Sensor: Battery SOC | Fell 100%→95% smoothly across the forced-discharge phase (2026-07-19) — reliable, use as ground truth for direction. | High |
| `bpPwr` | Battery power (+charge / −discharge) | W | Sensor: Battery power | Read **positive** ("charging") for almost the whole forced-discharge+export phase while SOC was clearly falling. Sign looks inverted in that operating mode — see capture log below. | **Flagged — needs follow-up** |
| `mpptPwr` | Solar power (sum of strings) | W | Sensor: Solar power | Steady 10.5–12.7 kW all test, tracked expected midday output. | High |
| `sysGridPwr` (field 515) | Grid exchange (+import / −export) | W | Sensor: Grid power | Confirmed: jumped from ≈−11 kW to ≈−23.5 kW within ~2s the instant discharge+export was commanded, and unwound the same way switching back. | High |
| derived: solar + grid − battery | Home power | W | Sensor: Home power | Only passed its sanity guard on 13 of 615 messages (~2%) during the test — heuristic is unreliable outside steady self-consumption. | Low (confirmed unreliable during load-shifting) |
| `emsWordMode` | Work mode | – | Sensor: Work mode | Not observed live over MQTT in this capture (no `mapped.work_mode` samples) — still needs a dedicated test. | Untested |
| n/a | Device status | – | Sensor: Status | | |
| n/a | Online / reachable | bool | Binary sensor: Online | | |
| `sysBatChgUpLimit` | Backup SOC limit | % | Sensor: Backup SOC limit | | |
| `sysBatDsgDownLimit` | Discharge SOC limit | % | Sensor: Discharge SOC limit | | |
| `emsFeedPwr` | Feed power limit | W | Sensor: Feed power limit | | |
| `emsFeedRatio` | Feed ratio | % | Sensor: Feed ratio | | |
| fields 1463/1464/1467 | Phase A voltage/current/power | V, A, W | Sensors: Phase A * | | |
| fields 1465/1466/1468 | Phase B voltage/current/power | V, A, W | Sensors: Phase B * | | |
| `pcsCPhase.*` | Phase C voltage/current/power | V, A, W | Sensors: Phase C * | | |
| field 53 (fallback 21) | Total PCS active power (`pcs_act_pwr`) | W | **Decoded only — no entity** | Alternates between mirror-image +/− readings on nearly every consecutive message, for the entire 12-minute capture (not just during the test). Strongly suggests the 1467+1468 phase-sum path and the field-53 fallback disagree on sign convention. | **Bug suspected — see capture log** |
| fields 1476–1479, 1480–1483 sum | Per-string power, strings 1–5 | W | **Decoded only — rolled into total** | All 5 strings tracked together, one string (4) rose while others fell slightly — normal cloud/shading variance, no anomaly. | High |
| `mpptHeartBeat[31].1` | Per-string voltage, strings 1–5 | V | **Decoded only — no entity** | | |
| `mpptHeartBeat[31].2` | Per-string current, strings 1–5 | A | **Decoded only — no entity** | | |
| `mpptHeartBeat[31].4` (or power > 5 W) | Per-string active flag | bool | **Decoded only — no entity** | All 5 strings stayed "active" the whole test. | High |
| field 3 | Pack serial number | – | Entity attribute | Unstable across messages: the serial reporting as "pack slot 1" changed 4 times, slot 2 changed 3 times, slot 4 changed twice, across the 12-minute capture. | **Flagged — slot≠stable physical pack** |
| field 5 | Pack slot index | – | Entity attribute | `bp_pack_count` itself alternated between 1, 2, and 4 packs reporting per message — not every message carries the full fleet. | Confirmed sparse |
| field 11 (fallback 10) | Pack SOC | % | Sensor: Battery pack N SOC | | |
| `voltage_v × current_a`, sign-flipped | Pack power | W | Sensor: Battery pack N power | Only pack slots 1 and 4 showed large power (±9–11.6 kW) during the forced-discharge phase; slots 2 and 3 stayed within ±15 W the entire capture. | **Flagged — confirm only 2 packs were cycling** |
| field 22 | Pack temperature | °C | Sensor: Battery pack N temperature | Flat 22–23 °C all test — no anomaly. | High |
| field 20 | Pack state of health | % | Entity attribute | Flat ~99.9% all test — no anomaly. | High |
| field 45 (mV → V) | Pack voltage | V | Entity attribute | | |
| field 43 (deci-amps) | Pack current | A | Entity attribute | | |
| fields 33 / 34 | Cell temp max / min | °C | Entity attribute | | |
| field 29 | Remaining energy | Wh | Entity attribute | | |
| field 44 | Pack "power" twin — **discarded**, unreliable | – | Not used at all | Reproduced the documented wild swings again: −18 to +113,531 across just 20 samples. Confirms the existing discard decision is correct. | High (confirmed unreliable, correctly discarded) |
| fields 1553–1556 | Inverter temperatures 1–4 | °C | **Decoded only — no entity** | Drifted down 5–7.5 °C over the 12 minutes (67.9→62.6 °C etc.) — plausible cooling as load dropped. | High |
| field 517 | Mirror of summed solar strings | W | Identified, not parsed | | |
| field 516 | Residual (517 + 515), not battery | W | Identified, not parsed | | |
| fields 262 / 1448 | System / backup SOC fallback | % | Decoded (used only when pack data absent) | | |

## Capture log

### 2026-07-19 — forced discharge + grid export test

`captures/inverter_raw/20260719-120345/` — 615 messages over 12:13, while
switching the inverter to intelligent mode, forcing battery discharge +
grid export, then switching back to self-powered. **No annotations were
typed this run** (`annotation_count: 0`), so the phase boundaries below are
inferred from grid power, PCS power, and fleet SOC trends instead — confirm
against your own memory, and type notes live next time.

Full breakdown, charts, and evidence tables:
[inverter-capture-analysis](/C:/Users/andre/.cursor/projects/e-Cursor-Projects-ecoflow-ocean-ha/canvases/inverter-capture-analysis.canvas.tsx)

Inferred phases:

1. **0:00–3:13** — baseline / "intelligent mode, wait" — export ≈−10 to
   −11 kW, battery near-idle trickle, SOC 100%. No visible change from
   normal self-powered behavior.
2. **3:13–10:28** (~7.3 min) — forced discharge + export — grid export and
   PCS power both jump to roughly double the baseline within ~2 seconds and
   hold steady (≈−23.5 kW / ≈24 kW). Fleet SOC falls 100%→95%.
3. **10:28–10:52** (~24s) — switch back to self-powered — grid ramps down
   through a brief +814 W import spike and settles near zero; PCS power
   collapses from ~24 kW to under 1 kW.
4. **10:52–12:13** (capture end) — self-powered resumed, but the capture
   stopped too soon after the switch to see a clean recharge trend.

New/unmapped raw fields that moved in step with the test (low sample count —
treat as leads, not confirmed):

- `raw.1.1.353` / `raw.1.1.354` — paired, power-like, ≈−12,000 W → ≈−375 W
  (n=4).
- `raw.1.1.661` / `raw.1.1.663` — paired, power-like, ≈−11,900 W → ≈−100 W
  (n=3).
- `raw.1.1.1587` / `raw.1.1.1588` — paired, power-like, ≈−11,700 W → ≈0–3 W
  (n=2, very low confidence).
- `raw.1[0].1.16` — 0 → 65535 (0xFFFF) — bitmask-shaped, likely a
  status/error flags word rather than a measurement.

### Reactive / apparent power hunt (2026-07-19)

The official Developer API docs (`developer.ecoflow.com/us/document/PP2`)
confirm `pcsAPhase`/`pcsBPhase`/`pcsCPhase` each have `reactPwr` and
`apparentPwr` fields alongside `vol`/`amp`/`actPwr` — we only decode the
latter three (fields 1463–1468). Correlated field 1467 (phase A active
power) against every other `raw.1.1.*` field in the same messages (292
aligned samples) looking for a plausible reactive-power companion.
**No confident match found:**

- Fields 515/518 correlate almost perfectly (r=1.00 / r=0.995) but match
  known total-power quantities in magnitude and sign, not an orthogonal
  reactive component.
- Everything else was a weak (|r|<0.6), likely-spurious correlation with
  temperature fields that just happen to drift over the same 12 minutes —
  not a real reactive-power relationship.

This system likely runs close to unity power factor most of the time
(reactive power near zero relative to active power), which would make a
real reactive-power field hard to distinguish from noise in a capture like
this one. A follow-up capture would need a period with genuinely
non-unity PF (e.g. large motor/compressor loads running, or a dedicated
diagnostic mode) to make the field stand out. Not adding a `reactPwr`/
`apparentPwr` sensor without a confirmed field — see `field 44` above for
why we don't wire up unconfirmed guesses.

## Unknowns to hunt for during testing

These don't have a confirmed meaning yet — watch `field_catalog.csv` for
fields that move in step with a specific action:

- **Forced discharge test**: which raw field(s) jump when you force the
  battery to discharge hard? Does `battery_power_w` / field-derived power
  actually flip sign correctly, or does it lag/glitch?
- **Work mode change**: does `emsWordMode` (or any other field) change
  *before* power flow actually shifts, or only after?
- **String disconnect**: unplugging one MPPT string should zero exactly one
  of fields 1476–1483 — confirms the string → physical input mapping.
- **Backup SOC limit change**: does `sysBatChgUpLimit`/`sysBatDsgDownLimit`
  update live over MQTT, or only after a REST refresh?
- Any raw field with high `distinct` count and non-power-like magnitude in
  `field_catalog.csv` that isn't in the table above — flag it here for
  investigation.

## Adding a new field to this table

```
| <field/path> | <what you believe it is> | <unit> | <today's exposure, or "none"> | <what you observed> | <low/medium/high> |
```
