"""Run with: python tests/test_v04.py  (from the repo folder)."""
import builtins
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import camera, config, configure, update  # noqa: E402
from camagent.uplink import Uplink  # noqa: E402

# 1. Frame rate and bitrate are counted over the last 10 s, not ffmpeg's since-start average
cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["platform"]["camera_id"] = "x"
u = Uplink(cfg)
lines = []
t = [1000.0]
for i in range(1, 13):                      # 12 seconds: 25 frames/s, 500 kB/s (4 Mbps); ffmpeg's "fps" lies
    lines += [f"frame={25 * i}\n", "fps=17.0\n", f"total_size={500_000 * i}\n", "bitrate=999kbits/s\n",
              "speed=1x\n", "progress=continue\n"]
clock = iter([1000.0 + i for i in range(1, 13)])
with mock.patch("camagent.uplink.time.time", side_effect=lambda: next(clock)):
    u._read_progress(mock.Mock(stdout=io.StringIO("".join(lines))))
assert u.stats["fps"] == 25.0 and u.stats["fps_camera"] == 25.0, u.stats
assert u.stats["bitrate_kbps"] == 4000.0, u.stats

# 2. Stream measurement from ffprobe packets
packets = [{"pts_time": f"{i / 15:.3f}", "size": "33333", "flags": "K_" if i % 60 == 0 else "__"} for i in range(91)]
out = json.dumps({"streams": [{"avg_frame_rate": "30/1", "height": 1440}], "packets": packets})
with mock.patch("camagent.camera.subprocess.run", return_value=mock.Mock(stdout=out)):
    m = camera.measure_stream("ffprobe", "rtsp://x", seconds=6)
assert (m["declared_fps"], m["actual_fps"], m["height"], m["keyframe_s"]) == (30.0, 15.0, 1440, 4.0), m
assert 3900 < m["kbps"] < 4100, m
tips = " ".join(camera.stream_advice(m, max_height=1080))
assert "would be declined" in tips and "30 fps" in tips and "Only 15" in tips and "I-frame interval" in tips, tips
assert camera.stream_advice({"declared_fps": 15, "actual_fps": 15, "kbps": 4000, "keyframe_s": 2, "height": 1080},
                            max_height=1080) == []

# 3. Setup stops when the stream is over the camera's limit (limit comes from the server)
d = Path(tempfile.mkdtemp()); main = d / "camagent.toml"
block = ["CAMERA=lim_0001",
         "SRT_URL=srt://ingest.yonderview.net:8890?streamid=publish:lim_0001:lim_0001:PW&latency=400000&pkt_size=1316&passphrase=PHRASE&pbkeylen=16",
         "MQTT_HOST=mqtt.yonderview.net", "MQTT_PORT=8883", "MQTT_TLS=true", "MQTT_USER=lim_0001", "MQTT_PASS=MQ", ""]
measures = iter([{"height": 1440, "declared_fps": 25, "actual_fps": 25}, {"height": 1080, "declared_fps": 25, "actual_fps": 25}])
def fake_mqtt(c):
    c["_server"] = {"max_height": 1080}
    return True, "ok"
answers = iter(["n", "10.0.0.8", "", "admin", "2", "rtsp://10.0.0.8/main", "n", *block, "",
                "2",                      # over the limit -> "I've changed the camera: check again"
                "n"])
patches = [mock.patch.object(builtins, "input", lambda *a: next(answers)),
           mock.patch("getpass.getpass", lambda *a: ""),
           mock.patch.object(configure.camera, "find_ffmpeg", lambda *a: "/usr/bin/ffmpeg"),
           mock.patch.object(configure.camera, "find_ffprobe", lambda *a: "/usr/bin/ffprobe"),
           mock.patch.object(configure.camera, "probe", lambda *a: [{"codec_type": "video", "codec_name": "h264", "height": 1440}]),
           mock.patch.object(configure.camera, "measure_stream", lambda *a, **k: next(measures)),
           mock.patch.object(configure.camera, "onvif_inspect", side_effect=OSError("no onvif")),
           mock.patch.object(configure, "test_mqtt", fake_mqtt),
           mock.patch.object(configure.service, "is_installed", lambda: False),
           mock.patch.object(configure.service, "is_admin", lambda: True)]
for p in patches: p.start()
configure.add(main)
for p in patches: p.stop()
_, cams, _ = config.load_all(main)
assert cams[0]["platform"]["camera_id"] == "lim_0001"
assert "_measure" not in (main.parent / "cameras" / "lim_0001.toml").read_text()      # nothing temporary saved

# 4. Updates install the latest release when there is one
resp = mock.MagicMock(); resp.__enter__.return_value.read.return_value = b'{"tag_name": "v0.4.0"}'
with mock.patch("urllib.request.urlopen", return_value=resp):
    assert update.latest_release("https://github.com/msullivan1993/camagent.git") == "v0.4.0"
with mock.patch("urllib.request.urlopen", side_effect=OSError("404")):
    assert update.latest_release("https://github.com/msullivan1993/camagent.git") is None

# 5. The agent remembers what the server says (limit, decline)
from camagent.agent import CameraAgent  # noqa: E402
c2 = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
c2["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=False, fps_configured=25)
c2["platform"].update(camera_id="ag_1", ingest_host="i", srt_password="s", mqtt_host="m", mqtt_password="p")
a = CameraAgent(c2)
a._apply_config(json.dumps({"max_height": 1080, "declined": "This camera is sending 1440p; its limit is 1080p."}))
assert a.status()["declined"].startswith("This camera") and a._info()["uplink"]["fps_configured"] == 25
print("v0.4 OK")
