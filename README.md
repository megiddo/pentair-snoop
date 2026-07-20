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

Subcommands (`decode-file`, `watch`, `capture`, …) arrive in later Track A milestones.

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
| `cli` | Command | CLI entry / future subcommands |
| `transport` | Strategy | Serial / TCP / hex-file I/O (stub) |
| `framer` | Parser | Sync seek + frame extract (stub → A1) |
| `messages` | Command | Typed message DTOs (stub → A2) |
| `registry` | Factory | cmd-byte → parser map (stub → A2) |
| `session` | Facade | Thin session over transport + framer |

## Out of scope for A0

Framer/checksum, decode, live transports, capture, and write craft are Track A1+.
