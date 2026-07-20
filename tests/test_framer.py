"""A1 tests: framer, checksum, HexFileTransport, fixture framing."""

from __future__ import annotations

from pathlib import Path

import pytest

from pentairsnoop.framer import (
    FrameKind,
    Framer,
    a5_checksum_bytes,
    intellichlor_checksum,
    pentair_checksum,
    verify_a5_frame,
    verify_intellichlor_frame,
)
from pentairsnoop.session import Session
from pentairsnoop.transport import HexFileTransport, parse_hex_bytes

# Known-good vectors from parent lib/index.php (contiguous hex).
_INDEX_VECTORS = {
    "cmd_x05": "ff00ffa5070f10050813160217060e0000012e",
    "info": "ffffffffffffffff00ffa50c0f10080d4848555a620400000000000000028a",
    "set_temp": "ff00ffa507102088042b60050001f8",
    "set_temp_ack": "ff00ffa50c2010010188016b",
    "light_on": (
        "ff00ffa50c0f10021d13330000000000000021000000043b3b00003c"
        "00000004000085df000d0381"
    ),
}

# PHP ShortInfo sample + Josh Block IntelliChlor example (research §3.4).
_IC_PHP = bytes.fromhex("1002500000621003")
_IC_BLOCK = bytes.fromhex("100250000000621003")


def _a5_body_from_hex(hx: str) -> bytes:
    raw = bytes.fromhex(hx)
    a5 = raw.index(0xA5)
    while a5 > 0 and not (
        (a5 >= 2 and raw[a5 - 2 : a5] == b"\x00\xff")
        or (a5 >= 3 and raw[a5 - 3 : a5] == b"\xff\x00\xff")
    ):
        a5 = raw.index(0xA5, a5 + 1)
    return raw[a5:]


@pytest.mark.parametrize("name,hx", list(_INDEX_VECTORS.items()))
def test_pentair_checksum_matches_php_vectors(name: str, hx: str) -> None:
    frame = _a5_body_from_hex(hx)
    body, trailer = frame[:-2], frame[-2:]
    assert pentair_checksum(body) == (trailer[0] << 8) | trailer[1]
    assert verify_a5_frame(frame) is True
    assert a5_checksum_bytes(body) == trailer
    _ = name


def test_checksum_rejects_corruption() -> None:
    frame = _a5_body_from_hex(_INDEX_VECTORS["set_temp"])
    bad = bytearray(frame)
    bad[-1] ^= 0xFF
    assert verify_a5_frame(bytes(bad)) is False


def test_framer_standard_a5_with_idle_padding() -> None:
    fr = Framer()
    raw = bytes.fromhex(_INDEX_VECTORS["info"])
    frames = fr.feed(raw)
    assert len(frames) == 1
    assert frames[0].kind is FrameKind.A5
    assert frames[0].checksum_ok is True
    assert frames[0].raw.endswith(bytes.fromhex("028a"))


def test_framer_sync_00_ff_a5_without_ff00_preamble() -> None:
    body = _a5_body_from_hex(_INDEX_VECTORS["set_temp"])
    stream = b"\x00\xff" + body
    frames = Framer().feed(stream)
    assert len(frames) == 1
    assert frames[0].checksum_ok is True


def test_framer_streaming_chunks() -> None:
    raw = bytes.fromhex(_INDEX_VECTORS["light_on"])
    fr = Framer()
    assert fr.feed(raw[:5]) == []
    assert fr.feed(raw[5:17]) == []
    frames = fr.feed(raw[17:])
    assert len(frames) == 1
    assert frames[0].checksum_ok is True


def test_intellichlor_checksum_and_verify() -> None:
    for sample in (_IC_PHP, _IC_BLOCK):
        data = sample[2:-3]
        assert sample[-3] == intellichlor_checksum(data)
        assert verify_intellichlor_frame(sample) is True


def test_framer_intellichlor_not_treated_as_a5() -> None:
    fr = Framer()
    frames = fr.feed(_IC_PHP + _IC_BLOCK)
    assert len(frames) == 2
    assert all(f.kind is FrameKind.INTELLICHLOR for f in frames)
    assert all(f.checksum_ok for f in frames)
    assert frames[0].raw == _IC_PHP


def test_framer_mixed_a5_and_intellichlor() -> None:
    a5 = bytes.fromhex(_INDEX_VECTORS["set_temp_ack"])
    stream = b"\xff\xff" + a5 + _IC_PHP + bytes.fromhex(_INDEX_VECTORS["cmd_x05"])
    frames = Framer().feed(stream)
    assert [f.kind for f in frames] == [
        FrameKind.A5,
        FrameKind.INTELLICHLOR,
        FrameKind.A5,
    ]
    assert all(f.checksum_ok for f in frames)


def test_framer_noise_then_frame() -> None:
    frames = Framer().feed(b"\x01\x02\x03" + bytes.fromhex(_INDEX_VECTORS["cmd_x05"]))
    assert len(frames) == 1
    assert frames[0].checksum_ok is True


def test_framer_reset() -> None:
    fr = Framer()
    fr.feed(bytes.fromhex(_INDEX_VECTORS["set_temp"])[:8])
    fr.reset()
    assert fr.feed(bytes.fromhex(_INDEX_VECTORS["set_temp"]))[0].checksum_ok


def test_parse_hex_bytes_contiguous_and_dump() -> None:
    assert parse_hex_bytes("ff00ffa5") == bytes.fromhex("ff00ffa5")
    dump = "< 0x0\t ff 00 ff a5 01\n< 0x10\t 02 03\n"
    assert parse_hex_bytes(dump) == bytes.fromhex("ff00ffa5010203")


def test_hex_file_transport_parity_and_readonly(tmp_path: Path) -> None:
    path = tmp_path / "sample.hex"
    path.write_text("ff00ffa5070f10050813160217060e0000012e\n", encoding="utf-8")
    t = HexFileTransport(path)
    assert t.path == path
    t.open()
    assert t.read(4) == bytes.fromhex("ff00ffa5")
    assert t.remaining > 0
    rest = t.read_all()
    assert rest.startswith(bytes.fromhex("070f"))
    assert t.read(1) == b""
    with pytest.raises(NotImplementedError):
        t.write(b"\x00")
    t.close()
    with pytest.raises(RuntimeError):
        t.read(1)
    with pytest.raises(RuntimeError):
        t.read_all()


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "samples" / "status_temps.hex").is_file(),
    reason="parent samples/ not present",
)
def test_frame_status_temps_fixture(samples_dir: Path) -> None:
    path = samples_dir / "status_temps.hex"
    session = Session(HexFileTransport(path))
    frames = session.read_frames(chunk_size=8)
    assert len(frames) >= 1
    assert frames[0].kind is FrameKind.A5
    assert frames[0].checksum_ok is True


@pytest.mark.skipif(
    not (
        Path(__file__).resolve().parents[2]
        / "samples"
        / "log_breakdown"
        / "001_baseline_filter_on.hex"
    ).is_file(),
    reason="parent samples/log_breakdown not present",
)
def test_frame_log_breakdown_fixture(samples_dir: Path) -> None:
    path = samples_dir / "log_breakdown" / "001_baseline_filter_on.hex"
    t = HexFileTransport(path)
    t.open()
    try:
        frames = Framer().feed(t.read_all())
    finally:
        t.close()
    assert len(frames) >= 1
    assert frames[0].kind is FrameKind.A5
    assert frames[0].checksum_ok is True


def test_verify_helpers_reject_short_or_bad() -> None:
    assert verify_a5_frame(b"\xa5\x01") is False
    assert verify_intellichlor_frame(b"\x10\x02\x10\x03") is False
    assert verify_intellichlor_frame(b"\x00\x01\x02\x03\x04") is False


def test_framer_malformed_intellichlor_recovers() -> None:
    # 10 02 then immediate 10 03 (too short) then a valid IC frame.
    junk = b"\x10\x02\x10\x03" + _IC_PHP
    frames = Framer().feed(junk)
    assert any(f.raw == _IC_PHP for f in frames)


def test_hex_transport_read_zero_and_comment_lines(tmp_path: Path) -> None:
    path = tmp_path / "c.hex"
    path.write_text("# comment\nff 00\n/* skip\n*/\na5\n", encoding="utf-8")
    t = HexFileTransport(path)
    t.open()
    assert t.read(0) == b""
    assert t.read_all() == bytes.fromhex("ff00a5")
    t.close()


def test_parse_hex_angle_prefix_and_empty_fallback() -> None:
    assert parse_hex_bytes("<notahex ff aa") == bytes.fromhex("ffaa")
    assert parse_hex_bytes("<") == b""
    assert parse_hex_bytes("hello\nworld") == b""


def test_framer_intellichlor_runaway_without_etx() -> None:
    # STX then a long run without ETX → abandon and recover on a later frame.
    fr = Framer()
    assert fr.feed(b"\x10\x02" + (b"\x00" * 70)) == []
    frames = fr.feed(_IC_PHP)
    assert any(f.kind is FrameKind.INTELLICHLOR and f.raw == _IC_PHP for f in frames)
