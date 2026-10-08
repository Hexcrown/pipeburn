"""USB drive discovery and safety checks (Linux, via util-linux ``lsblk``)."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from .core import PipeburnError
from .util import human_bytes


class DeviceError(PipeburnError):
    kind = "device"


# Mount points that mean "this disk is part of the running system".
_SYSTEM_MOUNTS = {"/", "/boot", "/efi", "/etc", "/home", "/opt", "/srv", "/nix", "[SWAP]"}
_SYSTEM_PREFIXES = (
    "/boot", "/usr", "/var", "/nix",
    "/run/live", "/run/initramfs", "/run/archiso", "/lib/live", "/cdrom",
)


def _is_system_mount(mountpoint: str) -> bool:
    if mountpoint in _SYSTEM_MOUNTS:
        return True
    return any(mountpoint == p or mountpoint.startswith(p + "/") for p in _SYSTEM_PREFIXES)


@dataclass(frozen=True)
class Device:
    path: str  # e.g. /dev/sdb
    name: str  # e.g. sdb
    size: int  # bytes
    model: str
    vendor: str
    mountpoints: tuple = ()  # deepest first, so they can be unmounted in order

    @property
    def label(self) -> str:
        return f"{self.vendor} {self.model}".strip() or "USB drive"

    @property
    def display(self) -> str:
        return f"{self.label} - {human_bytes(self.size)} ({self.path})"


def _truthy(value) -> bool:
    return value in (True, 1, "1", "true", "True")


def _collect_mounts(node: dict) -> list:
    raw = node.get("mountpoints")
    if raw is None:  # older util-linux has a single "mountpoint" column
        raw = [node.get("mountpoint")]
    mounts = [m for m in raw if m]
    for child in node.get("children") or []:
        mounts += _collect_mounts(child)
    return mounts


def parse_lsblk(data: dict) -> list:
    """Pick the whole disks that are USB, writable, non-empty and not in use by the system."""
    devices = []
    for node in data.get("blockdevices", []):
        if node.get("type") != "disk":
            continue
        if (node.get("tran") or "").lower() != "usb":
            continue
        if _truthy(node.get("ro")):
            continue
        size = int(node.get("size") or 0)
        if size <= 0:  # empty card-reader slot
            continue
        mounts = _collect_mounts(node)
        if any(_is_system_mount(m) for m in mounts):
            continue
        devices.append(
            Device(
                path=node.get("path") or "/dev/" + node["name"],
                name=node["name"],
                size=size,
                model=(node.get("model") or "").strip(),
                vendor=(node.get("vendor") or "").strip(),
                mountpoints=tuple(sorted(set(mounts), key=len, reverse=True)),
            )
        )
    return devices


def lsblk_json() -> dict:
    last_error = ""
    for columns in (
        "NAME,PATH,TYPE,TRAN,RO,SIZE,MODEL,VENDOR,MOUNTPOINTS",
        "NAME,PATH,TYPE,TRAN,RO,SIZE,MODEL,VENDOR,MOUNTPOINT",
    ):
        try:
            proc = subprocess.run(
                ["lsblk", "-J", "-b", "-o", columns],
                capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError:
            raise DeviceError("lsblk was not found (install util-linux).") from None
        except subprocess.TimeoutExpired:
            raise DeviceError("lsblk timed out.") from None
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout)
            except ValueError as e:
                raise DeviceError(f"Could not read lsblk output: {e}") from e
        last_error = proc.stderr.strip()
    raise DeviceError(f"lsblk failed: {last_error}")


def list_usb_devices() -> list:
    return parse_lsblk(lsblk_json())


def validate_target(path: str, devices: Optional[list] = None) -> Device:
    """Return the Device for ``path`` or raise. Re-checks at write time, never trusts the UI."""
    real = os.path.realpath(path)
    for device in devices if devices is not None else list_usb_devices():
        if os.path.realpath(device.path) == real:
            return device
    raise DeviceError(
        f"{path} is not a writable USB disk (or it holds the running system); refusing to write."
    )


def unmount_all(device: Device, runner: Callable = subprocess.run) -> None:
    for mountpoint in device.mountpoints:
        proc = runner(["umount", mountpoint], capture_output=True, text=True)
        if proc.returncode != 0:
            raise DeviceError(f"Could not unmount {mountpoint}: {proc.stderr.strip()}")


def reread_partitions(path: str) -> None:
    """Best effort: tell the kernel to re-read the new partition table."""
    try:
        import fcntl

        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.ioctl(fd, 0x125F)  # BLKRRPART
        finally:
            os.close(fd)
    except Exception:
        pass
