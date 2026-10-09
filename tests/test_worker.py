import gzip
import hashlib
import json
import subprocess
import sys

WORKER = [sys.executable, "-m", "pipeburn.worker"]


def parse_events(stdout):
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def run_worker_all(*args):
    """Run the worker; returns every event, "log" events included."""
    proc = subprocess.run(
        [*WORKER, *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
    )
    return proc, parse_events(proc.stdout)


def run_worker(*args):
    """Run the worker; returns the non-log events."""
    proc, events = run_worker_all(*args)
    return proc, [e for e in events if e["event"] != "log"]


def test_dry_run_end_to_end(server, tmp_path, image_data):
    server.serve("/img", image_data)
    out = tmp_path / "out.img"
    proc, events = run_worker(f"--url={server.url('/img')}", f"--device={out}", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == image_data
    kinds = [e["event"] for e in events]
    assert kinds[0] == "phase" and events[0]["name"] == "write"
    assert "progress" in kinds and kinds[-1] == "done"
    done = events[-1]
    assert done["sha256"] == hashlib.sha256(image_data).hexdigest()
    assert done["verified"] is True and done["written"] == len(image_data)
    phases = [e["name"] for e in events if e["event"] == "phase"]
    assert phases == ["write", "verify"]


def test_no_verify_and_decompression(server, tmp_path, image_data):
    packed = gzip.compress(image_data)
    server.serve("/img", packed)
    out = tmp_path / "out.img"
    proc, events = run_worker(f"--url={server.url('/img')}", f"--device={out}", "--dry-run", "--no-verify")
    assert proc.returncode == 0
    assert out.read_bytes() == image_data
    assert events[-1]["compression"] == "gzip" and events[-1]["verified"] is None

    raw = tmp_path / "raw.img"
    run_worker(f"--url={server.url('/img')}", f"--device={raw}", "--dry-run", "--no-decompress")
    assert raw.read_bytes() == packed


def test_checksum_mismatch_reports_error(server, tmp_path, image_data):
    server.serve("/img", image_data)
    proc, events = run_worker(
        f"--url={server.url('/img')}", f"--device={tmp_path / 'o.img'}", "--dry-run", "--sha256=" + "0" * 64
    )
    assert proc.returncode == 1
    assert events[-1]["event"] == "error" and events[-1]["kind"] == "checksum"


def test_refuses_a_target_that_is_not_a_usb_disk(server, tmp_path, image_data):
    server.serve("/img", image_data)
    target = tmp_path / "precious.txt"
    target.write_text("do not overwrite")
    proc, events = run_worker(f"--url={server.url('/img')}", f"--device={target}")  # no --dry-run
    assert proc.returncode == 1
    assert events[-1]["event"] == "error" and events[-1]["kind"] == "device"
    assert target.read_text() == "do not overwrite"


def test_bad_url_reports_error(tmp_path):
    proc, events = run_worker("--url=file:///etc/passwd", f"--device={tmp_path / 'o'}", "--dry-run")
    assert proc.returncode == 1 and events[-1]["kind"] == "error"


def test_cancel_over_stdin(server, tmp_path):
    server.serve_slow("/slow")  # ~50 MiB over several seconds
    proc = subprocess.Popen(
        [*WORKER, f"--url={server.url('/slow')}", f"--device={tmp_path / 'o.img'}", "--dry-run",
         "--control-stdin"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        for line in proc.stdout:
            if json.loads(line)["event"] == "progress":
                break
        proc.stdin.write("cancel\n")
        proc.stdin.flush()
        rest = [json.loads(line) for line in proc.stdout if line.strip()]
        assert proc.wait(timeout=20) == 130
        assert rest[-1]["event"] == "error" and rest[-1]["kind"] == "cancelled"
    finally:
        proc.kill()
        proc.wait()


def test_gui_going_away_cancels_the_write(server, tmp_path):
    server.serve_slow("/slow")
    proc = subprocess.Popen(
        [*WORKER, f"--url={server.url('/slow')}", f"--device={tmp_path / 'o.img'}", "--dry-run",
         "--control-stdin"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        for line in proc.stdout:
            if json.loads(line)["event"] == "progress":
                break
        proc.stdin.close()  # EOF, as if the GUI crashed
        assert proc.wait(timeout=20) == 130
    finally:
        proc.kill()
        proc.wait()


def test_log_events_are_sent_and_url_secrets_stay_out(server, tmp_path, image_data):
    server.serve("/img?token=SECRET", image_data)
    proc, events = run_worker_all(
        f"--url={server.url('/img?token=SECRET')}", f"--device={tmp_path / 'o.img'}", "--dry-run"
    )
    assert proc.returncode == 0
    logs = [e for e in events if e["event"] == "log"]
    assert logs and {e["level"] for e in logs} <= {"INFO", "WARNING", "ERROR"}  # no DEBUG without --debug
    text = "\n".join(e["message"] for e in logs)
    assert "Compression: none" in text and "Finished successfully" in text
    assert "SECRET" not in proc.stdout and "SECRET" not in proc.stderr


def test_debug_flag_adds_debug_events(server, tmp_path, image_data):
    server.serve("/img", image_data)
    proc, events = run_worker_all(
        f"--url={server.url('/img')}", f"--device={tmp_path / 'o.img'}", "--dry-run", "--debug"
    )
    assert proc.returncode == 0
    debug = [e["message"] for e in events if e["event"] == "log" and e["level"] == "DEBUG"]
    assert any(m.startswith("HTTP 200") for m in debug)


def test_unexpected_error_logs_a_traceback(tmp_path):
    crash = (
        "import sys, pipeburn.worker as w\n"
        "def boom(*a, **k):\n"
        "    raise RuntimeError('boom')\n"
        "w.burn = boom\n"
        "sys.exit(w.main(sys.argv[1:]))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", crash, "--url=http://127.0.0.1:1/x", f"--device={tmp_path / 'o'}", "--dry-run"],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    events = parse_events(proc.stdout)
    assert proc.returncode == 1
    assert events[-1]["event"] == "error" and events[-1]["kind"] == "internal"
    assert "boom" in events[-1]["message"]
    errors = [e for e in events if e["event"] == "log" and e["level"] == "ERROR"]
    assert errors and "Traceback" in errors[-1]["message"] and "RuntimeError: boom" in errors[-1]["message"]


def test_cancelled_run_logs_it_and_ends_with_the_error_event(server, tmp_path):
    server.serve_slow("/slow")
    proc = subprocess.Popen(
        [*WORKER, f"--url={server.url('/slow')}", f"--device={tmp_path / 'o.img'}", "--dry-run",
         "--control-stdin"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        for line in proc.stdout:
            if json.loads(line)["event"] == "progress":
                break
        proc.stdin.write("cancel\n")
        proc.stdin.flush()
        rest = parse_events(proc.stdout.read())
        assert proc.wait(timeout=20) == 130
        assert any(e["event"] == "log" and e["message"] == "Cancelled" for e in rest)
        assert rest[-1]["event"] == "error" and rest[-1]["kind"] == "cancelled"
    finally:
        proc.kill()
        proc.wait()


def test_events_travel_over_a_unix_socket_and_cancel_comes_back(server, tmp_path, image_data):
    import os
    import socket
    import tempfile
    import threading

    server.serve("/img", image_data)
    sock_dir = tempfile.mkdtemp(prefix="pb-")
    path = os.path.join(sock_dir, "s")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    out = tmp_path / "out.img"
    proc = subprocess.Popen(
        [*WORKER, f"--url={server.url('/img')}", f"--device={out}", "--dry-run", f"--connect={path}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    listener.settimeout(30)
    conn, _ = listener.accept()
    received = b""
    conn.settimeout(30)
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        received += chunk
    proc.wait(timeout=30)
    events = [json.loads(l) for l in received.decode().splitlines() if l.strip()]
    kinds = [e["event"] for e in events]
    assert proc.returncode == 0 and kinds[-1] == "done" and "progress" in kinds
    assert proc.stdout.read().strip() == ""
    assert out.read_bytes() == image_data
    conn.close()
    listener.close()


def test_connecting_to_a_missing_socket_fails_cleanly(tmp_path):
    proc = subprocess.run(
        [*WORKER, "--url=http://127.0.0.1:9/x", f"--device={tmp_path / 'o'}", "--dry-run",
         f"--connect={tmp_path / 'nope'}"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 1 and "Could not connect" in proc.stderr
