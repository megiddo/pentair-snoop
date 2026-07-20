# pentairsnoop

**pentairsnoop** is a command-line tool for listening to and recording traffic on a Pentair pool/spa controller’s RS-485 bus. Use it to:

- Watch live messages from the panel (over a network serial bridge or a USB RS-485 adapter)
- Save labeled captures while you press buttons on the wireless remote
- Decode saved hex dumps into readable JSON
- Build and compare experimental “write” packets (off by default — dry-run unless you opt in)

It is meant for lab and reverse-engineering work on a machine that can see the bus (for example an SSH session to a Raspberry Pi or a host with an Elfin EW11 bridge).

## Install

Requires **Python 3.11+**.

```bash
cd pentairsnoop
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Check that it works:

```bash
pentairsnoop --help
```

## How you connect

Almost every live command needs **exactly one** of these sources:

| Connection | Typical use |
|------------|-------------|
| `--tcp HOST:PORT` | Elfin EW11 (or similar) serial-to-Ethernet bridge on your LAN |
| `--serial /dev/…` | USB RS-485 adapter plugged into this machine |
| `--file path.hex` | Offline replay of a saved hex dump (no live bus) |

**Lab starting defaults** (confirm on your hardware):

| Setting | Default |
|---------|---------|
| TCP | `10.0.0.11:8899` (omit the host after `--tcp` to use this) |
| Serial | **9600 8N1** |

**Tip:** Only one program should open the same TCP port or serial device at a time. If another service already owns the link, `watch` / `capture` will fight it.

---

## Commands

### `decode-file` — turn a hex dump into one JSON object per message

Reads a `.hex` file and prints **NDJSON** (one JSON object per line). Useful for quick inspection in a pager or pipe.

```bash
pentairsnoop decode-file ../samples/status_temps.hex
```

What this does: opens the fixture, finds framed bus messages, and prints each decoded message as JSON.

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `path` (required) | Hex file to decode | `../samples/status_temps.hex` |
| `--pretty` | Indent each JSON object for reading | `… --pretty` |
| `--include-quarantine` | Also print frames that failed checksum checks | `… --include-quarantine` |

```bash
pentairsnoop decode-file ../samples/status_temps.hex --pretty
```

---

### `dump` — decode a hex dump as one JSON array

Same decode as `decode-file`, but emits a **single JSON array** (pretty-printed by default). Handy when you want to open the result in an editor.

```bash
pentairsnoop dump ../samples/status_temps.hex
```

What this does: decodes the whole file, then prints `[ {…}, {…}, … ]`.

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `path` (required) | Hex file to decode | `../samples/status_temps.hex` |
| `--compact` | One-line JSON instead of indented | `… --compact` |
| `--include-quarantine` | Include bad-checksum frames | `… --include-quarantine` |

```bash
pentairsnoop dump ../samples/status_temps.hex --compact > /tmp/status.json
```

---

### `watch` — stream live (or replayed) messages as JSON

Opens a connection, keeps it open, and prints each decoded message as NDJSON until you stop it (Ctrl+C) or hit a limit.

```bash
pentairsnoop watch --tcp
```

What this does: connects to the default EW11 address, reconnects if the link drops, and prints every framed message it understands.

```bash
pentairsnoop watch --serial /dev/ttyUSB0 --cmd 0x02 --cmd 0x08 --pretty
```

What this does: listens on a USB RS-485 adapter and only prints status (`0x02`) and temperature-info (`0x08`) messages, formatted for reading.

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `--tcp [HOST:PORT]` | Live TCP bridge (default host/port if omitted) | `--tcp` or `--tcp 192.168.1.50:8899` |
| `--serial PORT` | Live serial device | `--serial /dev/ttyUSB0` |
| `--file PATH` | Replay a hex file until EOF | `--file ../samples/status_temps.hex` |
| `--cmd BYTE` | Only show this command byte (repeatable; comma lists OK) | `--cmd 0x02 --cmd 0x08` or `--cmd 0x02,0x08` |
| `--pretty` | Indent each JSON object | `--pretty` |
| `--include-quarantine` | Also show bad-checksum frames | `--include-quarantine` |
| `--max-messages N` | Stop after N printed messages | `--max-messages 20` |

---

### `capture` — record a continuous session to disk

Same connection options as `watch`, but writes a **session folder** with timestamped raw bytes and framed messages for later study.

```bash
pentairsnoop capture --serial /dev/ttyUSB0 -o samples/captures --label idle-status
```

What this does: opens the serial port, creates a new folder under `samples/captures/`, and records until you press Ctrl+C. Use a clear `--label` so you remember what you were doing (idle bus, toggling filter, etc.).

See also: [Capture procedure](docs/capture-procedure.md).

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `--tcp [HOST:PORT]` | Live TCP bridge | `--tcp` |
| `--serial PORT` | Live serial device | `--serial /dev/ttyUSB0` |
| `--file PATH` | Offline hex (format / tool testing) | `--file ../samples/status_temps.hex` |
| `-o` / `--out DIR` | Parent folder for new sessions (default `samples/captures`) | `-o /tmp/captures` |
| `--label TEXT` | Suffix in the auto session folder name | `--label circuit-filter` |
| `--session-dir DIR` | Use this exact folder instead of auto-naming | `--session-dir /tmp/my-run` |
| `--max-frames N` | Stop after N framed messages | `--max-frames 50` |

Each session typically contains `meta.json`, `raw.ndjson`, `frames.ndjson`, and `NOTES.md` (for your handwritten observations).

---

### `capture-capabilities` — guided remote walk (labeled captures)

Interactive mode for reverse engineering: the tool prompts you to press something on the **wireless remote**, records bus traffic while that capability is “armed,” then moves to the next item in a fixed catalog (spa on/off, lights, setpoints, etc.).

Keys while prompted:

| Key | Meaning |
|-----|---------|
| `d` | **Done** — finish this capability and go to the next |
| `s` | **Skip** — skip this capability |
| `q` | **Quit** — end the session early |

```bash
pentairsnoop capture-capabilities --tcp -o samples/captures
```

What this does: connects to the EW11, creates a capability-guided session folder, and walks the catalog one prompt at a time while labeling recorded frames with the current capability id.

```bash
printf 'd\ns\nq\n' | pentairsnoop capture-capabilities \
  --file ../samples/status_temps.hex \
  -o /tmp/cap --only idle_baseline,spa_on_off
```

What this does: dry-run against a hex file with scripted answers (useful to practice the UI without the panel).

**This mode is listen-only** — it never sends commands to the bus.

See also: [Capability-guided capture](docs/capability-capture.md).

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `--tcp` / `--serial` / `--file` | Same sources as `watch` | `--serial /dev/ttyUSB0` |
| `-o` / `--out DIR` | Parent folder for the session | `-o samples/captures` |
| `--label TEXT` | Session name suffix (default `capability-guided`) | `--label panel-night1` |
| `--session-dir DIR` | Exact output folder | `--session-dir /tmp/caps1` |
| `--only ID,ID` | Run only these catalog ids (order preserved) | `--only spa_on_off,pool_light_on_off` |
| `--skip-id ID,ID` | Mark these skipped without asking | `--skip-id set_clock` |
| `--from-id ID` | Resume the walk starting at this id | `--from-id spa_temp_set` |
| `--catalog PATH` | Use a custom catalog JSON instead of the built-in list | `--catalog ./my_catalog.json` |
| `--arm-on-prompt` | Start recording as soon as the prompt appears (**default**) | (default) |
| `--arm-on-key` | Wait for you to press `a` before recording that window | `--arm-on-key` |

Afterward, copy the session folder (or a tarball) back to your development machine.

---

### `triage-capabilities` — summarize a capability session

Offline helper: read a hauled session’s `index.jsonl` and show which capabilities were done/skipped and how many frames each got.

```bash
pentairsnoop triage-capabilities samples/captures/20260720T180000Z_capability-guided
```

What this does: prints a table of capability id → status → frame counts so you can see what is worth studying first.

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `session_dir` (required) | Path to the session folder | `…/20260720T180000Z_capability-guided` |
| `--json` | Machine-readable summary instead of a table | `… --json` |

---

### `extract-capability` — pull one capability’s frames

Offline helper: filter the session’s `frames.ndjson` down to a single capability id.

```bash
pentairsnoop extract-capability \
  samples/captures/20260720T180000Z_capability-guided \
  spa_on_off -o /tmp/spa_on_off.ndjson
```

What this does: writes only the frames tagged `spa_on_off` so you can inspect that window alone.

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `session_dir` (required) | Session folder with `frames.ndjson` | `…/…_capability-guided` |
| `capability_id` (required) | Exact id to keep | `spa_on_off` |
| `-o` / `--out PATH` | Write NDJSON to a file (default: stdout) | `-o /tmp/spa.ndjson` |

---

### `craft-circuit` — build a circuit on/off packet (dry-run by default)

Prints the hex bytes for a circuit change request. Does **not** send anything unless you pass `--send`.

```bash
pentairsnoop craft-circuit pool_light on
```

What this does: builds an “turn pool light on” packet and prints its hex. Use this to compare against bytes you captured from the real remote.

Circuit names include: `spa`, `pool`, `cleaner`, `water_feature`, `spa_light`, `pool_light`, `heat_boost` (or a raw id like `0x06`).

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `circuit` (required) | Name or numeric id | `spa_light` or `0x05` |
| `state` (required) | `on` / `off` (or `1` / `0`) | `off` |
| `--protocol BYTE` | Protocol field in the packet | `--protocol 0x07` |
| `--dst BYTE` | Destination address | `--dst 0x10` |
| `--src BYTE` | Source address | `--src 0x20` |
| `--diff REF_HEX` | Also compare against a captured/reference hex string | `--diff ff00ffa5…` |
| `--json` | Print structured JSON instead of bare hex | `--json` |
| `--send` | Actually transmit (requires `--tcp` or `--serial`) | see below |
| `--tcp` / `--serial` | Link to use only with `--send` | `--send --tcp` |
| `--listen-seconds SEC` | How long to listen before/after a send (default `2.0`) | `--listen-seconds 3` |

```bash
# Dry-run only (safe)
pentairsnoop craft-circuit spa on --json

# Live send — lab only; listens before and after
# pentairsnoop craft-circuit spa on --send --serial /dev/ttyUSB0 --listen-seconds 2
```

---

### `craft-heat` — build a heat / setpoint packet (dry-run by default)

```bash
pentairsnoop craft-heat --pool-set 86 --spa-set 96 --mode 0x05
```

What this does: builds a heat-change packet with pool and spa setpoints (Fahrenheit) and a mode byte, then prints hex. Default is dry-run.

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `--pool-set F` (required) | Pool setpoint °F | `--pool-set 86` |
| `--spa-set F` (required) | Spa setpoint °F | `--spa-set 96` |
| `--mode BYTE` | Raw heater mode byte | `--mode 0x05` |
| `--pack-modes` | Build mode from `--pool-mode` / `--spa-mode` instead | `--pack-modes` |
| `--pool-mode 0-3` | With `--pack-modes` (default `1`) | `--pool-mode 1` |
| `--spa-mode 0-3` | With `--pack-modes` (default `1`) | `--spa-mode 1` |
| `--protocol` / `--dst` / `--src` | Same address fields as `craft-circuit` | `--src 0x20` |
| `--diff` / `--json` / `--send` / `--tcp` / `--serial` / `--listen-seconds` | Same as `craft-circuit` | |

```bash
pentairsnoop craft-heat --pool-set 86 --spa-set 96 --mode 0x05 --diff ff00ffa507102088042b60050001f8
```

What this does: craft the packet and immediately show a field-by-field diff against a reference hex string you pasted from a capture.

---

### `diff-tx` — compare two TX hex strings

```bash
pentairsnoop diff-tx \
  ff00ffa507102088042b60050001f8 \
  ff00ffa507102088042b60050001f8
```

What this does: compares addresses, payload, and checksum between a crafted packet and a reference (for example from a capture) and prints the differences as JSON.

#### Parameters

| Parameter | What it does | Example |
|-----------|--------------|---------|
| `crafted` (required) | First hex string | `ff00ffa5…` |
| `reference` (required) | Second hex string | `ff00ffa5…` |
| `--pretty` | Indent the JSON result | `--pretty` |

---

## Suggested workflows

### 1. First look at saved bus traffic

```bash
pentairsnoop dump ../samples/status_temps.hex | less
```

### 2. Live monitor while someone uses the remote

```bash
pentairsnoop watch --serial /dev/ttyUSB0 --pretty
```

### 3. Structured capture for reverse engineering

On the machine attached to the bus:

```bash
pentairsnoop capture-capabilities --serial /dev/ttyUSB0 -o samples/captures
```

Follow the prompts with the wireless remote (`d` / `s` / `q`). Then copy the session directory home and run:

```bash
pentairsnoop triage-capabilities path/to/session
pentairsnoop extract-capability path/to/session spa_on_off -o /tmp/spa.ndjson
```

### 4. Continuous unlabeled recording

```bash
pentairsnoop capture --tcp --label evening-idle -o samples/captures
```

Stop with Ctrl+C when finished; fill in `NOTES.md` in the session folder.

---

## Tests

```bash
pytest
```

---

## More docs

- [Capture procedure](docs/capture-procedure.md) — wiring, safety, and continuous `capture` sessions
- [Capability-guided capture](docs/capability-capture.md) — catalog walk, keys, and session layout
- [Capture index](samples/captures/README.md) — where hauled sessions live
