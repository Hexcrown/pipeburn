import plistlib
import types

import pytest

from pipeburn import devices_mac
from pipeburn.devices import DeviceError


def listing(*entries):
    return {"AllDisksAndPartitions": list(entries)}


def disk(ident, mounts=(), apfs=()):
    return {
        "DeviceIdentifier": ident,
        "Size": 16_000_000_000,
        "Partitions": [{"DeviceIdentifier": f"{ident}s{i + 1}", "MountPoint": m} for i, m in enumerate(mounts)],
        "APFSVolumes": [{"DeviceIdentifier": f"{ident}s9", "MountPoint": m} for m in apfs],
    }


def info(**over):
    base = {"BusProtocol": "USB", "Internal": False, "WritableMedia": True,
            "TotalSize": 16_000_000_000, "MediaName": "SanDisk Cruzer"}
    base.update(over)
    return base


def test_usb_stick_is_listed_with_raw_twin_and_mounts():
    found = devices_mac.parse_diskutil(
        listing(disk("disk4", mounts=["/Volumes/A", "/Volumes/A/Sub"])), {"disk4": info()}
    )
    assert len(found) == 1
    d = found[0]
    assert (d.path, d.raw_path, d.write_path) == ("/dev/disk4", "/dev/rdisk4", "/dev/rdisk4")
    assert d.block_align == 4096 and d.size == 16_000_000_000 and d.label == "SanDisk Cruzer"
    assert d.mountpoints == ("/Volumes/A/Sub", "/Volumes/A")


@pytest.mark.parametrize("override", [
    {"BusProtocol": "SATA"},
    {"Internal": True},
    {"WritableMedia": False},
    {"VirtualOrPhysical": "Virtual"},
])
def test_unsuitable_disks_are_skipped(override):
    assert devices_mac.parse_diskutil(listing(disk("disk4")), {"disk4": info(**override)}) == []


@pytest.mark.parametrize("mount", ["/", "/System/Volumes/Data", "/Users/me", "/Library/Foo"])
def test_disks_holding_the_system_are_skipped(mount):
    assert devices_mac.parse_diskutil(listing(disk("disk4", apfs=[mount])), {"disk4": info()}) == []


def test_other_volumes_mounts_are_fine():
    found = devices_mac.parse_diskutil(listing(disk("disk4", mounts=["/Volumes/USBSTICK"])), {"disk4": info()})
    assert len(found) == 1


def test_validate_target_accepts_raw_and_block_names():
    found = devices_mac.parse_diskutil(listing(disk("disk4")), {"disk4": info()})
    assert devices_mac.validate_target("/dev/rdisk4", found).path == "/dev/disk4"
    assert devices_mac.validate_target("/dev/disk4", found).path == "/dev/disk4"
    with pytest.raises(DeviceError):
        devices_mac.validate_target("/dev/disk0", found)
    with pytest.raises(DeviceError):
        devices_mac.validate_target("/dev/rdisk9", found)


def fake_runner(outputs):
    def run(cmd, capture_output=True, timeout=None, text=False):
        key = " ".join(cmd[1:])
        if key not in outputs:
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"no such thing")
        return types.SimpleNamespace(returncode=0, stdout=plistlib.dumps(outputs[key]), stderr=b"")

    return run


def test_list_usb_devices_runs_diskutil_and_parses_plists():
    runner = fake_runner({
        "list -plist external physical": listing(disk("disk4"), disk("disk5")),
        "info -plist /dev/disk4": info(),
        "info -plist /dev/disk5": info(BusProtocol="Thunderbolt"),
    })
    found = devices_mac.list_usb_devices(runner)
    assert [d.path for d in found] == ["/dev/disk4"]


def test_diskutil_failure_is_a_device_error():
    with pytest.raises(DeviceError, match="failed"):
        devices_mac.list_usb_devices(fake_runner({}))


def test_unmount_uses_unmountdisk_once():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stderr="")

    d = devices_mac.parse_diskutil(listing(disk("disk4", mounts=["/Volumes/A", "/Volumes/B"])), {"disk4": info()})[0]
    devices_mac.unmount_all(d, runner)
    assert calls == [["diskutil", "unmountDisk", "/dev/disk4"]]


def test_unmount_failure_is_reported():
    def runner(cmd, **kw):
        return types.SimpleNamespace(returncode=1, stderr="busy")

    d = devices_mac.parse_diskutil(listing(disk("disk4")), {"disk4": info()})[0]
    with pytest.raises(DeviceError, match="busy"):
        devices_mac.unmount_all(d, runner)


def test_zero_size_disk_is_skipped():
    empty = disk("disk4")
    empty["Size"] = 0
    assert devices_mac.parse_diskutil(listing(empty), {"disk4": info(TotalSize=0)}) == []
