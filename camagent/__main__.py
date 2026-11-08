"""camagent command line. Run `camagent` on its own for a menu.

  camagent add                add another camera on this computer
  camagent configure [CAM]    change a camera's settings (or add your first camera)
  camagent list               show every camera here and how it's doing
  camagent remove [CAM]       stop sending a camera from this computer
  camagent run                run CamAgent in this window (what the background service runs)
  camagent discover           search the network for cameras that answer to ONVIF
  camagent signin             sign in to your YonderView account on this computer
  camagent signout            sign out of YonderView on this computer
  camagent doctor [CAM]       check everything CamAgent needs, and say how to fix problems
  camagent gpu-test [CAM]     compare ways the graphics chip can make a camera's lower qualities
  camagent install-service    install CamAgent as a background service and start it
  camagent uninstall-service  remove the background service
  camagent restart            restart the background service
  camagent update             install the latest signed release and restart CamAgent
  camagent version
"""
import argparse
import logging
import sys

from . import __version__


def _no_quickedit():
    """Windows: a click inside a console window ("QuickEdit") freezes the program until a key is pressed, so CamAgent
    looked stuck after signing in (2026-11-08). Turn QuickEdit off for CamAgent's own window; copy and paste still
    work with right-click. Nothing happens without a console (the service) or on Linux."""
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        handle = k.GetStdHandle(-10)                       # the window's keyboard input
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(handle, ctypes.byref(mode)):
            k.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)   # QuickEdit off, keep the other settings
    except Exception:  # noqa: BLE001 - never stop CamAgent over the window's settings
        pass


def main(argv=None):
    _no_quickedit()
    ap = argparse.ArgumentParser(prog="camagent", description="CamAgent sends your cameras to YonderView. "
                                 "Run camagent with no command to see the menu.")
    ap.add_argument("--config", help="path to camagent.toml (default: the usual place)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run CamAgent in this window (for testing; normally CamAgent runs in the background)")
    sub.add_parser("add", help="add a camera")
    p = sub.add_parser("configure", help="change a camera's settings")
    p.add_argument("camera", nargs="?")
    sub.add_parser("list", help="show your cameras on this computer and how they're doing")
    p = sub.add_parser("remove", help="remove a camera from this computer")
    p.add_argument("camera", nargs="?")
    sub.add_parser("discover", help="search the network for cameras")
    sub.add_parser("signin", help="sign in to your YonderView account on this computer (shows a code)")
    sub.add_parser("signout", help="sign out of YonderView on this computer")
    p = sub.add_parser("doctor", help="check for problems")
    p.add_argument("camera", nargs="?")
    sub.add_parser("install-service", help="start CamAgent in the background (and with the computer)")
    sub.add_parser("uninstall-service", help="stop CamAgent running in the background")
    sub.add_parser("restart", help="restart CamAgent")
    up = sub.add_parser("update", help="update CamAgent now and restart it")
    up.add_argument("--ref", help="release tag to install (default: the latest release)")
    up.add_argument("--unsigned", action="store_true",
                    help="install --ref (for example a branch) straight from git without checking a signature "
                         "(testing only)")
    up.add_argument("--auto", action="store_true",
                    help="scheduled run: install only a newer release, check it, and go back to the old one if needed")
    up.add_argument("--jitter", type=int, default=0, help=argparse.SUPPRESS)
    up.add_argument("--if-requested", action="store_true", help=argparse.SUPPRESS)
    w = sub.add_parser("weather", help="add, change or remove a camera's weather station (beta)")
    w.add_argument("camera", nargs="?")
    pr = sub.add_parser("probe", help="show what a camera can do (focus, day/night, wiper...)")
    pr.add_argument("camera", nargs="?")
    pr.add_argument("--test-moves", action="store_true", help="also check that exact moves work (turns the camera)")
    g = sub.add_parser("gpu-test", help="compare ways this computer's graphics chip can make a camera's "
                                       "lower qualities (15 seconds each)")
    g.add_argument("camera", nargs="?")
    sub.add_parser("box-setup", help="for a Linux computer that only runs CamAgent: write less to its flash "
                                          "storage (asks first)")
    sub.add_parser("remote", help=argparse.SUPPRESS)       # the website setup helper (runs in the background)
    sub.add_parser("version", help="show which version of CamAgent is installed")

    # allow --config after the subcommand too
    for p in sub.choices.values():
        p.add_argument("--config", dest="config_sub", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    cfg_path = getattr(args, "config_sub", None) or args.config

    if args.cmd is None:
        from .menu import run as menu
        try:
            menu(cfg_path)
        except (KeyboardInterrupt, EOFError):
            print()
        return

    if args.cmd == "version":
        print(f"camagent {__version__}")
        return

    if args.cmd == "run":
        logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger("zeep").setLevel(logging.WARNING)
        from .tools import install_ring
        install_ring()                              # the recent log, for the camera's Health tab
        from .agent import Supervisor
        Supervisor(cfg_path).run()

    elif args.cmd in ("configure", "add", "remove"):
        from . import configure
        try:
            if args.cmd == "configure":
                configure.run(cfg_path, args.camera)
            elif args.cmd == "add":
                configure.add(cfg_path)
            else:
                configure.remove(cfg_path, args.camera)
        except (KeyboardInterrupt, EOFError):
            print("\nCanceled. Nothing was saved.")

    elif args.cmd in ("signin", "signout"):
        from . import configure, link
        try:
            if args.cmd == "signout":
                configure.sign_out(cfg_path)
            else:
                site, token, name = configure.account_token(cfg_path)
                if token:
                    print(f"You're signed in as {name or 'your account'}. To add a camera, type: camagent add")
        except link.LinkError as e:
            raise SystemExit(str(e))
        except (KeyboardInterrupt, EOFError):
            print("\nCanceled. Nothing was changed.")

    elif args.cmd == "list":
        from .configure import list_cameras
        list_cameras(cfg_path)

    elif args.cmd == "discover":
        from .camera import discover
        cams = discover()
        if not cams:
            print("No cameras answered. Check that your camera is on and is on the same network as this computer.")
            print("Also check that ONVIF is turned on in the camera's web page. (ONVIF is the camera's control and")
            print("setup standard. CamAgent uses it to talk to your camera.)")
        for c in cams:
            print(f"{c['host']}:{c['port']}  {c['name']} {c['hardware']}".rstrip())

    elif args.cmd == "doctor":
        logging.basicConfig(level=logging.ERROR)
        from .doctor import run
        sys.exit(run(cfg_path, args.camera))

    elif args.cmd == "remote":
        from .remote import main as remote_main
        sys.exit(remote_main(cfg_path))

    elif args.cmd == "box-setup":
        from .storage import run
        sys.exit(run())

    elif args.cmd == "gpu-test":
        from .gputest import run
        sys.exit(run(cfg_path, args.camera))

    elif args.cmd == "install-service":
        from .service import install
        install(cfg_path)

    elif args.cmd == "uninstall-service":
        from .service import uninstall
        uninstall()

    elif args.cmd == "restart":
        from .service import restart
        restart()

    elif args.cmd == "probe":
        from .controls import cli_probe
        cli_probe(cfg_path, args.camera, moves=args.test_moves)

    elif args.cmd == "weather":
        from .configure import weather_station
        weather_station(cfg_path, args.camera)

    elif args.cmd == "update":
        from .update import run
        run(cfg_path, args.ref, auto=args.auto, jitter=args.jitter, unsigned=args.unsigned,
            if_requested=args.if_requested)


if __name__ == "__main__":
    main()
