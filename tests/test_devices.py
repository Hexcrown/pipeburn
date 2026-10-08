import os
from types import SimpleNamespace

import pytest

from pipeburn import devices
from pipeburn.util import RateMeter, human_bytes, human_duration

SAMPLE = {
    "blockdevices": [
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "tran": "nvme", "ro": False,
         "size": 512110190592, "model": "Internal SSD", "vendor": None, "mountpoints": [None],
         "children": [
             {"name": "nvme0n1p1", "type": "part", "mountpoints": ["/boot/efi"]},
             {"name": "nvme0n1p2", "type": "part", "mountpoints": ["/"]},
         ]},
        # a normal USB stick with one mounted partition: allowed
        {"name": "sda", "path": "/dev/sda", "type": "disk", "tran": "usb", "ro": False,
         "size": 15518924800, "model": "Cruzer Blade ", "vendor": "SanDisk ", "mountpoints": [None],
         "children": [
             {"name": "sda1", "type": "part", "mountpoints": ["/media/user/DATA"]},
             {"name": "sda2", "type": "part", "mountpoints": [None]},
         ]},
        # USB disk that holds the root filesystem: refused
        {"name": "sdb", "path": "/dev/sdb", "type": "disk", "tran": "usb", "ro": False,
         "size": 64000000000, "model": "Portable SSD", "vendor": "X", "mountpoints": [None],
         "children": [{"name": "sdb1", "type": "part", "mountpoints": ["/"]}]},
        # read-only, empty, live-USB, and nested system mounts: refused
        {"name": "sdc", "path": "/dev/sdc", "type": "disk", "tran": "usb", "ro": True,
         "size": 8000000000, "model": "Locked", "vendor": "", "mountpoints": [None]},
        {"name": "sdd", "path": "/dev/sdd", "type": "disk", "tran": "usb", "ro": False,
         "size": 0, "model": "Empty reader", "vendor": "", "mountpoints": [None]},
        {"name": "sde", "path": "/dev/sde", "type": "disk", "tran": "usb", "ro": False,
         "size": 8000000000, "model": "Live", "vendor": "", "mountpoints": [None],
         "children": [{"name": "sde1", "type": "part", "mountpoints": ["/run/live/medium"]}]},
        {"name": "sdg", "path": "/dev/sdg", "type": "disk", "tran": "usb", "ro": False,
         "size": 8000000000, "model": "LVM", "vendor": "", "mountpoints": [None],
         "children": [{"name": "sdg1", "type": "part", "mountpoints": [None], "children": [
             {"name": "vg-home", "type": "lvm", "mountpoints": ["/home"]}]}]},
        # not whole USB disks
        {"name": "loop0", "path": "/dev/loop0", "type": "loop", "tran": None, "ro": False,
         "size": 100000000, "model": None, "vendor": None, "mountpoints": ["/snap/core"]},
        {"name": "sr0", "path": "/dev/sr0", "type": "rom", "tran": "sata", "ro": False,
         "size": 1073741312, "model": "DVD", "vendor": "", "mountpoints": [None]},
        # older util-linux: string booleans/sizes and a singular "mountpoint"
        {"name": "sdf", "path": "/dev/sdf", "type": "disk", "tran": "usb", "ro": "0",
         "size": "31914983424", "model": "Old", "vendor": "Kingston", "mountpoint": "/mnt/x"},
    ]
}


def test_parse_lsblk_keeps_only_safe_usb_disks():
    found = devices.parse_lsblk(SAMPLE)
    assert [d.name for d in found] == ["sda", "sdf"]
    sda = found[0]
    assert (sda.vendor, sda.model) == ("SanDisk", "Cruzer Blade")
    assert sda.mountpoints == ("/media/user/DATA",)
    assert "14.5 GiB" in sda.display and "/dev/sda" in sda.display
    assert found[1].size == 31914983424 and found[1].mountpoints == ("/mnt/x",)


def test_mountpoints_are_ordered_deepest_first():
    data = {"blockdevices": [{
        "name": "sda", "type": "disk", "tran": "usb", "ro": False, "size": 1000, "model": "m",
        "vendor": "v", "mountpoints": [None],
        "children": [{"name": "sda1", "type": "part", "mountpoints": ["/mnt/a"]},
                     {"name": "sda2", "type": "part", "mountpoints": ["/mnt/a/inner"]}]}]}
    assert devices.parse_lsblk(data)[0].mountpoints == ("/mnt/a/inner", "/mnt/a")


def test_validate_target_follows_symlinks_and_refuses_others(tmp_path):
    found = devices.parse_lsblk(SAMPLE)
    link = tmp_path / "by-id-usb-stick"
    os.symlink("/dev/sda", link)
    assert devices.validate_target(str(link), found).name == "sda"
    for refused in ("/dev/nvme0n1", "/dev/sdb", "/dev/doesnotexist", str(tmp_path)):
        with pytest.raises(devices.DeviceError):
            devices.validate_target(refused, found)


def test_unmount_all_runs_in_order_and_reports_failures():
    device = devices.Device("/dev/sda", "sda", 1000, "m", "v", ("/mnt/a/inner", "/mnt/a"))
    calls = []

    def ok(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    devices.unmount_all(device, runner=ok)
    assert calls == [["umount", "/mnt/a/inner"], ["umount", "/mnt/a"]]

    def busy(cmd, **kw):
        return SimpleNamespace(returncode=32, stderr="target is busy")

    with pytest.raises(devices.DeviceError, match="busy"):
        devices.unmount_all(device, runner=busy)


def test_human_formatting():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KiB"
    assert human_bytes(5 * 1024**3) == "5.0 GiB"
    assert human_duration(75) == "01:15"
    assert human_duration(3700) == "1:01:40"
    assert human_duration(None) == "--:--"


def test_rate_meter():
    now = [0.0]
    meter = RateMeter(window=5.0, clock=lambda: now[0])
    assert meter.speed() == 0.0 and meter.eta(0, 100) is None
    for t, total in [(0, 0), (1, 1000), (2, 2000)]:
        now[0] = float(t)
        meter.add(total)
    assert meter.speed() == 1000.0
    assert meter.eta(2000, 5000) == 3.0
    assert meter.eta(2000, None) is None
