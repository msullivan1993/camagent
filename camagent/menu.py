"""`camagent` with no command: a simple menu for everything, so nobody has to remember commands."""
import os
import subprocess
import sys
from collections import deque

from . import __version__, config, service


def _elevate_or_explain() -> bool:
    """True if we're already an administrator. On Windows, offers to reopen the menu as administrator."""
    if service.is_admin():
        return True
    if os.name == "nt":
        print("camagent needs administrator rights to read and change its settings.")
        if input("Reopen as administrator? (Y/n): ").strip().lower() in ("", "y", "yes"):
            import ctypes
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "-m camagent", None, 1)
            if ret > 32:
                return False                 # the new window takes over
            print("Windows didn't allow it. Open an administrator Command Prompt and run: camagent")
        return False
    print("camagent needs root to read and change its settings. Run:  sudo camagent")
    return False


def _auto_on(config_path=None):
    try:
        return bool(config.load_or_defaults(config_path)["update"].get("auto", True))
    except Exception:  # noqa: BLE001
        return True


def _service_state():
    from .doctor import _service_state as state
    return state()


def show_log(lines=40):
    if os.name == "nt":
        path = config.base_dir() / "logs" / "camagent-service.out.log"
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                tail = deque(f, maxlen=lines)
        except OSError:
            print(f"No log yet at {path}")
            return
        print(f"--- last {lines} lines of {path} ---")
        print("".join(tail).rstrip())
    else:
        subprocess.run(["journalctl", "-u", service.SERVICE, "-n", str(lines), "--no-pager"])


def _discover():
    from .camera import discover
    print("Searching (3 seconds)...")
    cams = discover()
    if not cams:
        print("No ONVIF cameras answered.")
    for c in cams:
        print(f"  {c['host']}:{c['port']}  {c['name']} {c['hardware']}".rstrip())


def installed_version():
    """The version now installed on disk (this menu may still be running an older one)."""
    import subprocess
    import sys
    try:
        out = subprocess.run([sys.executable, "-m", "camagent", "version"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or __version__
    except Exception:  # noqa: BLE001
        return __version__


def disk_version():
    """The version installed on disk right now. An update elsewhere (the nightly automatic one, or
    'camagent update' in another window) replaces the files under a menu that's already open."""
    import re
    from pathlib import Path
    try:
        text = (Path(__file__).resolve().parent / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else __version__
    except OSError:
        return __version__


def reopen_if_updated(config_path=None):
    """If camagent was updated while this menu was open, reopen it on the new version (instead of running the
    old code against new files)."""
    new = disk_version()
    if new != __version__:
        print(f"\ncamagent was updated to {new} while this menu was open (it's running {__version__}).")
        relaunch(config_path)


def relaunch(config_path=None):
    """Start the menu again on the newly installed version, replacing this one."""
    import os
    import subprocess
    import sys
    args = [sys.executable, "-m", "camagent"] + (["--config", str(config_path)] if config_path else [])
    print("Reopening camagent on the new version...\n")
    sys.stdout.flush()
    if os.name == "nt":                      # Windows: run the new menu here, then leave when it closes
        raise SystemExit(subprocess.call(args))
    os.execv(sys.executable, args)           # Linux/Pi: become the new menu in place


def _update_and_relaunch(config_path=None):
    from . import update
    update.run(config_path)
    new = installed_version()
    if new != __version__:
        print(f"\nUpdated from {__version__} to {new}.")
        relaunch(config_path)


def _account_label(config_path=None):
    try:
        acct = config.load_or_defaults(config_path)["account"]
    except Exception:  # noqa: BLE001
        return ""
    return f"signed in as {acct.get('name') or 'your account'}" if acct.get("token") else "not signed in"


def _do(label, action):
    """Run one menu action, turning problems into a plain message instead of a crash."""
    print()
    try:
        action()
    except SystemExit as e:
        if e.code not in (None, 0) and not isinstance(e.code, int):
            print(e.code)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
    except Exception as e:  # noqa: BLE001
        print(f"That didn't work: {e}")


def _choose(title, actions, back="Quit"):
    """Print a numbered list; returns the chosen (label, action), or None for 0 / Enter."""
    print(title)
    for i, (label, _) in enumerate(actions, 1):
        print(f"  {i}. {label}")
    print(f"  0. {back}")
    while True:
        choice = input("Choose: ").strip()
        if choice in ("0", "q", "quit", "exit", "back", "b", ""):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(actions):
            return actions[int(choice) - 1]
        print("Not a valid choice.")


def _submenu(title, actions):
    """A group of related actions: pick one (it runs), or 0 / Enter to go back."""
    picked = _choose(f"\n--- {title} ---", actions, back="Back")
    if picked:
        _do(*picked)
        return True
    return False


def run(config_path=None):
    if not _elevate_or_explain():
        return
    from . import configure, controls, doctor
    while True:
        reopen_if_updated(config_path)
        try:
            _, cams, problems = config.load_all(config_path)
        except Exception as e:  # noqa: BLE001
            cams, problems = [], [str(e)]
        state = _service_state()
        installed = state != "not installed"
        signed_in = bool(config.load_or_defaults(config_path)["account"].get("token")) if not problems else False
        camera_tools = [
            ("Change its settings", lambda: configure.run(config_path)),
            ("Weather station (beta)", lambda: configure.weather_station(config_path)),
            ("Remove it from this computer", lambda: configure.remove(config_path)),
        ]
        service_tools = [
            ("Restart the service" if installed else "Install and start the service",
             service.restart if installed else (lambda: service.install(config_path))),
            ("Show the recent log", show_log),
        ]
        update_tools = [
            ("Update camagent now", lambda: _update_and_relaunch(config_path)),
            ("Automatic updates: " + ("on (turn off)" if _auto_on(config_path) else "off (turn on)"),
             lambda: configure.toggle_auto_update(config_path)),
        ]
        account_tools = [
            ("Sign out on this computer", lambda: configure.sign_out(config_path)) if signed_in else
            ("Sign in to YonderView", lambda: configure.account_token(config_path)),
        ]
        more_tools = [
            ("Find cameras on the network", _discover),
            ("What a camera supports (focus, day/night, wiper...)", lambda: controls.cli_probe(config_path)),
            ("Fix a camera's stream (H.264, up to 1080p, 2 s keyframes)", lambda: controls.cli_fix_stream(config_path)),
        ]
        actions = [
            ("Show cameras and their status", lambda: configure.list_cameras(config_path)),
            ("Add a camera", lambda: configure.add(config_path)),
            ("Change a camera...  (settings, weather station, remove)", ("Change a camera", camera_tools)),
            ("Check everything (doctor)", lambda: doctor.run(config_path)),
            ("Service and log...", ("Service and log", service_tools)),
            ("Updates...", ("Updates", update_tools)),
            ("YonderView account...", ("YonderView account", account_tools)),
            ("More tools...", ("More tools", more_tools)),
        ]
        print(f"\n=== camagent {__version__} ===")
        print(f"Cameras: {len(cams)}   Service: {state}   YonderView: {_account_label(config_path)}"
              + (f"   Problems: {len(problems)}" if problems else ""))
        picked = _choose("", actions)
        if picked is None:
            return
        reopen_if_updated(config_path)            # updated while sitting at the prompt: don't run the old code
        label, action = picked
        if isinstance(action, tuple):                     # a group: its own short menu
            if not _submenu(*action):
                continue
        else:
            _do(label, action)
        try:
            input("\nPress Enter to return to the menu...")
        except (KeyboardInterrupt, EOFError):
            return
