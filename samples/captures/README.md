# Capture index (`samples/captures/`)

Annotated live RS485 capture sessions for **this** controller. Layout and
tooling are ready; **live corpus is pending hardware** (antenna↔controller tap).

Do **not** add fabricated “panel success” TX files here. Fixture format checks
belong in unit tests (`tests/test_capture.py`) or temporary `--file` runs
outside this index.

## Session layout

Each capture is a directory:

```text
YYYYMMDDTHHMMSSZ_<label>/
  meta.json       # format_version, source, started_at/ended_at, counts
  raw.ndjson      # {"t","offset","len","hex"} per transport read
  frames.ndjson   # {"t","seq","kind","checksum_ok","raw"} per frame
  NOTES.md        # human annotation (intent, actions, observed TX)
```

See [`docs/capture-procedure.md`](../../docs/capture-procedure.md) for wiring,
safety, and CLI examples.

## Planned sessions (pending live bus)

| Status | Label | Intent |
|--------|-------|--------|
| pending | `idle-status` | Baseline status/temps, no panel changes |
| pending | `circuit-*` | Each circuit toggle from panel/remote |
| pending | `heat-setpoint` | Heat / setpoint change from panel |

When a real session is recorded, add a row under **Recorded sessions** with the
directory name and a one-line summary. Link `NOTES.md` for ACK/status outcomes.

## Recorded sessions

_None yet — live annotated captures pending hardware._

## Related fixtures (not A4 live corpus)

Parent-tree hex fixtures (`../samples/status_temps.hex`,
`../samples/log_breakdown/`, …) remain **read-path regression** samples. They
are not substitutes for antenna↔controller write captures.
