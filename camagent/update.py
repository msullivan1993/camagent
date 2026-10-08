"""Update camagent in place from its Git repository, then restart the service."""
import subprocess
import sys

from . import __version__, config, service


def run(config_path=None, ref=None):
    service._require_admin()
    cfg = config.load_or_defaults(config_path)
    repo = cfg["update"]["repo"]
    ref = ref or cfg["update"].get("ref") or "main"
    if "OWNER" in repo:
        raise SystemExit("Set [update] repo in the config to your repository URL first.")

    spec = f"camagent @ git+{repo}@{ref}"
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    print(f"Updating camagent {__version__} from {repo} ({ref})")
    # first pass picks up any new dependencies; second forces the code itself to refresh
    subprocess.run(pip + ["--upgrade", spec], check=True)
    subprocess.run(pip + ["--upgrade", "--force-reinstall", "--no-deps", spec], check=True)

    new = subprocess.run([sys.executable, "-m", "camagent", "version"],
                         capture_output=True, text=True).stdout.strip()
    print(f"Installed: {new}")
    service.refresh_unit(config_path)
    service.restart()
