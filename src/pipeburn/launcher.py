"""Builds the command line that runs the worker, elevating with pkexec if needed."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .core import PipeburnError

SYSTEM_HELPER = "/usr/libexec/pipeburn-worker"


def _system_helper() -> Optional[str]:
    try:
        info = os.stat(SYSTEM_HELPER)
    except OSError:
        return None
    if info.st_uid != 0 or info.st_mode & 0o022 or not os.access(SYSTEM_HELPER, os.X_OK):
        return None
    return SYSTEM_HELPER


def build_command(
    *,
    url: str,
    device: str,
    sha256: Optional[str] = None,
    verify: bool = True,
    decompress: bool = True,
    dry_run: bool = False,
    debug: bool = False,
) -> list:
    command = []
    needs_root = not dry_run and hasattr(os, "geteuid") and os.geteuid() != 0
    helper = _system_helper() if needs_root else None
    if needs_root:
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise PipeburnError(
                "pkexec was not found. Install polkit, or start Pipeburn as root."
            )
        if helper:
            command += [pkexec, helper]
        else:
            # pkexec scrubs the environment, so hand the import path over explicitly.
            package_parent = str(Path(__file__).resolve().parent.parent)
            command += [pkexec, "/usr/bin/env", f"PYTHONPATH={package_parent}"]

    # "--opt=value" keeps a value that starts with "-" from being read as an option.
    if not helper:
        command += [sys.executable, "-m", "pipeburn.worker"]
    command += [f"--url={url}", f"--device={device}", "--control-stdin"]
    if sha256 and sha256.strip():
        command.append(f"--sha256={sha256.strip()}")
    if not verify:
        command.append("--no-verify")
    if not decompress:
        command.append("--no-decompress")
    if dry_run:
        command.append("--dry-run")
    if debug:
        command.append("--debug")
    return command
