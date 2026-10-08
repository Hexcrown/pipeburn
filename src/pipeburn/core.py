"""Pipeburn core: stream an image from a URL straight onto a block device.

Standard library only. This module just moves bytes and does not decide which
device is safe to write; callers (see worker.py) do that first.

    HTTP response -> sha256 (of the bytes as published) -> optional decompress
    -> write to target -> fsync -> optional read-back verification
"""

from __future__ import annotations

import errno
import hashlib
import http.client
import logging
import lzma
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from . import __version__
from .logs import redact_url
from .util import human_bytes

log = logging.getLogger(__name__)

READ_CHUNK = 1024 * 1024
WRITE_CHUNK = 4 * 1024 * 1024
_LOG_STEP = 256 * 1024 * 1024  # debug-log write progress every this many bytes
USER_AGENT = f"pipeburn/{__version__} (+https://github.com/Hexcrown/pipeburn)"
_BLKFLSBUF = 0x1261  # Linux ioctl: flush and invalidate a block device's cache

# progress callback: (phase, bytes_done, bytes_total_or_None)
ProgressCb = Callable[[str, int, Optional[int]], None]


class PipeburnError(Exception):
    """Base class. ``kind`` is a short machine-readable category."""

    kind = "error"


class Cancelled(PipeburnError):
    kind = "cancelled"


class ChecksumMismatch(PipeburnError):
    kind = "checksum"


class VerifyError(PipeburnError):
    kind = "verify"


class ImageTooLarge(PipeburnError):
    kind = "size"


@dataclass
class Result:
    downloaded: int  # bytes received from the server (as published)
    written: int  # bytes written to the target (after decompression)
    sha256: str  # sha256 of the bytes as downloaded (compare with the published one)
    compression: str  # "none", "gzip", "xz" or "zstd"
    verified: Optional[bool]  # True if read back and matched, None if not checked

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Decompression
# --------------------------------------------------------------------------


def detect_compression(head: bytes) -> str:
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if head.startswith(b"\x28\xb5\x2f\xfd"):
        return "zstd"
    return "none"


class _IdentityDecoder:
    def decompress(self, data: bytes) -> bytes:
        return data

    def finish(self) -> bytes:
        return b""


class _GzipDecoder:
    def __init__(self) -> None:
        self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._mid = False  # inside an unfinished member

    def decompress(self, data: bytes) -> bytes:
        out = []
        try:
            while data:
                out.append(self._d.decompress(data))
                if self._d.eof:
                    rest = self._d.unused_data
                    self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    self._mid = False
                    # concatenated gzip members are legal; anything else is padding
                    data = rest if rest.startswith(b"\x1f\x8b") else b""
                else:
                    self._mid = True
                    data = b""
        except zlib.error as e:
            raise PipeburnError(f"Corrupt gzip data: {e}") from e
        return b"".join(out)

    def finish(self) -> bytes:
        if self._mid:
            raise PipeburnError("The gzip stream ended early (truncated download?).")
        return b""


class _XzDecoder:
    def __init__(self) -> None:
        self._d = lzma.LZMADecompressor()
        self._mid = False

    def decompress(self, data: bytes) -> bytes:
        out = []
        try:
            while data:
                if self._d.eof:
                    self._d = lzma.LZMADecompressor()
                out.append(self._d.decompress(data))
                if self._d.eof:
                    data = self._d.unused_data.lstrip(b"\x00")
                    self._mid = False
                else:
                    self._mid = True
                    data = b""
        except lzma.LZMAError as e:
            raise PipeburnError(f"Corrupt xz data: {e}") from e
        return b"".join(out)

    def finish(self) -> bytes:
        if self._mid:
            raise PipeburnError("The xz stream ended early (truncated download?).")
        return b""


def _new_zstd_decompressor():
    try:  # Python 3.14+
        from compression.zstd import ZstdDecompressor

        return ZstdDecompressor()
    except ImportError:
        pass
    try:
        import zstandard

        return zstandard.ZstdDecompressor().decompressobj()
    except ImportError:
        raise PipeburnError(
            "This image is zstd-compressed. Use Python 3.14+ or install the "
            "extra: pip install 'pipeburn[zstd]'"
        ) from None


class _ZstdDecoder:
    def __init__(self) -> None:
        self._d = _new_zstd_decompressor()

    def decompress(self, data: bytes) -> bytes:
        try:
            return self._d.decompress(data)
        except Exception as e:  # library-specific error types
            raise PipeburnError(f"Corrupt zstd data: {e}") from e

    def finish(self) -> bytes:
        if getattr(self._d, "eof", True) is False:
            raise PipeburnError("The zstd stream ended early (truncated download?).")
        return b""


def make_decoder(compression: str):
    return {
        "none": _IdentityDecoder,
        "gzip": _GzipDecoder,
        "xz": _XzDecoder,
        "zstd": _ZstdDecoder,
    }[compression]()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def normalize_sha256(value: Optional[str]) -> Optional[str]:
    """Accept a bare hash or a ``sha256sum`` line ('<hash>  file.iso')."""
    if value is None or not value.strip():
        return None
    token = value.strip().split()[0].lower()
    if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
        raise PipeburnError("The SHA256 must be 64 hexadecimal characters.")
    return token


_FALLBACK_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt")


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca"):
        return ctx
    candidates = []
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates.extend(_FALLBACK_CA_FILES)
    for path in candidates:
        if os.path.isfile(path):
            try:
                ctx.load_verify_locations(cafile=path)
            except (ssl.SSLError, OSError):
                continue
            log.debug("No default CA certificates; loaded %s", path)
            return ctx
    return ctx


def open_url(url: str, timeout: float = 30.0):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise PipeburnError("The URL must start with http:// or https://")
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    try:
        resp = urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())
    except urllib.error.HTTPError as e:
        raise PipeburnError(f"The server answered HTTP {e.code} {e.reason}.") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise PipeburnError(
                f"Could not verify the server's certificate: {reason}\n"
                "Python has no trusted CA certificates. On macOS run "
                "'Install Certificates.command' from your Python folder in Applications, "
                "or 'pip install certifi'; or set SSL_CERT_FILE to a CA bundle."
            ) from e
        raise PipeburnError(f"Could not connect: {reason}") from e
    if urllib.parse.urlparse(resp.geturl()).scheme not in ("http", "https"):
        resp.close()
        raise PipeburnError("The server redirected to a non-HTTP address; refusing.")
    return resp


def _content_length(resp) -> Optional[int]:
    try:
        n = int(resp.headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _read_chunk(resp) -> bytes:
    try:
        return resp.read(READ_CHUNK)
    except (OSError, http.client.HTTPException) as e:
        raise PipeburnError(f"The download failed: {e}") from e


def _check_cancel(cancel: Optional[threading.Event]) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("Cancelled.")


def _write_all(f, data) -> None:
    view = memoryview(data)
    try:
        offset = 0
        while offset < len(view):
            offset += f.write(view[offset:])
    finally:
        view.release()


def _drop_cache(fd: int) -> None:
    """Best effort: make a later read-back hit the device, not the page cache."""
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError) as e:
        log.debug("posix_fadvise skipped: %s", e)
    try:
        import fcntl

        fcntl.ioctl(fd, _BLKFLSBUF)
    except Exception as e:  # not a block device, not root, or not Linux
        log.debug("Cache flush ioctl skipped: %s", e)


def _write_error(e: OSError, target: str) -> PipeburnError:
    if e.errno == errno.ENOSPC:
        return ImageTooLarge("The image is larger than the target drive.")
    if isinstance(e, PermissionError):
        return PipeburnError(f"Permission denied writing to {target}. Raw devices need root.")
    return PipeburnError(f"Writing to {target} failed: {e.strerror or e}")


class _Throttle:
    """Rate-limit progress callbacks so a fast link doesn't flood the UI."""

    def __init__(self, callback: Optional[ProgressCb], interval: float = 0.1):
        self._callback = callback
        self._interval = interval
        self._last = 0.0

    def __call__(self, phase: str, done: int, total: Optional[int], force: bool = False) -> None:
        if self._callback is None:
            return
        now = time.monotonic()
        if force or now - self._last >= self._interval:
            self._last = now
            self._callback(phase, done, total)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def verify_readback(
    target: str,
    length: int,
    expected_digest: str,
    progress: Optional[ProgressCb] = None,
    cancel: Optional[threading.Event] = None,
) -> bool:
    """Re-read ``length`` bytes from ``target`` and compare their sha256."""
    digest = hashlib.sha256()
    done = 0
    with open(target, "rb", buffering=0) as f:
        _drop_cache(f.fileno())
        while done < length:
            _check_cancel(cancel)
            data = f.read(min(WRITE_CHUNK, length - done))
            if not data:
                log.warning("Read-back ended early at %d of %d bytes", done, length)
                return False  # target shorter than what we wrote
            digest.update(data)
            done += len(data)
            if progress is not None:
                progress("verify", done, length)
    matches = digest.hexdigest() == expected_digest
    if not matches:
        log.warning("Read-back hash differs: expected %s, got %s", expected_digest, digest.hexdigest())
    return matches


def burn(
    url: str,
    target: str,
    *,
    expected_sha256: Optional[str] = None,
    verify: bool = True,
    decompress: str = "auto",
    device_size: Optional[int] = None,
    progress: Optional[ProgressCb] = None,
    cancel: Optional[threading.Event] = None,
    timeout: float = 30.0,
) -> Result:
    """Stream ``url`` onto ``target`` (a block device or a plain file).

    The SHA256 is computed over the bytes as downloaded, so it matches what
    distros publish even when the file is .xz/.gz/.zst on the wire. It is
    checked after writing; a mismatch means the target now holds a bad image.
    """
    expected = normalize_sha256(expected_sha256)
    if decompress not in ("auto", "none"):
        raise ValueError("decompress must be 'auto' or 'none'")
    emit = _Throttle(progress)
    log.info(
        "Starting: url=%s target=%s verify=%s decompress=%s checksum_given=%s",
        redact_url(url), target, verify, decompress, expected is not None,
    )

    with open_url(url, timeout) as resp:
        total = _content_length(resp)
        log.debug(
            "HTTP %s from %s (content-type %s, content-length %s)",
            getattr(resp, "status", "?"), redact_url(resp.geturl()),
            resp.headers.get("Content-Type"), total,
        )
        first = _read_chunk(resp)
        if not first:
            raise PipeburnError("The server returned an empty file.")
        log.debug("First bytes: %s", first[:8].hex())
        compression = "none" if decompress == "none" else detect_compression(first)
        log.info(
            "Compression: %s; %s", compression,
            f"{human_bytes(total)} to download" if total is not None else "size unknown",
        )

        # An uncompressed image that cannot fit is rejected before anything is written.
        if compression == "none" and device_size is not None and total is not None and total > device_size:
            raise ImageTooLarge(
                f"The image is {human_bytes(total)} but the drive holds only {human_bytes(device_size)}."
            )

        decoder = make_decoder(compression)
        sha_in, sha_out = hashlib.sha256(), hashlib.sha256()
        downloaded = written = 0
        buf = bytearray()

        try:
            with open(target, "wb", buffering=0) as out:

                def flush() -> None:
                    nonlocal written
                    if not buf:
                        return
                    if device_size is not None and written + len(buf) > device_size:
                        raise ImageTooLarge(
                            f"The decompressed image is larger than the drive ({human_bytes(device_size)})."
                        )
                    _write_all(out, buf)
                    sha_out.update(buf)
                    if (written + len(buf)) // _LOG_STEP != written // _LOG_STEP:
                        log.debug("Wrote %s so far", human_bytes(written + len(buf)))
                    written += len(buf)
                    buf.clear()

                chunk = first
                while chunk:
                    _check_cancel(cancel)
                    downloaded += len(chunk)
                    sha_in.update(chunk)
                    buf += decoder.decompress(chunk)
                    if len(buf) >= WRITE_CHUNK:
                        flush()
                    emit("write", downloaded, total)
                    chunk = _read_chunk(resp)

                if total is not None and downloaded != total:
                    raise PipeburnError(
                        f"The connection closed early: got {human_bytes(downloaded)} "
                        f"of {human_bytes(total)}."
                    )
                buf += decoder.finish()
                flush()
                os.fsync(out.fileno())
                _drop_cache(out.fileno())
        except OSError as e:
            raise _write_error(e, target) from e

    emit("write", downloaded, total, force=True)

    digest = sha_in.hexdigest()
    log.info(
        "Download complete: %s received, %s written, sha256 %s",
        human_bytes(downloaded), human_bytes(written), digest,
    )
    if expected is not None and digest != expected:
        raise ChecksumMismatch(
            f"SHA256 mismatch.\nExpected: {expected}\nGot:      {digest}\n"
            "The drive now holds a bad image; do not boot from it."
        )
    if expected is not None:
        log.info("SHA256 matches the expected value")

    verified: Optional[bool] = None
    if verify:
        log.info("Verifying what was written (%s)", human_bytes(written))
        if not verify_readback(target, written, sha_out.hexdigest(), progress=emit, cancel=cancel):
            raise VerifyError(
                "Read-back check failed: the drive did not keep what was written "
                "(a failing or fake-capacity stick?)."
            )
        emit("verify", written, written, force=True)
        verified = True
        log.info("Read-back verification passed")

    return Result(downloaded, written, digest, compression, verified)
