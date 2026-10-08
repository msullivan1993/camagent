"""camagent command line.

  camagent configure          interactive setup (find camera, pick stream, server details)
  camagent run                run the agent in the foreground (what the service runs)
  camagent discover           list ONVIF cameras on the network
  camagent doctor             check everything the agent needs, with fixes
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
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the agent in the foreground")
    sub.add_parser("configure", help="interactive setup")
    sub.add_parser("discover", help="list ONVIF cameras on the network")
    sub.add_parser("doctor", help="check everything the agent needs")
    sub.add_parser("install-service", help="install and start the service")
    sub.add_parser("uninstall-service", help="remove the service")
    sub.add_parser("restart", help="restart the service")
    up = sub.add_parser("update", help="update from Git and restart")
    up.add_argument("--ref", help="branch or tag to install (default from config)")
    sub.add_parser("version", help="print the version")

    # allow --config after the subcommand too
    for p in sub.choices.values():
        p.add_argument("--config", dest="config_sub", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    cfg_path = getattr(args, "config_sub", None) or args.config

    if args.cmd == "version":
        print(f"camagent {__version__}")
        return

    if args.cmd == "run":
        logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger("zeep").setLevel(logging.WARNING)
        from . import config
        from .agent import Agent
        try:
            cfg = config.load(cfg_path)
        except FileNotFoundError:
            raise SystemExit(f"No config found at {cfg_path or config.default_config_path()}. "
                             "Run: camagent configure")
        Agent(cfg).run()

    elif args.cmd == "configure":
        from .configure import run
        try:
            run(cfg_path)
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled; nothing saved.")

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
        sys.exit(run(cfg_path))

    elif args.cmd == "install-service":
        from .service import install
        install(cfg_path)

    elif args.cmd == "uninstall-service":
        from .service import uninstall
        uninstall()

    elif args.cmd == "restart":
        from .service import restart
        restart()

    elif args.cmd == "update":
        from .update import run
        run(cfg_path, args.ref)


if __name__ == "__main__":
    main()
