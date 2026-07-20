# Capture sessions

Saved bus recordings live here. Copy sessions from the machine attached to the panel into this folder (or point `-o` here when capturing on a shared checkout).

## Layout

Each continuous or capability-guided session is a directory:

```text
YYYYMMDDTHHMMSSZ_<label>/
  meta.json
  raw.ndjson
  frames.ndjson
  NOTES.md          # what you did / what you saw
  index.jsonl       # capability-guided sessions only
  by_capability/    # capability-guided sessions only
```

## Recorded sessions

_None checked in yet — add a row when you haul a real session home._

| Session folder | Notes |
|----------------|-------|
| _(empty)_ | |

## Related

- [Capture procedure](../../docs/capture-procedure.md)
- [Capability-guided capture](../../docs/capability-capture.md)
