# Capture procedure (ops)

Lab procedure for recording RS485 traffic between the wireless antenna and the
pool/spa controller. Tooling lives in `pentairsnoop`; this document is **ops**,
not protocol truth.

## Goal

Produce annotated capture sessions under `samples/captures/` so Track A5 (write
craft) and Track D can compare crafted TX against **real** panel/remote writes
on **this** controller.

Until hardware is available, the CLI still supports `--file` fixture replay to
validate the **capture log format**. Do **not** invent “panel success” captures.

## Safety

1. **Power / equipment** — Prefer capturing with the pool equipment powered as
   normal. If you must break into the RS485 pair, power down first when your
   site practice requires it, then restore power only after the tap is secure.
2. **Do not short A/B** — Keep differential pair polarity consistent with the
   existing bus. Mark wires before disconnecting.
3. **Single bus owner for writes** — While capturing, do not run PHP, EW11
   clients, or `pentairservice` write paths that contend for the same link
   unless that contention is the experiment.
4. **No crafted TX during baseline captures** — Idle / panel / remote sessions
   should be listen-only so the corpus is trustworthy for A5 diffs.
5. **Isolation** — USB-RS485 adapters can inject ground loops. Prefer a
   properly isolated adapter when tapping near equipment grounds.

## Termination / wiring notes

Pentair EasyTouch-style buses are typically **RS485 half-duplex** at a lab
starting rate of **9600 8N1** (see package README — **unconfirmed for all
adapters** until validated).

- Tap **between the wireless antenna (or ScreenLogic/remote path) and the
  controller** so you see both panel/remote TX and controller replies.
- If the bus already has termination at the ends, **do not add a second
  terminator** at the tap unless measurements show reflections; a high-impedance
  listen tap is preferred.
- EW11 TCP bridge (`10.0.0.11:8899` starting default from `settings.php`) is an
  alternate listen point when the bridge already sits on the bus — still avoid
  second writers on port 8899.

## Session checklist (human lab)

Record separate sessions (separate labels / directories):

| Session | Label suggestion | Actions |
|---------|------------------|---------|
| Idle status | `idle-status` | No panel changes; several minutes of status/temps |
| Circuit toggles | `circuit-<name>` | Toggle **each** circuit from panel or remote; note which |
| Heat setpoint | `heat-setpoint` | Change heat/setpoint from panel; note before/after |

After each session, fill `NOTES.md` in that session directory. Only annotate
bytes that appear in `raw.ndjson` / `frames.ndjson`.

## CLI

From `pentairsnoop/` (venv activated):

```bash
# Live serial tap (device path is site-specific)
pentairsnoop capture --serial /dev/ttyUSB0 -o samples/captures --label idle-status

# EW11 TCP listen (do not share the port with other clients)
pentairsnoop capture --tcp -o samples/captures --label circuit-filter

# Format / tooling check from an existing hex fixture (not a panel-success claim)
pentairsnoop capture --file ../samples/status_temps.hex \
  -o /tmp/captures --label format-check --max-frames 5
```

Each run creates:

```text
samples/captures/YYYYMMDDTHHMMSSZ_<label>/
  meta.json       # source, timestamps, counts
  raw.ndjson      # timestamped transport chunks
  frames.ndjson   # timestamped framed messages
  NOTES.md        # human annotation stub
```

Stop with Ctrl-C (files are closed and `meta.json` finalized) or `--max-frames`.

## Capability-guided session (SSH + wireless remote)

For kickoff reverse engineering, prefer a **one-capability-at-a-time** walk so
the haul-back corpus is labeled. Catalog + schema:
[`capability-capture.md`](capability-capture.md). Interactive CLI
(`capture-capabilities`) lands in A2.1; until then use this ops outline.

1. SSH to the machine on the RS485/EW11 bus (other building). Keep the wireless
   remote in hand; the CLI only listens and prompts.
2. Start capability mode (listen-only — no `--send` / craft):

   ```bash
   pentairsnoop capture-capabilities --tcp -o samples/captures --label capability-guided
   # or: --serial /dev/ttyUSB0
   ```

3. For each capability the tool prints a prompt and arms recording. Exercise the
   remote as instructed, then type **`d`** (Done) or **`s`** (Skip). Optional
   **`q`** quits the session early. Enter after the letter is OK.
4. When finished, haul the session tree back to the dev box:

   ```bash
   tar czf ~/capability-guided-YYYYMMDD.tgz -C samples/captures YYYYMMDDTHHMMSSZ_capability-guided
   # scp/rsync tarball → unpack under pentairsnoop/samples/captures/
   ```

IntelliChlor is **excluded** from the default 17-entry catalog. Fill session
`NOTES.md` (and per-cap notes) with remote label quirks — especially filter/pool
naming.

## Index

Keep `samples/captures/README.md` updated when real sessions land. Until then the
directory is intentionally empty of bus corpus (layout + procedure only).
