import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pipeburn import gui, logs  # noqa: E402
from pipeburn.core import PipeburnError  # noqa: E402
from pipeburn.gui import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def spin_until(condition, timeout=30.0):
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(20)
    poll.timeout.connect(lambda: loop.quit() if condition() else None)
    poll.start()
    QTimer.singleShot(int(timeout * 1000), loop.quit)
    loop.exec()
    poll.stop()
    assert condition(), "timed out waiting for the GUI"


@pytest.fixture
def window(qapp, tmp_path, clean_logging):
    logs.setup_logging(log_file=False)
    out = tmp_path / "out.img"
    win = MainWindow(dry_run_path=str(out))
    win.messages = []
    win._message = lambda kind, title, text: win.messages.append((kind, title, text))
    win._confirm = lambda device: True
    win.out = out
    yield win
    win.close()


def test_dry_run_burn(window, server, image_data):
    server.serve("/img", image_data)
    window.url_edit.setText(server.url("/img"))
    window.start_btn.click()
    assert window._running and not window.start_btn.isEnabled() and window.cancel_btn.isEnabled()
    spin_until(lambda: not window._running)
    assert window.out.read_bytes() == image_data
    assert window.messages and window.messages[-1][0] == "info"
    assert "verified" in window.status.text()
    assert window.progress.value() == window.progress.maximum()
    assert window.start_btn.isEnabled() and not window.cancel_btn.isEnabled()


def test_checksum_failure_is_shown(window, server, image_data):
    server.serve("/img", image_data)
    window.url_edit.setText(server.url("/img"))
    window.sha_edit.setText("0" * 64)
    window.start_btn.click()
    spin_until(lambda: not window._running)
    kind, title, text = window.messages[-1]
    assert kind == "error" and "mismatch" in text.lower()
    assert window.status.text().startswith("Failed")


def test_cancel(window, server):
    server.serve_slow("/slow")
    window.url_edit.setText(server.url("/slow"))
    window.start_btn.click()
    spin_until(lambda: window._saw_progress)
    window.cancel_btn.click()
    spin_until(lambda: not window._running)
    assert window.status.text().startswith("Cancelled")
    assert not window.messages  # a cancel is not an error popup


def test_input_validation_blocks_start(window):
    window.url_edit.setText("ftp://example.org/x.iso")
    window.start_btn.click()
    assert not window._running and window.messages[-1][0] == "warn"

    window.url_edit.setText("https://example.org/x.iso")
    window.sha_edit.setText("not-a-hash")
    window.start_btn.click()
    assert not window._running and "64 hexadecimal" in window.messages[-1][2]


def test_declined_confirmation_starts_nothing(window):
    window._confirm = lambda device: False
    window.url_edit.setText("https://example.org/x.iso")
    window.start_btn.click()
    assert not window._running and not window.out.exists()


def test_worker_log_events_reach_the_pane_and_the_report(window, server, image_data):
    server.serve("/img?token=SECRET", image_data)
    window.url_edit.setText(server.url("/img?token=SECRET"))
    window.start_btn.click()
    spin_until(lambda: not window._running)
    pane = window.log_view.toPlainText()
    assert "Compression: none" in pane and "Helper exited with code 0" in pane
    report = window._log_report()
    assert report.startswith("Pipeburn ") and "Compression: none" in report
    assert "SECRET" not in pane and "SECRET" not in report


def test_copy_log_button(window):
    window.copy_btn.click()
    assert "Log copied" in window.status.text()


def test_helper_command_log_hides_url_secrets(window):
    text = window._safe_command(
        ["python", "--url=https://u:p@example.org/a.iso?token=SECRET", "--device=/dev/sdb"]
    )
    assert "SECRET" not in text and "u:p" not in text and "example.org/a.iso" in text


def test_debug_flag_reaches_the_helper_command(qapp, tmp_path, monkeypatch, clean_logging):
    seen = {}

    def fake_build_command(**kwargs):
        seen.update(kwargs)
        raise PipeburnError("stop here")

    monkeypatch.setattr(gui, "build_command", fake_build_command)
    win = MainWindow(dry_run_path=str(tmp_path / "o.img"), debug=True)
    win._message = lambda *args: None
    win._confirm = lambda device: True
    win.url_edit.setText("https://example.org/x.iso")
    win.start_btn.click()
    win.close()
    assert seen["debug"] is True and seen["dry_run"] is True


def test_app_icon_file_loads(qapp):
    assert not gui.load_icon().isNull()
