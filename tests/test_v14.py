"""Run with: python tests/test_v14.py  (from the repo folder).
Raspberry Pi and Rockchip video engines for converting and for host-made versions (0.14)."""
import json, sys, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import convert, versions as V

# 1. On ARM boards the Pi's engine is tried first, then Rockchip, then the CPU; PCs are unchanged
with mock.patch("os.name", "posix"), mock.patch("platform.machine", return_value="aarch64"):
    assert convert.candidates() == ["v4l2m2m", "v4l2m2m-lite", "rkmpp", "x264"]
with mock.patch("os.name", "posix"), mock.patch("platform.machine", return_value="x86_64"):
    assert convert.candidates()[0] == "vaapi" and "v4l2m2m" not in convert.candidates()

# 2. Converting: the Pi engine decodes too ("-lite": the CPU decodes, e.g. 4K the Pi can't); yuv420p frames
pre, post = convert.args("v4l2m2m")
assert pre == ["-c:v", "h264_v4l2m2m"] and "h264_v4l2m2m" in post and "format=yuv420p" in ",".join(post)
pre, post = convert.args("v4l2m2m-lite")
assert pre == [] and "h264_v4l2m2m" in post
assert "-force_key_frames" in post and "-bf" in post                 # keyframe every 2 s, no B-frames, as always
pre, post = convert.args("rkmpp")
assert pre == [] and "h264_rkmpp" in post and "CBR" in post
for m in ("v4l2m2m", "v4l2m2m-lite", "rkmpp"):
    assert m in convert.LABELS

# 3. Host-made versions can use them (the GPU check offers them on ARM)
assert {"v4l2m2m", "v4l2m2m-lite", "rkmpp"} <= set(V.GPU_METHODS)
outs = [{"suffix": "720", "height": 720, "fps": 0, "kbps": 3000, "url": "srt://x/720"},
        {"suffix": "480", "height": 480, "fps": 30, "kbps": 1200, "url": "srt://x/480"}]
cmd = V.args("ffmpeg", "v4l2m2m", "rtsp://cam", outs)
i = cmd.index("-i")
assert cmd[i - 2:i] == ["-c:v", "h264_v4l2m2m"]                       # decoder chosen before the input
assert cmd.count("h264_v4l2m2m") == 3 and "format=yuv420p" in cmd[cmd.index("-filter_complex") + 1]
cmd = V.args("ffmpeg", "v4l2m2m-lite", "rtsp://cam", outs)
assert cmd.count("h264_v4l2m2m") == 2
cmd = V.args("ffmpeg", "rkmpp", "rtsp://cam", outs)
assert cmd.count("h264_rkmpp") == 2 and "format=nv12" in cmd[cmd.index("-filter_complex") + 1]
# 4. The versions check uses a 1080p clip (a Pi's decoder stops at 1080p); converting keeps its 4K one
import tempfile
seen = []
with mock.patch("subprocess.run", side_effect=lambda cmd, **k: seen.append(cmd) or mock.Mock(returncode=0)):
    with tempfile.TemporaryDirectory() as d:
        convert._test_clip("ffmpeg", d, "1920x1080")
        convert._test_clip("ffmpeg", d)
assert "testsrc2=size=1920x1080:rate=15" in seen[0] and "testsrc2=size=3840x2160:rate=15" in seen[1]
import inspect
assert 'size="1920x1080"' in inspect.getsource(V.Versions._find_method)

# 5. GPU decoding keeps failing on a real camera: fall back to decoding on the CPU before giving up
from camagent import config
c = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
c["platform"].update(camera_id="pi_0001")
c["stream"].update(encoder="v4l2m2m")
assert V.Versions(c).method is None                     # a Pi's engine doesn't make versions unless asked (0.14.10)
c["stream"].update(pi_engine_versions=True)
v = V.Versions(c)
assert v.method == "v4l2m2m"
v.wanted = json.dumps([[], None]); v.keep_until = time.time() + 600
v.fast_fails = V.MAX_FAST_FAILS - 1
v.started = time.time()
v.proc = mock.Mock(poll=mock.Mock(return_value=1))
with mock.patch.object(v, "_start"):
    v._stop.wait = mock.Mock(side_effect=[False, True])        # one pass of the watch loop
    v._watch()
assert v.method == "v4l2m2m-lite" and not v.gave_up
v.stop()
print("test_v14: all good")
