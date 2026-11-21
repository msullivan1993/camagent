#!/bin/bash
# Build ffmpeg-rockchip (Rockchip video engine + RGA in ffmpeg) and install it in /opt/ffmpeg-rockchip, where
# CamAgent 0.25+ finds it. For Rockchip boards (RK3576 ROCK 4D, RK3588...) running Radxa OS / Debian 12 with the
# vendor kernel (it has the MPP and RGA drivers). Takes about 15-25 minutes on an 8-core board.
#
#   sudo apt install -y build-essential git pkg-config nasm librockchip-mpp-dev librga-dev libdrm-dev libx264-dev libssl-dev libsrt-openssl-dev libfreetype-dev
#   bash build-ffmpeg-rockchip.sh          (as your normal user; it asks for sudo only to copy into /opt)
#
# Pinned to one commit of nyanmisaka/ffmpeg-rockchip (the 7.1 line), so every board runs the same, known code.
set -euo pipefail
COMMIT=0dc90ed512267173074e96ba0a129c3219fd355f
PREFIX=/opt/ffmpeg-rockchip
WORK="${TMPDIR:-/tmp}/ffmpeg-rockchip-build"

for p in librockchip-mpp-dev librga-dev libdrm-dev libsrt-openssl-dev libfreetype-dev; do
  dpkg -s "$p" >/dev/null 2>&1 || { echo "Install the build tools first (see the top of this script): $p is missing."; exit 1; }
done
rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK"
git clone --branch 7.1 --single-branch https://github.com/nyanmisaka/ffmpeg-rockchip.git src
cd src
git checkout -q "$COMMIT"
[ "$(git rev-parse HEAD)" = "$COMMIT" ] || { echo "Not the pinned commit; stopping."; exit 1; }
./configure --prefix="$PREFIX" --enable-gpl --enable-version3 --enable-libdrm --enable-rkmpp --enable-rkrga \
  --enable-libx264 --enable-openssl --enable-libsrt --enable-libfreetype --disable-doc --disable-debug --disable-ffplay
make -j"$(nproc)"
sudo make install
"$PREFIX/bin/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q h264_rkmpp || { echo "Built, but without the Rockchip encoder."; exit 1; }
"$PREFIX/bin/ffmpeg" -hide_banner -protocols 2>/dev/null | grep -qw srt || { echo "Built, but without SRT (CamAgent sends video with it)."; exit 1; }
echo "Installed: $PREFIX/bin/ffmpeg (Rockchip video engine and RGA). Restart CamAgent to use it: sudo camagent restart"
