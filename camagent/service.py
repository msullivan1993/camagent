"""Install the agent as a system service: systemd on Linux, WinSW on Windows."""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from .config import base_dir, default_config_path

SERVICE = "camagent"
UNIT_PATH = Path("/etc/systemd/system/camagent.service")
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"


def is_admin() -> bool:
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _require_admin():
    if not is_admin():
        hint = "an administrator Command Prompt or PowerShell" if os.name == "nt" else "sudo"
        raise SystemExit(f"This needs administrator rights. Run it again from {hint}.")


def _run(cmd, check=True):
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=check)


# ---------- Linux ----------

def _linux_unit(config_path) -> str:
    try:
        import pwd
        pwd.getpwnam("camagent")
        user = "User=camagent\n"
    except (KeyError, ImportError):
        user = ""
    return f"""[Unit]
Description=camagent (camera uplink and PTZ agent)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
{user}StateDirectory=camagent
Environment=PYTHONUNBUFFERED=1
Environment=HOME=/var/lib/camagent
Environment=XDG_CACHE_HOME=/var/lib/camagent/cache
ExecStart={sys.executable} -m camagent run --config {config_path}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


# ---------- Windows ----------

def _win_paths():
    d = base_dir()
    return d / "camagent-service.exe", d / "camagent-service.xml"


def _win_xml(config_path) -> str:
    log_dir = base_dir() / "logs"
    return f"""<service>
  <id>{SERVICE}</id>
  <name>camagent</name>
  <description>camagent (camera uplink and PTZ agent)</description>
  <executable>{sys.executable}</executable>
  <arguments>-u -m camagent run --config "{config_path}"</arguments>
  <workingdirectory>{base_dir()}</workingdirectory>
  <logpath>{log_dir}</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
  <onfailure action="restart" delay="5 sec"/>
  <stoptimeout>15 sec</stoptimeout>
</service>
"""


# ---------- public ----------

def install(config_path=None):
    _require_admin()
    config_path = Path(config_path or default_config_path())
    if not config_path.exists():
        raise SystemExit(f"No config at {config_path}. Run: camagent configure")

    if os.name == "nt":
        exe, xml = _win_paths()
        exe.parent.mkdir(parents=True, exist_ok=True)
        if not exe.exists():
            print(f"Downloading WinSW service wrapper to {exe}")
            urllib.request.urlretrieve(WINSW_URL, exe)
        xml.write_text(_win_xml(config_path), encoding="utf-8")
        if is_installed():
            _run([exe, "stop"], check=False)
        else:
            _run([exe, "install"])
        _run([exe, "start"])
        print(f"Service installed. Logs: {base_dir() / 'logs'}")
    else:
        UNIT_PATH.write_text(_linux_unit(config_path), encoding="utf-8")
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", SERVICE])
        _run(["systemctl", "restart", SERVICE])
        print("Service installed. Logs: journalctl -u camagent -f")
    from .config import load_or_defaults
    if load_or_defaults(config_path)["update"].get("auto", True) and not update_schedule_installed():
        install_update_schedule(config_path)


def uninstall():
    _require_admin()
    if os.name == "nt":
        exe, _ = _win_paths()
        if exe.exists():
            _run([exe, "stop"], check=False)
            _run([exe, "uninstall"], check=False)
    else:
        _run(["systemctl", "disable", "--now", SERVICE], check=False)
        UNIT_PATH.unlink(missing_ok=True)
        _run(["systemctl", "daemon-reload"], check=False)
    if update_schedule_installed():
        remove_update_schedule()
    print("Service removed.")


def is_installed() -> bool:
    if os.name == "nt":
        r = subprocess.run(["sc", "query", SERVICE], capture_output=True, text=True)
        return r.returncode == 0
    return UNIT_PATH.exists()


def refresh_unit(config_path=None):
    """Linux: rewrite the systemd unit so service settings added in newer versions apply."""
    if os.name == "nt" or not UNIT_PATH.exists():
        return
    UNIT_PATH.write_text(_linux_unit(Path(config_path or default_config_path())), encoding="utf-8")
    _run(["systemctl", "daemon-reload"], check=False)


def restart():
    _require_admin()
    if not is_installed():
        print("Service isn't installed; nothing to restart.")
        return
    if os.name == "nt":
        exe, _ = _win_paths()
        _run([exe, "restart"])
    else:
        _run(["systemctl", "restart", SERVICE])


# ---------- nightly update schedule ----------
TASK_NAME = "camagent update"
TIMER_PATH = Path("/etc/systemd/system/camagent-update.timer")
TIMER_SERVICE_PATH = Path("/etc/systemd/system/camagent-update.service")


def update_schedule_installed() -> bool:
    if os.name == "nt":
        return subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME], capture_output=True).returncode == 0
    return TIMER_PATH.exists()


def install_update_schedule(config_path=None):
    """Nightly around 3 AM (with a random delay so agents don't all check at once), as root / SYSTEM."""
    _require_admin()
    config_path = Path(config_path or default_config_path())
    if os.name == "nt":
        cmd = f'"{sys.executable}" -m camagent update --auto --jitter 3600 --config "{config_path}"'
        _run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", cmd, "/SC", "DAILY", "/ST", "03:00",
              "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"])
    else:
        TIMER_SERVICE_PATH.write_text(f"""[Unit]
Description=camagent nightly update check

[Service]
Type=oneshot
ExecStart={sys.executable} -m camagent update --auto --config {config_path}
""", encoding="utf-8")
        TIMER_PATH.write_text("""[Unit]
Description=camagent nightly update check

[Timer]
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
""", encoding="utf-8")
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", "--now", "camagent-update.timer"])
    print("Automatic updates: on (nightly, around 3 AM).")


def remove_update_schedule():
    _require_admin()
    if os.name == "nt":
        _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
    else:
        _run(["systemctl", "disable", "--now", "camagent-update.timer"], check=False)
        TIMER_PATH.unlink(missing_ok=True)
        TIMER_SERVICE_PATH.unlink(missing_ok=True)
        _run(["systemctl", "daemon-reload"], check=False)
    print("Automatic updates: off.")
