"""Run with: python tests/test_multicamera.py  (from the repo folder; needs the camagent dependencies)."""
import builtins, json, os, sys, tempfile, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, configure, agent

d = Path(tempfile.mkdtemp()); main = d / "camagent.toml"
legacy = config.load_or_defaults(None if False else main)
legacy["camera"].update(host="192.168.1.10", rtsp_url="rtsp://192.168.1.10/live", username="admin", password="pw")
legacy["platform"].update(camera_id="test1234", ingest_host="ingest.yonderview.net", srt_password="s1",
                          srt_passphrase="p1", mqtt_host="mqtt.yonderview.net", mqtt_port=8883, mqtt_tls=True, mqtt_password="m1")
config.save(legacy, main)                       # a 0.1.3-style single-camera file

# 1. old file still loads as one camera
m, cams, probs = config.load_all(main)
assert [c["platform"]["camera_id"] for c in cams] == ["test1234"] and cams[0]["_legacy"] and not probs
assert cams[0]["agent"]["max_move_ms"] == 2000

# 2. editing migrates it into cameras/ and strips camagent.toml
config.save_camera(cams[0], main)
txt = main.read_text()
assert "[platform]" not in txt and "[agent]" in txt and "[update]" in txt, txt
m, cams, _ = config.load_all(main)
assert len(cams) == 1 and not cams[0]["_legacy"] and cams[0]["platform"]["srt_password"] == "s1"
assert (d / "cameras" / "test1234.toml").exists()

# 3. add a second camera
c2 = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
c2["camera"].update(host="192.168.1.11", rtsp_url="rtsp://192.168.1.11/live", ptz=False)
c2["platform"].update(camera_id="ridge_0002", ingest_host="ingest.yonderview.net", srt_password="s2",
                      mqtt_host="mqtt.yonderview.net", mqtt_password="m2")
config.save_camera(c2, main)
m, cams, _ = config.load_all(main)
assert sorted(c["platform"]["camera_id"] for c in cams) == ["ridge_0002", "test1234"]

# 4. a broken camera file doesn't take the others down
(d / "cameras" / "broken.toml").write_text("this is [not toml")
m, cams, probs = config.load_all(main)
assert len(cams) == 2 and len(probs) == 1 and "broken.toml" in probs[0]

# 5. supervisor starts every valid camera; one with missing settings is skipped, not fatal
(d / "cameras" / "half.toml").write_text('[platform]\ncamera_id = "half"\n')
started = []
with mock.patch.object(agent.CameraAgent, "start", lambda self: started.append(self.cam_id)), \
     mock.patch.object(config, "status_path", lambda: d / "status.json"):
    sup = agent.Supervisor(main)
    assert sorted(a.cam_id for a in sup.agents) == ["ridge_0002", "test1234"]
    # each camera has its own broker login
    assert {a.cam_id: a.mq._username.decode() for a in sup.agents} == {"ridge_0002": "ridge_0002", "test1234": "test1234"}
    for a in sup.agents: a.start()
    sup._write_status()
    st = json.loads((d / "status.json").read_text())
    assert set(st["cameras"]) == {"ridge_0002", "test1234"}
assert sorted(started) == ["ridge_0002", "test1234"]
(d / "cameras" / "broken.toml").unlink(); (d / "cameras" / "half.toml").unlink()

# 6. list output (with live status file)
with mock.patch.object(config, "status_path", lambda: d / "status.json"):
    configure.list_cameras(main)

# 7. remove
answers = iter(["y", "n"])
with mock.patch.object(builtins, "input", lambda *a: next(answers)), \
     mock.patch.object(configure.service, "is_installed", lambda: False), \
     mock.patch.object(configure.service, "is_admin", lambda: True):
    configure.remove(main, "ridge_0002")
m, cams, _ = config.load_all(main)
assert [c["platform"]["camera_id"] for c in cams] == ["test1234"]

# 8. srt url / per-camera uplink command unchanged
from camagent.uplink import Uplink
with mock.patch("camagent.uplink.find_ffmpeg", lambda *_: "/usr/bin/ffmpeg"):
    cmd = Uplink(cams[0]).command()
assert cmd[-1].startswith("srt://ingest.yonderview.net:8890?streamid=publish:test1234:test1234:s1"), cmd[-1]

# 9. invalid camera IDs can't escape the folder
try:
    config.camera_path("../evil", main); raise SystemExit("should have failed")
except ValueError:
    pass
print("multi-camera: OK")
