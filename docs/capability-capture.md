# Capability-guided capture

Use this when you are at a machine that can see the bus (often over SSH) and you have the **wireless remote** in hand. The tool walks a fixed list of actions, prompts you for each one, and labels the recorded traffic so you can reverse-engineer features later.

## Quick start

```bash
pentairsnoop capture-capabilities --serial /dev/ttyUSB0 -o samples/captures
# or: pentairsnoop capture-capabilities --tcp -o samples/captures
```

For each capability:

1. Read the prompt (example: turn spa on, then off).
2. Do that on the wireless remote (or skip if it does not apply).
3. Press **`d`** (done), **`s`** (skip), or **`q`** (quit early).

Recording for that capability is **armed when the prompt appears** (default). The tool never transmits to the bus in this mode.

## Keys

| Key | Meaning |
|-----|---------|
| `d` | Done with this capability — save the window and continue |
| `s` | Skip this capability |
| `q` | Quit the walk early (session files still kept) |

With `--arm-on-key`, press **`a`** first to start recording that window, then `d` / `s` when finished.

## Useful options

```bash
# Only a few capabilities
pentairsnoop capture-capabilities --tcp -o samples/captures \
  --only idle_baseline,spa_on_off,filter_pump_on_off,spa_temp_set

# Resume later in the list
pentairsnoop capture-capabilities --tcp -o samples/captures --from-id spa_temp_set

# Pre-skip things you know you cannot do tonight
pentairsnoop capture-capabilities --tcp -o samples/captures --skip-id set_clock,filter_schedule
```

Practice UI without hardware:

```bash
printf 'd\ns\nq\n' | pentairsnoop capture-capabilities \
  --file ../samples/status_temps.hex \
  -o /tmp/cap --only idle_baseline,spa_on_off
```

## What you get on disk

Under `-o` (default `samples/captures/`), a session folder named like `YYYYMMDDTHHMMSSZ_capability-guided/` containing:

| Path | Purpose |
|------|---------|
| `meta.json` | Session metadata |
| `index.jsonl` | One line per capability (done / skipped, counts) |
| `raw.ndjson` / `frames.ndjson` | Full stream for the session |
| `by_capability/<nn>_<id>/` | Per-capability slices when dual-write is enabled |
| `NOTES.md` | Your notes |

Default catalog length is **17** capabilities (spa/pool circuits, lights, heat setpoints/modes, schedule/clock where applicable). Salt-cell / IntelliChlor items are **not** in the default list.

## After you haul the session home

```bash
pentairsnoop triage-capabilities path/to/session
pentairsnoop extract-capability path/to/session spa_on_off -o /tmp/spa.ndjson
```

`triage-capabilities` shows what was done and how busy each window was. `extract-capability` pulls one labeled slice for closer study.

## Related

- [Capture procedure](capture-procedure.md) — continuous unlabeled `capture`
- [Main README](../README.md) — full parameter reference
