"""Run with: python tests/test_v05.py  (from the repo folder)."""
import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402
from camagent.uplink import Uplink  # noqa: E402

cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=False)
cfg["platform"].update(camera_id="lat_1", ingest_host="ingest.example", srt_password="s", mqtt_host="m",
                       mqtt_password="p")
a = CameraAgent(cfg)

# 1. Latency from the server: applied and the stream restarted once; same value again does nothing
with mock.patch.object(a.uplink, "restart_now") as rs:
    a._apply_config(json.dumps({"latency_ms": 2000}))
    a._apply_config(json.dumps({"latency_ms": 2000}))
assert rs.call_count == 1 and a.cfg["stream"]["srt_latency_ms"] == 2000
assert "latency=2000000" in config.srt_url(a.cfg)                    # what ffmpeg will use (microseconds)
with mock.patch.object(a.uplink, "restart_now") as rs:
    a._apply_config(json.dumps({"latency_ms": 99999}))               # clamped to a sane range
assert a.cfg["stream"]["srt_latency_ms"] == 8000

# 2. A planned restart replaces ffmpeg immediately and isn't counted as a failure
procs = []
class FakeProc:
    def __init__(self, *a, **k):
        import io
        self.stdout, self.stderr, self.returncode, self._alive = io.StringIO(""), io.StringIO(""), None, True
        procs.append(self)
    def poll(self): return None if self._alive else 0
    def terminate(self): self._alive = False; self.returncode = 0
    def kill(self): self.terminate()
    def wait(self, t=None): return 0
u = Uplink(cfg)
with mock.patch("camagent.uplink.subprocess.Popen", FakeProc), \
     mock.patch.object(Uplink, "command", lambda self: ["ffmpeg"]):
    t = threading.Thread(target=u.run, daemon=True); t.start()
    time.sleep(0.3)
    u.restart_now("test")
    time.sleep(1.5)
    u.stop(); t.join(5)
assert len(procs) >= 2 and u.stats["restarts"] == 0, (len(procs), u.stats["restarts"])
print("v0.5 OK")

# 3. Scheduled updates: only newer releases, verified, rolled back when the new one doesn't come up
from camagent import service, update  # noqa: E402
assert update.version_tuple("v0.10.0") > update.version_tuple("0.9.9")

def scenario(latest, healthy_after, auto=True):
    calls = []
    cfgd = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
    cfgd["update"]["auto"] = auto
    statuses = iter([{"cameras": {"a": {"streaming": True}}}])              # before the update: 1 streaming
    with mock.patch.object(update.service, "_require_admin"), \
         mock.patch.object(update.config, "load_or_defaults", return_value=cfgd), \
         mock.patch.object(update, "latest_release", return_value=latest), \
         mock.patch.object(update, "_install", side_effect=lambda repo, ref: calls.append(ref) or ref), \
         mock.patch.object(update.service, "refresh_unit"), \
         mock.patch.object(update.service, "restart", side_effect=lambda: calls.append("restart")), \
         mock.patch.object(update, "_status", side_effect=lambda: next(statuses, {})), \
         mock.patch.object(update, "_verify", side_effect=lambda v, before, wait=0: calls.append(f"verify {v}") or
                           (healthy_after if v != update.__version__ else True)), \
         mock.patch.object(update, "_log", side_effect=lambda m: calls.append("log: " + m)):
        update.run(auto=True)
    return calls

c = scenario("v9.9.9", healthy_after=True)
assert c[:3] == [f"log: updating {update.__version__} -> v9.9.9", "v9.9.9", "restart"] and "log: updated to v9.9.9; cameras are streaming" in c, c
c = scenario("v9.9.9", healthy_after=False)
assert f"v{update.__version__}" in c and any(f"rolled back to {update.__version__}" in x for x in c), c                  # went back
c = scenario(f"v{update.__version__}", healthy_after=True)
assert c == [f"log: up to date ({update.__version__})"], c
c = scenario("v9.9.9", healthy_after=True, auto=False)
assert c == ["log: automatic updates are off; nothing to do"], c

# 4. The schedule: Windows task as SYSTEM, nightly, spread out
ran = []
with mock.patch.object(service, "_require_admin"), mock.patch.object(service.os, "name", "nt"), \
     mock.patch.object(service, "_run", side_effect=lambda cmd, check=True: ran.append(cmd)):
    service.install_update_schedule("C:/ProgramData/camagent/camagent.toml")
cmd = ran[0]
assert cmd[:4] == ["schtasks", "/Create", "/TN", "camagent update"] and "SYSTEM" in cmd and "03:00" in cmd
assert "update --auto --jitter 3600" in cmd[cmd.index("/TR") + 1]

# 5. Setup asks once; "no" turns it off
import tempfile  # noqa: E402
from camagent import configure  # noqa: E402
d = Path(tempfile.mkdtemp()); mp = d / "camagent.toml"
config.save_main({s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}, mp)
with mock.patch.object(configure, "yes", return_value=False), \
     mock.patch.object(configure.service, "update_schedule_installed", return_value=False), \
     mock.patch.object(configure.service, "is_installed", return_value=True):
    configure._ask_auto_update(mp)
m = config.load_or_defaults(mp)
assert m["update"]["auto"] is False and m["update"]["asked"] is True
with mock.patch.object(configure, "yes", side_effect=AssertionError("asked twice")):
    configure._ask_auto_update(mp)                                       # not asked again
print("auto-update OK")
