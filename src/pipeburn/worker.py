"""Privileged worker. Validates the target, unmounts it, streams the image.

Runs as root (the GUI launches it through pkexec) and reports progress to
stdout as one JSON object per line. With --control-stdin, a "cancel" line on
stdin (or the GUI disappearing) aborts the write; that is needed because a
normal user cannot signal a process that pkexec has turned into root.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading

from .core import Cancelled, PipeburnError, burn
from .devices import reread_partitions, unmount_all, validate_target
from .util import RateMeter

_cancel = threading.Event()


def _emit(event: str, **fields) -> None:
    try:
        sys.stdout.write(json.dumps({"event": event, **fields}) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, ValueError, OSError):
        _cancel.set()  # nobody is listening any more


def _watch_stdin() -> None:
    try:
        for line in sys.stdin:
            if line.strip().lower() == "cancel":
                break
    except (OSError, ValueError):
        pass
    _cancel.set()  # explicit cancel, or EOF because the GUI went away


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipeburn-worker",
        description="Stream an image from a URL to a USB drive, reporting JSON lines.",
    )
    p.add_argument("--url", required=True)
    p.add_argument("--device", required=True, help="e.g. /dev/sdb")
    p.add_argument("--sha256", help="expected SHA256 of the file as downloaded")
    p.add_argument("--no-verify", action="store_true", help="skip the read-back check")
    p.add_argument("--no-decompress", action="store_true", help="write .xz/.gz/.zst files as-is")
    p.add_argument("--dry-run", action="store_true",
                   help="treat --device as a plain file; skips USB checks and unmounting")
    p.add_argument("--control-stdin", action="store_true",
                   help="treat a 'cancel' line (or EOF) on stdin as cancel")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: _cancel.set())
    signal.signal(signal.SIGINT, lambda *_: _cancel.set())
    if args.control_stdin:
        threading.Thread(target=_watch_stdin, daemon=True).start()

    state = {"phase": "write", "meter": RateMeter()}

    def on_progress(phase, done, total):
        if phase != state["phase"]:
            state["phase"], state["meter"] = phase, RateMeter()
            _emit("phase", name=phase)
        meter = state["meter"]
        meter.add(done)
        _emit("progress", phase=phase, done=done, total=total,
              speed=round(meter.speed()), eta=meter.eta(done, total))

    try:
        device_size = None
        if args.dry_run:
            target = args.device
        else:
            _emit("phase", name="check")
            device = validate_target(args.device)
            target, device_size = device.path, device.size
            _emit("phase", name="unmount")
            unmount_all(device)
        _emit("phase", name="write")
        result = burn(
            args.url, target,
            expected_sha256=args.sha256,
            verify=not args.no_verify,
            decompress="none" if args.no_decompress else "auto",
            device_size=device_size,
            progress=on_progress,
            cancel=_cancel,
        )
        if not args.dry_run:
            reread_partitions(target)
        _emit("done", **result.as_dict())
        return 0
    except PipeburnError as e:
        _emit("error", kind=e.kind, message=str(e))
        return 130 if isinstance(e, Cancelled) else 1
    except Exception as e:  # last resort so the GUI always gets an answer
        _emit("error", kind="internal", message=f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
