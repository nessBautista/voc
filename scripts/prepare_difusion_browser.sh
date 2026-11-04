#!/usr/bin/env bash
# Debian 12 user-space Chromium libraries; no system package installation.
set -euo pipefail
DIFUSION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
BROWSER_ROOT="$DIFUSION_ROOT/data/browser-libs"
APT_WORK="$(mktemp -d)"
trap 'rm -rf -- "$APT_WORK"' EXIT
mkdir -p "$APT_WORK/lists/partial" "$APT_WORK/cache/archives/partial" "$BROWSER_ROOT"
apt_options=(-o "Dir::State::lists=$APT_WORK/lists" -o "Dir::Cache=$APT_WORK/cache" -o Debug::NoLocking=true)
apt-get "${apt_options[@]}" update
apt-get "${apt_options[@]}" -y --download-only --no-install-recommends install \
  libglib2.0-0 libnss3 libatk1.0-0 libdbus-1-3 libx11-6 libxcomposite1 \
  libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 libxcb1 libxkbcommon0 \
  libasound2 libatspi2.0-0 fonts-dejavu-core
for archive in "$APT_WORK"/cache/archives/*.deb; do
  [[ -f "$archive" ]] && dpkg-deb -x "$archive" "$BROWSER_ROOT"
done
cat > "$BROWSER_ROOT/fonts.conf" <<EOF
<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig><dir>$BROWSER_ROOT/usr/share/fonts</dir><dir>/usr/share/fonts</dir><cachedir>$BROWSER_ROOT/font-cache</cachedir></fontconfig>
EOF
