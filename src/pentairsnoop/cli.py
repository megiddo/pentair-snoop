"""Pattern: Command — CLI entry maps user intents to lab operations."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from pentairsnoop import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser (subcommands arrive in A1+)."""
    parser = argparse.ArgumentParser(
        prog="pentairsnoop",
        description=(
            "Lab CLI for Pentair RS485 protocol inspection, decode, and capture."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv and run the selected command; return a process exit code."""
    parser = build_parser()
    parser.parse_args(list(argv) if argv is not None else None)
    # A0 skeleton: no subcommands yet; --help / --version handled by argparse.
    return 0
