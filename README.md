# pentairsnoop

Python lab CLI for Pentair RS485 protocol inspection, offline fixture decode, live
watch, and capture. Sibling repo under the parent phpentair tree (ignored by the
parent git checkout).

## Plans (parent)

Design and milestones live in the parent repo:

- [Track A milestones](../agents/plans/milestones-track-a-pentairsnoop.md) — this project
- [Software design](../agents/plans/03-software-design.md) — CLI layout and patterns
- [Protocol notes from code](../agents/plans/04-protocol-notes-from-code.md)
- [Overview](../agents/plans/00-overview.md)

## Lab defaults (starting config)

These are **starting** values from the parent tree. Treat them as unconfirmed for
all adapters until validated in capture sessions.

| Setting | Value | Source |
|---------|-------|--------|
| Serial baud / framing | **9600 8N1** (no parity, 1 stop bit) | Parent `local_rs485.py` |
| EW11 TCP port | **8899** | Parent `api/app/settings.php` (`pentair.port`) |
| EW11 TCP host | Configurable (lab example: `10.0.0.11`) | Parent `api/app/settings.php` (`pentair.endpoint`) |

Serial defaults are a lab starting point only — **unconfirmed for all adapters**.

## Python version

Requires **Python ≥ 3.11** (`requires-python` in `pyproject.toml`). Developed and
verified on **3.12**.

## Install

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
pentairsnoop --help
# or
python -m pentairsnoop --help
```

### Decode fixtures (A2)

```bash
# NDJSON: one JSON object per message (typed fields + raw hex)
pentairsnoop decode-file ../samples/status_temps.hex
pentairsnoop decode-file ../samples/status_temps.hex --pretty

# Pretty JSON array (use --compact for one line)
pentairsnoop dump ../samples/status_temps.hex
pentairsnoop dump ../samples/log_breakdown/001_baseline_filter_on.hex --include-quarantine
```

JSON shape follows PHP `Command::toJson` (camelCase fields, `raw` as hex). Unknown
command bytes and IntelliChlor frames emit `type_name: Unknown` with `raw` hex.
Bad-checksum frames are quarantined (omitted unless `--include-quarantine`).

### Watch (A3)

Persistent TCP or serial connection with exponential backoff + jitter reconnect.
Session opens the transport **once** and streams frames (no open/close per frame).

```bash
# EW11 TCP (defaults from settings.php: 10.0.0.11:8899)
pentairsnoop watch --tcp
pentairsnoop watch --tcp 10.0.0.11:8899

# USB-RS485 (9600 8N1 starting defaults — unconfirmed for all adapters)
pentairsnoop watch --serial /dev/ttyUSB0

# Offline fixture replay (EOF ends watch)
pentairsnoop watch --file ../samples/status_temps.hex

# Only SystemStatus (0x02) and TempStatus/Info (0x08)
pentairsnoop watch --tcp --cmd 0x02 --cmd 0x08
pentairsnoop watch --file ../samples/status_temps.hex --cmd 0x02,0x08 --pretty
```

Do not run live `--tcp` against EW11 while PHP or `pentairservice` also holds port 8899.

### Capture (A4)

Timestamped **raw** + **framed** NDJSON logs for human annotation (FR-S4). Procedure:
[`docs/capture-procedure.md`](docs/capture-procedure.md). Session index:
[`samples/captures/README.md`](samples/captures/README.md) (live corpus pending
hardware — do not invent panel-success captures).

```bash
# Live serial or TCP (listen-only for baseline / panel sessions)
pentairsnoop capture --serial /dev/ttyUSB0 -o samples/captures --label idle-status
pentairsnoop capture --tcp -o samples/captures --label circuit-filter

# Format check from a fixture (not a claim of panel TX success)
pentairsnoop capture --file ../samples/status_temps.hex \
  -o /tmp/captures --label format-check --max-frames 5
```

Each session directory contains `meta.json`, `raw.ndjson`, `frames.ndjson`, and
`NOTES.md`.

Later milestone: write craft (A5).

## Tests and coverage

```bash
pytest
```

Configured for package coverage with fail-under **95%**
(`--cov=pentairsnoop --cov-fail-under=95`).

### Hex fixtures

Fixture hex files are **not** copied into this repo. Tests resolve the parent
`samples/` directory via `tests/conftest.py` (`samples_dir` fixture):

```text
pentair/samples/          ← fixtures (status_temps.hex, log_breakdown/, …)
pentair/pentairsnoop/     ← this package
```

A1+ decode tests will load those paths; A0 only documents and asserts the fixture
root when present.

## Package layout

Aligned with [03-software-design.md](../agents/plans/03-software-design.md):

| Module | Pattern | Role |
|--------|---------|------|
| `cli` | Command | CLI: `decode-file`, `dump`, `watch`, `capture` |
| `transport` | Strategy | `HexFileTransport`, `TcpTransport`, `SerialTransport` (+ reconnect) |
| `framer` | Parser | A5 + IntelliChlor sync seek, length extract, checksum (A1) |
| `messages` | Command | `SystemStatus`, `TempStatus`, `Unknown` DTOs + `to_json` |
| `registry` | Factory | Explicit cmd-byte → parser map (`MessageRegistry.default`) |
| `session` | Facade | Frame + decode; quarantine; persistent `iter_messages` / `iter_frames` |
| `capture` | Writer + Decorator | Timestamped `raw.ndjson` / `frames.ndjson`; `RecordingTransport` tee |

## A1 framing notes

- **Standard A5:** sync on `00 FF A5` / `FF 00 FF A5` (leading idle `FF` tolerated). Layout after A5: `PROTO DST SRC CMD LEN PAYLOAD CS_HI CS_LO`. Checksum is u16 BE = sum(A5..payload) mod 65536 (PHP `pentaircs`).
- **IntelliChlor:** second framer for `10 02 … CS8 … 10 03` (not truncated A5 / not header-only `100250`).
