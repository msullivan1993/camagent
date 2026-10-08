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
