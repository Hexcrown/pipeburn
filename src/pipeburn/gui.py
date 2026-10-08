"""Pipeburn GUI (PySide6). All privileged work happens in worker.py."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Optional

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .core import PipeburnError
from .devices import Device, list_usb_devices
from .launcher import build_command
from .util import human_bytes, human_duration

_PHASE_TEXT = {
    "check": "Checking the drive...",
    "unmount": "Unmounting the drive...",
    "write": "Streaming the image to the drive...",
    "verify": "Reading the drive back to verify...",
}
_PROGRESS_LABEL = {"write": "Writing", "verify": "Verifying"}
_PROGRESS_STEPS = 1000


class MainWindow(QWidget):
    def __init__(self, dry_run_path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("Pipeburn")
        self.resize(660, 540)
        self._dry_run_path = dry_run_path
        self._proc: Optional[QProcess] = None
        self._buffer = ""
        self._running = False
        self._done: Optional[dict] = None
        self._error: Optional[dict] = None
        self._saw_progress = False

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.org/distro.iso   (.iso.xz, .gz and .zst work too)")
        self.sha_edit = QLineEdit()
        self.sha_edit.setPlaceholderText("Optional: SHA256 of the file at that URL")
        self.device_box = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        device_row = QHBoxLayout()
        device_row.addWidget(self.device_box, 1)
        device_row.addWidget(self.refresh_btn)
        self.verify_check = QCheckBox("Read the drive back and verify after writing")
        self.verify_check.setChecked(True)

        form = QFormLayout()
        form.addRow("Image URL", self.url_edit)
        form.addRow("SHA256", self.sha_edit)
        form.addRow("Drive", device_row)
        form.addRow("", self.verify_check)

        self.start_btn = QPushButton("Burn")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.start_btn)

        self.progress = QProgressBar()
        self.progress.setRange(0, _PROGRESS_STEPS)
        self.status = QLabel("Ready.")
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Pipeburn</b> - stream an ISO straight from a URL to a USB drive"))
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.log, 1)

        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self.cancel)
        self.refresh_devices()

    # ----- helpers (overridable in tests) --------------------------------

    def _message(self, kind: str, title: str, text: str) -> None:
        {"info": QMessageBox.information, "warn": QMessageBox.warning, "error": QMessageBox.critical}[kind](
            self, title, text
        )

    def _confirm(self, device: Optional[Device]) -> bool:
        if device is None:  # dry run writes to a plain file
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Erase this drive?")
        box.setText(f"Everything on {device.label} ({human_bytes(device.size)}) will be permanently erased.")
        lines = [f"Device: {device.path}", f"Size: {human_bytes(device.size)}"]
        if device.mountpoints:
            lines.append("Mounted at: " + ", ".join(device.mountpoints) + " (will be unmounted)")
        box.setInformativeText("\n".join(lines))
        burn_button = box.addButton("Erase and burn", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_button)
        box.exec()
        return box.clickedButton() is burn_button

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _set_running(self, running: bool) -> None:
        self._running = running
        for widget in (self.url_edit, self.sha_edit, self.device_box, self.refresh_btn,
                       self.verify_check, self.start_btn):
            widget.setEnabled(not running)
        self.cancel_btn.setEnabled(running)

    # ----- devices -------------------------------------------------------

    def refresh_devices(self) -> None:
        self.device_box.clear()
        if self._dry_run_path:
            self.device_box.addItem(f"Dry run -> {self._dry_run_path}", None)
            self._set_status("Dry-run mode: writing to a file. No drive will be touched.")
            return
        try:
            devices = list_usb_devices()
        except PipeburnError as e:
            self._set_status(str(e))
            return
        for device in devices:
            self.device_box.addItem(device.display, device)
        if not devices:
            self._set_status("No USB drives found. Plug one in and press Refresh.")
        else:
            self._set_status("Ready.")

    # ----- running -------------------------------------------------------

    def start(self) -> None:
        url = self.url_edit.text().strip()
        sha = self.sha_edit.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            self._message("warn", "Pipeburn", "Enter an http:// or https:// image URL.")
            return
        if sha and not re.fullmatch(r"[0-9a-fA-F]{64}", sha.split()[0]):
            self._message("warn", "Pipeburn", "The SHA256 must be 64 hexadecimal characters.")
            return
        device = self.device_box.currentData()
        if not self._dry_run_path and device is None:
            self._message("warn", "Pipeburn", "Select a USB drive first.")
            return
        if not self._confirm(device):
            return
        try:
            command = build_command(
                url=url,
                device=self._dry_run_path or device.path,
                sha256=sha,
                verify=self.verify_check.isChecked(),
                dry_run=bool(self._dry_run_path),
            )
        except PipeburnError as e:
            self._message("error", "Pipeburn", str(e))
            return

        self._buffer = ""
        self._done = self._error = None
        self._saw_progress = False
        self.progress.setRange(0, _PROGRESS_STEPS)
        self.progress.setValue(0)
        self.log.clear()
        self._set_status("Starting...")
        self._set_running(True)

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_output)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_proc_error)
        self._proc.start(command[0], command[1:])

    def cancel(self) -> None:
        if self._proc is not None:
            self._proc.write(b"cancel\n")
            self.cancel_btn.setEnabled(False)
            self._set_status("Cancelling...")

    def _on_output(self) -> None:
        if self._proc is None:
            return
        self._buffer += bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._handle_line(line.strip())

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict) and "event" in event:
            self._handle_event(event)
        else:
            self.log.appendPlainText(line)  # e.g. pkexec's own messages

    def _handle_event(self, event: dict) -> None:
        kind = event["event"]
        if kind == "phase":
            name = event.get("name", "")
            self.progress.setRange(0, _PROGRESS_STEPS)
            self.progress.setValue(0)
            self._set_status(_PHASE_TEXT.get(name, name))
            self.log.appendPlainText(_PHASE_TEXT.get(name, name))
        elif kind == "progress":
            self._saw_progress = True
            done, total = event.get("done", 0), event.get("total")
            if total:
                self.progress.setRange(0, _PROGRESS_STEPS)
                self.progress.setValue(min(_PROGRESS_STEPS, int(_PROGRESS_STEPS * done / total)))
            else:
                self.progress.setRange(0, 0)  # size unknown: busy indicator
            text = f"{_PROGRESS_LABEL.get(event.get('phase'), 'Working')}: {human_bytes(done)}"
            if total:
                text += f" of {human_bytes(total)}"
            if event.get("speed"):
                text += f" - {human_bytes(event['speed'])}/s"
            if event.get("eta") is not None:
                text += f" - ETA {human_duration(event['eta'])}"
            self._set_status(text)
        elif kind == "done":
            self._done = event
        elif kind == "error":
            self._error = event

    def _on_finished(self, exit_code: int, _status) -> None:
        self._on_output()
        if self._buffer.strip():
            self._handle_line(self._buffer.strip())
        self._buffer = ""
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.deleteLater()
        self._set_running(False)

        if self._done is not None:
            self.progress.setRange(0, _PROGRESS_STEPS)
            self.progress.setValue(_PROGRESS_STEPS)
            text = "Done. " + ("The drive was read back and verified." if self._done.get("verified") else "")
            self._set_status(text.strip())
            self._message("info", "Pipeburn", text.strip() + "\n\nYou can safely remove the drive.")
        elif self._error is not None:
            message = self._error.get("message", "Unknown error")
            if self._error.get("kind") == "cancelled":
                self._set_status("Cancelled. The drive is partly written; burn again before using it.")
            else:
                self._set_status("Failed: " + message.splitlines()[0])
                self._message("error", "Burn failed", message)
        elif exit_code in (126, 127):
            self._set_status("Authentication was cancelled or failed.")
        else:
            self._set_status(f"The helper stopped unexpectedly (exit code {exit_code}).")
            self._message("error", "Burn failed", self.log.toPlainText() or "The helper stopped unexpectedly.")

    def _on_proc_error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._proc = None
            self._set_running(False)
            self._set_status("Could not start the helper process.")
            self._message("error", "Pipeburn", "Could not start the helper process (is pkexec/polkit installed?).")

    def closeEvent(self, event) -> None:
        if self._proc is not None:
            self._proc.write(b"cancel\n")
            self._proc.waitForFinished(5000)
        super().closeEvent(event)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pipeburn", description="Stream an ISO from a URL to a USB drive.")
    parser.add_argument("--dry-run", metavar="FILE",
                        help="write to FILE instead of a USB drive (a safe way to try the app)")
    args, qt_args = parser.parse_known_args(argv)
    app = QApplication([sys.argv[0], *qt_args])
    window = MainWindow(dry_run_path=args.dry_run)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
