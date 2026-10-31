"""Install the agent as a system service: systemd on Linux, WinSW on Windows."""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .config import base_dir, default_config_path

SERVICE = "camagent"
UNIT_PATH = Path("/etc/systemd/system/camagent.service")
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
WINSW_SHA256 = "05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da"   # it runs the service: check it

# Windows (0.17): the service runs as its own limited account, not SYSTEM, so if a bad camera stream ever broke
# ffmpeg it couldn't take over the computer. [agent] windows_account in camagent.toml picks it:
WINDOWS_ACCOUNTS = {
    "limited": "NT SERVICE\\camagent",          # a "virtual account" just for this service (opt-in, 0.17.4)
    "localservice": "NT AUTHORITY\\LocalService",   # shared with other services (opt-in)
    "system": "LocalSystem",                      # the default: as before 0.17 (full system rights)
}
LOCAL_SERVICE_SID = "S-1-5-19"


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
        hint = ("from an administrator Command Prompt or PowerShell (or the CamAgent shortcut in the Start menu)"
                if os.name == "nt" else "with sudo in front (for example: sudo camagent)")
        raise SystemExit(f"This needs administrator rights. Run it again {hint}.")


def _run(cmd, check=True):
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=check)


# ---------- Linux ----------

def _linux_unit(config_path) -> str:
    try:
        import pwd
        pwd.getpwnam("camagent")
        user = "User=camagent\n"
        # The video engines (Raspberry Pi /dev/video10-12, Rockchip and Intel /dev/dri, /dev/mpp_service) are open
        # only to these groups; without them the encoder check fails and the server makes the lower qualities.
        import grp
        groups = []
        for g in ("video", "render"):
            try:
                grp.getgrnam(g)
                groups.append(g)
            except KeyError:
                pass
        if groups:
            user += f"SupplementaryGroups={' '.join(groups)}\n"
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
# CamAgent itself stops within 15 s; an ffmpeg stuck in a video driver can't be killed at all, so don't wait the
# default 90 s for it on every restart (2026-10-22, Pi 3B: a leftover made each restart take 1.5 minutes)
TimeoutStopSec=15
# Sandbox (0.16): ffmpeg reads video from the network, so if a bad camera stream ever broke it, it still couldn't
# change the system, read home folders, or gain privileges. It may write only its own /var/lib/camagent and a private
# /tmp. Devices stay reachable for the hardware encoders (/dev/dri, /dev/video*).
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
UMask=0077

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
  <arguments>-I -u -m camagent run --config "{config_path}"</arguments>
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


def service_sid(name=SERVICE) -> str:
    """The SID Windows gives a service's own account (NT SERVICE\\<name>): S-1-5-80- and the SHA-1 of the upper-case
    name in UTF-16LE, as five numbers. Known without asking Windows (folder permissions are set by SID: account
    names differ by language)."""
    import hashlib
    import struct
    digest = hashlib.sha1(name.upper().encode("utf-16-le")).digest()
    return "S-1-5-80-" + "-".join(str(n) for n in struct.unpack("<5I", digest))


def windows_account(config_path=None):
    """(account name for sc.exe, its SID or None for SYSTEM, the setting's name)."""
    from .config import load_or_defaults
    try:
        mode = str(load_or_defaults(config_path)["agent"].get("windows_account") or "system").strip().lower()
    except Exception:  # noqa: BLE001 - a broken settings file: as before 0.17
        mode = "system"
    # "virtual" is what 0.17.0-0.17.2 wrote into every settings file as their default, not a host's choice: it means
    # the default now. The limited account is chosen with "limited" (0.17.4).
    if mode not in WINDOWS_ACCOUNTS:
        mode = "system"
    sid = {"limited": service_sid(), "localservice": LOCAL_SERVICE_SID}.get(mode)
    return WINDOWS_ACCOUNTS[mode], sid, mode


def _icacls(*args):
    r = subprocess.run(["icacls", *[str(a) for a in args]], capture_output=True, text=True)
    if r.returncode:
        print(f"  (couldn't change the permissions of {args[0]}: {(r.stdout + r.stderr).strip()[-200:]})")
    return r.returncode == 0


def _win_grant_access(sid):
    """What the service's account may do in CamAgent's folder (Administrators and SYSTEM only otherwise): read
    and run everything (settings, the program), write only its logs, its state and status.json. Never the
    YonderView sign-in (account.toml, kept to Administrators and SYSTEM)."""
    from .config import account_path, lock_windows_file, state_dir, status_path
    base = base_dir()
    _icacls(base, "/grant", f"*{sid}:(OI)(CI)(RX)")
    for d in (base / "logs", state_dir()):
        d.mkdir(parents=True, exist_ok=True)
        _icacls(d, "/grant", f"*{sid}:(OI)(CI)(M)")
    status = status_path()
    if not status.exists():
        status.write_text("{}", encoding="utf-8")
    _icacls(status, "/grant", f"*{sid}:(M)")             # rewritten in place (it can't make files next to it)
    acct = account_path()
    if acct.exists():
        lock_windows_file(acct)


def _configured_ffmpeg(config_path=None):
    """The ffmpeg the cameras' settings name, if any (they all share one)."""
    try:
        from .config import load_all
        cams = load_all(config_path)
        return (cams[0]["stream"].get("ffmpeg", "") if cams else "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _win_apply_account(config_path=None):
    """Run the service as its own account (see WINDOWS_ACCOUNTS). Takes effect at its next start. A limited account
    only when everything the service runs is in an all-users folder: otherwise (e.g. CamAgent made long ago on a
    one-user Python) it couldn't even start (0.17.0 on such a PC: "No Python at ..." every few seconds), so it stays
    LocalSystem and says why (doctor, the camera's Health tab)."""
    from .prereqs import ffmpeg_in_a_profile, unsafe_python
    name, sid, mode = windows_account(config_path)
    blocker = (unsafe_python() or ffmpeg_in_a_profile(_configured_ffmpeg(config_path))) if sid else ""
    if blocker:
        print(f"  CamAgent stays on LocalSystem for now: {blocker}.")
        name, sid = WINDOWS_ACCOUNTS["system"], None
    if sid:
        _win_grant_access(sid)
    # no "password=" at all: an empty one made Windows refuse the virtual account (error 1057, 0.17.0)
    cmd = ["sc", "config", SERVICE, "obj=", name]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(f"  (couldn't set the account CamAgent runs as to {name}: {(r.stdout + r.stderr).strip()[-200:]})")
        return False
    if mode != "system":
        print(f"  CamAgent runs as {name} ([agent] windows_account = \"{mode}\" in camagent.toml).")
    return True


def _winsw_ok(exe) -> bool:
    import hashlib
    try:
        return hashlib.sha256(Path(exe).read_bytes()).hexdigest() == WINSW_SHA256
    except OSError:
        return False


def _download_checked(url, sha256, dest):
    import hashlib
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f"The download from {url} isn't the expected file (its checksum differs), so it wasn't "
                         "installed. Try again later; if it keeps happening, contact support (Report a problem on "
                         "yonderview.net).")
    Path(dest).write_bytes(data)


# ---------- public ----------

def install(config_path=None):
    _require_admin()
    config_path = Path(config_path or default_config_path())
    if not config_path.exists():
        raise SystemExit(f"No settings found at {config_path}, so there's nothing to start yet. Set up a camera first: "
                         "open CamAgent and choose Add a camera (or type: camagent add)")

    if os.name == "nt":
        exe, xml = _win_paths()
        exe.parent.mkdir(parents=True, exist_ok=True)
        installed = is_installed()
        if not _winsw_ok(exe):                  # missing, or not the file it should be: checked on every install
            if exe.exists():
                print(f"The background helper {exe} isn't the expected file (its checksum differs); replacing it.")
                if installed:
                    subprocess.run(["sc", "stop", SERVICE], capture_output=True)
                    time.sleep(3)
            print(f"Downloading the helper that runs CamAgent in the background (WinSW) to {exe}")
            _download_checked(WINSW_URL, WINSW_SHA256, exe)
        xml.write_text(_win_xml(config_path), encoding="utf-8")
        if installed:
            _run([exe, "stop"], check=False)
        else:
            _run([exe, "install"])
        _win_apply_account(config_path)
        _run([exe, "start"])
        print(f"CamAgent now runs in the background and starts by itself with the computer. Its log is in "
              f"{base_dir() / 'logs'} (or open CamAgent > Show the recent log).")
    else:
        UNIT_PATH.write_text(_linux_unit(config_path), encoding="utf-8")
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", SERVICE])
        _run(["systemctl", "restart", SERVICE])
        print("CamAgent now runs in the background and starts by itself with the computer. To see its log: open "
              "CamAgent > Show the recent log (or type: journalctl -u camagent -f)")
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
    print("CamAgent no longer runs in the background, so no camera is being sent from this computer.")


def is_installed() -> bool:
    if os.name == "nt":
        r = subprocess.run(["sc", "query", SERVICE], capture_output=True, text=True)
        return r.returncode == 0
    return UNIT_PATH.exists()


def refresh_unit(config_path=None):
    """Rewrite the service's settings so ones added in newer versions apply (after an update, or the installer):
    the systemd unit on Linux; on Windows the WinSW settings and the account it runs as (0.17)."""
    if os.name == "nt":
        if not is_installed():
            return
        try:
            from .config import migrate_account
            migrate_account(config_path)        # the sign-in out of camagent.toml, before the service can read it
        except Exception:  # noqa: BLE001
            pass
        exe, xml = _win_paths()
        xml.write_text(_win_xml(Path(config_path or default_config_path())), encoding="utf-8")
        _win_apply_account(config_path)
        return
    if not UNIT_PATH.exists():
        return
    UNIT_PATH.write_text(_linux_unit(Path(config_path or default_config_path())), encoding="utf-8")
    _run(["systemctl", "daemon-reload"], check=False)
    # Updates from 0.15 run this (the new code) but not the new updater: move the sign-in to its root-only file here
    try:
        from .config import migrate_account
        migrate_account(config_path)
    except Exception:  # noqa: BLE001 - never stop an update over this; the menu and later updates try again
        pass


def restart():
    _require_admin()
    if not is_installed():
        print('CamAgent isn\'t running in the background yet. Open CamAgent and choose "Start CamAgent".')
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
    """Nightly around 3 AM (with a random delay so agents don't all check at once), as root / SYSTEM (installing
    needs those rights; the service itself runs as its own limited account)."""
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
    print("Automatic updates: on (CamAgent checks for a new version each night, around 3 AM).")


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
