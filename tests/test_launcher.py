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
