"""Run with: python tests/test_v11.py  (from the repo folder).
Converting a bigger or H.265 camera to 1080p H.264 before sending."""
import shutil, sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, convert
from camagent.uplink import Uplink


def cfg(**stream):
    c = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS + config.MAIN_SECTIONS}
    c["camera"].update(rtsp_url="rtsp://10.0.0.5/main", username="u", password="p")
    c["platform"].update(camera_id="cv_0001", ingest_host="ingest.example", srt_password="PW")
    c["stream"].update(ffmpeg="/usr/bin/ffmpeg", **stream)
    return c


# 1. Every method: H.264, no B-frames (low-delay viewing), a keyframe every 2 s, the bitrate asked for
for m in convert.LABELS:
    before, after = convert.args(m, 4000)
    a = " ".join(after)
    assert "-bf 0" in a and "expr:gte(t,n_forced*2)" in a and "-b:v 4000k" in a and "-maxrate 5000k" in a, m
    assert ("h264" in a or "libx264" in a), m
    assert "1080" in a, m                                         # shrinks to 1080p
for m in ("qsv", "vaapi", "nvenc"):                               # full GPU: frames stay on the GPU
    assert "-hwaccel" in convert.args(m)[0]
assert "-hwaccel" not in convert.args("qsv-lite")[0]              # GPU encoding only: CPU decodes and shrinks
assert "1080" not in " ".join(convert.args("qsv", scale=False)[1])  # already 1080p (an H.265 camera): no resize
assert convert.candidates()[-1] == "x264"                         # the CPU is always the last resort
assert convert.best([("qsv", None), ("qsv-lite", 2.5), ("x264", 1.1)]) == "qsv-lite"
assert convert.best([("qsv", None)]) == "x264"

# 2. The uplink: as it is (copy) unless converting; GPU options go before the camera input
with mock.patch("camagent.uplink.find_ffmpeg", lambda p: "/usr/bin/ffmpeg"):
    plain = Uplink(cfg()).command()
    assert "copy" in plain and "-hwaccel" not in plain
    up = Uplink(cfg(convert="1080p", encoder="qsv", source_height=2160, bitrate_kbps=4500))
    cmd = up.command()
    assert cmd.index("-hwaccel") < cmd.index("-i") and "h264_qsv" in cmd and "copy" not in cmd[cmd.index("-map"):cmd.index("-map") + 4]
    assert up.stats["convert"] == {"height": 1080, "encoder": "qsv", "fallback": False, "source_height": 2160}
    small = Uplink(cfg(convert="1080p", encoder="qsv", source_height=1080)).command()   # H.265 1080p: no resize
    assert not any("scale_qsv" in x for x in small)

    # 3. A GPU that keeps failing right away falls back to the CPU; network trouble doesn't count
    up._stderr.extend(["[out#0/srt] Connection refused"])
    assert not up._encoder_trouble()
    up._stderr.clear(); up._stderr.extend(["[h264_qsv @ 0x1] Error initializing an internal MFX session: unsupported (-3)"])
    assert up._encoder_trouble()
    up._fast_fails = Uplink.FAST_FAILS_TO_FALL_BACK
    cmd = up.command()
    assert "libx264" in cmd and up.stats["convert"]["fallback"] is True

# 4. Setup: a 4K camera -> convert here; the best working method is kept; capacity is judged from the hardware test
import builtins
from camagent import configure
assert convert.headroom(4.0, 3840, 2160, 15) == 4.0 and convert.headroom(4.0, 1920, 1080, 30) == 8.0
assert convert.headroom(None) == 0.0


def run_step(measure, detect_results, live_speeds, answers, codec="h264"):
    c = cfg()
    c["_measure"], c["_codec"] = measure, codec
    speeds, ans = iter(live_speeds), iter(answers)
    with mock.patch.object(builtins, "input", lambda *a: next(ans)), \
         mock.patch.object(convert, "detect", lambda ff, show=None: detect_results), \
         mock.patch.object(convert, "speed_on_camera", lambda *a, **k: next(speeds)), \
         mock.patch.object(builtins, "print", lambda *a, **k: None):
        configure.step_convert(c, "/usr/bin/ffmpeg")
    return c["stream"]


s4k = {"width": 3840, "height": 2160, "actual_fps": 15}
st = run_step(s4k, [("vaapi", None), ("vaapi-lite", 3.0), ("x264", 0.8)], [1.0], ["1"])
assert (st["convert"], st["encoder"], st["source_height"]) == ("1080p", "vaapi-lite", 2160)
st = run_step({"width": 1920, "height": 1080, "actual_fps": 20}, [], [], [])            # 1080p H.264: nothing to do
assert st["convert"] == "off"
st = run_step(s4k, [("x264", 1.1)], [1.0], ["1", "n"])                                  # weak CPU: asked, declined
assert st["convert"] == "off"
st = run_step(s4k, [("qsv", 4.0), ("x264", 2.0)], [None, 1.0], ["1"])                   # GPU fails on the camera: CPU
assert (st["convert"], st["encoder"]) == ("1080p", "x264")
st = run_step(s4k, [], [], ["3"])                                                       # send the 4K as it is
assert st["convert"] == "off"
st = run_step({"width": 1920, "height": 1080, "actual_fps": 15}, [("qsv", 6.0)], [1.0], ["1"], codec="hevc")
assert (st["convert"], st["encoder"]) == ("1080p", "qsv")                               # H.265 1080p -> H.264

# 5. The real thing, on this machine's ffmpeg (CPU only in CI): detection doesn't trip over missing GPUs,
#    and x264 makes 1080p H.264 with no B-frames and 2 s keyframes from a 4K source
ff = shutil.which("ffmpeg")
if ff:
    import json, subprocess, tempfile
    results = convert.detect(ff)
    assert dict(results)["x264"], results                         # the CPU always works
    with tempfile.TemporaryDirectory() as d:
        src, out = f"{d}/src.mp4", f"{d}/out.ts"
        subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=15", "-t", "5",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-pix_fmt", "yuv420p", src], check=True)
        before, after = convert.args("x264", 4500)
        subprocess.run([ff, "-loglevel", "error", *before, "-i", src, "-map", "0:v:0", *after, "-f", "mpegts", out],
                       check=True)
        p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=codec_name,width,height,has_b_frames", "-show_entries",
                                       "packet=pts_time,flags", "-of", "json", out],
                                      capture_output=True, text=True).stdout)
        st = p["streams"][0]
        assert (st["codec_name"], st["width"], st["height"], st.get("has_b_frames", 0)) == ("h264", 1920, 1080, 0), st
        keys = [float(k["pts_time"]) for k in p["packets"] if "K" in k["flags"]]
        gaps = [round(b - a, 2) for a, b in zip(keys, keys[1:])]
        assert keys and all(abs(g - 2) < 0.15 for g in gaps), gaps
print("v0.11 convert OK")
