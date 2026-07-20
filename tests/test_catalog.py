"""Unit tests for the A2.0 capability catalog (schema freeze)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pentairsnoop.catalog import (
    CAPABILITY_FORMAT_VERSION,
    CORE_IDS,
    DEFAULT_CATALOG,
    INTELLICHLOR_ID_PREFIX,
    SKIP_OK_IDS,
    Capability,
    catalog_to_jsonable,
    default_catalog_json_path,
    exclude_intellichlor,
    filter_catalog,
    get_by_id,
    load_catalog,
    write_catalog_json,
)

# Plan §3.2 skip_ok=yes ids (must match DEFAULT_CATALOG).
EXPECTED_SKIP_OK = frozenset(
    {
        "cleaner_on_off",
        "heat_boost_on_off",
        "spa_heat_mode",
        "pool_heat_mode",
        "spa_heat_enable",
        "filter_schedule",
        "set_clock",
        "aux_or_unknown_circuit",
    }
)

EXPECTED_CORE = frozenset(
    {
        "idle_baseline",
        "spa_on_off",
        "spa_light_on_off",
        "pool_light_on_off",
        "water_feature_on_off",
        "filter_pump_on_off",
        "spa_temp_set",
        "pool_temp_set",
        "idle_after",
    }
)

EXPECTED_ORDER = [
    "idle_baseline",
    "spa_on_off",
    "spa_light_on_off",
    "pool_light_on_off",
    "water_feature_on_off",
    "cleaner_on_off",
    "filter_pump_on_off",
    "heat_boost_on_off",
    "spa_temp_set",
    "pool_temp_set",
    "spa_heat_mode",
    "pool_heat_mode",
    "spa_heat_enable",
    "filter_schedule",
    "set_clock",
    "aux_or_unknown_circuit",
    "idle_after",
]


def test_default_catalog_count_and_unique_ids() -> None:
    assert len(DEFAULT_CATALOG) == 17
    ids = [c.id for c in DEFAULT_CATALOG]
    assert len(ids) == len(set(ids))
    assert ids == EXPECTED_ORDER


def test_required_fields_present() -> None:
    for cap in DEFAULT_CATALOG:
        assert cap.id
        assert cap.group
        assert cap.prompt
        assert isinstance(cap.skip_ok, bool)
        assert cap.expected_traffic is None or isinstance(cap.expected_traffic, str)


def test_no_intellichlor_in_default() -> None:
    for cap in DEFAULT_CATALOG:
        assert not cap.id.lower().startswith(INTELLICHLOR_ID_PREFIX)
    loaded = load_catalog()
    assert all(not c.id.lower().startswith(INTELLICHLOR_ID_PREFIX) for c in loaded)


def test_skip_ok_flags_match_plan() -> None:
    assert SKIP_OK_IDS == EXPECTED_SKIP_OK
    assert CORE_IDS == EXPECTED_CORE
    for cap in DEFAULT_CATALOG:
        if cap.id in EXPECTED_SKIP_OK:
            assert cap.skip_ok is True
        else:
            assert cap.skip_ok is False


def test_groups_are_known() -> None:
    allowed = {"baseline", "circuit", "heat", "schedule", "clock"}
    assert {c.group for c in DEFAULT_CATALOG} <= allowed


def test_capability_to_dict_roundtrip_fields() -> None:
    cap = DEFAULT_CATALOG[0]
    d = cap.to_dict()
    assert d["id"] == "idle_baseline"
    assert d["skip_ok"] is False
    assert "expected_traffic" in d


def test_load_catalog_builtin() -> None:
    caps = load_catalog()
    assert len(caps) == 17
    assert caps[0].id == "idle_baseline"
    # Mutable copy — mutating list must not alter DEFAULT_CATALOG tuple.
    caps.clear()
    assert len(DEFAULT_CATALOG) == 17


def test_json_load_override(tmp_path: Path) -> None:
    path = tmp_path / "override.json"
    payload = [
        {
            "id": "idle_baseline",
            "group": "baseline",
            "prompt": "custom baseline",
            "skip_ok": False,
            "expected_traffic": None,
        },
        {
            "id": "spa_on_off",
            "group": "circuit",
            "prompt": "custom spa",
            "skip_ok": False,
        },
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    caps = load_catalog(path)
    assert len(caps) == 2
    assert caps[0].prompt == "custom baseline"
    assert caps[1].expected_traffic is None


def test_json_load_object_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "wrapped.json"
    path.write_text(
        json.dumps(
            {
                "capabilities": [
                    {
                        "id": "set_clock",
                        "group": "clock",
                        "prompt": "clock",
                        "skip_ok": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    caps = load_catalog(path)
    assert len(caps) == 1
    assert caps[0].id == "set_clock"
    assert caps[0].skip_ok is True


def test_json_override_strips_intellichlor_by_default(tmp_path: Path) -> None:
    path = tmp_path / "with_ic.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "spa_on_off",
                    "group": "circuit",
                    "prompt": "spa",
                    "skip_ok": False,
                },
                {
                    "id": "intellichlor_status",
                    "group": "chlor",
                    "prompt": "should be filtered",
                    "skip_ok": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    caps = load_catalog(path)
    assert [c.id for c in caps] == ["spa_on_off"]
    caps_keep = load_catalog(path, exclude_intellichlor_ids=False)
    assert [c.id for c in caps_keep] == ["spa_on_off", "intellichlor_status"]


def test_exclude_intellichlor_helper() -> None:
    mixed = list(DEFAULT_CATALOG) + [
        Capability(
            id="intellichlor_probe",
            group="chlor",
            prompt="nope",
            skip_ok=True,
        )
    ]
    filtered = exclude_intellichlor(mixed)
    assert len(filtered) == 17
    assert all(not c.id.startswith(INTELLICHLOR_ID_PREFIX) for c in filtered)


def test_load_catalog_missing_fields(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"id": "x"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required fields"):
        load_catalog(path)


def test_load_catalog_bad_skip_ok(tmp_path: Path) -> None:
    path = tmp_path / "bad_skip.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "x",
                    "group": "g",
                    "prompt": "p",
                    "skip_ok": "yes",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="skip_ok must be a bool"):
        load_catalog(path)


def test_load_catalog_bad_expected_traffic(tmp_path: Path) -> None:
    path = tmp_path / "bad_et.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "x",
                    "group": "g",
                    "prompt": "p",
                    "skip_ok": False,
                    "expected_traffic": 123,
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected_traffic"):
        load_catalog(path)


def test_load_catalog_invalid_root(tmp_path: Path) -> None:
    path = tmp_path / "scalar.json"
    path.write_text(json.dumps("nope"), encoding="utf-8")
    with pytest.raises(ValueError, match="array or object"):
        load_catalog(path)


def test_load_catalog_object_without_capabilities(tmp_path: Path) -> None:
    path = tmp_path / "empty_obj.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="capabilities"):
        load_catalog(path)


def test_load_catalog_capabilities_not_array(tmp_path: Path) -> None:
    path = tmp_path / "caps_obj.json"
    path.write_text(json.dumps({"capabilities": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="capabilities must be a JSON array"):
        load_catalog(path)


def test_checked_in_json_snapshot_matches_default() -> None:
    path = default_catalog_json_path()
    assert path.is_file(), f"missing snapshot: {path}"
    from_file = load_catalog(path)
    assert catalog_to_jsonable(from_file) == catalog_to_jsonable(DEFAULT_CATALOG)


def test_write_catalog_json(tmp_path: Path) -> None:
    out = tmp_path / "out" / "cat.json"
    write_catalog_json(out)
    assert json.loads(out.read_text(encoding="utf-8")) == catalog_to_jsonable(
        DEFAULT_CATALOG
    )
    write_catalog_json(out, entries=DEFAULT_CATALOG[:2])
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 2


def test_get_by_id() -> None:
    assert get_by_id(DEFAULT_CATALOG, "spa_temp_set") is not None
    assert get_by_id(DEFAULT_CATALOG, "spa_temp_set").group == "heat"
    assert get_by_id(DEFAULT_CATALOG, "missing") is None


def test_filter_catalog_only_from_skip() -> None:
    only = filter_catalog(
        DEFAULT_CATALOG,
        only=["spa_temp_set", "idle_baseline", "spa_on_off"],
    )
    assert [c.id for c in only] == ["idle_baseline", "spa_on_off", "spa_temp_set"]

    from_mid = filter_catalog(DEFAULT_CATALOG, from_id="spa_temp_set")
    assert from_mid[0].id == "spa_temp_set"
    assert from_mid[-1].id == "idle_after"

    skipped = filter_catalog(
        DEFAULT_CATALOG,
        skip_ids=["filter_schedule", "set_clock"],
    )
    assert "filter_schedule" not in {c.id for c in skipped}
    assert len(skipped) == 15


def test_filter_catalog_from_id_missing() -> None:
    with pytest.raises(ValueError, match="--from-id"):
        filter_catalog(DEFAULT_CATALOG, from_id="not_a_cap")


def test_capability_format_version() -> None:
    assert CAPABILITY_FORMAT_VERSION == 1
