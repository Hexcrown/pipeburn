"""Builds the command line that runs the worker, elevating with pkexec if needed."""

from __future__ import annotations

import os
import shlex
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


def needs_socket(dry_run: bool = False) -> bool:
    """macOS elevates through osascript, which has no stdin, so events travel over a socket."""
    return (
        sys.platform == "darwin"
        and not dry_run
        and hasattr(os, "geteuid")
        and os.geteuid() != 0
    )


def applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _macos_elevate(worker_args: list) -> list:
    package_parent = str(Path(__file__).resolve().parent.parent)
    shell = " ".join(
        shlex.quote(part)
        for part in ["/usr/bin/env", f"PYTHONPATH={package_parent}", sys.executable, "-m", "pipeburn.worker",
                     *worker_args]
    )
    script = (
        f"do shell script {applescript_string(shell)} with administrator privileges "
        f"with prompt {applescript_string('Pipeburn needs your permission to erase a drive and write an image to it.')}"
    )
    return ["/usr/bin/osascript", "-e", script]


def build_command(
    *,
    url: str,
    device: str,
    sha256: Optional[str] = None,
    verify: bool = True,
    decompress: bool = True,
    dry_run: bool = False,
    debug: bool = False,
    connect: Optional[str] = None,
) -> list:
    if connect and needs_socket(dry_run):
        args = [f"--url={url}", f"--device={device}", f"--connect={connect}"]
        if sha256 and sha256.strip():
            args.append(f"--sha256={sha256.strip()}")
        if not verify:
            args.append("--no-verify")
        if not decompress:
            args.append("--no-decompress")
        if debug:
            args.append("--debug")
        return _macos_elevate(args)

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
