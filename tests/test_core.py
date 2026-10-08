import gzip
import hashlib
import logging
import lzma
import threading

import pytest

from pipeburn import core


def sha(data):
    return hashlib.sha256(data).hexdigest()


def burn(server, tmp_path, **kwargs):
    out = tmp_path / "out.img"
    result = core.burn(server.url("/img"), str(out), **kwargs)
    return result, out


def test_plain_image(server, tmp_path, image_data):
    server.serve("/img", image_data)
    result, out = burn(server, tmp_path)
    assert out.read_bytes() == image_data
    assert result.compression == "none"
    assert result.downloaded == result.written == len(image_data)
    assert result.sha256 == sha(image_data)
    assert result.verified is True


def test_gzip_is_decompressed_and_hash_covers_download(server, tmp_path, image_data):
    packed = gzip.compress(image_data)
    server.serve("/img", packed)
    result, out = burn(server, tmp_path)
    assert out.read_bytes() == image_data
    assert result.compression == "gzip"
    assert result.sha256 == sha(packed)
    assert result.downloaded == len(packed)


def test_gzip_multiple_members(server, tmp_path, image_data):
    a, b = image_data[:1_000_000], image_data[1_000_000:2_000_000]
    server.serve("/img", gzip.compress(a) + gzip.compress(b))
    _, out = burn(server, tmp_path)
    assert out.read_bytes() == a + b


def test_xz(server, tmp_path, image_data):
    packed = lzma.compress(image_data, preset=0)
    server.serve("/img", packed)
    result, out = burn(server, tmp_path)
    assert out.read_bytes() == image_data
    assert result.compression == "xz"


def test_zstd(server, tmp_path, image_data):
    zstandard = pytest.importorskip("zstandard")
    server.serve("/img", zstandard.ZstdCompressor().compress(image_data))
    result, out = burn(server, tmp_path)
    assert out.read_bytes() == image_data
    assert result.compression == "zstd"


def test_no_decompress_keeps_file_as_is(server, tmp_path, image_data):
    packed = gzip.compress(image_data[:100_000])
    server.serve("/img", packed)
    result, out = burn(server, tmp_path, decompress="none")
    assert out.read_bytes() == packed
    assert result.compression == "none"


def test_truncated_gzip_is_detected(server, tmp_path, image_data):
    packed = gzip.compress(image_data)
    server.serve("/img", packed[: len(packed) // 2])
    with pytest.raises(core.PipeburnError, match="ended early"):
        burn(server, tmp_path)


def test_checksum_match_accepts_uppercase_and_sha256sum_lines(server, tmp_path, image_data):
    server.serve("/img", image_data)
    burn(server, tmp_path, expected_sha256=sha(image_data).upper())
    burn(server, tmp_path, expected_sha256=sha(image_data) + "  distro.iso")


def test_checksum_mismatch(server, tmp_path, image_data):
    server.serve("/img", image_data)
    with pytest.raises(core.ChecksumMismatch) as info:
        burn(server, tmp_path, expected_sha256="0" * 64)
    assert info.value.kind == "checksum"
    assert "bad image" in str(info.value)


def test_malformed_checksum_fails_before_any_network_use(tmp_path):
    with pytest.raises(core.PipeburnError, match="64 hexadecimal"):
        core.burn("http://127.0.0.1:1/x", str(tmp_path / "o"), expected_sha256="abc")


def test_connection_closed_early(server, tmp_path, image_data):
    server.serve_truncated("/img", image_data[:200_000])
    with pytest.raises(core.PipeburnError, match="closed early"):
        burn(server, tmp_path)


def test_uncompressed_image_too_large_is_rejected_before_writing(server, tmp_path, image_data):
    server.serve("/img", image_data)
    out = tmp_path / "out.img"
    with pytest.raises(core.ImageTooLarge):
        core.burn(server.url("/img"), str(out), device_size=len(image_data) - 1)
    assert not out.exists()


def test_compressed_image_too_large_is_caught_while_writing(server, tmp_path, image_data):
    server.serve("/img", lzma.compress(image_data, preset=0))
    with pytest.raises(core.ImageTooLarge):
        burn(server, tmp_path, device_size=len(image_data) - 100)


def test_cancel(server, tmp_path, image_data):
    server.serve("/img", image_data)
    cancel = threading.Event()
    with pytest.raises(core.Cancelled):
        burn(server, tmp_path, cancel=cancel, progress=lambda *_: cancel.set())


def test_http_error(server, tmp_path):
    with pytest.raises(core.PipeburnError, match="404"):
        burn(server, tmp_path)


def test_redirect_is_followed(server, tmp_path, image_data):
    server.serve("/real", image_data)
    server.redirect("/img", "/real")
    result, out = burn(server, tmp_path)
    assert out.read_bytes() == image_data


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/x.iso", "not a url", ""])
def test_only_http_urls_are_accepted(url, tmp_path):
    with pytest.raises(core.PipeburnError, match="http"):
        core.burn(url, str(tmp_path / "o"))


def test_connection_refused(tmp_path):
    with pytest.raises(core.PipeburnError, match="connect"):
        core.burn("http://127.0.0.1:1/x.iso", str(tmp_path / "o"))


def test_empty_body(server, tmp_path):
    server.serve("/img", b"")
    with pytest.raises(core.PipeburnError, match="empty"):
        burn(server, tmp_path)


def test_unknown_content_length(server, tmp_path, image_data):
    server.serve_no_length("/img", image_data)
    events = []
    result, out = burn(server, tmp_path, progress=lambda *e: events.append(e))
    assert out.read_bytes() == image_data
    assert events[0][2] is None  # total unknown


def test_progress_reports_write_then_verify(server, tmp_path, image_data):
    server.serve("/img", image_data)
    events = []
    burn(server, tmp_path, progress=lambda *e: events.append(e))
    writes = [e for e in events if e[0] == "write"]
    assert writes[-1] == ("write", len(image_data), len(image_data))
    assert events[-1] == ("verify", len(image_data), len(image_data))


def test_verify_can_be_disabled(server, tmp_path, image_data):
    server.serve("/img", image_data)
    result, _ = burn(server, tmp_path, verify=False)
    assert result.verified is None


def test_verify_readback_detects_corruption_and_short_media(tmp_path):
    data = b"A" * 100_000
    path = tmp_path / "dev.img"
    path.write_bytes(data)
    digest = sha(data)
    assert core.verify_readback(str(path), len(data), digest)
    path.write_bytes(b"A" * 99_999 + b"B")
    assert not core.verify_readback(str(path), len(data), digest)
    path.write_bytes(data[:50_000])
    assert not core.verify_readback(str(path), len(data), digest)


def test_detect_compression():
    assert core.detect_compression(gzip.compress(b"x")) == "gzip"
    assert core.detect_compression(lzma.compress(b"x")) == "xz"
    assert core.detect_compression(b"\x28\xb5\x2f\xfd....") == "zstd"
    assert core.detect_compression(b"\x00" * 32768) == "none"


def test_logging_records_key_steps_and_redacts_url_secrets(server, tmp_path, image_data, captured_logs):
    server.serve("/img?token=SECRET", image_data)
    core.burn(server.url("/img?token=SECRET"), str(tmp_path / "out.img"), expected_sha256=sha(image_data))
    text = "\n".join(r.getMessage() for r in captured_logs)
    assert "SECRET" not in text
    for expected in ("Starting", "Compression: none", "Download complete", "SHA256 matches",
                     "Read-back verification passed"):
        assert expected in text


def test_debug_logging_includes_http_details(server, tmp_path, image_data, captured_logs):
    server.serve("/img", image_data)
    burn(server, tmp_path, verify=False)
    debug = [r.getMessage() for r in captured_logs if r.levelno == logging.DEBUG]
    assert any(m.startswith("HTTP 200") for m in debug)
    assert any(m.startswith("First bytes") for m in debug)


def test_readback_mismatch_is_logged_as_a_warning(tmp_path, captured_logs):
    path = tmp_path / "dev.img"
    path.write_bytes(b"A" * 1000)
    assert not core.verify_readback(str(path), 1000, "0" * 64)
    warnings = [r.getMessage() for r in captured_logs if r.levelno == logging.WARNING]
    assert any("differs" in m for m in warnings)


def test_ssl_context_falls_back_to_a_system_bundle(monkeypatch, tmp_path):
    import ssl

    class Bare:
        loaded = []

        def cert_store_stats(self):
            return {"x509_ca": 0}

        def load_verify_locations(self, cafile=None):
            self.loaded.append(cafile)

    bare = Bare()
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    monkeypatch.setattr(core.ssl, "create_default_context", lambda: bare)
    monkeypatch.setattr(core, "_certifi_path", lambda: None)
    monkeypatch.setattr(core, "_FALLBACK_CA_FILES", (str(bundle),))
    assert core._ssl_context() is bare
    assert str(bundle) in Bare.loaded


def test_certificate_failure_gets_a_helpful_message(monkeypatch):
    import ssl
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))

    monkeypatch.setattr(core.urllib.request, "urlopen", boom)
    with pytest.raises(core.PipeburnError, match="Install Certificates.command"):
        core.open_url("https://example.org/x.iso")


def test_icon_assets_ship_with_the_package():
    from pathlib import Path

    assets = Path(core.__file__).parent / "assets"
    for name in ("pipeburn.png", "pipeburn.svg", "pipeburn.icns", "pipeburn.ico"):
        assert (assets / name).stat().st_size > 0


def test_theme_stylesheet_is_well_formed():
    from pipeburn import theme

    css = theme.STYLESHEET
    assert css.count("{") == css.count("}")
    for colour in (theme.BG, theme.EMBER, theme.STEEL):
        assert colour in css
    assert "QPushButton#primary" in css


def test_package_version_matches_pyproject():
    import tomllib
    from pathlib import Path

    from pipeburn import __version__

    root = Path(core.__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert data["project"]["version"] == __version__


def test_helper_script_runs_isolated_and_only_calls_the_worker():
    from pathlib import Path

    root = Path(core.__file__).resolve().parents[2]
    text = (root / "packaging" / "pipeburn-worker").read_text()
    assert text.splitlines()[0] == "#!/usr/bin/python3 -I"
    assert "pipeburn.worker" in text
