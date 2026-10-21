"""camagent command line. Run `camagent` on its own for a menu.

  camagent add                set up another camera on this machine
  camagent configure [CAM]    change a camera's settings (or set up the first one)
  camagent list               every camera here, with live status
  camagent remove [CAM]       stop sending a camera from this machine
  camagent run                run the agent in the foreground (what the service runs)
  camagent discover           list ONVIF cameras on the network
  camagent signin             sign this computer in to your YonderView account
  camagent signout            forget this computer's YonderView sign-in
  camagent doctor [CAM]       check everything the agent needs, with fixes
  camagent install-service    install and start the system service
  camagent uninstall-service  remove the system service
  camagent restart            restart the service
  camagent update             update from Git and restart
  camagent version
"""
import argparse
import logging
import sys

from . import __version__


def main(argv=None):
    ap = argparse.ArgumentParser(prog="camagent", description="CamAgent sends your cameras to YonderView. Run it with no command for the menu.")
    ap.add_argument("--config", help="path to camagent.toml (default: the standard location)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run CamAgent in this window (for testing; normally it runs in the background)")
    sub.add_parser("add", help="set up a camera")
    p = sub.add_parser("configure", help="change a camera's settings")
    p.add_argument("camera", nargs="?")
    sub.add_parser("list", help="show the cameras on this computer and how they're doing")
    p = sub.add_parser("remove", help="remove a camera from this computer")
    p.add_argument("camera", nargs="?")
    sub.add_parser("discover", help="search the network for cameras")
    sub.add_parser("signin", help="sign this computer in to your YonderView account (shows a code)")
    sub.add_parser("signout", help="forget this computer's YonderView sign-in")
    p = sub.add_parser("doctor", help="check for problems")
    p.add_argument("camera", nargs="?")
    sub.add_parser("install-service", help="start CamAgent in the background (and with the computer)")
    sub.add_parser("uninstall-service", help="stop CamAgent running in the background")
    sub.add_parser("restart", help="restart CamAgent")
    up = sub.add_parser("update", help="update CamAgent now and restart it")
    up.add_argument("--ref", help="branch or tag to install (default from config)")
    up.add_argument("--auto", action="store_true", help="scheduled run: install only a newer release, verify, roll back if needed")
    up.add_argument("--jitter", type=int, default=0, help=argparse.SUPPRESS)
    w = sub.add_parser("weather", help="add, change or remove a camera's local weather station (beta)")
    w.add_argument("camera", nargs="?")
    pr = sub.add_parser("probe", help="show what a camera can do (focus, day/night, wiper...)")
    pr.add_argument("camera", nargs="?")
    pr.add_argument("--test-moves", action="store_true", help="also check that absolute moves work (turns the camera)")
    fx = sub.add_parser("fix-stream", help="set a camera's video to what YonderView needs: H.264, up to 1080p, a keyframe every 2 s (asks first)")
    fx.add_argument("camera", nargs="?")
    sub.add_parser("version", help="print the version")

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
            print("\nCancelled; nothing saved.")

    elif args.cmd in ("signin", "signout"):
        from . import configure, link
        try:
            if args.cmd == "signout":
                configure.sign_out(cfg_path)
            else:
                site, token, name = configure.account_token(cfg_path)
                if token:
                    print(f"Signed in as {name or 'your account'}. Set a camera up with: camagent add")
        except link.LinkError as e:
            raise SystemExit(str(e))
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")

    elif args.cmd == "list":
        from .configure import list_cameras
        list_cameras(cfg_path)

    elif args.cmd == "discover":
        from .camera import discover
        cams = discover()
        if not cams:
            print("No cameras answered. Check the camera is on, on this network, and has ONVIF turned on.")
        for c in cams:
            print(f"{c['host']}:{c['port']}  {c['name']} {c['hardware']}".rstrip())

    elif args.cmd == "doctor":
        logging.basicConfig(level=logging.ERROR)
        from .doctor import run
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

    elif args.cmd == "fix-stream":
        from .controls import cli_fix_stream
        cli_fix_stream(cfg_path, args.camera)

    elif args.cmd == "probe":
        from .controls import cli_probe
        cli_probe(cfg_path, args.camera, moves=args.test_moves)

    elif args.cmd == "weather":
        from .configure import weather_station
        weather_station(cfg_path, args.camera)

    elif args.cmd == "update":
        from .update import run
        run(cfg_path, args.ref, auto=args.auto, jitter=args.jitter)


if __name__ == "__main__":
    main()
