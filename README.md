# Pipeburn

**Write ISOs to USB straight from a URL.**

Paste a link, pick a drive, and Pipeburn streams the image directly to the device while hashing it on the fly. It's the `curl | dd` workflow with a GUI and safety rails: USB-only device picker, a confirmation showing the drive's model and size, optional SHA256 verification, and on-the-fly `.xz` / `.gz` / `.zst` decompression.

> **Status: early (v0.1), Linux only.** The core and GUI are tested against local HTTP servers and plain files. Try it on a spare stick first, because writing to a drive erases it.

## Install

```bash
git clone https://github.com/Hexcrown/pipeburn
cd pipeburn
python3 -m venv .venv && . .venv/bin/activate
pip install .            # or: pip install ".[zstd]" for .zst images on Python < 3.14
pipeburn
```

Needs `lsblk` and `umount` (util-linux, present on practically every distro) and `pkexec` (polkit) for the root prompt.

On macOS only `--dry-run` works so far. If you see a certificate error with a python.org install, run `Install Certificates.command` from the Python folder in Applications.

Want to look around without touching a drive? Run `pipeburn --dry-run /tmp/test.img`. It writes to a file instead.

## How it works

```
GUI (PySide6, unprivileged)  --pkexec-->  worker (root, stdlib only)
   URL, checksum, drive          JSON lines     validate -> unmount -> stream -> fsync -> read back
```

The worker re-checks the target itself, so a bug or tampering in the GUI cannot point it at a system disk.

- **USB disks only.** Internal disks, read-only media and empty card-reader slots never appear.
- **Never the running system.** Disks with `/`, `/boot`, `/home`, `/usr`, `/var`, swap or a live-USB mount are excluded.
- **Confirmation** shows model, size, device path and what will be unmounted.
- **SHA256** is computed over the bytes as downloaded, so it matches the checksum a distro publishes even for `.xz` / `.gz` / `.zst` downloads.
- **Read-back verification** re-reads what was written and compares hashes (best effort at bypassing the page cache).

## Logging and debugging

- **Log file:** `~/.local/state/pipeburn/pipeburn.log` (or `$XDG_STATE_HOME/pipeburn/`), rotating at about 1 MB with three older files kept. The same lines show in the window's log pane.
- **`pipeburn --debug`** adds detail (HTTP headers, first bytes of the image, write progress, drive lookups) and turns on debug logging in the root helper too.
- **Copy log** puts the session log, with version and Python info, on the clipboard. Paste it into a bug report.
- **The helper runs as root and never writes into your home directory.** Its log lines are sent to the GUI, which writes them to the file. Unexpected errors include a full traceback.
- **Secrets stay out.** URLs are logged without credentials, query strings or fragments (signed download links keep secrets there), and your home directory shows as `~` in copied logs. A log can still contain drive names, file paths and a checksum you entered, so skim it before posting publicly.

## Limits

- **No resume.** If the connection drops mid-write, the drive is half-written; start over.
- **The checksum is checked after writing.** A mismatch means the drive holds a bad image. Pipeburn says so, but it cannot know in advance.
- **Compressed images can't be size-checked up front.** An uncompressed image larger than the drive is rejected before writing; a compressed one is caught when it overflows.
- **Cancel leaves a partial write.** Burn again before using the drive.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The core (`core.py`) is standard library only and has no GUI imports, so most tests run without Qt. The GUI tests use the offscreen Qt platform and skip if PySide6 is missing.

```
src/pipeburn/
  core.py       stream, decompress, hash, write, verify
  devices.py    USB-only discovery and safety checks (lsblk)
  worker.py     privileged helper, reports JSON lines
  launcher.py   builds the pkexec command
  logs.py       log file, in-memory log for Copy log, URL redaction
  gui.py        PySide6 window
  assets/       app icon (svg, png, icns, ico)
packaging/      .desktop launcher for Linux
```

## Roadmap

- polkit policy file so the password prompt names Pipeburn instead of `env`
- Windows and macOS device backends
- AppImage / Flatpak packaging
- saved list of favorite image URLs

## License

MIT
