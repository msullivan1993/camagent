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


def _pip_install(spec):
    # -I everywhere: Python ignores the current folder (and PYTHON* settings), so nothing there can be run as root
    pip = [sys.executable, "-I", "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    # first pass picks up any new dependencies; second forces the code itself to refresh
    subprocess.run(pip + ["--upgrade", spec], check=True)
    subprocess.run(pip + ["--upgrade", "--force-reinstall", "--no-deps", spec], check=True)
    return subprocess.run([sys.executable, "-I", "-m", "camagent", "version"], capture_output=True,
                          text=True).stdout.strip()


def _install(repo, tag):
    """Install a signed release (see release.py). Raises release.NotSigned if it isn't one."""
    from . import release
    return _pip_install(str(release.fetch(repo, tag)))


def _install_unsigned(repo, ref):
    """Straight from git, unchecked: only when asked by hand with --unsigned (testing a branch)."""
    return _pip_install(f"camagent @ git+{repo}@{ref}")


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


def _refresh_unit(config_path=None):
    """Rewrite the service unit with the version just installed. This process is still the old version, so its
    own service.refresh_unit would write the old unit (0.14.2's video group only arrived one update later)."""
    import subprocess
    import sys
    code = ("import sys; from camagent import config, service; service.refresh_unit(sys.argv[1] or None); "
            "getattr(config, 'migrate_account', lambda p: None)(sys.argv[1] or None)")
    r = subprocess.run([sys.executable, "-I", "-c", code, str(config_path or "")], capture_output=True, text=True,
                       timeout=60)
    if r.returncode:
        service.refresh_unit(config_path)          # the new version couldn't start: keep the old unit current


def run(config_path=None, ref=None, auto=False, jitter=0, unsigned=False):
    from .release import NotSigned
    service._require_admin()
    cfg = config.load_or_defaults(config_path)
    repo = cfg["update"]["repo"]
    if "OWNER" in repo:
        raise SystemExit("Set [update] repo in the config to your repository URL first.")

    if not auto:
        asked = ref or cfg["update"].get("ref")
        ref = asked or latest_release(repo)
        if not ref:
            raise SystemExit("Couldn't find the latest release (GitHub didn't answer). Try again later.")
        if re.fullmatch(r"\d+\.\d+\.\d+", ref):
            ref = "v" + ref                          # "0.16.1" means the release v0.16.1
        if not asked and not unsigned and version_tuple(ref) < version_tuple(__version__):
            # "latest" older than what's installed: someone may be trying to push an old version. Only on request.
            raise SystemExit(f"The latest release ({ref}) is older than this one ({__version__}); not going back. "
                             f"To install it anyway: camagent update --ref {ref}")
        if unsigned:
            print(f"Installing camagent from {ref} ({repo}) WITHOUT checking its signature (--unsigned)...")
            print(f"Installed: {_install_unsigned(repo, ref)}")
        else:
            print(f"Updating from camagent {__version__} to {ref} (signed release)...")
            try:
                print(f"Installed: {_install(repo, ref)}")
            except NotSigned as e:
                raise SystemExit(f"Not installed: {e}.\nOnly releases signed with YonderView's release key are "
                                 f"installed. (For testing a branch: camagent update --ref <branch> --unsigned)")
        _refresh_unit(config_path)
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
        _refresh_unit(config_path)
        service.restart()
    except NotSigned as e:
        _log(f"NOT installing {tag}: {e}. Staying on {previous}.")
        return
    except Exception as e:  # noqa: BLE001
        _log(f"install of {tag} failed: {e}; staying on {previous}")
        return
    if _verify(tag.lstrip("vV"), before):
        return _log(f"updated to {tag}; cameras are streaming")

    _log(f"{tag} didn't come up healthy within {VERIFY_SECONDS}s; rolling back to {previous}")
    try:
        _install(repo, f"v{previous}")          # signed, usually the copy kept in releases/
        _refresh_unit(config_path)
        service.restart()
        _log(f"rolled back to {previous}" + (" and it's streaming again" if _verify(previous, before) else
                                             "; check the cameras (camagent doctor)"))
    except NotSigned as e:
        _log(f"can't roll back to {previous} automatically ({e}). {tag} stays; check the cameras (camagent doctor).")
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
