"""A2 tests: typed decode, registry, CLI decode-file/dump, fixture goldens."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pentairsnoop.cli import main
from pentairsnoop.framer import Frame, FrameKind, Framer, a5_checksum_bytes
from pentairsnoop.messages import (
    CIRCUIT_FILTER_PUMP,
    SystemStatus,
    TempStatus,
    Unknown,
    find_a5_index,
)
from pentairsnoop.registry import (
    CMD_CLOCK_BROADCAST,
    CMD_INFO,
    CMD_SYSTEM_STATUS,
    MessageRegistry,
)
from pentairsnoop.session import QuarantinedFrame, Session
from pentairsnoop.transport import HexFileTransport

# lib/index.php contiguous hex vectors.
_INFO = "ffffffffffffffff00ffa50c0f10080d4848555a620400000000000000028a"
_LIGHT_ON = (
    "ff00ffa50c0f10021d13330000000000000021000000043b3b00003c"
    "00000004000085df000d0381"
)
_CMD_X05 = "ff00ffa5070f10050813160217060e0000012e"
_IC = bytes.fromhex("1002500000621003")

_SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def test_find_a5_with_preamble_variants() -> None:
    raw_ff00 = bytes.fromhex(_LIGHT_ON)
    assert find_a5_index(raw_ff00) == 3
    padded = bytes.fromhex(_INFO)
    assert padded[find_a5_index(padded)] == 0xA5
    short_preamble = b"\x00\xff" + raw_ff00[3:]
    assert find_a5_index(short_preamble) == 2


def test_temp_status_parse_index_php_info() -> None:
    raw = bytes.fromhex(_INFO)
    msg = TempStatus.parse(raw)
    assert msg.command == CMD_INFO
    assert msg.protocol == 0x0C
    assert msg.destination == 0x0F
    assert msg.source == 0x10
    assert msg.length == 0x0D
    # Payload 48 48 55 5a 62 04… — PHP TempStatusBytes starts at index 10 (2nd payload).
    assert msg.water == 0x48
    assert msg.air == 0x55
    assert msg.waterSet == 0x5A
    assert msg.spaSet == 0x62
    assert msg.info == 0x04
    d = json.loads(msg.to_json())
    assert d["type_name"] == "TempStatus"
    assert d["water"] == 0x48
    assert d["raw"].endswith("028a")


def test_system_status_parse_light_on() -> None:
    raw = bytes.fromhex(_LIGHT_ON)
    msg = SystemStatus.parse(raw)
    assert msg.command == CMD_SYSTEM_STATUS
    assert msg.hours == 0x13
    assert msg.minutes == 0x33
    assert msg.circuits == 0x00
    assert msg.circuitStatus.filterPump == "off"
    assert msg.circuitStatus.cleanerPump == "off"
    # StatusCommandBytes WATER/HEATER/AIR at PHP 23/24/27 → 0x3b, 0x3b, 0x3c
    assert msg.waterTemp == 0x3B
    assert msg.heaterTemp == 0x3B
    assert msg.airTemp == 0x3C
    d = json.loads(msg.to_json(pretty=True))
    assert d["circuitStatus"]["poolLight"] == "off"


def test_system_status_circuit_flags() -> None:
    # Build minimal A5 frame with FILTER_PUMP bit set in circuits byte.
    payload = bytearray(29)
    payload[0] = 10  # hours
    payload[1] = 30  # minutes
    payload[2] = CIRCUIT_FILTER_PUMP | 0x02  # filter + cleaner
    payload[14] = 80  # water
    payload[15] = 81  # heater
    payload[18] = 70  # air
    body = bytes([0xA5, 0x01, 0x0F, 0x10, 0x02, 0x1D]) + bytes(payload)
    raw = b"\xff\x00\xff" + body + a5_checksum_bytes(body)
    msg = SystemStatus.parse(raw)
    assert msg.circuitStatus.filterPump == "on"
    assert msg.circuitStatus.cleanerPump == "on"
    assert msg.circuitStatus.waterFeature == "off"
    assert msg.waterTemp == 80
    assert msg.airTemp == 70


def test_registry_default_dispatches() -> None:
    reg = MessageRegistry.default()
    info = reg.decode_a5(bytes.fromhex(_INFO))
    assert isinstance(info, TempStatus)
    status = reg.decode_a5(bytes.fromhex(_LIGHT_ON))
    assert isinstance(status, SystemStatus)
    clock = reg.decode_a5(bytes.fromhex(_CMD_X05))
    assert isinstance(clock, Unknown)
    assert clock.command == CMD_CLOCK_BROADCAST
    assert clock.raw.hex().startswith("ff00ffa5")


def test_unknown_intellichlor_and_json() -> None:
    u = Unknown.parse(_IC)
    assert u.type_name == "Unknown"
    assert u.command == 0
    d = u.to_dict()
    assert d["raw"] == _IC.hex()


def test_quarantine_bad_checksum() -> None:
    good = bytes.fromhex(_LIGHT_ON)
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    frame = Frame(kind=FrameKind.A5, raw=bytes(bad), checksum_ok=False)
    sess = Session(HexFileTransport(Path("/dev/null")))
    item = sess.decode_frame(frame)
    assert isinstance(item, QuarantinedFrame)
    assert item.to_dict()["type_name"] == "Quarantined"


def test_session_decode_mixed_stream() -> None:
    stream = bytes.fromhex(_INFO) + _IC + bytes.fromhex(_CMD_X05)
    fr = Framer()
    frames = fr.feed(stream)
    sess = Session(HexFileTransport(Path("/dev/null")))
    decoded = [sess.decode_frame(f) for f in frames]
    assert isinstance(decoded[0], TempStatus)
    assert isinstance(decoded[1], Unknown)
    assert decoded[1].raw == _IC
    assert isinstance(decoded[2], Unknown)
    assert decoded[2].command == 0x05


@pytest.mark.skipif(not (_SAMPLES / "status_temps.hex").is_file(), reason="no samples")
def test_decode_status_temps_fixture(samples_dir: Path) -> None:
    path = samples_dir / "status_temps.hex"
    session = Session(HexFileTransport(path))
    items = session.read_messages()
    typed = [i for i in items if isinstance(i, (SystemStatus, TempStatus))]
    assert any(isinstance(i, TempStatus) for i in typed)
    temps = next(i for i in items if isinstance(i, TempStatus))
    # Fixture payload 48 48 56 5a 62 04… — PHP indices (2nd payload = water).
    assert temps.water == 0x48
    assert temps.air == 0x56
    assert temps.waterSet == 0x5A
    assert temps.spaSet == 0x62
    assert temps.info == 0x04
    blob = json.loads(temps.to_json())
    assert set(blob) >= {
        "water",
        "air",
        "waterSet",
        "spaSet",
        "info",
        "raw",
        "command",
        "type_name",
    }


@pytest.mark.skipif(
    not (_SAMPLES / "log_breakdown" / "001_baseline_filter_on.hex").is_file(),
    reason="no log_breakdown",
)
def test_decode_log_breakdown_has_typed_status(samples_dir: Path) -> None:
    path = samples_dir / "log_breakdown" / "001_baseline_filter_on.hex"
    items = Session(HexFileTransport(path)).read_messages()
    statuses = [i for i in items if isinstance(i, SystemStatus)]
    temps = [i for i in items if isinstance(i, TempStatus)]
    unknowns = [i for i in items if isinstance(i, Unknown)]
    assert statuses or temps
    for u in unknowns:
        assert len(u.raw) > 0
        assert isinstance(u.to_dict()["raw"], str)


def test_cli_decode_file_and_dump(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "mix.hex"
    path.write_text(_INFO + _CMD_X05, encoding="utf-8")
    assert main(["decode-file", str(path)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    first = json.loads(out[0])
    second = json.loads(out[1])
    assert first["type_name"] == "TempStatus"
    assert first["water"] == 0x48
    assert second["type_name"] == "Unknown"
    assert second["command"] == 0x05
    assert "raw" in second

    assert main(["dump", str(path)]) == 0
    arr = json.loads(capsys.readouterr().out)
    assert isinstance(arr, list)
    assert arr[0]["type_name"] == "TempStatus"

    assert main(["dump", str(path), "--compact"]) == 0
    compact = capsys.readouterr().out.strip()
    assert "\n" not in compact


def test_cli_decode_file_pretty_and_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "one.hex"
    path.write_text(_LIGHT_ON, encoding="utf-8")
    assert main(["decode-file", str(path), "--pretty"]) == 0
    text = capsys.readouterr().out
    assert '"type_name": "SystemStatus"' in text
    assert main(["decode-file", str(tmp_path / "nope.hex")]) == 1
    assert main(["dump", str(tmp_path / "nope.hex")]) == 1


def test_cli_quarantine_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = bytes.fromhex(_LIGHT_ON)
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    path = tmp_path / "bad.hex"
    path.write_text(bytes(bad).hex(), encoding="utf-8")
    assert main(["dump", str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(["dump", str(path), "--include-quarantine"]) == 0
    arr = json.loads(capsys.readouterr().out)
    assert arr[0]["type_name"] == "Quarantined"


def test_find_a5_skips_orphan_a5_byte() -> None:
    # Lone A5 then a real sync — orphan must not win.
    raw = b"\xa5" + bytes.fromhex(_LIGHT_ON)
    assert find_a5_index(raw) == 1 + 3


def test_registry_decode_a5_truncated() -> None:
    reg = MessageRegistry.default()
    msg = reg.decode_a5(b"\xff\x00\xff\xa5\x01")
    assert isinstance(msg, Unknown)


def test_message_parse_header_short_raises() -> None:
    raw = b"\xff\x00\xff\xa5\x01"
    with pytest.raises(ValueError):
        SystemStatus.parse(raw)
