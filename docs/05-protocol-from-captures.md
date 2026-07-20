# Protocol findings from captures (Track A5 / A2.4)

**Status:** Capture-compare tooling is **ready**. Offline **triage** tooling for
capability-guided sessions is **ready** (A2.3). Real panel/remote TX captures for
this controller are **not yet present** — do not treat the stubs below as
live-bus confirmation. Leave findings empty until A2.2 corpus lands.

## What is ready now

| Capability | Module / CLI | Evidence today |
|------------|--------------|----------------|
| Craft `0x86` CircuitChange | `craft.CircuitChange` / `craft-circuit` | Shape from research + PHP intent; CS via A5 sum |
| Craft `0x88` HeatChange | `craft.HeatChange` / `craft-heat` | **Byte match** vs `lib/index.php` `$set_temp` |
| Diff crafted vs reference TX | `compare.diff_tx` / `diff-tx` | Unit-tested with `$set_temp` as reference |
| Optional gated send + listen | `craft-* --send --tcp\|--serial` | Default is **dry-run** (hex only) |
| Capability session triage | `triage.summarize_session` / `triage-capabilities` | Fixture-tested against synthetic `index.jsonl` |
| Frame extract by capability | `triage.extract_frames` / `extract-capability` | Filters root `frames.ndjson` by `capability_id` |

## Offline triage (A2.3)

After hauling a capability-guided session tarball:

```bash
# List done / skipped / not_run with frame counts from index.jsonl
pentairsnoop triage-capabilities samples/captures/YYYYMMDDTHHMMSSZ_capability-guided

# Slice root frames when by_capability dual-write is incomplete
pentairsnoop extract-capability samples/captures/YYYYMMDDTHHMMSSZ_capability-guided spa_on_off \
  -o /tmp/spa_on_off.ndjson
```

See also: [capability-capture.md](capability-capture.md) (schema + CLI).

## Known hex used as reference (not panel captures)

From `lib/index.php` / `04-protocol-notes-from-code.md` §§7–8:

| Name | Hex | Role |
|------|-----|------|
| `$set_temp` | `ff00ffa507102088042b60050001f8` | HeatChange TX — **craft matches** |
| `$set_temp_ack` | `ff00ffa50c2010010188016b` | ACK `0x01` payload `0x88` — decode/listen only |
| `$light_on` | status `0x02` frame | **Not** a CircuitChange TX (status snapshot) |

Crafted pool-light ON (PHP encode shape, lab hypothesis):

```text
ff00ffa507102086020601016b
```

PROTO=`07` DST=`10` SRC=`20` CMD=`86` LEN=`02` payload=`06 01` CS=`016b`.

## When real captures arrive

1. Record panel/remote sessions per `docs/capture-procedure.md` / capability-guided mode.
2. Triage with `triage-capabilities`; open per-cap slices under `by_capability/` or
   `extract-capability` from root `frames.ndjson`.
3. Extract the successful **TX** hex from the slice (or annotated NOTES).
4. Diff:

```bash
pentairsnoop craft-circuit pool_light on --diff '<captured_tx_hex>'
pentairsnoop craft-heat --pool-set 43 --spa-set 96 --mode 0x05 --diff '<captured_tx_hex>'
# or
pentairsnoop diff-tx '<crafted>' '<captured>'
```

5. Record findings under the matching capability heading below (leave TBD until
   hardware corpus exists).

Do **not** invent match verdicts until those rows are filled from hardware.

## Open write questions (unchanged until capture)

- Write **SRC** (`0x20` vs `0x48` / ScreenLogic) and **PROTOCOL** on this install
- Circuit **write** ID for filter/pool body (`0x02` local name vs external `0x06`)
- Full request → ACK → status sequence on the live bus

## Safety

Default CLI emits hex only. `--send` is opt-in, requires `--tcp` or `--serial`,
and wraps TX in a listen window. Prefer offline/fixture lab buses; do not send
destructive writes to live hardware unless explicitly safe. Capability triage /
extract are **offline-only** (no transport).

---

## Per-capability findings stubs

Priority order follows plan §7.2. Findings are **TBD** until A2.2 live corpus
and A2.4 RE notes land. Use triage tooling to open the matching slice first.

### `idle_baseline`

**Findings:** TBD (observe-only baseline; no TX expected).

**How to open frames:**

```bash
pentairsnoop triage-capabilities <session_dir>
# by_capability/01_idle_baseline/frames.ndjson
# or: pentairsnoop extract-capability <session_dir> idle_baseline -o /tmp/idle.ndjson
```

### `filter_pump_on_off` (priority §7.2 #1)

**Findings:** TBD — resolve contested circuit write ID (`0x02` vs body `0x06`).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> filter_pump_on_off -o /tmp/filter_pump.ndjson
```

### `spa_on_off`

**Findings:** TBD — confirm local SPA circuit id ≈ `0x01`.

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> spa_on_off -o /tmp/spa.ndjson
```

### `spa_light_on_off`

**Findings:** TBD — confirm SPA_LIGHT write id `0x05` / status bit.

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> spa_light_on_off -o /tmp/spa_light.ndjson
```

### `pool_light_on_off`

**Findings:** TBD — confirm POOL_LIGHT write id `0x06` / status bit.

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> pool_light_on_off -o /tmp/pool_light.ndjson
```

### `water_feature_on_off`

**Findings:** TBD — confirm WATER_FEATURE / waterfall write id `0x04`.

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> water_feature_on_off -o /tmp/water_feature.ndjson
```

### `cleaner_on_off`

**Findings:** TBD (`skip_ok`; may be empty if skipped).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> cleaner_on_off -o /tmp/cleaner.ndjson
```

### `heat_boost_on_off`

**Findings:** TBD (`skip_ok`; HEAT_BOOST=`0x85` unconfirmed).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> heat_boost_on_off -o /tmp/heat_boost.ndjson
```

### `spa_temp_set` (priority §7.2 #3)

**Findings:** TBD — confirm `0x88` spa setpoint bytes vs craft.

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> spa_temp_set -o /tmp/spa_temp.ndjson
```

### `pool_temp_set` (priority §7.2 #3)

**Findings:** TBD — confirm `0x88` pool setpoint bytes vs craft.

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> pool_temp_set -o /tmp/pool_temp.ndjson
```

### `spa_heat_mode`

**Findings:** TBD (`skip_ok`; triage only if `status=done`).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> spa_heat_mode -o /tmp/spa_heat_mode.ndjson
```

### `pool_heat_mode`

**Findings:** TBD (`skip_ok`; triage only if `status=done`).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> pool_heat_mode -o /tmp/pool_heat_mode.ndjson
```

### `spa_heat_enable`

**Findings:** TBD (`skip_ok`).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> spa_heat_enable -o /tmp/spa_heat_enable.ndjson
```

### `filter_schedule`

**Findings:** TBD (`skip_ok`; may stay observe-only).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> filter_schedule -o /tmp/filter_schedule.ndjson
```

### `set_clock`

**Findings:** TBD (`skip_ok`; may stay observe-only).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> set_clock -o /tmp/set_clock.ndjson
```

### `aux_or_unknown_circuit`

**Findings:** TBD (`skip_ok`; note remote label in session NOTES).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> aux_or_unknown_circuit -o /tmp/aux.ndjson
```

### `idle_after`

**Findings:** TBD (observe-only cooldown window).

**How to open frames:**

```bash
pentairsnoop extract-capability <session_dir> idle_after -o /tmp/idle_after.ndjson
```

## Capture-compare log (legacy table)

| Scenario | Captured TX | Crafted TX | Match? | Notes (SRC/PROTO/circuit id) |
|----------|-------------|------------|--------|------------------------------|
| _(pending)_ | | | | |
