# Capture procedure

How to record continuous RS-485 traffic while equipment is running normally.

## Goal

Create a session folder under `samples/captures/` (or another `-o` directory) with timestamped raw and framed logs you can study later or share with someone helping decode the panel.

## Safety

1. Prefer capturing with pool equipment powered as usual. If you must break into the RS-485 pair, follow your site’s lockout practice before touching wires.
2. Do not short the A/B pair. Mark polarity before disconnecting anything.
3. Only one program should own the link. Pause other tools that open the same TCP port or serial device while you capture.
4. For baseline / remote-button captures, stay **listen-only** (do not use `craft-* --send` in the same session).
5. Prefer an **isolated** USB RS-485 adapter to reduce ground-loop risk near equipment.

## Wiring notes

Typical EasyTouch-style buses are half-duplex RS-485. This tool’s serial starting point is **9600 8N1** — confirm on your install.

- Tap **between the wireless antenna (or remote path) and the controller** so you see both directions of traffic.
- If the bus already has termination at the ends, do not add another terminator at the tap unless you are chasing reflections on purpose.

## Continuous capture

```bash
# USB RS-485 on this machine
pentairsnoop capture --serial /dev/ttyUSB0 -o samples/captures --label idle-status

# Network serial bridge (EW11-style)
pentairsnoop capture --tcp -o samples/captures --label circuit-filter
```

Stop with **Ctrl+C**. Each session folder includes:

| File | Contents |
|------|----------|
| `meta.json` | When/how the session was started |
| `raw.ndjson` | Timestamped raw byte chunks |
| `frames.ndjson` | Timestamped framed / decoded messages |
| `NOTES.md` | Your handwritten notes (what you pressed, what the display did) |

Choose a meaningful `--label` so folders stay sortable (`idle-status`, `spa-remote`, `heat-setpoint`, …).

### Practice without hardware

```bash
pentairsnoop capture --file ../samples/status_temps.hex \
  -o /tmp/captures --label format-check --max-frames 5
```

This only checks that the log format works. It is not a claim about live panel traffic.

## After the session

1. Fill in `NOTES.md` while details are fresh.
2. Copy the folder (or tar it) back to your development machine.
3. List sessions in [`samples/captures/README.md`](../samples/captures/README.md) when you archive them under this repo.

## Related

- [Capability-guided capture](capability-capture.md) — prompted walk of remote actions with labels
- [Main README](../README.md) — all commands and parameters
