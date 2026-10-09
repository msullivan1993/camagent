"""Run with: python tests/test_add_camera.py  (from the repo folder)."""
import builtins, sys, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, configure

d = Path(tempfile.mkdtemp()); main = d / "camagent.toml"
block = ["CAMERA=newcam_0003",
         "SRT_URL=srt://ingest.yonderview.net:8890?streamid=publish:newcam_0003:newcam_0003:PW&latency=400000&pkt_size=1316&passphrase=PHRASE&pbkeylen=16",
         "MQTT_HOST=mqtt.yonderview.net", "MQTT_PORT=8883", "MQTT_TLS=true", "MQTT_USER=newcam_0003", "MQTT_PASS=MQ", ""]
answers = iter(["n", "10.0.0.5", "", "admin",          # find camera: no search, host, port, user
                "2",                                    # ONVIF failed: continue without it
                "rtsp://10.0.0.5:554/stream1", "n",     # stream URL, no PTZ
                *block, "",                             # settings block, MQTT port default
                "n"])                                   # don't install service
patches = [mock.patch.object(builtins, "input", lambda *a: next(answers)),
           mock.patch("getpass.getpass", lambda *a: "campw"),
           mock.patch.object(configure.camera, "find_ffmpeg", lambda *a: "/usr/bin/ffmpeg"),
           mock.patch.object(configure.camera, "find_ffprobe", lambda *a: ""),
           mock.patch.object(configure.camera, "onvif_inspect", side_effect=OSError("no onvif")),
           mock.patch.object(configure, "test_mqtt", lambda cfg: (True, "ok")),
           mock.patch.object(configure.service, "is_installed", lambda: False),
           mock.patch.object(configure, "_ask_auto_update", lambda *a: None),
           mock.patch.object(configure.service, "is_admin", lambda: True)]
for p in patches: p.start()
configure.add(main)
for p in patches: p.stop()
_, cams, _ = config.load_all(main)
c = cams[0]
assert c["platform"]["camera_id"] == "newcam_0003" and c["platform"]["mqtt_tls"] is True and c["platform"]["mqtt_port"] == 8883
assert c["camera"]["password"] == "campw" and c["camera"]["ptz"] is False
assert main.exists() and "[platform]" not in main.read_text()
print("ADD OK")


# ---- Failures send the user back instead of carrying on ----
d = Path(tempfile.mkdtemp()); main = d / "camagent.toml"
logins = []
def inspect(host, port, user, pw):
    logins.append(user)
    if user != "onvif":
        raise Exception("login rejected")
    return {"manufacturer": "X", "model": "Y", "firmware": "1", "profiles": [
        {"name": "main", "token": "p1", "encoding": "H264", "width": 1920, "height": 1080, "fps": 30,
         "ptz": True, "rtsp_url": "rtsp://10.0.0.6/main"}]}
probes = iter([None, [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}]])
mqtt_results = iter([(False, "broker refused the login"), (True, "ok")])
answers = iter(["n", "10.0.0.6", "", "admin",          # first try: web login
                "1",                                    # ONVIF rejected -> go back
                "", "", "", "onvif",                    # search? (no), keep host, keep port, ONVIF user
                "1", "y",                               # pick profile 1, enable PTZ
                "1", "rtsp://10.0.0.6/main2",           # stream unreadable -> enter a different URL
                *block, "",                             # settings block, MQTT port
                "1", *block, "",                        # login failed -> paste again
                "n"])
patches = [mock.patch.object(builtins, "input", lambda *a: next(answers)),
           mock.patch("getpass.getpass", lambda *a: ""),
           mock.patch.object(configure.camera, "find_ffmpeg", lambda *a: "/usr/bin/ffmpeg"),
           mock.patch.object(configure.camera, "find_ffprobe", lambda *a: "/usr/bin/ffprobe"),
           mock.patch.object(configure.camera, "measure_stream", lambda *a, **k: {}),
           mock.patch.object(configure.camera, "probe", lambda *a: next(probes)),
           mock.patch.object(configure.camera, "onvif_inspect", inspect),
           mock.patch.object(configure, "test_mqtt", lambda cfg: next(mqtt_results)),
           mock.patch.object(configure.service, "is_installed", lambda: False),
           mock.patch.object(configure, "_ask_auto_update", lambda *a: None),
           mock.patch.object(configure.service, "is_admin", lambda: True)]
for p in patches: p.start()
configure.add(main)
for p in patches: p.stop()
_, cams, _ = config.load_all(main)
c = cams[0]
assert logins == ["admin", "onvif"], logins
assert c["camera"]["username"] == "onvif" and c["camera"]["ptz"] is True and c["camera"]["ptz_profile"] == "p1"
assert c["camera"]["rtsp_url"] == "rtsp://10.0.0.6/main2"
print("RETRY OK")


# ---- An H.265 stream stops setup until it's fixed or another stream is picked ----
d = Path(tempfile.mkdtemp()); main = d / "camagent.toml"
profiles = {"manufacturer": "X", "model": "Y", "firmware": "1", "profiles": [
    {"name": "main", "token": "p1", "encoding": "H265", "width": 2560, "height": 1440, "fps": 25, "ptz": False,
     "rtsp_url": "rtsp://10.0.0.7/main"},
    {"name": "sub", "token": "p2", "encoding": "H264", "width": 640, "height": 360, "fps": 15, "ptz": False,
     "rtsp_url": "rtsp://10.0.0.7/sub"}]}
hevc = [{"codec_type": "video", "codec_name": "hevc", "width": 2560, "height": 1440}]
h264 = [{"codec_type": "video", "codec_name": "h264", "width": 2560, "height": 1440}]
probes = iter([hevc, hevc, h264])
labels = []
real_choose = configure.choose
def spy_choose(items, label, allow_none_text=None):
    labels.extend(label(i) for i in items)
    return real_choose(items, label, allow_none_text)
answers = iter(["n", "10.0.0.7", "", "onvif",
                "2",                       # list shows H.264 first: pick #2 = the H.265 main stream
                "2",                       # H.265 -> pick a different stream
                "2",                       # pick main again (camera now switched to H.264)...
                "1",                       # ...still H.265 -> "I've changed it, check again" -> now H.264
                *block, "", "n"])
patches = [mock.patch.object(builtins, "input", lambda *a: next(answers)),
           mock.patch("getpass.getpass", lambda *a: ""),
           mock.patch.object(configure, "choose", spy_choose),
           mock.patch.object(configure.camera, "find_ffmpeg", lambda *a: "/usr/bin/ffmpeg"),
           mock.patch.object(configure.camera, "find_ffprobe", lambda *a: "/usr/bin/ffprobe"),
           mock.patch.object(configure.camera, "measure_stream", lambda *a, **k: {}),
           mock.patch.object(configure.camera, "probe", lambda *a: next(probes)),
           mock.patch.object(configure.camera, "onvif_inspect", lambda *a: profiles),
           mock.patch.object(configure, "test_mqtt", lambda cfg: (True, "ok")),
           mock.patch.object(configure.service, "is_installed", lambda: False),
           mock.patch.object(configure, "_ask_auto_update", lambda *a: None),
           mock.patch.object(configure.service, "is_admin", lambda: True)]
for p in patches: p.start()
configure.add(main)
for p in patches: p.stop()
assert labels[0].startswith("sub: H264"), labels[0]                 # H.264 listed first
assert "won't play" in labels[1]                                    # H.265 flagged
_, cams, _ = config.load_all(main)
assert cams[0]["camera"]["rtsp_url"] == "rtsp://10.0.0.7/main"
print("CODEC OK")
