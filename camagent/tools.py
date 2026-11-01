"""Remote tools (0.17.8): what the camera's owner can do from its Health tab on yonderview.net, a fixed list:
show this camera's recent log, run the checks (camagent doctor), restart CamAgent, update now.

No shell, no arguments from the website: each action does one fixed thing. Everything sent back has passwords
hidden (config.scrub) and is size-capped. "Update now" only asks the computer's own update job (the nightly one,
which runs as administrator) to look for a newer signed release at once; it installs nothing the website names.
"""
import collections
import logging
import os
import subprocess
import sys
import threading
import time

from .config import scrub, state_dir

MAX_OUTPUT = 20 * 1024          # bytes sent back: room to spare after JSON escaping (the website takes 64 KB)
LOG_LINES = 200
UPDATE_FLAG = "update-now"      # in the state folder: the update job sees it and checks right away


class RingLog(logging.Handler):
    """The last few thousand log lines, in memory (the service's log file or journal may not be readable by the
    service itself)."""

    def __init__(self, size=3000):
        super().__init__(level=logging.INFO)
        self.lines = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record):
        try:
            self.lines.append((record.name, self.format(record)))
        except Exception:  # noqa: BLE001 - logging must never fail the caller
            pass


RING = RingLog()


def install_ring():
    root = logging.getLogger()
    if RING not in root.handlers:
        root.addHandler(RING)


def _cap(text):
    text = scrub(text)
    data = text.encode("utf-8", "replace")
    if len(data) <= MAX_OUTPUT:
        return text
    return "(earlier lines left out)\n" + data[-MAX_OUTPUT:].decode("utf-8", "ignore")


def recent_log(cam_id, n=LOG_LINES, others=()):
    """This camera's lines and CamAgent's general ones, without general lines that name another camera on this PC
    (its owner may be someone else)."""
    others = [o for o in others if o and o != cam_id]
    mine = [line for name, line in list(RING.lines)
            if name.startswith(f"{cam_id}.") or ((name in ("camagent", "root") or name.startswith("camagent."))
                                                 and not any(o in line for o in others))]
    return _cap("\n".join(mine[-n:]) or "(nothing logged yet since CamAgent started)")


DOCTOR = threading.Lock()        # one run of the checks at a time (each logs in to the camera and opens its video)


def run_doctor(config_path, cam_id):
    """camagent doctor for this camera, as the service runs it (a minute at most)."""
    cmd = [sys.executable, "-I", "-m", "camagent"] + (["--config", str(config_path)] if config_path else [])
    cmd += ["doctor", "--", cam_id]
    env = dict(os.environ, NO_COLOR="1", CAMAGENT_NO_PAUSE="1")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, env=env, stdin=subprocess.DEVNULL)
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr and r.stderr.strip() else "")
    except subprocess.TimeoutExpired:
        out = "The checks took longer than 90 seconds and were stopped."
    except OSError as e:
        out = f"Couldn't run the checks: {e}"
    return _cap(out.strip())


def request_update():
    """Leave the flag for the update job (it runs as administrator and checks it every few minutes)."""
    path = state_dir() / UPDATE_FLAG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(time.time())), encoding="ascii")
    return {"requested": True}


def update_requested():
    return (state_dir() / UPDATE_FLAG).exists()


def clear_update_request():
    try:
        (state_dir() / UPDATE_FLAG).unlink()
    except OSError:
        pass


RESTART = threading.Event()      # the Supervisor exits with 75 after stopping everything (0.17.12)


def restart_soon(stop=None, delay=1.5):
    """After the answer has gone out: stop everything the normal way (ffmpeg included, which Windows wouldn't end
    for us), then exit with 75; the service manager starts CamAgent again (systemd Restart=always, WinSW restarts
    on a failure exit). If stopping hangs, exit anyway."""
    def go():
        time.sleep(delay)
        logging.getLogger("camagent").warning("restarting (asked from the camera's Health tab)")
        RESTART.set()
        if stop is not None:
            stop.set()
            time.sleep(30)                   # the Supervisor normally exits well before this
        os._exit(75)
    threading.Thread(target=go, daemon=True, name="restart").start()
    return {"restarting": True}
