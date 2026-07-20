# Capability-guided capture (schema freeze)

**Status:** A2.0 catalog + schema freeze. Interactive `capture-capabilities`
CLI lands in A2.1 — this document freezes the catalog, output layout, and CLI
surface so the corpus format is reviewable **without** live gear.

**Plan:** parent `agents/plans/08-capability-capture-mode.md`  
**Catalog code:** `pentairsnoop.catalog` (`DEFAULT_CATALOG`, 17 entries)  
**JSON snapshot:** `src/pentairsnoop/data/default_catalog.json`

## Catalog

Each entry has:

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | Stable snake_case string (paths + JSON) |
| `group` | yes | Walk hint: `baseline`, `circuit`, `heat`, `schedule`, `clock` |
| `prompt` | yes | Operator-facing text |
| `expected_traffic` | optional | RE hypothesis string — **not** a capture DoD |
| `skip_ok` | yes | `true` = skip expected; `false` = core seed |

Default walk length: **17**. Countable via:

```bash
python -c "from pentairsnoop.catalog import DEFAULT_CATALOG; print(len(DEFAULT_CATALOG))"
# or: jq 'length' src/pentairsnoop/data/default_catalog.json
```

### IntelliChlor exclusion

Salt-cell / IntelliChlor campaigns are **out of the default catalog** (install
belief: no IntelliChlor). Reserved id prefix: `intellichlor_*`. A future
`--include-optional intellichlor` flag may opt in; default remains off.

## Output layout (hybrid C)

Capability sessions extend the A4 `capture` tree:

```text
samples/captures/YYYYMMDDTHHMMSSZ_capability-guided/
  meta.json                 # session-level metadata
  catalog_snapshot.json     # ids + prompts used this run (frozen)
  index.jsonl               # one line per capability attempt (ordered)
  raw.ndjson                # entire session raw chunks (root only)
  frames.ndjson             # entire session frames (+ capability_id when armed)
  NOTES.md                  # operator session notes
  by_capability/
    01_idle_baseline/
      meta.json
      frames.ndjson         # frames whose capability_id matched
      NOTES.md
    02_spa_on_off/
      …
```

**Slice policy (hybrid C):** continuous `raw.ndjson` / `frames.ndjson` at the
session root **plus** per-capability `frames.ndjson` slices under
`by_capability/<nn>_<id>/`. Raw bytes stay at the root only.

### `meta.json` (session) example

```json
{
  "format_version": 1,
  "capability_format_version": 1,
  "mode": "capability_guided",
  "started_at": "2026-07-20T16:00:00.000000Z",
  "ended_at": "2026-07-20T16:45:00.000000Z",
  "label": "capability-guided",
  "source": {"kind": "tcp", "endpoint": "10.0.0.11:8899"},
  "tool": "pentairsnoop",
  "tool_version": "0.1.0",
  "listen_only": true,
  "catalog_count": 17,
  "capabilities_done": 12,
  "capabilities_skipped": 4,
  "capabilities_not_run": 1,
  "raw_chunks": 0,
  "frames": 0,
  "notes": "Bulk capability capture; no auto TX."
}
```

### `index.jsonl` (one object per capability attempt)

```json
{
  "seq": 3,
  "capability_id": "spa_temp_set",
  "status": "done",
  "started_at": "…Z",
  "ended_at": "…Z",
  "frame_seq_start": 140,
  "frame_seq_end": 188,
  "frames": 48,
  "dir": "by_capability/03_spa_temp_set",
  "skip_ok": false,
  "attempt": 1
}
```

`status`: `done` | `skipped` | `not_run` | `error`.

### Frame line extension

When a capability window is armed, frame records add `capability_id`:

```json
{"t":"…Z","seq":12,"kind":"a5","checksum_ok":true,"raw":"…","capability_id":"spa_on_off"}
```

## CLI surface (A2.1 — documented now)

Working name: `capture-capabilities` (sibling of `capture`; not yet wired).

```bash
pentairsnoop capture-capabilities --tcp
pentairsnoop capture-capabilities --tcp 10.0.0.11:8899
pentairsnoop capture-capabilities --serial /dev/ttyUSB0
pentairsnoop capture-capabilities --file ../samples/status_temps.hex

pentairsnoop capture-capabilities --tcp \
  -o samples/captures \
  --label capability-guided \
  --only spa_on_off,spa_temp_set \
  --skip-id filter_schedule,set_clock \
  --from-id spa_temp_set \
  --arm-on-prompt
```

| Flag | Purpose |
|------|---------|
| `--tcp` / `--serial` / `--file` | Same mutually exclusive transport as `capture` |
| `-o` / `--out` | Parent captures directory |
| `--label` | Session dirname suffix (default `capability-guided`) |
| `--session-dir` | Explicit root path |
| `--only id,id` | Run subset (order preserved from catalog) |
| `--skip-id id,id` | Pre-mark skip without prompting |
| `--from-id id` | Start walk at id (resume-friendly) |
| `--catalog path` | Optional JSON override of built-in catalog |
| `--arm-on-prompt` / `--arm-on-key` | State-machine variant |
| `--include-optional intellichlor` | Explicit opt-in only; default off |

### Listen-only rule (hard)

Capability mode must **not** call transport write / craft `--send`. No `--send`
flag on this subcommand. The operator exercises equipment via the **wireless
remote**; the tool only listens, prompts, and labels.

### Keys (A2.1)

| Key | Action |
|-----|--------|
| `d` | Done — keep labeled window |
| `s` | Skip — mark skipped |
| `q` | Quit session early |

## Haul-back

From the remote SSH host after a session:

```bash
tar czf ~/capability-guided-YYYYMMDD.tgz -C samples/captures YYYYMMDDTHHMMSSZ_capability-guided
# scp/rsync tarball to dev box → unpack under pentairsnoop/samples/captures/
```

See also: [capture-procedure.md](capture-procedure.md) (capability-guided ops subsection).
