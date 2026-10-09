"""Update camagent in place from its Git repository, then restart the service."""
import subprocess
import sys

from . import __version__, config, service


def run(config_path=None, ref=None):
    service._require_admin()
    cfg = config.load_or_defaults(config_path)
    repo = cfg["update"]["repo"]
    if "OWNER" in repo:
        raise SystemExit("Set [update] repo in the config to your repository URL first.")
    ref = ref or cfg["update"].get("ref") or latest_release(repo) or "main"

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


def latest_release(repo):
    """Tag of the newest published GitHub release, or None (private repo, no releases, offline)."""
    import json
    import re
    import urllib.request
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", repo)
    if not m:
        return None
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "camagent"})
        with urllib.request.urlopen(req, timeout=10) as r:
            tag = json.loads(r.read()).get("tag_name")
    except Exception:  # noqa: BLE001
        return None
    if tag:
        print(f"Latest release: {tag}")
    return tag
