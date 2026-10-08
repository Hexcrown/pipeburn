"""Logging shared by the GUI and the worker (standard library only).

The GUI owns the log file. The worker runs as root through pkexec, so it never
touches the user's home directory: it sends its log records to the GUI as JSON
"log" events instead.
"""

from __future__ import annotations

import collections
import logging
import logging.handlers
import os
import platform
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

LOGGER_NAME = "pipeburn"
LOG_FILE_NAME = "pipeburn.log"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
RING_CAPACITY = 2000


def log_dir() -> Path:
    """$XDG_STATE_HOME/pipeburn, or ~/.local/state/pipeburn."""
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base and os.path.isabs(base) else Path.home() / ".local" / "state"
    return root / "pipeburn"


def redact_url(url: str) -> str:
    """Drop credentials, query and fragment: signed download links keep secrets there."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        return "<unparseable url>"


class RingBufferHandler(logging.Handler):
    """Keeps the most recent formatted lines in memory for the Copy log button."""

    def __init__(self, capacity: int = RING_CAPACITY):
        super().__init__()
        self.lines: collections.deque = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:
            self.handleError(record)


_ring: Optional[RingBufferHandler] = None


def reset_logging() -> None:
    """Remove every handler this module or the worker added and restore defaults."""
    global _ring
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if not isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            handler.close()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    _ring = None


def setup_logging(debug: bool = False, log_file: bool = True) -> Optional[Path]:
    """Configure the "pipeburn" logger; returns the log file path, or None without one."""
    global _ring
    reset_logging()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)

    _ring = RingBufferHandler()
    _ring.setFormatter(formatter)
    logger.addHandler(_ring)

    path: Optional[Path] = None
    if log_file:
        try:
            directory = log_dir()
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / LOG_FILE_NAME
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        except OSError as e:
            path = None
            logger.warning("File logging is disabled: %s", e)
    return path


def recent_lines() -> list:
    return list(_ring.lines) if _ring is not None else []


def build_report(log_path: Optional[Path] = None, debug: bool = False) -> str:
    """Version info plus the recent log, with the home directory masked as ~."""
    from . import __version__

    header = [
        f"Pipeburn {__version__}",
        f"Python {platform.python_version()} on {platform.platform()}",
        f"Debug mode: {'on' if debug else 'off'}",
        f"Log file: {log_path if log_path else 'none'}",
        "",
    ]
    text = "\n".join(header + recent_lines())
    try:
        home = str(Path.home())
    except (RuntimeError, KeyError):
        home = ""
    if len(home) > 1:
        text = text.replace(home, "~")
    return text
