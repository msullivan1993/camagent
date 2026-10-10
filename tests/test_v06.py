"""Run with: python tests/test_v06.py  (from the repo folder)."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402
from camagent.ptz import PTZ  # noqa: E402

cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=True)
cfg["platform"].update(camera_id="pos_1", ingest_host="i", srt_password="s", mqtt_host="m", mqtt_password="p")

# 1. Reading the position from ONVIF GetStatus
p = PTZ(cfg)
p.connected, p.token = True, "tok"
p.ptz = mock.Mock()
p.ptz.GetStatus.return_value = NS(Position=NS(PanTilt=NS(x=0.25001, y=-0.1, space="generic"), Zoom=NS(x=0.5)))
assert p.position() == {"pan": 0.25, "tilt": -0.1, "zoom": 0.5, "space": "generic"}, p.position()
p.ptz.GetStatus.return_value = NS(Position=None)            # camera that doesn't report position
assert p.position() is None
p.connected = False
assert p.position() is None

# 2. Telemetry goes out only when the position changes, and includes it
a = CameraAgent(cfg)
a.ptz = mock.Mock(last_activity=time.time())
positions = iter([{"pan": 0.1, "tilt": 0, "zoom": 0}, {"pan": 0.1, "tilt": 0, "zoom": 0}, {"pan": 0.3, "tilt": 0, "zoom": 0}])
a.ptz.position.side_effect = lambda: next(positions, {"pan": 0.3, "tilt": 0, "zoom": 0})
sent = []
with mock.patch.object(a, "_publish_telemetry", side_effect=lambda: sent.append(dict(a.ptz_position))):
    stop = threading.Event()
    with mock.patch.object(stop, "wait", side_effect=lambda s: (len(sent) >= 2 or None) and stop.set()):
        a.track_position(stop)
assert [s["pan"] for s in sent] == [0.1, 0.3], sent                # unchanged reading wasn't re-sent
with mock.patch.object(a, "uplink", None):
    info = a._info()
assert info["ptz"]["position"]["pan"] == 0.3
print("v0.6 OK")
