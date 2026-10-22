#!/usr/bin/env bash
# camagent installer for Debian, Ubuntu, and Raspberry Pi OS.
#   curl -fsSL https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.sh | sudo bash
# Re-running it upgrades camagent and keeps your config.
#
# Private repository? Run with a read-only token:
#   sudo CAMAGENT_REPO=https://TOKEN@github.com/msullivan1993/camagent.git bash install.sh
set -euo pipefail

REPO="${CAMAGENT_REPO:-https://github.com/msullivan1993/camagent.git}"
REF="${CAMAGENT_REF:-}"     # default: the latest published release (below), never untested "main"
APP=/opt/camagent
ETC=/etc/camagent

if [ "$(id -u)" -ne 0 ]; then
  echo "This installer needs administrator (root) rights. Run it with sudo, like this:" >&2
  echo "  curl -fsSL https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.sh | sudo bash" >&2
  exit 1
fi

echo "==> Installing the programs CamAgent needs (Python, ffmpeg, Git). This can take a few minutes..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git curl >/dev/null

python3 - << 'EOF'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"CamAgent needs Python 3.11 or newer, but this system has {sys.version.split()[0]}. "
             "Update the operating system (for example to Raspberry Pi OS Bookworm or Ubuntu 24.04), then run this again.")
EOF

echo "==> Setting up CamAgent's folders, and its own user account to run as"
id camagent >/dev/null 2>&1 || useradd --system --home-dir /var/lib/camagent --no-create-home --shell /usr/sbin/nologin camagent
mkdir -p "$APP" "$ETC"
chown root:camagent "$ETC"
chmod 750 "$ETC"

if [ -z "$REF" ]; then
  REF=$(curl -fsSL -H "User-Agent: camagent-installer" https://api.github.com/repos/msullivan1993/camagent/releases/latest \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name") or "")') || REF=""
  if [ -z "$REF" ]; then
    echo "Couldn't find the latest CamAgent release (GitHub didn't answer). Check this computer's internet connection and try again in a few minutes." >&2
    exit 1
  fi
fi

echo "==> Installing CamAgent $REF"
[ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q --upgrade "camagent @ git+${REPO}@${REF}"
"$APP/venv/bin/pip" install -q --upgrade --force-reinstall --no-deps "camagent @ git+${REPO}@${REF}"
ln -sf "$APP/venv/bin/camagent" /usr/local/bin/camagent

echo
camagent version
echo
if [ -f "$ETC/camagent.toml" ]; then
  echo "Your existing settings were kept. To start using the new version now, type:"
  echo "  sudo camagent restart"
else
  echo "CamAgent is installed. Next, type this and choose \"Add a camera\":"
  echo "  sudo camagent"
fi
