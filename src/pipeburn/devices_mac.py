"""USB drive discovery and safety checks on macOS, via ``diskutil`` property lists."""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from typing import Callable, Optional

from .devices import Device, DeviceError
from .util import human_bytes

log = logging.getLogger(__name__)

_SYSTEM_PREFIXES = (
    "/System", "/Library", "/Applications", "/Users", "/private", "/usr", "/bin", "/sbin",
    "/opt", "/etc", "/var", "/cores", "/Volumes/Recovery", "/Volumes/Preboot",
)


def _is_system_mount(mountpoint: str) -> bool:
    if mountpoint == "/":
        return True
    return any(mountpoint == p or mountpoint.startswith(p + "/") for p in _SYSTEM_PREFIXES)


def _run_plist(args: list, runner: Callable = subprocess.run) -> dict:
    try:
        proc = runner(["diskutil", *args], capture_output=True, timeout=30)
    except FileNotFoundError:
        raise DeviceError("diskutil was not found; this does not look like macOS.") from None
    except subprocess.TimeoutExpired:
        raise DeviceError("diskutil timed out.") from None
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else proc.stderr
        raise DeviceError(f"diskutil {' '.join(args)} failed: {stderr.strip()}")
    try:
        return plistlib.loads(proc.stdout)
    except Exception as e:
        raise DeviceError(f"Could not read diskutil output: {e}") from e


def _mounts_of(entry: dict) -> list:
    mounts = []
    for key in ("Partitions", "APFSVolumes"):
        for part in entry.get(key) or []:
            if part.get("MountPoint"):
                mounts.append(part["MountPoint"])
    if entry.get("MountPoint"):
        mounts.append(entry["MountPoint"])
    return mounts


def parse_diskutil(listing: dict, infos: dict) -> list:
    """Pick whole USB disks that are writable, non-empty, real and not in use by the system.

    ``listing`` is ``diskutil list -plist external physical``; ``infos`` maps a
    device identifier (``disk4``) to its ``diskutil info -plist`` dictionary.
    """
    devices = []
    for entry in listing.get("AllDisksAndPartitions", []):
        ident = entry.get("DeviceIdentifier", "")
        info = infos.get(ident)
        if not ident or not info:
            continue
        if str(info.get("BusProtocol", "")).upper() != "USB":
            continue
        if info.get("Internal") or info.get("VirtualOrPhysical") == "Virtual" or info.get("Virtual"):
            continue
        if info.get("WritableMedia") is False:
            continue
        size = int(info.get("TotalSize") or info.get("Size") or entry.get("Size") or 0)
        if size <= 0:
            continue
        mounts = _mounts_of(entry)
        if any(_is_system_mount(m) for m in mounts):
            continue
        devices.append(
            Device(
                path=f"/dev/{ident}",
                name=ident,
                size=size,
                model=str(info.get("MediaName") or "").strip(),
                vendor="",
                mountpoints=tuple(sorted(set(mounts), key=len, reverse=True)),
                raw_path=f"/dev/r{ident}",
            )
        )
    return devices


def list_usb_devices(runner: Callable = subprocess.run) -> list:
    listing = _run_plist(["list", "-plist", "external", "physical"], runner)
    infos = {}
    for entry in listing.get("AllDisksAndPartitions", []):
        ident = entry.get("DeviceIdentifier")
        if not ident:
            continue
        try:
            infos[ident] = _run_plist(["info", "-plist", f"/dev/{ident}"], runner)
        except DeviceError as e:
            log.warning("Skipping %s: %s", ident, e)
    devices = parse_diskutil(listing, infos)
    log.debug("diskutil reports %d usable USB disk(s): %s", len(devices), [d.path for d in devices])
    return devices


def _normalize(path: str) -> str:
    real = os.path.realpath(path)
    base = os.path.basename(real)
    if base.startswith("rdisk"):
        return os.path.join(os.path.dirname(real), base[1:])
    return real


def validate_target(path: str, devices: Optional[list] = None) -> Device:
    """Return the Device for ``path`` (/dev/diskN or /dev/rdiskN) or raise."""
    wanted = _normalize(path)
    for device in devices if devices is not None else list_usb_devices():
        if device.path == wanted:
            log.info("Target %s resolved to %s (%s, %s)", path, device.path, device.label, human_bytes(device.size))
            return device
    raise DeviceError(
        f"{path} is not a writable external USB disk (or it holds the running system); refusing to write."
    )


def unmount_all(device: Device, runner: Callable = subprocess.run) -> None:
    log.info("Unmounting all volumes of %s", device.path)
    proc = runner(["diskutil", "unmountDisk", device.path], capture_output=True, text=True)
    if proc.returncode != 0:
        raise DeviceError(f"Could not unmount {device.path}: {proc.stderr.strip()}")


def reread_partitions(path: str) -> None:
    log.debug("macOS re-reads the partition table by itself")
