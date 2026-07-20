"""Shared fixtures. Hex samples live in the parent phpentair tree (not copied)."""

from __future__ import annotations

from pathlib import Path

import pytest

# Parent repo layout: pentair/pentairsnoop/ → pentair/samples/
_PARENT_SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture
def samples_dir() -> Path:
    """Path to parent ``samples/`` hex fixtures (used from A1+ decode tests)."""
    return _PARENT_SAMPLES
