"""Run with: python tests/test_v13.py  (from the repo folder).
Lower-quality versions made on this PC when the website asks (0.13)."""
import json, os, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, versions as V
from camagent.versions import Versions

LADDER = [{"suffix": "1080", "height": 1080, "fps": 0, "kbps": 6000}, {"suffix": "720", "height": 720, "fps": 0, "kbps": 3000},
          {"suffix": "480", "height": 480, "fps": 30, "kbps": 1200}]
MARK = {"line1": "YonderView.net", "line2": "Not for redistribution.", "every": 15, "on": ["480"]}


def cfg(**stream):
    c = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
    c["camera"].update(host="h", rtsp_url="rtsp://10.0.0.5/main", username="u", password="p", ptz=False)
    c["platform"].update(camera_id="vr_0001", ingest_host="ingest.example", srt_password="PW", srt_passphrase="PHRASE",
                         mqtt_host="127.0.0.1", mqtt_tls=False, mqtt_port=1883, mqtt_password="m")
    c["stream"].update(**stream)
    return c


# 1. Which versions: 720p and 480p always; 1080p only when we send more than 1080p
assert [v["suffix"] for v in V.pick(LADDER, 1080)] == ["720", "480"]
assert [v["suffix"] for v in V.pick(LADDER, 2160)] == ["1080", "720", "480"]

# 2. Each version goes to its own path with the camera's login and passphrase
url = config.srt_url(cfg(), path="vr_0001_720")
assert "streamid=publish:vr_0001_720:vr_0001:PW" in url and "passphrase=PHRASE" in url
assert "streamid=publish:vr_0001:vr_0001:PW" in config.srt_url(cfg())

# 3. One input (the camera is read and decoded once), one output per version, the mark only where asked
outs = [dict(v, url=f"srt://x/{v['suffix']}") for v in V.pick(LADDER, 1080)]
cmd = V.args("ffmpeg", "qsv", "rtsp://cam", outs, ["a.txt", "b.txt"], MARK)
assert cmd.count("-i") == 1 and cmd.count("h264_qsv") == 2
graph = cmd[cmd.index("-filter_complex") + 1]
cut = graph.index("[s1]scale")
assert "drawtext" in graph[cut:] and "drawtext" not in graph[:cut]          # 480p marked, 720p not
assert "-an" in cmd and "aac" not in cmd                                     # sound off unless the camera's is on
assert "aac" in V.args("ffmpeg", "x264", "rtsp://cam", outs, audio=True)

# 4. The real thing with this machine's ffmpeg (CPU, x264): the graph is valid and makes both versions
ff = shutil.which("ffmpeg")
if ff:
    d = Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("YonderView.net"); (d / "b.txt").write_text("Not for redistribution.")
    src = d / "src.mp4"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-t", "3", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)], check=True)
    outs = [dict(v, url=str(d / f"{v['suffix']}.ts")) for v in V.pick(LADDER, 1080)]
    cmd = V.args(ff, "x264", str(src), outs, ["a.txt", "b.txt"], MARK, wallclock=False)
    cmd = [x for x in cmd if x != "tcp" and x != "-rtsp_transport"]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=d)
    assert r.returncode == 0, r.stderr[-800:]
    for suffix, height in (("720", 720), ("480", 480)):
        h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                            "-of", "csv=p=0", str(d / f"{suffix}.ts")], capture_output=True, text=True).stdout.split()[0]
        assert h == str(height), (suffix, h)

# 5. Versions: starts when asked, stops when told or when the website stops asking, gives up on repeated failures
d = Path(tempfile.mkdtemp())
runs = d / "runs.txt"
fake = d / "ffmpeg"
fake.write_text(f"#!/bin/sh\necho run >> {runs}\nwhile :; do sleep 0.1; done\n")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
dying = d / "ffmpeg-dies"
dying.write_text(f"#!/bin/sh\necho 'versions ffmpeg: Unknown encoder' >&2\nexit 1\n")
dying.chmod(dying.stat().st_mode | stat.S_IEXEC)
with mock.patch("camagent.versions.find_ffmpeg", lambda p: str(fake)), \
     mock.patch("camagent.versions.convert.detect", lambda ff, methods=None, show=None: [("qsv", 5.0)]):
    v = Versions(cfg(source_height=1080))
    for _ in range(50):
        if v.checked: break
        time.sleep(0.05)
    assert v.capability()["ok"] and v.method == "qsv"
    r = v.want(LADDER, MARK, keep_s=180)
    assert r["ok"] and v.running()
    v.want(LADDER, MARK, keep_s=180)                       # "still wanted": no restart
    time.sleep(0.3)
    assert runs.read_text().count("run") == 1
    v.stop()
    assert not v.running()
    v.want(LADDER, MARK, keep_s=180)
    v.keep_until = time.time() - 1                          # the website stopped asking
    time.sleep(2.5)
    assert not v.running() and v.wanted is None
    v.shutdown()
with mock.patch("camagent.versions.find_ffmpeg", lambda p: str(dying)), \
     mock.patch("camagent.versions.convert.detect", lambda ff, methods=None, show=None: [("qsv", 5.0)]), \
     mock.patch.object(V, "FAST_S", 60):
    v = Versions(cfg(source_height=1080))
    time.sleep(0.3)
    v.want(LADDER, MARK)
    deadline = time.time() + 15
    while time.time() < deadline and not v.gave_up:
        time.sleep(0.2)
    assert v.gave_up and not v.capability()["ok"]           # the website stops asking; the server makes them
    assert "Unknown encoder" in v.error
    v.shutdown()

# 6. No GPU method: never offered; "off" in the config: not even checked
with mock.patch("camagent.versions.find_ffmpeg", lambda p: "/usr/bin/ffmpeg"), \
     mock.patch("camagent.versions.convert.detect", lambda ff, methods=None, show=None: [("qsv", None)]):
    v = Versions(cfg()); time.sleep(0.3)
    assert not v.capability()["ok"] and not v.want(LADDER)["ok"]
    v.shutdown()
assert not Versions(cfg(versions="off")).capability()["ok"]
v = Versions(cfg(versions="cpu")); time.sleep(0.3)
assert v.method == "x264" and v.capability()["ok"]               # no GPU, allowed on purpose
v.shutdown()

# 7. The agent: the website's "versions" command, and the capability in telemetry
from camagent.agent import CameraAgent
with mock.patch("camagent.versions.convert.detect", lambda ff, methods=None, show=None: [("qsv", 5.0)]):
    a = CameraAgent(cfg(source_height=1080, ffmpeg=str(fake)))
    time.sleep(0.3)
    with mock.patch.object(a.versions, "want", return_value={"ok": True}) as want, \
         mock.patch.object(a.versions, "stop", return_value={"ok": True}) as stop:
        a._handle({"op": "versions", "on": True, "versions": LADDER, "mark": MARK, "keep_s": 180})
        a._handle({"op": "versions", "on": False})
    want.assert_called_once_with(LADDER, MARK, 180); stop.assert_called_once()
    assert a._info()["versions"]["ok"] is True
    a.versions.shutdown()
print("v0.13 versions OK")
