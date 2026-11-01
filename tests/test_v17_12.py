"""0.17.12 (security review 2026-11-01): passwords scrubbed in more shapes; a camera's log shows no other camera's
lines; one run of the checks at a time; restart stops everything first; the update flag is cleared first."""
import logging
import sys
import threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import tools  # noqa: E402
from camagent.config import scrub  # noqa: E402

assert scrub("RTSP://admin:S3cret@10.0.0.5/x") == "RTSP://***@10.0.0.5/x"
assert scrub("rtsp://admin:p@ss@10.0.0.5:554/s") == "rtsp://***@10.0.0.5:554/s"
assert "p@ss" not in scrub("ffmpeg: rtsp://admin:p@ss@10.0.0.5:554/s: 401")
assert scrub("rtmp://u:p@h/live") == "rtmp://***@h/live"
assert scrub("rtsp://10.0.0.5/stream") == "rtsp://10.0.0.5/stream"
assert scrub("write to user@example.com") == "write to user@example.com"

# a camera's log: its lines and general ones, but not general lines about another camera on this PC
tools.RING.lines.clear()
tools.install_ring()
logging.getLogger("cam_a.uplink").warning("streaming")
logging.getLogger("camagent").warning("camera 'cam_b' not started: bad settings")
logging.getLogger("camagent").warning("camagent starting")
logging.getLogger("cam_b.uplink").warning("b streaming")
out = tools.recent_log("cam_a", others=["cam_a", "cam_b"])
assert "streaming" in out and "camagent starting" in out and "cam_b" not in out, out

# the output fits the website's message even after JSON escaping
assert tools.MAX_OUTPUT <= 20 * 1024

# one run of the checks at a time
assert tools.DOCTOR.acquire(blocking=False)
from camagent.agent import CameraAgent  # noqa: E402
fake = mock.Mock(spec=CameraAgent)
r = CameraAgent._tool(fake, "tool_doctor", "req1")
assert r["started"] is False and "already running" in r["error"]
tools.DOCTOR.release()

# restart: the Supervisor's stop is set (everything stops the normal way) and the exit code is 75
stop = threading.Event()
exits = []
with mock.patch.object(tools.time, "sleep"), mock.patch.object(tools.os, "_exit", side_effect=exits.append):
    tools.restart_soon(stop, delay=0)
    for t in threading.enumerate():
        if t.name == "restart":
            t.join(2)
assert stop.is_set() and tools.RESTART.is_set() and exits == [75]
tools.RESTART.clear()

# the doctor command can't take the camera ID as an option
with mock.patch.object(tools.subprocess, "run", return_value=mock.Mock(stdout="ok", stderr="")) as run:
    tools.run_doctor(None, "cam_a")
assert run.call_args[0][0][-2:] == ["--", "cam_a"]

# update now: the flag is cleared before anything that could fail
from camagent import update  # noqa: E402
calls = []
with mock.patch.object(tools, "update_requested", return_value=True), \
        mock.patch.object(tools, "clear_update_request", side_effect=lambda: calls.append("cleared")), \
        mock.patch.object(update.service, "_require_admin", side_effect=RuntimeError("boom")):
    try:
        update.run(if_requested=True)
    except RuntimeError:
        pass
assert calls == ["cleared"]
print("v0.17.12 OK")
