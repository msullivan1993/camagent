"""Update camagent from its GitHub releases, then restart the service.

`camagent update` (by hand, or from the menu) installs the latest release, or --ref.
`camagent update --auto` is the nightly scheduled run: it does nothing unless a newer release exists,
then installs it, restarts, checks the cameras come back, and rolls back to the previous version if not.
"""
import json
import random
import re
import subprocess
import sys
import time

from . import __version__, config, service

VERIFY_SECONDS = 150


def _log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        d = config.base_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "update.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def version_tuple(v):
    nums = re.findall(r"\d+", str(v).lstrip("vV").split("-")[0])
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def _install(repo, ref):
    spec = f"camagent @ git+{repo}@{ref}"
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    # first pass picks up any new dependencies; second forces the code itself to refresh
    subprocess.run(pip + ["--upgrade", spec], check=True)
    subprocess.run(pip + ["--upgrade", "--force-reinstall", "--no-deps", spec], check=True)
    return subprocess.run([sys.executable, "-m", "camagent", "version"],
                          capture_output=True, text=True).stdout.strip()


def _status():
    try:
        return json.loads(config.status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _streaming(status):
    return sum(1 for c in (status.get("cameras") or {}).values() if c.get("streaming"))


def _verify(version, streaming_before, wait=VERIFY_SECONDS):
    """Healthy = the new version is running and at least as many cameras stream as before (max 1 needed)."""
    need = min(1, streaming_before)
    deadline = time.time() + wait
    while time.time() < deadline:
        st = _status()
        fresh = time.time() - float(st.get("updated") or 0) < 30
        if fresh and str(st.get("version")) == str(version) and _streaming(st) >= need:
            return True
        time.sleep(5)
    return False


def run(config_path=None, ref=None, auto=False, jitter=0):
    service._require_admin()
    cfg = config.load_or_defaults(config_path)
    repo = cfg["update"]["repo"]
    if "OWNER" in repo:
        raise SystemExit("Set [update] repo in the config to your repository URL first.")

    if not auto:
        ref = ref or cfg["update"].get("ref") or latest_release(repo) or "main"
        print(f"Updating camagent {__version__} from {repo} ({ref})")
        print(f"Installed: {_install(repo, ref)}")
        service.refresh_unit(config_path)
        service.restart()
        # computers set up before automatic updates existed get them now (on by default; off in the menu)
        if cfg["update"].get("auto", True) and service.is_installed() and not service.update_schedule_installed():
            service.install_update_schedule(config_path)
        return

    # ----- nightly scheduled run -----
    if not cfg["update"].get("auto", True):
        return _log("automatic updates are off; nothing to do")
    if jitter:
        time.sleep(random.uniform(0, jitter))        # spread agents out so they don't all check at once
    tag = latest_release(repo)
    if not tag:
        return _log("couldn't check for a new release (offline, or none published); will try tomorrow")
    if version_tuple(tag) <= version_tuple(__version__):
        return _log(f"up to date ({__version__})")

    previous = __version__
    before = _streaming(_status())
    _log(f"updating {previous} -> {tag}")
    try:
        _install(repo, tag)
        service.refresh_unit(config_path)
        service.restart()
    except Exception as e:  # noqa: BLE001
        _log(f"install of {tag} failed: {e}; staying on {previous}")
        return
    if _verify(tag.lstrip("vV"), before):
        return _log(f"updated to {tag}; cameras are streaming")

    _log(f"{tag} didn't come up healthy within {VERIFY_SECONDS}s; rolling back to {previous}")
    try:
        _install(repo, f"v{previous}")
        service.refresh_unit(config_path)
        service.restart()
        _log(f"rolled back to {previous}" + (" and it's streaming again" if _verify(previous, before) else
                                             "; check the cameras (camagent doctor)"))
    except Exception as e:  # noqa: BLE001
        _log(f"rollback failed: {e}. Run 'camagent update --ref v{previous}' by hand.")


def latest_release(repo):
    """Tag of the newest published GitHub release (pre-releases are skipped), or None."""
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
