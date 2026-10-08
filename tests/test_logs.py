import logging
from pathlib import Path

from pipeburn import __version__, logs


def test_redact_url_drops_credentials_query_and_fragment():
    assert (
        logs.redact_url("https://user:pw@example.org:8443/a/b.iso?token=SECRET#frag")
        == "https://example.org:8443/a/b.iso"
    )
    assert logs.redact_url("http://[::1]:8000/x.iso?y=1") == "http://[::1]:8000/x.iso"
    assert logs.redact_url("https://example.org/distro.iso") == "https://example.org/distro.iso"


def test_redact_url_never_raises():
    assert logs.redact_url("http://host:99999999/x") == "<unparseable url>"
    assert isinstance(logs.redact_url("not a url"), str)
    assert isinstance(logs.redact_url(""), str)


def test_log_dir_follows_xdg_state_home_when_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert logs.log_dir() == tmp_path / "pipeburn"


def test_log_dir_ignores_relative_or_missing_xdg_state_home(monkeypatch):
    expected = Path.home() / ".local" / "state" / "pipeburn"
    monkeypatch.setenv("XDG_STATE_HOME", "relative/dir")
    assert logs.log_dir() == expected
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert logs.log_dir() == expected


def test_setup_logging_writes_the_file_and_keeps_lines_in_memory(monkeypatch, tmp_path, clean_logging):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = logs.setup_logging(debug=False)
    assert path == tmp_path / "pipeburn" / "pipeburn.log"
    assert (tmp_path / "pipeburn").stat().st_mode & 0o077 == 0  # private to the user
    logger = logging.getLogger("pipeburn.test")
    logger.info("hello world")
    logger.debug("too detailed")
    text = path.read_text()
    assert "hello world" in text and "too detailed" not in text
    assert any("hello world" in line for line in logs.recent_lines())


def test_debug_mode_also_logs_debug_records(monkeypatch, tmp_path, clean_logging):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = logs.setup_logging(debug=True)
    logging.getLogger("pipeburn.test").debug("very detailed")
    assert "very detailed" in path.read_text()


def test_file_logging_failure_is_not_fatal(monkeypatch, tmp_path, clean_logging):
    blocker = tmp_path / "blocker"
    blocker.write_text("this is a file, not a directory")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    assert logs.setup_logging() is None
    logging.getLogger("pipeburn.test").info("still works")
    lines = logs.recent_lines()
    assert any("File logging is disabled" in line for line in lines)
    assert any("still works" in line for line in lines)


def test_ring_buffer_keeps_only_the_newest_lines():
    ring = logs.RingBufferHandler(capacity=3)
    ring.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("pipeburn.ringtest")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(ring)
    try:
        for i in range(5):
            logger.info("m%d", i)
    finally:
        logger.removeHandler(ring)
        logger.propagate = True
    assert list(ring.lines) == ["m2", "m3", "m4"]


def test_report_has_version_info_and_masks_the_home_directory(monkeypatch, tmp_path, clean_logging):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    path = logs.setup_logging(debug=True)
    logging.getLogger("pipeburn.test").info("wrote %s", tmp_path / "out.img")
    report = logs.build_report(path, debug=True)
    assert report.startswith(f"Pipeburn {__version__}")
    assert "Debug mode: on" in report
    assert str(tmp_path) not in report
    assert "~/.local/state/pipeburn/pipeburn.log" in report
    assert "wrote ~/out.img" in report


def test_report_without_setup_still_works(clean_logging):
    report = logs.build_report()
    assert report.startswith("Pipeburn ") and "Log file: none" in report
