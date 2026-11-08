"""`camagent` with no command: a simple menu for everything, so nobody has to remember commands."""
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

from . import __version__, config, service, text


def _elevate_or_explain() -> bool:
    """True if we're already an administrator. On Windows, offers to reopen the menu as administrator."""
    if service.is_admin():
        return True
    if os.name == "nt":
        print("CamAgent needs administrator rights, because it keeps your camera passwords in its settings.")
        if input("Reopen CamAgent as administrator? (Y/n): ").strip().lower() in ("", "y", "yes"):
            import ctypes
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "-I -m camagent",
                                                       str(config.base_dir()), 1)
            if ret > 32:
                return False                 # the new window takes over
            print("Windows didn't allow that. Do this instead:")
            print("Right-click the CamAgent shortcut in the Start menu (or Command Prompt) and choose")
            print("\"Run as administrator\". If you opened Command Prompt, type: camagent")
        return False
    print("CamAgent needs administrator (root) rights, because it keeps your camera passwords in its settings.")
    print("Open CamAgent like this instead:  sudo camagent")
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
            print("There's no log yet, because CamAgent hasn't run in the background.")
            print("Choose 5 in the menu to start it.")
            return
        print(f"--- The last {lines} lines of {path} ---")
        print("".join(tail).rstrip())
    else:
        subprocess.run(["journalctl", "-u", service.SERVICE, "-n", str(lines), "--no-pager"])


def _discover():
    from .camera import discover
    print("Searching this network for cameras (3 seconds)...")
    cams = discover()
    if not cams:
        print("No cameras answered. Check that your camera is on and is on the same network as this computer.")
        print("Also check that ONVIF is turned on in the camera's web page. (ONVIF is the camera's control and")
        print("setup standard. CamAgent uses it to talk to your camera.)")
        print("You can still add a camera by typing its IP address during setup.")
    else:
        print(f"CamAgent found {len(cams)} camera{'s' if len(cams) != 1 else ''} (IP address:port, then name):")
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
        print(f"\nCamAgent updated itself to {new} while this menu was open.")
        relaunch(config_path)


def relaunch(config_path=None):
    """Start the menu again on the newly installed version, replacing this one."""
    import os
    import subprocess
    import sys
    # -I: Python ignores the current folder (and PYTHON* settings), as everywhere CamAgent starts itself
    args = [sys.executable, "-I", "-m", "camagent"] + (["--config", str(config_path)] if config_path else [])
    print("CamAgent is reopening the menu on the new version...\n")
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
    if acct.get("token"):
        return text.S_ACCOUNT_IN.format(name=acct.get("name") or text.S_ACCOUNT_FALLBACK)
    return text.S_ACCOUNT_OUT


def _do(label, action):
    """Run one menu action, turning problems into a plain message instead of a crash."""
    print()
    try:
        action()
    except SystemExit as e:
        if e.code not in (None, 0) and not isinstance(e.code, int):
            print(e.code)
    except (KeyboardInterrupt, EOFError):
        print(text.MENU_CANCELED)
    except Exception as e:  # noqa: BLE001
        print(text.MENU_WENT_WRONG.format(error=e))
        print(text.MENU_WENT_WRONG_2)
        print(text.MENU_WENT_WRONG_3)


def _choose(title, actions, back=text.MENU_QUIT):
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
        choice = input(text.MENU_PROMPT).strip()
        if choice in ("0", "q", "quit", "exit", "back", "b", ""):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(numbered):
            return numbered[int(choice) - 1]
        print(text.MENU_PICK_AGAIN.format(n=len(numbered), back=back.lower()))


def _submenu(title, actions):
    """A group of related actions: pick one (it runs), or 0 / Enter to go back."""
    picked = _choose(f"\n--- {title} ---", actions, back=text.MENU_BACK)
    if picked:
        _do(*picked)
        return True
    return False


def _sign_out(config_path=None):
    from . import configure
    if configure.yes("Sign out of YonderView on this computer?", default=False):
        configure.sign_out(config_path)
    else:
        print("Canceled. Nothing was changed.")


def _cameras_text(n):
    return text.S_CAMERA_ONE if n == 1 else text.S_CAMERAS_N.format(n=n)


def _problems_text(n):
    return text.S_PROBLEM_ONE if n == 1 else text.S_PROBLEMS_N.format(n=n)


def _running_text(state):
    return {"running": text.S_RUNNING, "stopped": text.S_STOPPED,
            "not installed": text.S_NOT_INSTALLED}.get(state, text.S_OTHER_STATE.format(state=state))


def _status_lines(cams, state, problems, config_path=None):
    """The few lines above the menu: how things stand, and the one thing to do next if something's missing."""
    lines = [text.S_LINE.format(cams=_cameras_text(len(cams)), running=_running_text(state),
                                account=_account_label(config_path))]
    if problems:
        lines.append(text.S_PROBLEMS.format(problems=_problems_text(len(problems))))
    elif not cams:
        lines.append(text.S_NO_CAMERAS)
    elif state == "not installed":
        lines.append(text.S_NEXT_START)
        lines.append(text.S_NEXT_START_2)
    elif state == "stopped":
        lines.append(text.S_STOPPED_NOTE)
    return lines


# ---------- the two ways to set up (0.22) ----------

WEBSITE, LOCAL = "website", "local"


def get_mode(config_path=None, cams=None):
    """"website", "local", or "" (not chosen yet, and no cameras here). A computer that already has cameras
    counts as "local", so nothing changes for hosts who set up before the two ways existed."""
    try:
        mode = str(config.load_or_defaults(config_path)["agent"].get("mode") or "").strip().lower()
    except Exception:  # noqa: BLE001
        mode = ""
    if mode in (WEBSITE, LOCAL):
        return mode
    if cams is None:
        try:
            cams = config.load_all(config_path)[1]
        except Exception:  # noqa: BLE001
            cams = []
    return LOCAL if cams else ""


def set_mode(mode, config_path=None):
    """Remember the way to set up in camagent.toml. Changes nothing else (no cameras are removed)."""
    main = config.load_or_defaults(config_path)
    main["agent"]["mode"] = mode
    config.save_main(main, config_path)


def ask_mode(config_path=None):
    """The first-run question. Returns the chosen mode (also saved), or None if the person quits."""
    print(f"\n{text.MODE_WELCOME}")
    print(text.MODE_QUESTION)
    print(f"\n  1. {text.MODE_WEBSITE}\n     {text.MODE_WEBSITE_ABOUT}")
    print(f"  2. {text.MODE_LOCAL}\n     {text.MODE_LOCAL_ABOUT}")
    print(f"\n{text.MODE_SWITCH_NOTE}")
    while True:
        choice = input(text.MODE_PROMPT).strip().lower()
        if choice in ("0", "q", "quit", "exit"):
            return None
        if choice in ("1", "2"):
            mode = WEBSITE if choice == "1" else LOCAL
            set_mode(mode, config_path)
            return mode
        print(text.MODE_RETRY)


def _switch(mode, config_path=None):
    set_mode(mode, config_path)
    print(text.SWITCHED_TO_WEBSITE if mode == WEBSITE else text.SWITCHED_TO_LOCAL)


def _site(config_path=None):
    from . import link
    try:
        site = config.load_or_defaults(config_path)["account"].get("site") or link.DEFAULT_SITE
    except Exception:  # noqa: BLE001
        site = link.DEFAULT_SITE
    return site.rstrip("/")


def _link_address(config_path=None):
    """The address people open to set up cameras, without https:// (yonderview.net/link)."""
    return _site(config_path).split("://", 1)[-1] + "/link"


def _open_link(config_path=None):
    url = _site(config_path) + "/link"
    print(text.W_OPEN_PRINT)
    print(f"\n  {url}\n")
    if not config.load_or_defaults(config_path)["account"].get("token"):
        print(text.W_OPEN_SIGNED_OUT)
    if os.name == "nt":
        try:
            import webbrowser
            if webbrowser.open(url):
                print(text.W_OPENING)
        except Exception:  # noqa: BLE001 - the address above is enough
            pass


def _website_ready(config_path=None):
    """Website mode needs this computer linked to the account, the website setup helper running (it does the
    work the website asks for) and, once there are cameras, the main background service. With no cameras the
    helper alone is installed: the main service has nothing to send, and the helper starts it with the first
    camera. Safe to run again: it only installs what's missing."""
    from . import configure
    path = Path(config_path or config.default_config_path())
    if not path.exists():
        config.save_main(config.load_or_defaults(path), path)     # the helper and the service need the file
    configure.account_token(path)                                  # signs in now if needed
    try:
        cams = config.load_all(path)[1]
    except Exception:  # noqa: BLE001
        cams = []
    if cams and not service.is_installed():
        service.install(path)                                      # also installs the helper
    elif not service.remote_installed():
        service.install_remote(path)


def _website_status_lines(cams, state, problems, config_path, numbers, signed_in):
    address = _link_address(config_path)
    lines = [text.W_STATE_LINE.format(account=_account_label(config_path), running=_running_text(state),
                                      cams=_cameras_text(len(cams)))]
    if problems:
        lines.append(text.W_PROBLEMS.format(problems=_problems_text(len(problems)), number=numbers[text.W_CHECK]))
    elif not signed_in:
        lines.append(text.W_NEED_SIGN_IN.format(number=numbers[text.W_SIGN_IN], address=address))
    elif not cams:
        lines.append(text.W_NO_CAMERAS.format(address=address))
    return lines


def signed_in_label(config_path=None):
    try:
        return bool(config.load_or_defaults(config_path)["account"].get("token"))
    except Exception:  # noqa: BLE001
        return False


def _website_menu(config_path, state):
    """One round of the website-mode menu. Returns False to leave CamAgent."""
    from . import configure, doctor
    try:
        _, cams, problems = config.load_all(config_path)
    except Exception as e:  # noqa: BLE001
        cams, problems = [], [str(e)]
    signed_in = signed_in_label(config_path) if not problems else False
    update_tools = _update_tools(config_path, configure)
    address = _link_address(config_path)
    actions = [
        (text.W_HEAD_SET_UP, None),
        (text.W_OPEN_LINK.format(address=address), lambda: _open_link(config_path)),
        (text.W_CAMERAS, lambda: configure.list_cameras(config_path)),
        (text.W_HEAD_HELP, None),
        (text.W_CHECK, lambda: doctor.run(config_path)),
        (text.W_LOG, show_log),
        (text.W_HEAD_SETTINGS, None),
        (text.W_SIGN_OUT if signed_in else text.W_SIGN_IN,
         (lambda: _sign_out(config_path)) if signed_in else (lambda: configure.account_token(config_path))),
        (text.W_UPDATES, (text.SUB_UPDATES, update_tools)),
        (text.W_SWITCH_TO_LOCAL, lambda: _switch(LOCAL, config_path)),
    ]
    numbers = {label: n for n, (label, _) in enumerate([a for a in actions if a[1] is not None], 1)}
    numbers[text.W_SIGN_IN] = numbers.get(text.W_SIGN_IN) or numbers[text.W_SIGN_OUT]
    print(f"\n{text.MENU_TITLE.format(version=__version__)}")
    for line in _website_status_lines(cams, state, problems, config_path, numbers, signed_in):
        print(line)
    picked = _choose("", actions)
    if picked is None:
        return False
    reopen_if_updated(config_path)
    label, action = picked
    if isinstance(action, tuple):
        if not _submenu(*action):
            return True
    else:
        _do(label, action)
        if label == text.W_SWITCH_TO_LOCAL:
            return True                                    # redraw in the other mode, no pause
    try:
        input(text.MENU_PRESS_ENTER)
    except (KeyboardInterrupt, EOFError):
        return False
    return True


def _update_tools(config_path, configure):
    return [
        (text.SUB_UPDATE_NOW, lambda: _update_and_relaunch(config_path)),
        (text.SUB_AUTO_ON if _auto_on(config_path) else text.SUB_AUTO_OFF,
         lambda: configure.toggle_auto_update(config_path)),
    ]


def _get_ready(config_path):
    """Website mode's first step each time CamAgent opens: sign in, install what's missing. Never crashes."""
    print(f"\n{text.W_WORKING}")
    try:
        _website_ready(config_path)
    except (KeyboardInterrupt, EOFError):
        print(text.MENU_CANCELED)
    except SystemExit as e:
        if e.code not in (None, 0):
            print(text.W_SETUP_FAILED.format(error=e.code))
            print(text.W_SETUP_RETRY)
    except Exception as e:  # noqa: BLE001
        print(text.W_SETUP_FAILED.format(error=e))
        print(text.W_SETUP_RETRY)


def run(config_path=None):
    if not _elevate_or_explain():
        return
    from . import configure, controls, doctor
    if not get_mode(config_path):                     # first run: no cameras and no way chosen yet
        if not ask_mode(config_path):
            return
    ready = False
    while True:
        reopen_if_updated(config_path)
        mode = get_mode(config_path) or LOCAL
        state = _service_state()
        if mode == WEBSITE:
            if not ready:
                _get_ready(config_path)
                ready = True
                state = _service_state()
            if not _website_menu(config_path, state):
                return
            continue
        ready = False                                  # coming back to website mode later checks again
        try:
            _, cams, problems = config.load_all(config_path)
        except Exception as e:  # noqa: BLE001
            cams, problems = [], [str(e)]
        installed = state != "not installed"
        signed_in = bool(config.load_or_defaults(config_path)["account"].get("token")) if not problems else False
        camera_tools = [
            (text.SUB_CHANGE_CAMERA, lambda: configure.run(config_path)),
            (text.SUB_WEATHER, lambda: configure.weather_station(config_path)),
            (text.SUB_REMOVE, lambda: configure.remove(config_path)),
        ]
        update_tools = _update_tools(config_path, configure)
        advanced_tools = [
            (text.SUB_SEARCH, _discover),
            (text.SUB_PROBE, lambda: controls.cli_probe(config_path)),
        ]
        actions = [
            (text.HEAD_CAMERAS, None),
            (text.M_LIST, lambda: configure.list_cameras(config_path)),
            (text.M_ADD, lambda: configure.add(config_path)),
            (text.M_CHANGE_OR_REMOVE, (text.SUB_CHANGE_OR_REMOVE, camera_tools)),
            (text.HEAD_WRONG, None),
            (text.M_CHECK, lambda: doctor.run(config_path)),
            (text.M_RESTART if installed else text.M_START,
             service.restart if installed else (lambda: service.install(config_path))),
            (text.M_LOG, show_log),
            (text.HEAD_SETTINGS, None),
            (text.M_SIGN_OUT if signed_in else text.M_SIGN_IN,
             (lambda: _sign_out(config_path)) if signed_in else (lambda: configure.account_token(config_path))),
            (text.M_UPDATES, (text.SUB_UPDATES, update_tools)),
            (text.M_ADVANCED, (text.SUB_ADVANCED, advanced_tools)),
            (text.M_SWITCH_TO_WEBSITE, lambda: _switch(WEBSITE, config_path)),
        ]
        print(f"\n{text.MENU_TITLE.format(version=__version__)}")
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
            if label == text.M_SWITCH_TO_WEBSITE:
                continue                                  # redraw in the other mode, no pause
        try:
            input(text.MENU_PRESS_ENTER)
        except (KeyboardInterrupt, EOFError):
            return
