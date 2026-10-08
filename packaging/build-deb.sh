#!/bin/sh
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(python3 -c "import tomllib,sys;print(tomllib.load(open(sys.argv[1],'rb'))['project']['version'])" "$ROOT/pyproject.toml")
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
OUT="${1:-$ROOT/dist}"
mkdir -p "$OUT"

PKG="$STAGE/pkg"
install -d "$PKG/DEBIAN" "$PKG/usr/lib/python3/dist-packages" "$PKG/usr/bin" "$PKG/usr/libexec" \
    "$PKG/usr/share/applications" "$PKG/usr/share/polkit-1/actions" \
    "$PKG/usr/share/icons/hicolor/512x512/apps" "$PKG/usr/share/icons/hicolor/scalable/apps" \
    "$PKG/usr/share/doc/pipeburn"

cp -r "$ROOT/src/pipeburn" "$PKG/usr/lib/python3/dist-packages/pipeburn"
find "$PKG/usr/lib/python3/dist-packages/pipeburn" -name __pycache__ -prune -exec rm -rf {} +
find "$PKG/usr/lib/python3/dist-packages/pipeburn" -type d -exec chmod 755 {} +
find "$PKG/usr/lib/python3/dist-packages/pipeburn" -type f -exec chmod 644 {} +

cat > "$PKG/usr/bin/pipeburn" <<'LAUNCH'
#!/usr/bin/python3
import sys

try:
    from pipeburn.gui import main
except ImportError as e:
    if "PySide6" not in str(e):
        raise
    sys.exit(
        "Pipeburn needs PySide6, which this system does not provide as a package.\n"
        "Install it with:  pip install --user --break-system-packages PySide6"
    )

sys.exit(main())
LAUNCH
chmod 755 "$PKG/usr/bin/pipeburn"

install -m 755 "$ROOT/packaging/pipeburn-worker" "$PKG/usr/libexec/pipeburn-worker"
install -m 644 "$ROOT/packaging/io.github.hexcrown.pipeburn.policy" "$PKG/usr/share/polkit-1/actions/"
install -m 644 "$ROOT/packaging/pipeburn.desktop" "$PKG/usr/share/applications/pipeburn.desktop"
install -m 644 "$ROOT/src/pipeburn/assets/pipeburn.png" "$PKG/usr/share/icons/hicolor/512x512/apps/pipeburn.png"
install -m 644 "$ROOT/src/pipeburn/assets/pipeburn.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/pipeburn.svg"
install -m 644 "$ROOT/LICENSE" "$PKG/usr/share/doc/pipeburn/copyright"

cat > "$PKG/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    rm -rf /usr/lib/python3/dist-packages/pipeburn
fi
POSTRM
chmod 755 "$PKG/DEBIAN/postrm"

SIZE=$(du -sk "$PKG" | cut -f1)
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: pipeburn
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Installed-Size: $SIZE
Depends: python3 (>= 3.10), pkexec | policykit-1, util-linux
Recommends: python3-pyside6.qtwidgets, python3-zstandard
Maintainer: Hexcrown <339384112+Hexcrown@users.noreply.github.com>
Homepage: https://github.com/Hexcrown/pipeburn
Description: Write an ISO from a URL straight to a USB drive
 Pipeburn streams an image from a URL to a USB drive without saving it first,
 hashing it on the way and optionally reading the drive back to verify it.
 .
 It decompresses .xz, .gz and .zst images on the fly and only offers
 removable USB drives.
CONTROL

dpkg-deb --root-owner-group --build "$PKG" "$OUT/pipeburn_${VERSION}_all.deb" >/dev/null
echo "$OUT/pipeburn_${VERSION}_all.deb"
