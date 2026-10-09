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


def run(config_path=None):
    if not _elevate_or_explain():
        return
    from . import configure, doctor, update
    while True:
        try:
            _, cams, problems = config.load_all(config_path)
        except Exception as e:  # noqa: BLE001
            cams, problems = [], [str(e)]
        state = _service_state()
        start_label = "Restart the service" if state != "not installed" else "Install and start the service"
        actions = [
            ("Show cameras and their status", lambda: configure.list_cameras(config_path)),
            ("Add a camera", lambda: configure.add(config_path)),
            ("Change a camera's settings", lambda: configure.run(config_path)),
            ("Remove a camera", lambda: configure.remove(config_path)),
            ("Check everything (doctor)", lambda: doctor.run(config_path)),
            ("Show the recent log", show_log),
            (start_label, (service.restart if state != "not installed"
                           else lambda: service.install(config_path))),
            ("Update camagent", lambda: update.run(config_path)),
            ("Automatic updates: " + ("on (turn off)" if _auto_on() else "off (turn on)"),
             lambda: configure.toggle_auto_update(config_path)),
            ("Find cameras on the network", _discover),
        ]
        print(f"\n=== camagent {__version__} ===")
        print(f"Cameras: {len(cams)}   Service: {state}" + (f"   Problems: {len(problems)}" if problems else ""))
        for i, (label, _) in enumerate(actions, 1):
            print(f"  {i}. {label}")
        print("  0. Quit")
        choice = input("Choose: ").strip()
        if choice in ("0", "q", "quit", "exit", ""):
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(actions):
            print("Not a valid choice.")
            continue
        label, action = actions[int(choice) - 1]
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
        try:
            input("\nPress Enter to return to the menu...")
        except (KeyboardInterrupt, EOFError):
            return
