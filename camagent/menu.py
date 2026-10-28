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
        print("CamAgent needs administrator rights, because its settings hold your camera passwords.")
        if input("Reopen as administrator? (Y/n): ").strip().lower() in ("", "y", "yes"):
            import ctypes
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "-I -m camagent",
                                                       str(config.base_dir()), 1)
            if ret > 32:
                return False                 # the new window takes over
            print("Windows didn't allow it. Instead: right-click the CamAgent shortcut in the Start menu (or Command Prompt),")
            print("choose \"Run as administrator\", and (in Command Prompt) type: camagent")
        return False
    print("CamAgent needs administrator (root) rights, because its settings hold your camera passwords.")
    print("Open it like this instead:  sudo camagent")
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
            print("There's no log yet, because CamAgent hasn't run in the background. Choose 5 in the menu to start it.")
            return
        print(f"--- last {lines} lines of {path} ---")
        print("".join(tail).rstrip())
    else:
        subprocess.run(["journalctl", "-u", service.SERVICE, "-n", str(lines), "--no-pager"])


def _discover():
    from .camera import discover
    print("Searching this network for cameras (3 seconds)...")
    cams = discover()
    if not cams:
        print("No cameras answered. Check that the camera is on, on the same network as this computer, and has")
        print("ONVIF turned on in its web page (ONVIF is the standard way CamAgent talks to cameras).")
        print("You can still add a camera by typing its IP address during setup.")
    else:
        print(f"Found {len(cams)} camera{'s' if len(cams) != 1 else ''} (IP address:port, then its name):")
    for c in cams:
        print(f"  {c['host']}:{c['port']}  {c['name']} {c['hardware']}".rstrip())


def installed_version():
    """The version now installed on disk (this menu may still be running an older one)."""
    import subprocess
    import sys
    try:
        out = subprocess.run([sys.executable, "-I", "-m", "camagent", "version"], capture_output=True, text=True,
                             timeout=30)
        return out.stdout.strip().split()[-1] if out.stdout.strip() else __version__   # "camagent 0.16.1" -> "0.16.1"
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
        print(f"\nCamAgent was updated to {new} while this menu was open.")
        relaunch(config_path)


def relaunch(config_path=None):
    """Start the menu again on the newly installed version, replacing this one."""
    import os
    import subprocess
    import sys
    # -I: Python ignores the current folder (and PYTHON* settings), as everywhere CamAgent starts itself
    args = [sys.executable, "-I", "-m", "camagent"] + (["--config", str(config_path)] if config_path else [])
    print("Reopening the menu on the new version...\n")
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
        print("\nCancelled. Nothing was changed.")
    except Exception as e:  # noqa: BLE001
        print(f"Something went wrong: {e}")
        print('Choose "Check for problems" (4) in the menu to look for the cause. Still stuck? Use "Report a problem"')
        print("on yonderview.net and include the message above.")


def _choose(title, actions, back="Quit"):
    """Print a numbered list; returns the chosen (label, action), or None for 0 / Enter. An entry whose action is
    None is a heading: printed, not numbered."""
    if title:
        print(title)
    numbered = []
    for label, action in actions:
        if action is None:
            print(f"\n {label}")
            continue
        numbered.append((label, action))
        print(f"  {len(numbered)}. {label}")
    print(f"\n  0. {back}")
    while True:
        choice = input("Type a number and press Enter: ").strip()
        if choice in ("0", "q", "quit", "exit", "back", "b", ""):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(numbered):
            return numbered[int(choice) - 1]
        print(f"Please type a number from 1 to {len(numbered)}, or 0 to {back.lower()}.")


def _submenu(title, actions):
    """A group of related actions: pick one (it runs), or 0 / Enter to go back."""
    picked = _choose(f"\n--- {title} ---", actions, back="Go back")
    if picked:
        _do(*picked)
        return True
    return False


def _sign_out(config_path=None):
    from . import configure
    if configure.yes("Sign this computer out of YonderView?", default=False):
        configure.sign_out(config_path)
    else:
        print("Cancelled. Nothing was changed.")


def _status_lines(cams, state, problems, config_path=None):
    """The few lines above the menu: how things stand, and the one thing to do next if something's missing."""
    running = {"running": "CamAgent is running", "stopped": "CamAgent is stopped",
               "not installed": "CamAgent isn't running yet"}.get(state, f"CamAgent: {state}")
    n = len(cams)
    lines = [f"{n} camera{'s' if n != 1 else ''}  |  {running}  |  YonderView: {_account_label(config_path)}"]
    if problems:
        n_p = len(problems)
        lines.append(f"! {n_p} problem{'s' if n_p != 1 else ''} with the saved settings. Choose 4 (Check for problems)"
                     " to see what's wrong and how to fix it.")
    elif not cams:
        lines.append("No cameras yet. Choose 2 to add one.")
    elif state == "not installed":
        lines.append("Next: choose 5 to start CamAgent. It then keeps sending your cameras in the background and")
        lines.append("starts by itself whenever this computer turns on.")
    elif state == "stopped":
        lines.append("CamAgent is stopped, so your cameras aren't being sent. Choose 5 to start it again.")
    return lines


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
            ("Change a camera's settings", lambda: configure.run(config_path)),
            ("Add or change a weather station (beta)", lambda: configure.weather_station(config_path)),
            ("Remove a camera from this computer", lambda: configure.remove(config_path)),
        ]
        update_tools = [
            ("Update CamAgent now", lambda: _update_and_relaunch(config_path)),
            ("Automatic nightly updates are " + ("ON: turn them off" if _auto_on(config_path) else "OFF: turn them on"),
             lambda: configure.toggle_auto_update(config_path)),
        ]
        advanced_tools = [
            ("Search the network for cameras", _discover),
            ("Show what a camera can do (focus, day/night, wiper...)", lambda: controls.cli_probe(config_path)),
            ("Set a camera's video to what YonderView needs (H.264, up to 1080p)",
             lambda: controls.cli_fix_stream(config_path)),
        ]
        actions = [
            ("Cameras", None),
            ("See my cameras and how they're doing", lambda: configure.list_cameras(config_path)),
            ("Add a camera", lambda: configure.add(config_path)),
            ("Change or remove a camera...", ("Change or remove a camera", camera_tools)),
            ("If something's wrong", None),
            ("Check for problems", lambda: doctor.run(config_path)),
            ("Restart CamAgent" if installed else "Start CamAgent (runs in the background, starts with the computer)",
             service.restart if installed else (lambda: service.install(config_path))),
            ("Show the recent log (useful for support)", show_log),
            ("Settings", None),
            ("Sign out of YonderView" if signed_in else "Sign in to YonderView",
             (lambda: _sign_out(config_path)) if signed_in else (lambda: configure.account_token(config_path))),
            ("Updates...", ("Updates", update_tools)),
            ("Advanced tools...", ("Advanced tools", advanced_tools)),
        ]
        print(f"\n=== CamAgent {__version__} ===")
        for line in _status_lines(cams, state, problems, config_path):
            print(line)
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
            input("\nPress Enter to go back to the menu.")
        except (KeyboardInterrupt, EOFError):
            return
