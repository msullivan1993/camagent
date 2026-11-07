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


HANDLERS = {"discover": _discover}


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


def run(cfg_path=None, once=False, sleep=time.sleep):
    """Check in, run what the website asks, repeat. once=True: a single check-in (tests)."""
    cfg_path = cfg_path or config.default_config_path()
    backoff = 5
    while True:
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
    run(cfg_path)
    return 0
