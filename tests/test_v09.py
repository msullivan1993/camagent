"""Run with: python tests/test_v09.py  (from the repo folder). Simulated ONVIF cameras."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402
from camagent.controls import Controls, NotAllowed, OnvifSession  # noqa: E402

cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["camera"].update(host="h", rtsp_url="rtsp://h/x", username="u", password="p", ptz=False)
cfg["platform"].update(camera_id="fx_1", ingest_host="i", srt_password="s", srt_passphrase="phrase0123", mqtt_host="m", mqtt_password="p")

# 1. A fixed camera gets controls through a plain ONVIF session
a = CameraAgent(cfg)
assert a.ptz is None and isinstance(a.session, OnvifSession) and a.controls is not None
cam = mock.Mock()
cam.create_media_service.return_value.GetProfiles.return_value = [NS(token="p0", VideoSourceConfiguration=NS(SourceToken="vs0"))]
with mock.patch("camagent.camera.onvif_connect", return_value=cam):
    a.session._connect()
assert (a.session.token, a.session.source, a.session.connected) == ("p0", "vs0", True)
img = cam.create_imaging_service.return_value
assert a._handle({"op": "daynight", "mode": "night", "keep": True})["ok"]
assert img.SetImagingSettings.call_args.args[0]["ImagingSettings"]["IrCutFilter"] == "OFF"

# 2. Permission refusals become a plain explanation, remembered for the website
img.SetImagingSettings.side_effect = Exception("SOAP Fault: ter:NotAuthorized - Sender not Authorized")
try:
    a._handle({"op": "autofocus"}); raise AssertionError("should refuse")
except NotAllowed as e:
    assert "higher level" in str(e)
assert a.controls.caps["denied"] == ["autofocus"]

# 3. Clock sync points the camera at a time server
assert a._handle.__self__ is a
with mock.patch.object(a.controls, "probe"), mock.patch.object(a, "_publish_telemetry"):
    a._handle({"op": "sync_clock"})
dev = cam.create_devicemgmt_service.return_value
assert dev.SetNTP.call_args.args[0]["NTPManual"][0]["DNSname"] == "pool.ntp.org"
assert dev.SetSystemDateAndTime.call_args.args[0]["DateTimeType"] == "NTP"

# 4. Pointing: absolute move, remembered until someone steers by hand
from camagent.ptz import PTZ  # noqa: E402
pcfg = {s: dict(cfg[s]) for s in cfg}
pcfg["camera"]["ptz"] = True
p = PTZ(pcfg)
p.connected, p.token, p.ptz = True, "p0", mock.Mock()
c = Controls(p)
c.point(0.4, 0.1)
assert p.ptz.AbsoluteMove.call_args.args[0]["Position"]["PanTilt"] == {"x": 0.4, "y": 0.1}
assert p.commanded["pan"] == 0.4
with mock.patch.object(p, "_send", create=True):
    p.move(1, 0, 0, 200)
assert p.commanded is None

# 5. Stream fixer: H.265 at 1440p with 4 s keyframes -> H.264 1080p, 2 s keyframes
enc = NS(token="ve0", Encoding="H265", Resolution=NS(Width=2560, Height=1440),
         RateControl=NS(FrameRateLimit=25, BitrateLimit=8000), H264=NS(GovLength=100))
media = mock.Mock()
media.GetProfiles.return_value = [NS(token="p0", VideoEncoderConfiguration=enc)]
media.GetVideoEncoderConfigurationOptions.return_value = NS(H264=NS(
    ResolutionsAvailable=[NS(Width=1920, Height=1080), NS(Width=1280, Height=720)], GovLengthRange=NS(Min=1, Max=300)))
s = NS(onvif=mock.Mock(), token="p0", source="vs0", ptz=None)
s.onvif.create_media_service.return_value = media
changes = Controls(s).stream_fixes(apply=True)
assert len(changes) == 4, changes
assert (enc.Encoding, enc.Resolution.Height, enc.H264.GovLength, enc.RateControl.BitrateLimit) == ("H264", 1080, 50, 4000)
assert media.SetVideoEncoderConfiguration.called

# 6. Camera alarms: a tamper event is reported once, delivered with telemetry, then cleared
events = []
msg = NS(Topic=NS(_value_1="tns1:VideoSource/GlobalSceneChange/ImagingService"), Message="State value=\"true\"")
pull = mock.Mock()
stop = threading.Event()
pull.PullMessages.side_effect = lambda *_: (stop.set(), NS(NotificationMessage=[msg]))[1]
s2 = NS(onvif=mock.Mock(), token="p0", source="vs0", ptz=None)
s2.onvif.create_pullpoint_service.return_value = pull
cc = Controls(s2)
cc.watch_events(stop, events.append)
assert events and events[0]["kind"] == "tamper" and events[0]["topic"] == "ImagingService" and cc.caps["events"]
a.events_pending.append(events[0])
a.mq = mock.Mock(); a.mq.publish.return_value = mock.Mock(rc=0); a.mqtt_connected = True
import json  # noqa: E402
assert a._publish_telemetry() and not a.events_pending
assert json.loads(a.mq.publish.call_args.args[1])["events"][0]["kind"] == "tamper"
print("v0.9 OK")

# 7. `camagent probe` on a fixed camera uses plain ONVIF and reports its controls
import io, contextlib  # noqa: E401,E402
from camagent import controls as ctlmod  # noqa: E402
fixed = {s: dict(cfg[s]) for s in cfg}
cam2 = mock.Mock()
cam2.create_media_service.return_value.GetProfiles.return_value = [NS(token="p0", VideoSourceConfiguration=NS(SourceToken="vs0"))]
with mock.patch("camagent.config.load_all", return_value=(None, [fixed], None)), \
     mock.patch("camagent.configure._pick", return_value=fixed), \
     mock.patch("camagent.camera.onvif_connect", return_value=cam2), \
     mock.patch.object(ctlmod.Controls, "probe", return_value={"focus": {"move": True}, "reboot": True}):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        ctlmod.cli_probe()
text = out.getvalue()
assert "Alarms:" in text and "Reboot:      yes" in text, text
print("v0.9.1 probe OK")
