"""Website setup (0.18, beta): `camagent remote`, the helper that lets cameras be set up on yonderview.net.

It runs with administrator rights (Windows: a SYSTEM task at startup; Linux: a root service), next to the streaming
service, because it holds this computer's link token (account.toml, which the streaming service never reads) and
because setting a camera up means writing CamAgent's protected settings files. It checks in with the website about
once a minute (every couple of seconds while someone has this computer's setup page open), runs the jobs it's
given and sends back the results.

Only the actions in HANDLERS: no shell, no files, no addresses outside this computer's own network. Anything else
the website might send is answered "unknown" and ignored. Without a link (signed out) it just waits.
"""
import logging
import os
import platform
import socket
import sys
import time
from pathlib import Path

from . import __version__, config, link

log = logging.getLogger("camagent.remote")
MAX_FOUND = 50


def _discover(job):
    from . import camera
    found = camera.discover(timeout=4)
    out = []
    for c in found[:MAX_FOUND]:
        out.append({"host": str(c.get("host", ""))[:64], "port": int(c.get("port") or 80),
                    "name": str(c.get("name", ""))[:80], "hardware": str(c.get("hardware", ""))[:80]})
    return {"cameras": out}


# ---------- a camera, found on this computer's network (website setup, phase 2) ----------
def _local_host(job):
    """The camera's address from the job, only if it's on a local network: CamAgent never lets the website send it
    (with a camera's login) to an address on the internet."""
    import ipaddress
    host = str((job.get("payload") or {}).get("host") or "").strip()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("that isn't a camera address on this network") from None
    if not (ip.is_private or ip.is_link_local) or ip.is_loopback or ip.is_multicast:
        raise ValueError("CamAgent only connects to cameras on this computer's own network")
    try:
        port = int((job.get("payload") or {}).get("port") or 80)
    except (TypeError, ValueError):
        port = 80
    if not 1 <= port <= 65535:
        raise ValueError("that port number isn't valid")
    return host, port


def _login(job):
    import json
    try:
        d = json.loads(job.get("secret") or "{}")
    except ValueError:
        d = {}
    return str(d.get("username") or ""), str(d.get("password") or "")


def _inspect(job):
    """Log in to the camera and say what it is: model, video streams (no logins in them), PTZ, its position."""
    from . import camera
    host, port = _local_host(job)
    user, pw = _login(job)
    info = camera.onvif_inspect(host, port, user, pw)
    pos = info.get("position") or None
    return {"manufacturer": str(info.get("manufacturer", ""))[:80], "model": str(info.get("model", ""))[:80],
            "firmware": str(info.get("firmware", ""))[:80], "login_level": str(info.get("login_level", ""))[:30],
            "profiles": [{k: p.get(k) for k in ("name", "token", "encoding", "width", "height", "fps", "ptz", "rtsp_url")}
                         for p in (info.get("profiles") or [])[:12]],
            "position": {"ok": bool(pos and pos.get("onvif")), "exact": bool(pos and pos.get("exact"))} if pos else None}


def _snapshot(job):
    """One picture from the camera's video (480 pixels wide), for the website's preview and the request's photo."""
    import base64
    import subprocess

    from . import camera
    from .config import rtsp_with_auth
    host, _ = _local_host(job)
    user, pw = _login(job)
    url = str((job.get("payload") or {}).get("rtsp_url") or "")
    from urllib.parse import urlparse
    if urlparse(url).scheme != "rtsp" or urlparse(url).hostname != host:
        raise ValueError("that video address isn't the camera's")
    ffmpeg = camera.find_ffmpeg("")
    if not ffmpeg:
        raise ValueError("ffmpeg isn't installed on this computer")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i",
                        rtsp_with_auth(url, user, pw), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "6",
                        "-f", "image2", "-c:v", "mjpeg", "pipe:1"], capture_output=True, timeout=25,
                       stdin=subprocess.DEVNULL)
    if r.returncode or not r.stdout:
        raise ValueError(config.scrub((r.stderr or b"").decode("utf-8", "replace").strip()[-200:]) or "no picture")
    if len(r.stdout) > 120_000:
        raise ValueError("the picture came out too big")
    return {"jpeg": base64.b64encode(r.stdout).decode("ascii")}


def _ptz_test(job):
    """The move-and-read test: turns the camera a little and back, and says whether its position follows."""
    from . import camera
    host, port = _local_host(job)
    user, pw = _login(job)
    r = camera.ptz_move_test(host, port, user, pw, str((job.get("payload") or {}).get("token") or ""))
    return {"follows": bool(r.get("follows")), "back": bool(r.get("back")), "reports": r.get("before") is not None}


def _stage(job):
    """Keep this camera's setup on this computer (the login encrypted) until YonderView approves the camera and
    tells CamAgent to connect it (phase 3)."""
    import json

    from . import secret
    host, port = _local_host(job)
    user, pw = _login(job)
    p = job.get("payload") or {}
    setup_id = str(p.get("setup") or "")
    if not setup_id.isalnum() or len(setup_id) > 40:
        raise ValueError("bad setup id")
    folder = config.state_dir() / "pending"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"host": host, "onvif_port": port, "username": user, "password": secret.protect(pw),
            "rtsp_url": str(p.get("rtsp_url") or "")[:300], "ptz": bool(p.get("ptz")),
            "ptz_profile": str(p.get("token") or "")[:64], "staged": int(time.time())}
    path = folder / f"{setup_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {"staged": True}


HANDLERS = {"discover": _discover, "inspect": _inspect, "snapshot": _snapshot, "ptz_test": _ptz_test,
            "stage": _stage}


def _info(cfg_path):
    try:
        _, cams, _ = config.load_all(cfg_path)
        n = len(cams)
    except Exception:  # noqa: BLE001
        n = None
    return {"version": __version__, "os": f"{platform.system()} {platform.release()}"[:60],
            "hostname": socket.gethostname()[:60], "python": platform.python_version(), "cameras": n}


def _account(cfg_path):
    try:
        acct = config.load(cfg_path)["account"]
    except Exception:  # noqa: BLE001
        return None, None
    return acct.get("site") or link.DEFAULT_SITE, acct.get("token") or ""


def run_job(job):
    """(ok, result or error) for one job from the website."""
    kind = job.get("kind")
    handler = HANDLERS.get(kind)
    if handler is None:
        return False, f"unknown job: {str(kind)[:30]}"
    try:
        return True, handler(job)
    except Exception as e:  # noqa: BLE001 - one job failing never stops the helper
        log.warning("job %s failed: %s", kind, e)
        return False, config.scrub(str(e))[:300]


def _installed():
    """The CamAgent version on disk now (an update may have replaced the code this helper started with)."""
    import re
    try:
        text = (Path(__file__).parent / "__init__.py").read_text(encoding="utf-8")
        return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    except (OSError, AttributeError):
        return __version__


def run(cfg_path=None, once=False, sleep=time.sleep):
    """Check in, run what the website asks, repeat. once=True: a single check-in (tests). Returns "updated" when a
    newer CamAgent was installed meanwhile: main() then exits, and the service manager starts the new version."""
    cfg_path = cfg_path or config.default_config_path()
    backoff = 5
    while True:
        if _installed() != __version__:
            log.warning("CamAgent %s was installed; the helper restarts to use it", _installed())
            return "updated"
        site, token = _account(cfg_path)
        if not token:
            if once:
                return "signed out"
            sleep(300)                     # not linked: check the sign-in file again in a while
            continue
        try:
            reply = link.checkin(site, token, _info(cfg_path))
            backoff = 5
        except link.NotSignedIn:
            log.warning("this computer was unlinked on the website; waiting for a new sign-in")
            if once:
                return "unlinked"
            sleep(300)
            continue
        except link.LinkError as e:
            log.info("couldn't reach YonderView (%s); trying again in %ss", e, backoff)
            if once:
                return "offline"
            sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        for job in reply.get("jobs") or []:
            if not isinstance(job, dict) or not isinstance(job.get("id"), int):
                continue
            ok, out = run_job(job)
            try:
                link.job_result(site, token, job["id"], ok, out)
            except link.LinkError as e:
                log.info("couldn't send a result (%s)", e)
        if once:
            return "ok"
        try:
            wait = max(2, min(300, int(reply.get("next_s") or 60)))
        except (TypeError, ValueError):
            wait = 60
        sleep(wait)


def main(cfg_path=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stdout)
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
        print("camagent remote runs as root (it reads this computer's YonderView sign-in).")
        return 1
    log.info("camagent %s: website setup helper started", __version__)
    if run(cfg_path) == "updated":
        return 75                          # systemd (Restart=always) or the Windows task starts the new version
    return 0
