import sys

import pytest

from pipeburn import launcher
from pipeburn.core import PipeburnError


def test_root_runs_the_worker_directly(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 0)
    cmd = launcher.build_command(url="http://x/a.iso", device="/dev/sdb")
    assert cmd[:3] == [sys.executable, "-m", "pipeburn.worker"]
    assert "--control-stdin" in cmd


def test_dry_run_never_elevates(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 1000)
    cmd = launcher.build_command(url="http://x/a.iso", device="out.img", dry_run=True)
    assert cmd[0] == sys.executable and "--dry-run" in cmd


def test_non_root_uses_pkexec_and_passes_import_path(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/pkexec")
    monkeypatch.setattr(launcher, "_system_helper", lambda: None)
    cmd = launcher.build_command(url="http://x/a.iso", device="/dev/sdb")
    assert cmd[0] == "/usr/bin/pkexec" and cmd[1] == "/usr/bin/env"
    assert cmd[2].startswith("PYTHONPATH=")
    assert cmd[3] == sys.executable


def test_missing_pkexec_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    with pytest.raises(PipeburnError, match="pkexec"):
        launcher.build_command(url="http://x/a.iso", device="/dev/sdb")


def test_debug_flag_is_forwarded_only_when_asked(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 0)
    assert "--debug" not in launcher.build_command(url="http://x/a.iso", device="/dev/sdb")
    assert "--debug" in launcher.build_command(url="http://x/a.iso", device="/dev/sdb", debug=True)


def test_options_and_values_are_not_confusable(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 0)
    cmd = launcher.build_command(
        url="-evil", device="/dev/sdb", sha256=" " + "a" * 64 + " ", verify=False, decompress=False
    )
    assert "--url=-evil" in cmd
    assert "--sha256=" + "a" * 64 in cmd
    assert "--no-verify" in cmd and "--no-decompress" in cmd


def _fake_stat(uid=0, mode=0o100755):
    import types

    return types.SimpleNamespace(st_uid=uid, st_mode=mode)


def test_installed_helper_is_preferred_and_named_in_the_prompt(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/pkexec")
    monkeypatch.setattr(launcher.os, "stat", lambda path: _fake_stat())
    monkeypatch.setattr(launcher.os, "access", lambda path, mode: True)
    cmd = launcher.build_command(url="http://x/a.iso", device="/dev/sdb")
    assert cmd[:2] == ["/usr/bin/pkexec", launcher.SYSTEM_HELPER]
    assert "-m" not in cmd and "--control-stdin" in cmd and "--url=http://x/a.iso" in cmd


def test_helper_not_owned_by_root_is_ignored(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/pkexec")
    monkeypatch.setattr(launcher.os, "stat", lambda path: _fake_stat(uid=1000))
    monkeypatch.setattr(launcher.os, "access", lambda path, mode: True)
    cmd = launcher.build_command(url="http://x/a.iso", device="/dev/sdb")
    assert cmd[1] == "/usr/bin/env"


def test_group_writable_helper_is_ignored(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/pkexec")
    monkeypatch.setattr(launcher.os, "stat", lambda path: _fake_stat(mode=0o100775))
    monkeypatch.setattr(launcher.os, "access", lambda path, mode: True)
    cmd = launcher.build_command(url="http://x/a.iso", device="/dev/sdb")
    assert cmd[1] == "/usr/bin/env"


def test_missing_helper_falls_back_to_env_python(monkeypatch):
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/pkexec")

    def nope(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(launcher.os, "stat", nope)
    cmd = launcher.build_command(url="http://x/a.iso", device="/dev/sdb")
    assert cmd[1] == "/usr/bin/env" and "-m" in cmd


def test_macos_uses_osascript_and_a_socket(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 501)
    assert launcher.needs_socket() and not launcher.needs_socket(dry_run=True)
    cmd = launcher.build_command(
        url='https://x/a.iso?q="1"&r=$(touch /tmp/pwned)', device="/dev/disk4",
        connect="/tmp/pb-x/s", debug=True,
    )
    assert cmd[:2] == ["/usr/bin/osascript", "-e"] and len(cmd) == 3
    script = cmd[2]
    assert script.startswith("do shell script ") and "with administrator privileges" in script
    assert "--device=/dev/disk4" in script and "--connect=/tmp/pb-x/s" in script and "--debug" in script
    assert "--control-stdin" not in script
    assert "'--url=https://x/a.iso?q=\\\"1\\\"&r=$(touch /tmp/pwned)'" in script


def test_applescript_strings_escape_quotes_and_backslashes():
    assert launcher.applescript_string('a"b\\c') == '"a\\"b\\\\c"'


def test_macos_dry_run_runs_the_worker_directly(monkeypatch):
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher.os, "geteuid", lambda: 501)
    cmd = launcher.build_command(url="http://x/a.iso", device="out.img", dry_run=True, connect="/tmp/s")
    assert cmd[0] == sys.executable and "--dry-run" in cmd and "--control-stdin" in cmd
