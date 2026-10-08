import http.server
import logging
import os
import random
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
# Subprocesses (the worker) need the source tree on their path too.
os.environ["PYTHONPATH"] = SRC + os.pathsep + os.environ.get("PYTHONPATH", "")


class TestServer:
    """Tiny HTTP server whose routes the tests register."""

    __test__ = False

    def __init__(self):
        self.routes = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                route = outer.routes.get(self.path)
                if route is None:
                    self.send_error(404)
                    return
                try:
                    route(self)
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def serve(self, path, body):
        def handler(req):
            req.send_response(200)
            req.send_header("Content-Length", str(len(body)))
            req.end_headers()
            req.wfile.write(body)

        self.routes[path] = handler

    def serve_no_length(self, path, body):
        def handler(req):
            req.send_response(200)
            req.send_header("Connection", "close")
            req.end_headers()
            req.wfile.write(body)
            req.close_connection = True

        self.routes[path] = handler

    def serve_truncated(self, path, body, missing=1000):
        def handler(req):
            req.send_response(200)
            req.send_header("Content-Length", str(len(body) + missing))
            req.send_header("Connection", "close")
            req.end_headers()
            req.wfile.write(body)
            req.close_connection = True

        self.routes[path] = handler

    def redirect(self, path, to):
        def handler(req):
            req.send_response(302)
            req.send_header("Location", to)
            req.send_header("Content-Length", "0")
            req.end_headers()

        self.routes[path] = handler

    def serve_slow(self, path, chunks=200, size=256 * 1024, delay=0.02):
        def handler(req):
            req.send_response(200)
            req.send_header("Content-Length", str(chunks * size))
            req.end_headers()
            block = b"\0" * size
            for _ in range(chunks):
                req.wfile.write(block)
                req.wfile.flush()
                time.sleep(delay)

        self.routes[path] = handler


@pytest.fixture
def server():
    s = TestServer()
    yield s
    s.close()


@pytest.fixture(scope="session")
def image_data():
    # Bigger than one write chunk (4 MiB) and not a multiple of any chunk size.
    return random.Random(1).randbytes(5 * 1024 * 1024 + 17)


@pytest.fixture
def clean_logging():
    """Undo whatever setup_logging / the worker did to the shared "pipeburn" logger."""
    from pipeburn import logs

    logs.reset_logging()
    yield
    logs.reset_logging()


@pytest.fixture
def captured_logs():
    """Every record the pipeburn loggers emit during the test, debug level included."""
    records = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("pipeburn")
    old_level = logger.level
    handler = Collector()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(old_level)
