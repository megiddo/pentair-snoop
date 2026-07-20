"""Pattern: Facade — load `.env` transport defaults for the CLI.

CLI ``--tcp`` / ``--serial`` / ``--file`` always override these settings.
Default mode is **serial** (tty), not TCP/EW11.

Uses a tiny built-in ``.env`` parser (no third-party dotenv dependency).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from pentairsnoop.transport import (
    DEFAULT_EW11_HOST,
    DEFAULT_EW11_PORT,
    DEFAULT_SERIAL_BAUD,
)

DEFAULT_TRANSPORT = "serial"
DEFAULT_SERIAL_DEVICE = "/dev/ttyUSB0"

ENV_TRANSPORT = "PENTAIR_TRANSPORT"
ENV_SERIAL_DEVICE = "PENTAIR_SERIAL_DEVICE"
ENV_SERIAL_BAUD = "PENTAIR_SERIAL_BAUD"
ENV_TCP_HOST = "PENTAIR_TCP_HOST"
ENV_TCP_PORT = "PENTAIR_TCP_PORT"

_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$"
)


def parse_dotenv_text(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; ignore comments and blank lines."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def find_dotenv(start: Path | None = None) -> Path | None:
    """Walk upward from ``start`` (default: cwd) looking for a ``.env`` file."""
    cur = (start or Path.cwd()).resolve()
    for directory in (cur, *cur.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_file(path: Path | None = None, *, override: bool = False) -> Path | None:
    """Load dotenv into ``os.environ``. Returns the path loaded, if any."""
    env_path = path if path is not None else find_dotenv()
    if env_path is None or not env_path.is_file():
        return None
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for key, value in parse_dotenv_text(text).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


@dataclass(frozen=True)
class TransportSettings:
    """Resolved transport preferences from the environment / ``.env`` file."""

    transport: str  # "serial" | "tcp"
    serial_device: str
    serial_baud: int
    tcp_host: str
    tcp_port: int

    @property
    def tcp_endpoint(self) -> str:
        return f"{self.tcp_host}:{self.tcp_port}"


def load_transport_settings(*, env_file: Path | None = None) -> TransportSettings:
    """Read transport settings after optionally loading a ``.env`` file."""
    load_env_file(env_file)
    transport = _env(ENV_TRANSPORT, DEFAULT_TRANSPORT).lower()
    if transport not in ("serial", "tcp"):
        transport = DEFAULT_TRANSPORT
    baud_raw = _env(ENV_SERIAL_BAUD, str(DEFAULT_SERIAL_BAUD))
    try:
        baud = int(baud_raw, 0)
    except ValueError:
        baud = DEFAULT_SERIAL_BAUD
    port_raw = _env(ENV_TCP_PORT, str(DEFAULT_EW11_PORT))
    try:
        tcp_port = int(port_raw, 0)
    except ValueError:
        tcp_port = DEFAULT_EW11_PORT
    return TransportSettings(
        transport=transport,
        serial_device=_env(ENV_SERIAL_DEVICE, DEFAULT_SERIAL_DEVICE),
        serial_baud=baud,
        tcp_host=_env(ENV_TCP_HOST, DEFAULT_EW11_HOST),
        tcp_port=tcp_port,
    )


def resolve_cli_transport(
    *,
    tcp: str | None,
    serial: str | None,
    file: Path | None,
    settings: TransportSettings | None = None,
) -> tuple[str | None, str | None, Path | None, int]:
    """Apply CLI overrides on top of ``.env`` defaults.

    Returns ``(tcp_endpoint, serial_device, file, serial_baud)``.
    Exactly one of tcp / serial / file is set.

    - ``--file`` wins over everything.
    - ``--tcp`` / ``--serial`` override ``.env`` (empty flag value → env host/device).
    - No flags → ``.env`` (default serial tty).
    """
    cfg = settings if settings is not None else load_transport_settings()
    if file is not None:
        return None, None, file, cfg.serial_baud
    if tcp is not None:
        endpoint = tcp if tcp != "" else cfg.tcp_endpoint
        return endpoint, None, None, cfg.serial_baud
    if serial is not None:
        device = serial if serial != "" else cfg.serial_device
        return None, device, None, cfg.serial_baud
    if cfg.transport == "tcp":
        return cfg.tcp_endpoint, None, None, cfg.serial_baud
    return None, cfg.serial_device, None, cfg.serial_baud
