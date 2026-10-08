import gzip
import hashlib
import json
import subprocess
import sys

WORKER = [sys.executable, "-m", "pipeburn.worker"]


def run_worker(*args):
    proc = subprocess.run(
        [*WORKER, *args], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
    )
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return proc, events


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
