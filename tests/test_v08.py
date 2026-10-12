"""Run with: python tests/test_v08.py  (from the repo folder). Simulated ONVIF cameras."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402
from camagent.controls import Controls, aux_kind, describe, off_for  # noqa: E402

AUX = ["tt:Wiper|On", "tt:Washer|On", "tt:IRLamp|On", "tt:IRLamp|Off"]


def fake_ptz(aux=AUX, focus=True, ircut=("ON", "OFF", "AUTO")):
    cam = mock.Mock()
    dev = cam.create_devicemgmt_service.return_value
    dev.GetDeviceInformation.return_value = NS(Manufacturer="NORMAL", Model="4K-PTZDT", FirmwareVersion="V5.1")
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    dev.GetSystemDateAndTime.return_value = NS(UTCDateTime=NS(
        Date=NS(Year=now.year, Month=now.month, Day=now.day), Time=NS(Hour=now.hour, Minute=now.minute, Second=now.second)))
    img = cam.create_imaging_service.return_value
    img.GetOptions.return_value = NS(Focus=NS(AutoFocusModes=["AUTO", "MANUAL"] if focus else []),
                                     IrCutFilterModes=list(ircut))
    img.GetMoveOptions.return_value = NS(Continuous=NS(Speed=NS(Min=-1, Max=1)) if focus else None)
    ptzsvc = mock.Mock()
    ptzsvc.GetNodes.return_value = [NS(HomeSupported=True, MaximumNumberOfPresets=255, AuxiliaryCommands=list(aux),
                                       SupportedPTZSpaces=NS(AbsolutePanTiltPositionSpace=[1]))]
    return NS(onvif=cam, source="vs0", ptz=ptzsvc, token="prof0", last_activity=0.0, moves=0), img, ptzsvc, dev


# 1. Probe: reports what's there, touches nothing
p, img, ptzsvc, dev = fake_ptz()
c = Controls(p)
caps = c.probe()
assert caps["device"]["model"] == "4K-PTZDT" and caps["reboot"] and abs(caps["clock_drift_s"]) < 5
assert caps["ptz"] == {"home": True, "presets": 255, "absolute": True}
assert caps["focus"] == {"auto": True, "move": True} and caps["daynight"] == ["AUTO", "OFF", "ON"]
assert caps["aux"] == AUX
assert not ptzsvc.SendAuxiliaryCommand.called and not img.Move.called and not dev.SystemReboot.called
text = "\n".join(describe(caps))
assert "NORMAL 4K-PTZDT" in text and "tt:Wiper|On" in text

# 2. Aux: only offered commands; wiper cooldown; lights switch themselves off
assert aux_kind("tt:Wiper|On") == "wiper" and aux_kind("tt:IRLamp|On") == "light" and aux_kind("tt:Heater|On") == "heater"
assert off_for("tt:IRLamp|On", AUX) == "tt:IRLamp|Off"
try:
    c.aux("tt:Rocket|On"); raise AssertionError("should refuse")
except ValueError:
    pass
c.aux("tt:Wiper|On")
try:
    c.aux("tt:Wiper|On"); raise AssertionError("cooldown")
except RuntimeError as e:
    assert "wait" in str(e)
c.light_s = 0.2
r = c.aux("tt:IRLamp|On")
assert r["off_in_s"] == 0.2
time.sleep(0.5)
sent = [call.args[0]["AuxiliaryData"] for call in ptzsvc.SendAuxiliaryCommand.call_args_list]
assert sent == ["tt:Wiper|On", "tt:IRLamp|On", "tt:IRLamp|Off"], sent        # turned itself off
c.aux("tt:IRLamp|On", keep=True)                                              # the host chose to keep it on
time.sleep(0.4)
assert [x.args[0]["AuxiliaryData"] for x in ptzsvc.SendAuxiliaryCommand.call_args_list][-1] == "tt:IRLamp|On"

# 3. Day/night: forced modes go back to automatic
c.daynight_s = 0.2
assert c.daynight("night")["reverts_in_s"] == 0.2
time.sleep(0.5)
modes = [x.args[0]["ImagingSettings"]["IrCutFilter"] for x in img.SetImagingSettings.call_args_list]
assert modes == ["OFF", "AUTO"], modes

# 4. Focus moves stop on their own
c.focus("far", ms=150)
time.sleep(0.4)
assert img.Move.call_args.args[0]["Focus"]["Continuous"]["Speed"] == 0.5 and img.Stop.called

# 5. The agent tells real position reports from fixed placeholder values
cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=True)
cfg["platform"].update(camera_id="pz_8", ingest_host="i", srt_password="s", mqtt_host="m", mqtt_password="p")
for reports, expected in (([0.25] * 8, "fake"), ([0.25, 0.25, 0.4, 0.4, 0.4], "real")):
    a = CameraAgent(cfg)
    a.ptz = mock.Mock(connected=False, moves=0, last_activity=0.0)
    seq = iter(reports)
    a.ptz.position.side_effect = lambda: {"pan": next(seq, reports[-1]), "tilt": 0.25, "zoom": 0.25}
    stop = threading.Event()
    calls = {"n": 0}

    def tick(_s):
        calls["n"] += 1
        a.ptz.moves += 1                                      # someone keeps steering it
        a.ptz.last_activity = time.time() - 10
        if calls["n"] > 6:
            stop.set()
    with mock.patch.object(a, "_publish_telemetry"), mock.patch.object(stop, "wait", side_effect=tick):
        a.track_position(stop)
    assert a.position_kind == expected, (reports, a.position_kind)
print("v0.8 OK")

# --- 0.8.1: the daylight-saving quirk in reported UTC, and the absolute-move test ---
import datetime as _dt  # noqa: E402
p, img, ptzsvc, dev = fake_ptz()
now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)          # firmware forgot the DST hour
dev.GetSystemDateAndTime.return_value = NS(DaylightSavings=True, TimeZone=NS(TZ="EST5EDT,M3.2.0,M11.1.0"), UTCDateTime=NS(
    Date=NS(Year=now.year, Month=now.month, Day=now.day), Time=NS(Hour=now.hour, Minute=now.minute, Second=now.second)))
caps = Controls(p).probe()
assert caps["clock_note"] == "dst" and "OK (the camera reports UTC without daylight saving" in "\n".join(describe(caps))
from camagent import controls as _c  # noqa: E402
answers = iter([True, True, True])
with mock.patch("camagent.configure.yes", side_effect=lambda *a, **k: next(answers)), mock.patch.object(_c.time, "sleep"):
    assert _c.test_moves(p) is True
xs = [c.args[0]["Position"]["PanTilt"]["x"] for c in ptzsvc.AbsoluteMove.call_args_list]
assert xs == [-0.5, 0.5] and ptzsvc.GotoHomePosition.called
answers = iter([True, False, False])
with mock.patch("camagent.configure.yes", side_effect=lambda *a, **k: next(answers)), mock.patch.object(_c.time, "sleep"):
    assert _c.test_moves(p) is False                                        # it ignored the moves
print("v0.8.1 OK")
