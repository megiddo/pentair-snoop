"""Pattern: Catalog + Data Transfer Object — capability walk definitions for bulk capture.

The default catalog freezes plan §3.2 from ``08-capability-capture-mode.md``.
IntelliChlor / salt-cell entries are **excluded** from ``DEFAULT_CATALOG`` (CR-9);
optional opt-in is reserved for a future ``--include-optional intellichlor`` flag
in the A2.1 ``capture-capabilities`` CLI (not implemented here).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# Reserved id prefix — never present in the default walk (see §3.3).
INTELLICHLOR_ID_PREFIX = "intellichlor"

CAPABILITY_FORMAT_VERSION = 1


@dataclass(frozen=True)
class Capability:
    """Pattern: Data Transfer Object — one interactive capability catalog entry.

    Fields match the A2.0 schema freeze (plan §3.1):

    * ``id`` — stable snake_case string (paths + JSON).
    * ``group`` — ordering / walk hint (baseline, circuit, heat, …).
    * ``prompt`` — operator-facing text shown during the walk.
    * ``expected_traffic`` — optional RE hypothesis string (not a capture DoD).
    * ``skip_ok`` — ``True`` when skipping is expected on this panel/remote.
    """

    id: str
    group: str
    prompt: str
    skip_ok: bool
    expected_traffic: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON snapshot / session ``catalog_snapshot.json``."""
        return asdict(self)


def _cap(
    id: str,
    group: str,
    prompt: str,
    *,
    skip_ok: bool,
    expected_traffic: str | None = None,
) -> Capability:
    return Capability(
        id=id,
        group=group,
        prompt=prompt,
        skip_ok=skip_ok,
        expected_traffic=expected_traffic,
    )


# Built-in default walk — exactly 17 entries from plan §3.2 (IntelliChlor excluded).
DEFAULT_CATALOG: tuple[Capability, ...] = (
    _cap(
        "idle_baseline",
        "baseline",
        "Listen only for ~60–90s with **no** remote presses. "
        "Press **d** when ready to end the baseline window.",
        skip_ok=False,
        expected_traffic=(
            "Periodic `0x02` status, `0x08` temps; optional `0x05` clock. "
            "Establishes quiet background."
        ),
    ),
    _cap(
        "spa_on_off",
        "circuit",
        "On the wireless remote: turn **spa ON**, wait ~5s, turn **spa OFF**. "
        "Press **d** when finished, or **s** to skip.",
        skip_ok=False,
        expected_traffic=(
            "Remote→panel `0x86` (circuit≈`0x01`) + `0x01` ACK; "
            "status `0x02` bit changes."
        ),
    ),
    _cap(
        "spa_light_on_off",
        "circuit",
        "Turn **spa light ON**, wait ~5s, turn **spa light OFF**. [d=Done / s=Skip]",
        skip_ok=False,
        expected_traffic=(
            "`0x86` (local SPA_LIGHT=`0x05`) + ACK; status bit `SPA_LIGHT` `0x08`."
        ),
    ),
    _cap(
        "pool_light_on_off",
        "circuit",
        "Turn **pool light ON**, wait ~5s, turn **pool light OFF**. [d=Done / s=Skip]",
        skip_ok=False,
        expected_traffic=(
            "`0x86` (local POOL_LIGHT=`0x06`) + ACK; status bit `POOL_LIGHT` `0x10`."
        ),
    ),
    _cap(
        "water_feature_on_off",
        "circuit",
        "Turn **water feature / waterfall ON**, wait ~5s, turn **OFF**. "
        "Use whatever the remote labels this circuit. [d/s]",
        skip_ok=False,
        expected_traffic=(
            "`0x86` (WATER_FEATURE=`0x04`); status bit `0x04`. API name: waterfall."
        ),
    ),
    _cap(
        "cleaner_on_off",
        "circuit",
        "Turn **cleaner ON**, wait ~5s, turn **cleaner OFF**. [d/s]",
        skip_ok=True,
        expected_traffic=(
            "`0x86` (CLEANER=`0x03`); status `CLEANER_PUMP` `0x02`. "
            "Sample: `002_cleaner_on.hex`."
        ),
    ),
    _cap(
        "filter_pump_on_off",
        "circuit",
        "Turn **filter / pool pump ON**, wait ~5s, turn **OFF** "
        "(or the remote’s equivalent body/filter circuit). "
        "Note the **exact remote label** in NOTES if it differs. [d/s]",
        skip_ok=False,
        expected_traffic=(
            "`0x86` — **ID contested** (local `POOL=0x02` vs external body `0x06`); "
            "status often `FILTER_PUMP` `0x20`. Highest-value circuit ID capture."
        ),
    ),
    _cap(
        "heat_boost_on_off",
        "circuit",
        "If the remote has **heat boost** (or spa boost): toggle ON then OFF. "
        "Otherwise **s**. [d/s]",
        skip_ok=True,
        expected_traffic=(
            "`0x86` with HEAT_BOOST=`0x85` (unconfirmed on this panel)."
        ),
    ),
    _cap(
        "spa_temp_set",
        "heat",
        "Set the **spa temperature to 96°F**, wait until the display settles, "
        "then set it to **90°F**. [d/s]",
        skip_ok=False,
        expected_traffic=(
            "`0x88` HeatChange (spa setpoint bytes) + ACK `0x01`/`0x88`; "
            "later `0x08` spa set field. Sample: `007_spa_temp_set_93.hex`."
        ),
    ),
    _cap(
        "pool_temp_set",
        "heat",
        "Set the **pool temperature to 85°F**, wait, then set it to **80°F** "
        "(or two distinct values the remote allows). [d/s]",
        skip_ok=False,
        expected_traffic=(
            "`0x88` pool setpoint + ACK; `0x08` water/pool set. "
            "Sample: `006_pool_temp_set_85.hex`."
        ),
    ),
    _cap(
        "spa_heat_mode",
        "heat",
        "Change **spa heat mode** through available remote options "
        "(e.g. Off → Heater → Solar/HeatPump if present). "
        "Return to a known mode when done. [d/s]",
        skip_ok=True,
        expected_traffic=(
            "Often packed into `0x88` mode byte; possible separate `0xA8` (njsPC) — "
            "**unknown** on this firmware. Watch `0x08` info/mode and `0x02` heater fields."
        ),
    ),
    _cap(
        "pool_heat_mode",
        "heat",
        "Change **pool heat mode** through available options; "
        "return to a known mode. [d/s]",
        skip_ok=True,
        expected_traffic=(
            "Same as spa heat mode; mode nibble may share `0x88` payload."
        ),
    ),
    _cap(
        "spa_heat_enable",
        "heat",
        "If distinct from mode: turn **spa heat ON** then **OFF** "
        "(or enable/disable heater for spa). Else **s**. [d/s]",
        skip_ok=True,
        expected_traffic=(
            "May look like `0x88` and/or circuit bits; sample `008_spa_heat_on.hex`."
        ),
    ),
    _cap(
        "filter_schedule",
        "schedule",
        "If the remote/panel UI can edit **filter pump calendar / schedule**, "
        "make one visible change (e.g. shift a start time by 15 minutes), "
        "then revert if easy. If no schedule UI, **s**. [d/s]",
        skip_ok=True,
        expected_traffic=(
            "**Unknown** command set on classic EasyTouch RS485 (not in local enum). "
            "Capture whatever TX appears; mark NOTES with UI path used."
        ),
    ),
    _cap(
        "set_clock",
        "clock",
        "Set the panel **clock date and time** to a deliberate wrong time "
        "(e.g. +1 hour), wait ~10s, then set it back to approximately correct. [d/s]",
        skip_ok=True,
        expected_traffic=(
            "May produce write(s) then `0x05` clock broadcast and/or time fields "
            "in `0x02`. Local `CLOCK_BROADCAST` parser unused — raw frames still valuable."
        ),
    ),
    _cap(
        "aux_or_unknown_circuit",
        "circuit",
        "If the remote has any **other** labeled circuit/feature not already exercised "
        "(AUX, blower, jet, etc.), toggle ON/OFF once and write the remote label "
        "into NOTES. Else **s**. [d/s]",
        skip_ok=True,
        expected_traffic="Unknown `0x86` circuit id — do not assume enum.",
    ),
    _cap(
        "idle_after",
        "baseline",
        "Final listen-only window (~30–60s), no presses. [d]",
        skip_ok=False,
        expected_traffic=(
            "Confirms bus returns to steady status cadence after exercises."
        ),
    ),
)

# Ids known to be skip_ok=True in plan §3.2 (for tests / reviewers).
SKIP_OK_IDS: frozenset[str] = frozenset(
    c.id for c in DEFAULT_CATALOG if c.skip_ok
)

# Ids known to be skip_ok=False (core seed).
CORE_IDS: frozenset[str] = frozenset(
    c.id for c in DEFAULT_CATALOG if not c.skip_ok
)


def default_catalog_json_path() -> Path:
    """Path to the checked-in JSON snapshot of ``DEFAULT_CATALOG`` (if packaged)."""
    return Path(__file__).resolve().parent / "data" / "default_catalog.json"


def _capability_from_mapping(raw: dict[str, Any]) -> Capability:
    """Build a ``Capability`` from a JSON object; validate required fields."""
    missing = [k for k in ("id", "group", "prompt", "skip_ok") if k not in raw]
    if missing:
        raise ValueError(f"catalog entry missing required fields: {missing}")
    expected = raw.get("expected_traffic")
    if expected is not None and not isinstance(expected, str):
        raise ValueError(
            f"expected_traffic must be a string or null for id={raw.get('id')!r}"
        )
    skip_ok = raw["skip_ok"]
    if not isinstance(skip_ok, bool):
        raise ValueError(f"skip_ok must be a bool for id={raw.get('id')!r}")
    return Capability(
        id=str(raw["id"]),
        group=str(raw["group"]),
        prompt=str(raw["prompt"]),
        skip_ok=skip_ok,
        expected_traffic=expected,
    )


def exclude_intellichlor(entries: Iterable[Capability]) -> list[Capability]:
    """Filter out IntelliChlor / salt-cell reserved ids (default catalog policy)."""
    return [
        c
        for c in entries
        if not c.id.lower().startswith(INTELLICHLOR_ID_PREFIX)
    ]


def load_catalog(
    path: Path | str | None = None,
    *,
    exclude_intellichlor_ids: bool = True,
) -> list[Capability]:
    """Load the capability catalog.

    Pattern: Factory Method — built-in default or JSON file override.

    * ``path is None`` → return a copy of ``DEFAULT_CATALOG``.
    * ``path`` set → load JSON array (or ``{"capabilities": [...]}`` object).

    When ``exclude_intellichlor_ids`` is true (default), any entry whose id starts
    with ``intellichlor`` is dropped so accidental overrides cannot reintroduce
    salt-cell campaigns into the default walk policy.
    """
    if path is None:
        entries = list(DEFAULT_CATALOG)
    else:
        entries = _load_catalog_file(Path(path))
    if exclude_intellichlor_ids:
        entries = exclude_intellichlor(entries)
    return entries


def _load_catalog_file(path: Path) -> list[Capability]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        items = data.get("capabilities")
        if items is None:
            raise ValueError(
                "catalog JSON object must contain a 'capabilities' array"
            )
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("catalog JSON must be an array or object with capabilities")
    if not isinstance(items, list):
        raise ValueError("capabilities must be a JSON array")
    return [_capability_from_mapping(item) for item in items]


def catalog_to_jsonable(entries: Sequence[Capability]) -> list[dict[str, Any]]:
    """Serialize catalog entries for snapshots / docs."""
    return [c.to_dict() for c in entries]


def write_catalog_json(
    path: Path | str,
    entries: Sequence[Capability] | None = None,
) -> None:
    """Write a reviewable JSON snapshot (array of capability objects)."""
    caps = list(DEFAULT_CATALOG) if entries is None else list(entries)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(catalog_to_jsonable(caps), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_by_id(
    catalog: Sequence[Capability],
    capability_id: str,
) -> Capability | None:
    """Return the first entry with ``id == capability_id``, or ``None``."""
    for entry in catalog:
        if entry.id == capability_id:
            return entry
    return None


def filter_catalog(
    catalog: Sequence[Capability],
    *,
    only: Sequence[str] | None = None,
    skip_ids: Sequence[str] | None = None,
    from_id: str | None = None,
) -> list[Capability]:
    """Apply future CLI selection flags (``--only``, ``--skip-id``, ``--from-id``).

    Order of the input catalog is preserved. ``--only`` keeps listed ids in
    catalog order (not flag order). ``--from-id`` drops entries before the match.
    ``--skip-id`` removes ids without prompting (A2.1 will mark them skipped).
    """
    entries = list(catalog)
    if from_id is not None:
        try:
            start = next(i for i, c in enumerate(entries) if c.id == from_id)
        except StopIteration as exc:
            raise ValueError(f"--from-id not in catalog: {from_id!r}") from exc
        entries = entries[start:]
    if only is not None:
        wanted = set(only)
        entries = [c for c in entries if c.id in wanted]
    if skip_ids:
        drop = set(skip_ids)
        entries = [c for c in entries if c.id not in drop]
    return entries
