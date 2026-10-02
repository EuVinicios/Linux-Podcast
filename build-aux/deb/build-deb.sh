#!/usr/bin/env bash
# Build an architecture-independent .deb (Ubuntu 26.04+ / Debian with GNOME 50+).
set -euo pipefail

cd "$(dirname "$0")/../.."
PACKAGE=podflow
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' src/config.py)"
STAGE="build/deb/${PACKAGE}_${VERSION}_all"
OUTPUT="dist/${PACKAGE}_${VERSION}_all.deb"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" dist

make --no-print-directory install DESTDIR="$PWD/$STAGE" PREFIX=/usr PYTHON=/usr/bin/python3

DOC="$STAGE/usr/share/doc/$PACKAGE"
install -d "$DOC"
install -m644 build-aux/deb/copyright "$DOC/copyright"
{
    echo "$PACKAGE ($VERSION) unstable; urgency=medium"
    echo
    echo "  * Release $VERSION. See https://github.com/EuVinicios/Linux-Podcast/releases"
    echo
    echo " -- EuVinicios <EuVinicios@users.noreply.github.com>  $(date -R)"
} | gzip -9n > "$DOC/changelog.gz"

find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE/usr" -type f -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/podflow"

INSTALLED_SIZE="$(du -sk "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PACKAGE
Version: $VERSION
Architecture: all
Maintainer: EuVinicios <EuVinicios@users.noreply.github.com>
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.11), python3-gi (>= 3.50), gir1.2-glib-2.0, gir1.2-gtk-4.0 (>= 4.20), gir1.2-adw-1 (>= 1.9), gir1.2-graphene-1.0, gir1.2-pango-1.0, gir1.2-gdkpixbuf-2.0, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good
Recommends: gstreamer1.0-libav, gstreamer1.0-pipewire | gstreamer1.0-pulseaudio
Section: sound
Priority: optional
Homepage: https://github.com/EuVinicios/Linux-Podcast
Description: native GNOME podcast player with Brazilian charts
 PodFlow is a podcast app for GNOME built with GTK 4 and Libadwaita,
 inspired by the curation of Apple Podcasts in Brazil: top podcasts and
 episodes, an Explore page with featured shows and genre shelves, an
 Up Next queue, continue listening, offline downloads, a sleep timer and
 pitch-preserving playback speed. Integrates with GNOME Shell media
 controls through MPRIS.
EOF

dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$OUTPUT" >/dev/null
echo "Pacote gerado: $OUTPUT"
dpkg-deb --info "$OUTPUT" | sed -n '1,4p;/Package:/,$p' | head -20
