# Protocol findings from captures (Track A5)

**Status:** Capture-compare tooling is **ready**. Real panel/remote TX captures for
this controller are **not yet present** — do not treat the verdicts below as
live-bus confirmation.

## What is ready now

| Capability | Module / CLI | Evidence today |
|------------|--------------|----------------|
| Craft `0x86` CircuitChange | `craft.CircuitChange` / `craft-circuit` | Shape from research + PHP intent; CS via A5 sum |
| Craft `0x88` HeatChange | `craft.HeatChange` / `craft-heat` | **Byte match** vs `lib/index.php` `$set_temp` |
| Diff crafted vs reference TX | `compare.diff_tx` / `diff-tx` | Unit-tested with `$set_temp` as reference |
| Optional gated send + listen | `craft-* --send --tcp\|--serial` | Default is **dry-run** (hex only) |

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

1. Record panel/remote sessions per `docs/capture-procedure.md` (idle, circuit
   toggle, heat setpoint).
2. Extract the successful **TX** hex from `frames.ndjson` (or annotated NOTES).
3. Diff:

```bash
pentairsnoop craft-circuit pool_light on --diff '<captured_tx_hex>'
pentairsnoop craft-heat --pool-set 43 --spa-set 96 --mode 0x05 --diff '<captured_tx_hex>'
# or
pentairsnoop diff-tx '<crafted>' '<captured>'
```

4. Record here:

| Scenario | Captured TX | Crafted TX | Match? | Notes (SRC/PROTO/circuit id) |
|----------|-------------|------------|--------|------------------------------|
| _(pending)_ | | | | |

Do **not** invent match verdicts until those rows are filled from hardware.

## Open write questions (unchanged until capture)

- Write **SRC** (`0x20` vs `0x48` / ScreenLogic) and **PROTOCOL** on this install
- Circuit **write** ID for filter/pool body (`0x02` local name vs external `0x06`)
- Full request → ACK → status sequence on the live bus

## Safety

Default CLI emits hex only. `--send` is opt-in, requires `--tcp` or `--serial`,
and wraps TX in a listen window. Prefer offline/fixture lab buses; do not send
destructive writes to live hardware unless explicitly safe.
