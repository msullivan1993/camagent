"""Run with: python tests/test_v16_5.py  (from the repo folder). 0.16.5: removing a camera always restarts CamAgent
(the last one too); a camera that says it doesn't report position is known at once; presets carry their position
when the camera gives it."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, configure  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402
from camagent.ptz import PTZ  # noqa: E402

cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=True)
cfg["platform"].update(camera_id="pos_1", ingest_host="i", srt_password="s", mqtt_host="m", mqtt_password="p")

# 1. removing the last camera restarts the service (it kept streaming before), without asking
for n in (1, 2):
    cams = [{"platform": {"camera_id": f"c{i}"}, "_source": f"/x/c{i}.toml"} for i in range(n)]
    with mock.patch.object(configure, "_check_writable"), \
            mock.patch.object(configure.config, "load_all", return_value=({}, cams, [])), \
            mock.patch.object(configure.config, "remove_camera") as rm, \
            mock.patch.object(configure, "yes", return_value=True) as asked, \
            mock.patch.object(configure.service, "is_installed", return_value=True), \
            mock.patch.object(configure.service, "restart") as restart, \
            mock.patch("builtins.print"):
        configure.remove("/x/camagent.toml", "c0")
    assert rm.called and restart.call_count == 1, (n, restart.call_count)
    assert asked.call_count == 1                                     # only "Remove camera ...?"

# 2. the camera says up front it doesn't report position: "fake" at once, no moves needed
a = CameraAgent(cfg)
a.ptz = mock.Mock(last_activity=0.0, moves=0, reports_position=False, commanded=None)
a.ptz.position.return_value = {"pan": 0.25, "tilt": 0.25, "zoom": 0.25}
stop = threading.Event()
with mock.patch.object(a, "_publish_telemetry") as pub, mock.patch.object(stop, "wait", side_effect=lambda s: stop.set()):
    a.track_position(stop)
assert a.position_kind == "fake" and pub.called
b = CameraAgent(cfg)                                                  # says nothing (None): the move test decides
b.ptz = mock.Mock(last_activity=0.0, moves=0, reports_position=None, commanded=None)
b.ptz.position.return_value = {"pan": 0.25, "tilt": 0.25, "zoom": 0.25}
stop = threading.Event()
with mock.patch.object(b, "_publish_telemetry"), mock.patch.object(stop, "wait", side_effect=lambda s: stop.set()):
    b.track_position(stop)
assert b.position_kind == "unknown"

# 3. presets: the position when the camera includes it
p = PTZ(cfg)
p.connected, p.token = True, "tok"
p.ptz = mock.Mock()
p.ptz.GetPresets.return_value = [
    NS(token="1", Name="Home-WSW", PTZPosition=NS(PanTilt=NS(x=-0.40001, y=0.1), Zoom=NS(x=0.2))),
    NS(token="2", Name="NNW", PTZPosition=None),
]
assert p.presets() == [{"token": "1", "name": "Home-WSW", "pan": -0.4, "tilt": 0.1, "zoom": 0.2},
                       {"token": "2", "name": "NNW"}], p.presets()

# 4. what the camera says about StatusPosition is read when PTZ connects
with mock.patch("camagent.ptz.onvif_connect") as oc:
    cam = oc.return_value
    cam.create_media_service.return_value.GetProfiles.return_value = [NS(token="p1", Name="main", PTZConfiguration=1,
                                                                          VideoSourceConfiguration=None)]
    cam.create_ptz_service.return_value.GetServiceCapabilities.return_value = NS(StatusPosition=False)
    q = PTZ(cfg)
    q._connect()
assert q.reports_position is False and q.connected
print("v0.16.5 OK")
