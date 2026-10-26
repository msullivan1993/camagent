"""Run with: python tests/test_v16_4.py  (from the repo folder). 0.16.4: the website asks where the camera points
right now (before saving a map direction), and the position is checked every 5 s even when nobody steers it here."""
import sys
import threading
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402

cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=True)
cfg["platform"].update(camera_id="pos_1", ingest_host="i", srt_password="s", mqtt_host="m", mqtt_password="p")

# 1. "position": read from the camera this moment, sent on if it moved, stale cache never used
a = CameraAgent(cfg)
a.ptz = mock.Mock(last_activity=0.0, moves=0)
a.ptz_position = {"pan": 0.32, "tilt": 0.5, "zoom": 0.1, "t": 1}       # from before the camera was moved elsewhere
a.ptz.position.return_value = {"pan": -0.68, "tilt": 0.5, "zoom": 0.1, "space": ""}
with mock.patch.object(a, "_publish_telemetry") as pub:
    got = a._handle({"op": "position"})
assert got["pan"] == -0.68 and got["fresh"], got
assert a.ptz_position["pan"] == -0.68 and pub.call_count == 1
with mock.patch.object(a, "_publish_telemetry") as pub:                 # same place: nothing new to send
    a._handle({"op": "position"})
assert pub.call_count == 0
a.ptz.position.return_value = None
try:
    a._handle({"op": "position"})
    raise AssertionError("no reading should be an error")
except RuntimeError:
    pass
a.position_kind = "fake"                                               # cameras that always report one spot
a.ptz.commanded = {"pan": 0.2, "tilt": 0, "zoom": 0}
assert a._handle({"op": "position"}) == {"pan": 0.2, "tilt": 0, "zoom": 0, "commanded": True}

# 2. idle checks every 5 s, busy every 2 s
a = CameraAgent(cfg)
a.ptz = mock.Mock(last_activity=0.0, moves=0)
a.ptz.position.return_value = {"pan": 0.1, "tilt": 0, "zoom": 0}
waits = []
stop = threading.Event()
with mock.patch.object(a, "_publish_telemetry"), \
        mock.patch.object(stop, "wait", side_effect=lambda s: (waits.append(s), len(waits) >= 2 and stop.set())):
    a.track_position(stop)
assert waits == [5, 5], waits
a.ptz.last_activity = time.time()
waits.clear()
stop = threading.Event()
with mock.patch.object(a, "_publish_telemetry"), \
        mock.patch.object(stop, "wait", side_effect=lambda s: (waits.append(s), stop.set())):
    a.track_position(stop)
assert waits == [2], waits
print("v0.16.4 OK")
