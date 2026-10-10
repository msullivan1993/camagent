"""camagent command line. Run `camagent` on its own for a menu.

  camagent add                set up another camera on this machine
  camagent configure [CAM]    change a camera's settings (or set up the first one)
  camagent list               every camera here, with live status
  camagent remove [CAM]       stop sending a camera from this machine
  camagent run                run the agent in the foreground (what the service runs)
  camagent discover           list ONVIF cameras on the network
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
    ap = argparse.ArgumentParser(prog="camagent", description="YonderView camera site agent")
    ap.add_argument("--config", help="path to camagent.toml (default: the standard location)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run the agent in the foreground")
    sub.add_parser("add", help="set up another camera")
    p = sub.add_parser("configure", help="change a camera's settings")
    p.add_argument("camera", nargs="?")
    sub.add_parser("list", help="list cameras and their status")
    p = sub.add_parser("remove", help="remove a camera from this machine")
    p.add_argument("camera", nargs="?")
    sub.add_parser("discover", help="list ONVIF cameras on the network")
    p = sub.add_parser("doctor", help="check everything the agent needs")
    p.add_argument("camera", nargs="?")
    sub.add_parser("install-service", help="install and start the service")
    sub.add_parser("uninstall-service", help="remove the service")
    sub.add_parser("restart", help="restart the service")
    up = sub.add_parser("update", help="update from Git and restart")
    up.add_argument("--ref", help="branch or tag to install (default from config)")
    up.add_argument("--auto", action="store_true", help="scheduled run: install only a newer release, verify, roll back if needed")
    up.add_argument("--jitter", type=int, default=0, help=argparse.SUPPRESS)
    w = sub.add_parser("weather", help="add, change or remove a camera's local weather station (beta)")
    w.add_argument("camera", nargs="?")
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

    elif args.cmd == "list":
        from .configure import list_cameras
        list_cameras(cfg_path)

    elif args.cmd == "discover":
        from .camera import discover
        cams = discover()
        if not cams:
            print("No ONVIF cameras answered.")
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

    elif args.cmd == "weather":
        from .configure import weather_station
        weather_station(cfg_path, args.camera)

    elif args.cmd == "update":
        from .update import run
        run(cfg_path, args.ref, auto=args.auto, jitter=args.jitter)


if __name__ == "__main__":
    main()
