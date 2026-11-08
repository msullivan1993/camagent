"""Website setup (0.18, beta): `camagent remote`, the helper that lets cameras be set up on yonderview.net.

It runs with administrator rights (Windows: a SYSTEM task at startup; Linux: a root service), next to the streaming
service, because it holds this computer's link token (account.toml, which the streaming service never reads) and
because setting a camera up means writing CamAgent's protected settings files. It checks in with the website about
once a minute (every couple of seconds while someone has this computer's setup page open), runs the jobs it's
given and sends back the results.

Only the actions in HANDLERS: no shell, no files, no addresses outside this computer's own network. Anything else
the website might send is answered "unknown" and ignored. Without a link (signed out) it just waits.

0.19: "connect" sets up an approved camera (from a setup kept with its camera request, or a login sent with the job)
and "forget" deletes a kept setup; kept setups are deleted by themselves after 30 days.
"""
import logging
import os
import platform
import socket
import sys
import threading
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
        raise ValueError("That isn't a camera address on this network") from None
    if not (ip.is_private or ip.is_link_local) or ip.is_loopback or ip.is_multicast:
        raise ValueError("CamAgent only connects to cameras on this computer's own network")
    try:
        port = int((job.get("payload") or {}).get("port") or 80)
    except (TypeError, ValueError):
        port = 80
    if not 1 <= port <= 65535:
        raise ValueError("That port number isn't valid")
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
        raise ValueError("That video address isn't the camera's")
    ffmpeg = camera.find_ffmpeg("")
    if not ffmpeg:
        raise ValueError("ffmpeg isn't installed on this computer. Install it, then try again")
    r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i",
                        rtsp_with_auth(url, user, pw), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "6",
                        "-f", "image2", "-c:v", "mjpeg", "pipe:1"], capture_output=True, timeout=25,
                       stdin=subprocess.DEVNULL)
    if r.returncode or not r.stdout:
        raise ValueError(config.scrub((r.stderr or b"").decode("utf-8", "replace").strip()[-200:])
                         or "CamAgent got no picture from the camera")
    if len(r.stdout) > 120_000:
        raise ValueError("The camera's picture is too big for CamAgent to send")
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
    path = _pending_file(p.get("setup"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)           # only CamAgent's administrator side (this helper) reads these
    except OSError:
        pass
    data = {"host": host, "onvif_port": port, "username": user, "password": secret.protect(pw),
            "rtsp_url": str(p.get("rtsp_url") or "")[:300], "ptz": bool(p.get("ptz")),
            "ptz_profile": str(p.get("token") or "")[:64], "convert": p.get("convert") == "1080p",
            "source_height": _int(p.get("source_height")), "staged": int(time.time())}
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {"staged": True}


def _int(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


PENDING_DAYS = 30            # a kept setup YonderView hasn't asked for by then is deleted (the login with it)
_cfg_path = None             # set by run(): the settings file the connect job writes next to


def _pending_file(setup_id):
    setup_id = str(setup_id or "")
    if not setup_id.isalnum() or len(setup_id) > 40:
        raise ValueError("That setup ID isn't valid")
    return config.state_dir() / "pending" / f"{setup_id}.json"


def _forget(job):
    """Delete a kept setup (YonderView turned the request down, or the host took it back)."""
    path = _pending_file((job.get("payload") or {}).get("setup"))
    existed = path.exists()
    path.unlink(missing_ok=True)
    return {"forgotten": existed}


def prune_pending(now=None):
    """Kept setups older than PENDING_DAYS are deleted, logins and all."""
    folder = config.state_dir() / "pending"
    now = now or time.time()
    for f in folder.glob("*.json") if folder.exists() else []:
        try:
            if now - f.stat().st_mtime > PENDING_DAYS * 86400:
                f.unlink()
                log.info("deleted a camera setup YonderView never asked for (%s)", f.stem)
        except OSError:
            pass


def _camera_details(job):
    """(camera section, convert, source height) from the kept setup, or from the job itself (login included)."""
    import json

    from . import secret
    p = job.get("payload") or {}
    if p.get("setup"):
        path = _pending_file(p["setup"])
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("CamAgent no longer has this camera's setup. CamAgent keeps a setup for 30 days. Set the "
                             "camera up again from its page on YonderView") from None
        pw = secret.unprotect(d.get("password") or "")
        if d.get("password") and not pw:
            raise ValueError("CamAgent couldn't read the camera's saved password. Set the camera up again")
        cam = {"host": d["host"], "onvif_port": int(d.get("onvif_port") or 80), "username": d.get("username", ""),
               "password": pw, "rtsp_url": d.get("rtsp_url", ""), "ptz": bool(d.get("ptz")),
               "ptz_profile": d.get("ptz_profile", "")}
        _local_host({"payload": {"host": cam["host"], "port": cam["onvif_port"]}})
        return cam, bool(d.get("convert")), _int(d.get("source_height")), path
    host, port = _local_host(job)
    user, pw = _login(job)
    cam = {"host": host, "onvif_port": port, "username": user, "password": pw,
           "rtsp_url": str(p.get("rtsp_url") or "")[:300], "ptz": bool(p.get("ptz")),
           "ptz_profile": str(p.get("token") or "")[:64]}
    return cam, p.get("convert") == "1080p", _int(p.get("source_height")), None


def _connect(job):
    """Connect an approved camera: fetch its connection settings from YonderView (the same request CamAgent's own
    menu makes), save the camera here with its login encrypted, check YonderView takes the login, and start it."""
    import copy
    from urllib.parse import urlparse

    from . import camera, convert, link, service
    from .configure import _use_block, test_mqtt
    p = job.get("payload") or {}
    slug = str(p.get("slug") or "")
    if not config.CAMERA_ID.fullmatch(slug):
        raise ValueError("That isn't a camera ID")
    details, want_convert, height, pending = _camera_details(job)
    if urlparse(details["rtsp_url"]).scheme != "rtsp" or urlparse(details["rtsp_url"]).hostname != details["host"]:
        raise ValueError("That video address isn't the camera's")
    main_path = Path(_cfg_path or config.default_config_path())
    site, token = _account(main_path)
    if not token:
        raise ValueError("You haven't signed in to YonderView on this computer yet")
    state, block, info = link.connect(site, token, slug, replace=bool(p.get("replace")))
    if state == "confirm":
        raise ValueError("needs_confirm: this camera already has connection settings")
    if state == "approve":
        raise ValueError("YonderView doesn't allow this computer to set up that camera yet")
    main, cams, _ = config.load_all(main_path)
    cam = {s: copy.deepcopy(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
    for s in config.MAIN_SECTIONS:
        cam[s] = copy.deepcopy(main[s])
    old = next((c for c in cams if c["platform"].get("camera_id") == slug), None)
    if old:                                    # set up again: keep its stream choices (audio, weather station...)
        for s in ("stream", "weather"):
            cam[s].update(copy.deepcopy(old.get(s) or {}))
        cam["_source"], cam["_legacy"] = old.get("_source"), old.get("_legacy")
    cam["camera"].update(details)
    _use_block(cam, block)
    ffmpeg = camera.find_ffmpeg(cam["stream"].get("ffmpeg") or (cams[0]["stream"].get("ffmpeg", "") if cams else ""))
    if ffmpeg:
        cam["stream"]["ffmpeg"] = ffmpeg
    limit = int(info.get("max_height") or 1080)
    out = {"camera": slug, "convert": "off"}
    if height:
        cam["stream"]["source_height"] = height
    if want_convert or (height and height > limit):
        if not ffmpeg:
            raise ValueError("This camera's video is bigger than 1080p, and ffmpeg isn't installed to convert it. "
                             "Install ffmpeg, then try again")
        method = convert.best(convert.detect(ffmpeg, timeout=20))
        if not method:
            raise ValueError("This computer can't convert the camera's video to 1080p. Set the camera itself to "
                             "1080p")
        cam["stream"].update(convert="1080p", encoder=method)
        out.update(convert="1080p", encoder=convert.LABELS.get(method, method))
    ok, msg = test_mqtt(cam)
    if not ok:
        raise ValueError(f"YonderView didn't accept the camera's new login: {msg}")
    config.save_camera(cam, main_path)
    try:
        if service.is_installed():
            service.restart(remote=False)          # not this helper: it's still answering the website
        else:
            service.install(main_path)
    except SystemExit as e:                        # the service functions stop with a message for the person
        raise ValueError(str(e)) from None
    if pending:
        pending.unlink(missing_ok=True)            # the login now lives (encrypted) in the camera's settings file
    log.info("camera %s set up from the website", slug)
    return out


HANDLERS = {"discover": _discover, "inspect": _inspect, "snapshot": _snapshot, "ptz_test": _ptz_test,
            "stage": _stage, "connect": _connect, "forget": _forget}


# ---------- what this computer can do with video (0.20): measured once, told to the website ----------
CAPABILITY_FILE = "capability.json"
_measuring = threading.Event()


def _fingerprint(ffmpeg):
    """Changes when the hardware or ffmpeg changes: then the test runs again."""
    try:
        size = os.path.getsize(ffmpeg) if ffmpeg else 0
    except OSError:
        size = 0
    return f"{platform.machine()}|{platform.processor()}|{os.cpu_count()}|{ffmpeg}|{size}"


def capability():
    """The last video test's summary, or None (not tested yet)."""
    import json
    try:
        return json.loads((config.state_dir() / CAPABILITY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def measure(cfg_path=None):
    """Test how this computer makes video (each way it could: graphics chip or processor), on a 1080p test clip,
    and keep the summary. About a minute or two of hard work for the computer."""
    import json

    from . import camera, convert
    from .versions import GPU_METHODS
    try:
        _, cams, _ = config.load_all(cfg_path)
        configured = cams[0]["stream"].get("ffmpeg", "") if cams else ""
    except Exception:  # noqa: BLE001
        configured = ""
    ffmpeg = camera.find_ffmpeg(configured)
    if not ffmpeg:
        return None
    results = convert.detect(ffmpeg, size="1920x1080", timeout=20)
    gpu = next(((m, s) for m, s in results if s and m in GPU_METHODS and s >= 1.2), None)
    cpu = next((s for m, s in results if m == "x264"), None)
    out = {"gpu": gpu[0] if gpu else "", "gpu_label": convert.LABELS.get(gpu[0], gpu[0]) if gpu else "",
           "gpu_speed": gpu[1] if gpu else None, "cpu_speed": cpu, "at": int(time.time()),
           "fp": _fingerprint(ffmpeg)}
    path = config.state_dir() / CAPABILITY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    log.info("video test: %s", f"{out['gpu_label']} at {out['gpu_speed']}x" if gpu else "no usable graphics chip")
    return out


def _measure_if_needed(cfg_path):
    """In the background, so check-ins carry on: when there's no test result yet, or the hardware or ffmpeg
    changed since."""
    from . import camera
    if _measuring.is_set():
        return
    try:
        _, cams, _ = config.load_all(cfg_path)
        configured = cams[0]["stream"].get("ffmpeg", "") if cams else ""
    except Exception:  # noqa: BLE001
        configured = ""
    known = capability()
    if known and known.get("fp") == _fingerprint(camera.find_ffmpeg(configured)):
        return

    def go():
        _measuring.set()
        try:
            measure(cfg_path)
        except Exception as e:  # noqa: BLE001 - the website then just doesn't know yet
            log.warning("the video test didn't work: %s", e)
        finally:
            _measuring.clear()
    threading.Thread(target=go, daemon=True, name="video-test").start()


def _info(cfg_path):
    try:
        _, cams, _ = config.load_all(cfg_path)
        n = len(cams)
    except Exception:  # noqa: BLE001
        n = None
    out = {"version": __version__, "os": f"{platform.system()} {platform.release()}"[:60],
           "hostname": socket.gethostname()[:60], "python": platform.python_version(), "cameras": n}
    cap = capability()
    if cap:
        out["encoders"] = {k: cap.get(k) for k in ("gpu", "gpu_label", "gpu_speed", "cpu_speed", "at")}
    return out


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
    global _cfg_path
    cfg_path = _cfg_path = cfg_path or config.default_config_path()
    backoff, pruned = 5, 0
    while True:
        if time.time() - pruned > 3600:
            prune_pending()
            if not once:
                _measure_if_needed(cfg_path)       # once per hardware/ffmpeg; checked hourly
            pruned = time.time()
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
        print("CamAgent's website setup helper (camagent remote) must run as root. It reads this computer's "
              "YonderView sign-in.")
        return 1
    log.info("camagent %s: website setup helper started", __version__)
    if run(cfg_path) == "updated":
        return 75                          # systemd (Restart=always) or the Windows task starts the new version
    return 0
