"""A5 tests: CircuitChange / HeatChange craft, diff-tx, CLI dry-run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pentairsnoop.cli import (
    build_parser,
    cmd_craft_circuit,
    cmd_craft_heat,
    cmd_diff_tx,
    cmd_send_with_listen,
    main,
)
from pentairsnoop.compare import diff_tx
from pentairsnoop.craft import (
    HEAT_POOL_MODE,
    HEAT_SPA_MODE,
    CircuitChange,
    CircuitId,
    HeatChange,
    build_a5_hex,
    pack_heat_mode,
    parse_circuit_name,
)
from pentairsnoop.registry import (
    CMD_CIRCUIT_CHANGE_REQUEST,
    CMD_TEMP_CHANGE_REQUEST,
    MessageRegistry,
)
# lib/index.php known write / ACK vectors.
_SET_TEMP = "ff00ffa507102088042b60050001f8"
_SET_TEMP_ACK = "ff00ffa50c2010010188016b"
# Crafted pool-light ON (PHP shape): protocol 07, DST 10, SRC 20, cmd 86, id 06, on.
_CIRCUIT_POOL_LIGHT_ON = "ff00ffa507102086020601016b"


def test_pack_heat_mode_matches_php_or() -> None:
    assert pack_heat_mode(pool_mode=1, spa_mode=1) == 0x05
    assert pack_heat_mode(pool_mode=1, spa_mode=1) == (HEAT_POOL_MODE | HEAT_SPA_MODE)
    with pytest.raises(ValueError):
        pack_heat_mode(pool_mode=4, spa_mode=0)


def test_parse_circuit_name() -> None:
    assert parse_circuit_name("pool_light") == CircuitId.POOL_LIGHT
    assert parse_circuit_name("0x06") == 0x06
    assert parse_circuit_name("6") == 6
    with pytest.raises(ValueError, match="unknown circuit"):
        parse_circuit_name("waterfall")


def test_heat_change_matches_set_temp_hex() -> None:
    msg = HeatChange.build(pool_set=0x2B, spa_set=0x60, mode=0x05)
    assert msg.to_hex() == _SET_TEMP
    assert msg.command == CMD_TEMP_CHANGE_REQUEST
    assert msg.length == 4
    assert msg.destination == 0x10
    assert msg.source == 0x20
    assert msg.protocol == 0x07


def test_heat_change_roundtrip_parse() -> None:
    built = HeatChange.build(pool_set=43, spa_set=96, mode=0x05)
    parsed = HeatChange.parse(bytes.fromhex(_SET_TEMP))
    assert parsed.pool_set == 43
    assert parsed.spa_set == 96
    assert parsed.mode == 0x05
    assert parsed.to_hex() == built.to_hex()
    d = parsed.to_dict()
    assert d["type_name"] == "HeatChange"
    assert d["pool_set"] == 43


def test_circuit_change_pool_light_on_checksum() -> None:
    msg = CircuitChange.build(CircuitId.POOL_LIGHT, True)
    assert msg.to_hex() == _CIRCUIT_POOL_LIGHT_ON
    assert msg.command == CMD_CIRCUIT_CHANGE_REQUEST
    assert msg.circuit == 0x06
    assert msg.status == 1


def test_circuit_change_parse_not_temp_status() -> None:
    """Regression: PHP CircuitChange::parse wrongly returned TempStatus."""
    raw = bytes.fromhex(_CIRCUIT_POOL_LIGHT_ON)
    msg = CircuitChange.parse(raw)
    assert type(msg).__name__ == "CircuitChange"
    assert msg.circuit == 0x06
    assert msg.status == 1
    assert msg.to_dict()["on"] is True


def test_circuit_change_off() -> None:
    msg = CircuitChange.build(0x01, False, protocol=0x07)
    assert msg.status == 0
    parsed = CircuitChange.parse(msg.raw)
    assert parsed.status == 0


def test_diff_tx_set_temp_match() -> None:
    crafted = HeatChange.build(pool_set=43, spa_set=96, mode=0x05).to_hex()
    d = diff_tx(crafted, _SET_TEMP)
    assert d.match
    fields = {x.field: x for x in d.deltas}
    assert fields["protocol"].match
    assert fields["destination"].match
    assert fields["source"].match
    assert fields["command"].match
    assert fields["payload"].match
    assert fields["checksum"].match
    assert fields["checksum_ok"].crafted is True


def test_diff_tx_mismatch_payload() -> None:
    crafted = HeatChange.build(pool_set=44, spa_set=96, mode=0x05).to_hex()
    d = diff_tx(crafted, _SET_TEMP)
    assert not d.match
    payload = next(x for x in d.deltas if x.field == "payload")
    assert not payload.match


def test_diff_tx_ignores_preamble_padding() -> None:
    """A5 body match still counts if leading idle FF differs."""
    body = bytes.fromhex(_SET_TEMP)
    padded = b"\xff\xff" + body
    d = diff_tx(padded.hex(), _SET_TEMP)
    assert d.match
    full = next(x for x in d.deltas if x.field == "full_hex")
    assert not full.match  # preamble differs
    a5 = next(x for x in d.deltas if x.field == "a5_body_hex")
    assert a5.match


def test_registry_decodes_write_cmds() -> None:
    reg = MessageRegistry.default()
    heat = reg.decode_a5(bytes.fromhex(_SET_TEMP))
    assert isinstance(heat, HeatChange)
    circ = reg.decode_a5(bytes.fromhex(_CIRCUIT_POOL_LIGHT_ON))
    assert isinstance(circ, CircuitChange)


def test_build_a5_hex_rejects_huge_payload() -> None:
    with pytest.raises(ValueError):
        build_a5_hex(
            protocol=7,
            destination=0x10,
            source=0x20,
            command=0x88,
            payload=bytes(256),
        )


def test_cli_craft_heat_dry_run_matches_set_temp(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "craft-heat",
            "--pool-set",
            "43",
            "--spa-set",
            "96",
            "--mode",
            "0x05",
            "--diff",
            _SET_TEMP,
        ]
    )
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out == _SET_TEMP


def test_cli_craft_heat_pack_modes(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "craft-heat",
            "--pool-set",
            "43",
            "--spa-set",
            "96",
            "--pack-modes",
            "--pool-mode",
            "1",
            "--spa-mode",
            "1",
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == _SET_TEMP


def test_cli_craft_circuit_and_diff(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "craft-circuit",
            "pool_light",
            "on",
            "--diff",
            _CIRCUIT_POOL_LIGHT_ON,
            "--json",
        ]
    )
    assert code == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["hex"] == _CIRCUIT_POOL_LIGHT_ON
    assert obj["diff"]["match"] is True


def test_cli_diff_tx_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert cmd_diff_tx(_SET_TEMP, _SET_TEMP, pretty=True) == 0
    assert cmd_diff_tx(
        HeatChange.build(pool_set=1, spa_set=1, mode=0).to_hex(),
        _SET_TEMP,
        pretty=False,
    ) == 2
    capsys.readouterr()


def test_cli_craft_circuit_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["craft-circuit", "nope", "on"]) == 1
    assert "error" in capsys.readouterr().err


def test_cli_send_requires_transport(capsys: pytest.CaptureFixture[str]) -> None:
    code = cmd_craft_circuit(
        circuit="0x06",
        state="on",
        protocol=0x07,
        dst=0x10,
        src=0x20,
        diff=None,
        as_json=False,
        send=True,
        tcp=None,
        serial=None,
        listen_seconds=0.0,
    )
    assert code == 1
    assert "requires" in capsys.readouterr().err


def test_send_with_listen_mock_transport(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gated --send: listen windows + write against an in-memory transport."""

    class _Mem:
        def __init__(self) -> None:
            self.written: list[bytes] = []
            self._reads = 0

        def open(self) -> None:
            pass

        def close(self) -> None:
            pass

        def read(self, n: int) -> bytes:
            self._reads += 1
            if self._reads == 2:
                return bytes.fromhex(_SET_TEMP_ACK)
            return b""

        def write(self, data: bytes) -> None:
            self.written.append(data)

    mem = _Mem()

    def _fake_tcp(host: str, port: int) -> _Mem:
        return mem

    monkeypatch.setattr("pentairsnoop.cli.TcpTransport", _fake_tcp)
    # Speed up listen loops.
    monkeypatch.setattr("pentairsnoop.cli.time.sleep", lambda _s: None)

    tx = bytes.fromhex(_SET_TEMP)
    code = cmd_send_with_listen(
        tx, tcp="10.0.0.11:8899", serial=None, listen_seconds=0.01
    )
    assert code == 0
    assert mem.written == [tx]
    out = capsys.readouterr()
    assert "SEND" in out.err


def test_parser_lists_craft_commands() -> None:
    p = build_parser()
    help_txt = p.format_help()
    assert "craft-circuit" in help_txt
    assert "craft-heat" in help_txt
    assert "diff-tx" in help_txt


def test_heat_parse_rejects_wrong_cmd() -> None:
    with pytest.raises(ValueError, match="0x88"):
        HeatChange.parse(bytes.fromhex(_CIRCUIT_POOL_LIGHT_ON))
    with pytest.raises(ValueError, match="0x86"):
        CircuitChange.parse(bytes.fromhex(_SET_TEMP))


def test_cmd_craft_heat_direct(capsys: pytest.CaptureFixture[str]) -> None:
    code = cmd_craft_heat(
        pool_set=43,
        spa_set=96,
        mode=0x05,
        protocol=0x07,
        dst=0x10,
        src=0x20,
        diff=_SET_TEMP,
        as_json=False,
        send=False,
        tcp=None,
        serial=None,
        listen_seconds=0,
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == _SET_TEMP


def test_diff_tx_invalid_hex() -> None:
    assert cmd_diff_tx("zz", _SET_TEMP, pretty=False) == 1


def test_compare_normalize_0x_and_odd() -> None:
    from pentairsnoop.compare import _normalize_hex

    assert _normalize_hex("0x" + _SET_TEMP).hex() == _SET_TEMP
    with pytest.raises(ValueError, match="odd-length"):
        _normalize_hex("abc")
    with pytest.raises(ValueError, match="too short"):
        from pentairsnoop.compare import _header_fields

        _header_fields(b"\xa5\x01")


def test_cli_craft_heat_default_mode(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["craft-heat", "--pool-set", "43", "--spa-set", "96"])
    assert code == 0
    assert capsys.readouterr().out.strip() == _SET_TEMP


def test_cli_craft_mismatch_exit(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "craft-heat",
            "--pool-set",
            "1",
            "--spa-set",
            "1",
            "--mode",
            "0",
            "--diff",
            _SET_TEMP,
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "MISMATCH" in err


def test_listen_seconds_zero() -> None:
    from pentairsnoop.cli import _listen_and_log
    from pentairsnoop.session import Session

    class _T:
        def open(self) -> None:
            pass

        def close(self) -> None:
            pass

        def read(self, n: int) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            pass

    sess = Session(_T())  # type: ignore[arg-type]
    assert _listen_and_log(sess, seconds=0, label="x") == 0


def test_send_serial_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _Mem:
        written: list[bytes] = []

        def open(self) -> None:
            pass

        def close(self) -> None:
            pass

        def read(self, n: int) -> bytes:
            return b""

        def write(self, data: bytes) -> None:
            self.written.append(data)

    mem = _Mem()
    monkeypatch.setattr("pentairsnoop.cli.SerialTransport", lambda port: mem)
    monkeypatch.setattr("pentairsnoop.cli.time.sleep", lambda _s: None)
    assert (
        cmd_send_with_listen(b"\x01", tcp=None, serial="/dev/null", listen_seconds=0)
        == 0
    )
    assert mem.written == [b"\x01"]


def test_parse_short_length() -> None:
    # Valid CS but length claims 0 payload for 0x86 — craft a short header.
    # a5 07 10 20 86 00 + cs over a5..len
    from pentairsnoop.framer import a5_checksum_bytes

    body = bytes.fromhex("a50710208600")
    raw = bytes((0xFF, 0x00, 0xFF)) + body + a5_checksum_bytes(body)
    with pytest.raises(ValueError, match=">= 2"):
        CircuitChange.parse(raw)
    body88 = bytes.fromhex("a50710208801ff")  # len=1
    raw88 = bytes((0xFF, 0x00, 0xFF)) + body88 + a5_checksum_bytes(body88)
    with pytest.raises(ValueError, match=">= 4"):
        HeatChange.parse(raw88)


def test_craft_heat_pack_modes_cli_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "craft-heat",
            "--pool-set",
            "43",
            "--spa-set",
            "96",
            "--pack-modes",
            "--pool-mode",
            "9",
        ]
    )
    assert code == 1
    assert "error" in capsys.readouterr().err


def test_main_diff_tx(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff-tx", _SET_TEMP, _SET_TEMP]) == 0
    capsys.readouterr()


def test_cmd_craft_with_send_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "pentairsnoop.cli.cmd_send_with_listen",
        lambda *a, **k: 0,
    )
    code = cmd_craft_circuit(
        circuit="0x06",
        state="1",
        protocol=0x07,
        dst=0x10,
        src=0x20,
        diff=None,
        as_json=False,
        send=True,
        tcp="10.0.0.11:8899",
        serial=None,
        listen_seconds=0,
    )
    assert code == 0
    code = cmd_craft_heat(
        pool_set=43,
        spa_set=96,
        mode=0x05,
        protocol=0x07,
        dst=0x10,
        src=0x20,
        diff=None,
        as_json=True,
        send=True,
        tcp="10.0.0.11:8899",
        serial=None,
        listen_seconds=0,
    )
    assert code == 0
