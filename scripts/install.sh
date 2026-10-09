#!/usr/bin/env bash
# camagent installer for Debian, Ubuntu, and Raspberry Pi OS.
#   curl -fsSL https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.sh | sudo bash
# Re-running it upgrades camagent and keeps your config.
#
# Private repository? Run with a read-only token:
#   sudo CAMAGENT_REPO=https://TOKEN@github.com/msullivan1993/camagent.git bash install.sh
set -euo pipefail

REPO="${CAMAGENT_REPO:-https://github.com/msullivan1993/camagent.git}"
REF="${CAMAGENT_REF:-main}"
APP=/opt/camagent
ETC=/etc/camagent

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run as root (sudo)." >&2
  exit 1
fi

echo "==> Installing system packages (python3, ffmpeg, git)"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git >/dev/null

python3 - << 'EOF'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"Python 3.11 or newer is required (found {sys.version.split()[0]}).")
EOF

echo "==> Creating service account and folders"
id camagent >/dev/null 2>&1 || useradd --system --home-dir /var/lib/camagent --no-create-home --shell /usr/sbin/nologin camagent
mkdir -p "$APP" "$ETC"
chown root:camagent "$ETC"
chmod 750 "$ETC"

echo "==> Installing camagent from $REPO ($REF)"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q --upgrade "camagent @ git+${REPO}@${REF}"
"$APP/venv/bin/pip" install -q --upgrade --force-reinstall --no-deps "camagent @ git+${REPO}@${REF}"
ln -sf "$APP/venv/bin/camagent" /usr/local/bin/camagent

echo
camagent version
echo
if [ -f "$ETC/camagent.toml" ]; then
  echo "Existing config kept. Restart to apply the update:"
  echo "  sudo camagent restart"
else
  echo "Next step:"
  echo "  sudo camagent        (then choose Add a camera)"
fi
