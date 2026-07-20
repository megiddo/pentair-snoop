"""Pattern: Parser — sync seek and length-based frame extraction from a byte stream."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FrameKind(Enum):
    """Framed message family on the RS485 bus."""

    A5 = "a5"
    INTELLICHLOR = "intellichlor"


@dataclass(frozen=True)
class Frame:
    """One extracted bus frame with optional checksum verification result."""

    kind: FrameKind
    raw: bytes
    checksum_ok: bool


# Standard preamble variants (idle FF* then sync). Checksum starts at A5.
_SYNC_00_FF_A5 = bytes((0x00, 0xFF, 0xA5))
_SYNC_FF_00_FF_A5 = bytes((0xFF, 0x00, 0xFF, 0xA5))

# IntelliChlor DLE framing (not truncated A5 / not PHP ShortInfo-as-header-only).
_IC_STX = bytes((0x10, 0x02))
_IC_ETX = bytes((0x10, 0x03))
# Practical upper bound for chlor payload + CS before abandoning STX.
_IC_MAX_FRAME = 64

# After A5: PROTO DST SRC CMD LEN
_A5_HEADER_AFTER = 5


def pentair_checksum(data: bytes) -> int:
    """Pattern: Parser — PHP ``Command::pentaircs``: sum of bytes mod 65536.

    For standard frames, ``data`` is ``A5 .. last payload byte`` (exclude the
    two-byte trailer). Result is the big-endian u16 checksum value.
    """
    return sum(data) % 65536


def a5_checksum_bytes(body_from_a5: bytes) -> bytes:
    """Return the two-byte big-endian trailer for ``body_from_a5`` (includes A5)."""
    cs = pentair_checksum(body_from_a5)
    return bytes(((cs >> 8) & 0xFF, cs & 0xFF))


def verify_a5_frame(frame_from_a5: bytes) -> bool:
    """True if trailing u16 BE matches ``pentair_checksum`` over A5..payload."""
    if len(frame_from_a5) < 1 + _A5_HEADER_AFTER + 2:
        return False
    body, trailer = frame_from_a5[:-2], frame_from_a5[-2:]
    expected = pentair_checksum(body)
    actual = (trailer[0] << 8) | trailer[1]
    return expected == actual


def intellichlor_checksum(data_between_stx_and_cs: bytes) -> int:
    """IntelliChlor CS8: ``(sum(data) + 18) % 256`` (PACKET_SPEC / njsPC-equivalent).

    ``data_between_stx_and_cs`` is the bytes after ``10 02`` and before the CS8
    byte (i.e. excluding STX, CS, and ``10 03``). Equivalent to summing
    ``10 02`` + data mod 256.
    """
    return (sum(data_between_stx_and_cs) + 18) % 256


def verify_intellichlor_frame(frame: bytes) -> bool:
    """True if frame is ``10 02 | data | CS8 | 10 03`` with a matching CS8."""
    if len(frame) < 5 or frame[:2] != _IC_STX or frame[-2:] != _IC_ETX:
        return False
    cs = frame[-3]
    data = frame[2:-3]
    return cs == intellichlor_checksum(data)


def _find_a5_sync(buf: bytes) -> tuple[int, int] | None:
    """Return ``(discard_before, a5_index)`` for the earliest A5 sync in ``buf``.

    Accepts ``00 FF A5`` or ``FF 00 FF A5`` (leading idle ``FF`` tolerated by
    searching). Returns indices into ``buf``; ``a5_index`` points at the ``A5``.
    """
    best: tuple[int, int] | None = None

    # Prefer longer preamble when both overlap; pick earliest A5.
    idx = 0
    while True:
        i = buf.find(_SYNC_FF_00_FF_A5, idx)
        if i < 0:
            break
        a5 = i + 3
        cand = (i, a5)
        if best is None or a5 < best[1]:
            best = cand
        idx = i + 1

    idx = 0
    while True:
        i = buf.find(_SYNC_00_FF_A5, idx)
        if i < 0:
            break
        a5 = i + 2
        cand = (i, a5)
        if best is None or a5 < best[1]:
            best = cand
        idx = i + 1

    return best


def _find_ic_stx(buf: bytes) -> int:
    return buf.find(_IC_STX)


def _earliest_sync(buf: bytes) -> tuple[str, int, int] | None:
    """Choose the earliest sync: ``('a5', discard, a5_idx)`` or ``('ic', stx, stx)``."""
    a5 = _find_a5_sync(buf)
    ic = _find_ic_stx(buf)

    candidates: list[tuple[str, int, int]] = []
    if a5 is not None:
        discard, a5_idx = a5
        candidates.append(("a5", discard, a5_idx))
    if ic >= 0:
        candidates.append(("ic", ic, ic))
    if not candidates:
        return None
    return min(candidates, key=lambda c: c[2])


class Framer:
    """Pattern: Parser — extract A5 and IntelliChlor frames from a byte stream.

    Standard layout after sync::

        A5 PROTO DST SRC CMD LEN PAYLOAD[LEN] CS_HI CS_LO

    IntelliChlor (second framer; not broken/truncated A5)::

        10 02 | data… | CS8 | 10 03
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        """Clear the internal reassembly buffer."""
        self._buf.clear()

    def feed(self, data: bytes) -> list[Frame]:
        """Accept raw bus bytes; return zero or more complete frames."""
        if data:
            self._buf.extend(data)
        return self._drain()

    def _drain(self) -> list[Frame]:
        out: list[Frame] = []
        while True:
            sync = _earliest_sync(bytes(self._buf))
            if sync is None:
                # Keep a short tail in case a sync straddles the next feed.
                if len(self._buf) > 4:
                    del self._buf[:-4]
                break

            kind, start, mark = sync
            if start > 0:
                del self._buf[:start]
                continue

            if kind == "a5":
                taken = self._try_take_a5(mark)
            else:
                taken = self._try_take_intellichlor(mark)

            if taken is None:
                break
            if isinstance(taken, Frame):
                out.append(taken)
            # taken is True → consumed noise / advanced; keep draining
        return out

    def _try_take_a5(self, a5_index: int) -> Frame | bool | None:
        # Need A5 + 5 header bytes to know LEN.
        need_hdr = a5_index + 1 + _A5_HEADER_AFTER
        if len(self._buf) < need_hdr:
            return None
        length = self._buf[a5_index + 5]
        total = need_hdr + length + 2
        if len(self._buf) < total:
            return None

        # Include preamble before A5 when present (PHP normalizes to ff00ffa5).
        raw = bytes(self._buf[:total])
        del self._buf[:total]
        from_a5 = raw[a5_index:]
        return Frame(
            kind=FrameKind.A5,
            raw=raw,
            checksum_ok=verify_a5_frame(from_a5),
        )

    def _try_take_intellichlor(self, stx_index: int) -> Frame | bool | None:
        # Search for 10 03 after STX; CS8 is the byte immediately before ETX.
        search_from = stx_index + 2
        window_end = min(len(self._buf), stx_index + _IC_MAX_FRAME)
        etx = bytes(self._buf).find(_IC_ETX, search_from, window_end)
        if etx < 0:
            # Incomplete within window: wait for more bytes, or abandon STX.
            if len(self._buf) - stx_index >= _IC_MAX_FRAME:
                del self._buf[0]
                return True
            return None
        # Require STX + ≥1 data byte + CS + ETX (minimum 6 bytes).
        if etx < stx_index + 5:
            del self._buf[0]
            return True

        end = etx + 2
        raw = bytes(self._buf[stx_index:end])
        del self._buf[:end]
        return Frame(
            kind=FrameKind.INTELLICHLOR,
            raw=raw,
            checksum_ok=verify_intellichlor_frame(raw),
        )
