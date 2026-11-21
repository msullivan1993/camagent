"""0.25: Rockchip boards (RK3576/RK3588...) use their video hardware end to end with ffmpeg-rockchip: the engine
decodes (H.264 or H.265), the RGA resizes and the engine encodes, for converting to 1080p and for host-made versions;
"-lite" keeps the old encode-only way; CamAgent finds ffmpeg-rockchip in /opt; Rockchip boards try it first."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import convert, prereqs, versions as V  # noqa: E402

# 1. On a Rockchip board, Rockchip first (no Raspberry Pi engine to try); other ARM boards keep the Pi first
with mock.patch("os.name", "posix"), mock.patch("platform.machine", return_value="aarch64"), \
        mock.patch.object(convert, "rockchip", return_value=True):
    assert convert.candidates() == ["rkmpp", "rkmpp-lite", "x264"]
with mock.patch("os.name", "posix"), mock.patch("platform.machine", return_value="aarch64"), \
        mock.patch.object(convert, "rockchip", return_value=False):
    assert convert.candidates() == ["v4l2m2m", "v4l2m2m-lite", "rkmpp", "rkmpp-lite", "x264"]
assert "rkmpp" in convert.LABELS and "rkmpp-lite" in convert.LABELS

# 2. Converting to 1080p: decode in hardware into the RGA, keyframe every 2 s, no B-frames, CBR
pre, post = convert.args("rkmpp")
assert pre == ["-hwaccel", "rkmpp", "-hwaccel_output_format", "drm_prime"]
vf = post[post.index("-vf") + 1]
assert vf == "scale_rkrga=w=-2:h='min(1080,ih)':format=nv12"
assert "h264_rkmpp" in post and "CBR" in post and "-force_key_frames" in post and "-bf" in post
pre, post = convert.args("rkmpp", scale=False)                        # an H.265 1080p camera: re-encode only
assert "-vf" not in post and "h264_rkmpp" in post

# 3. Host-made versions: frames stay in hardware; a marked version comes down for the mark, then goes on
outs = [{"suffix": "720", "height": 720, "fps": 0, "kbps": 2500, "url": "srt://x/720"},
        {"suffix": "480c", "height": 480, "fps": 15, "kbps": 1000, "url": "srt://x/480c"}]
assert {"rkmpp", "rkmpp-lite"} <= set(V.GPU_METHODS)
cmd = V.args("ffmpeg", "rkmpp", "rtsp://cam", outs)
i = cmd.index("-i")
assert cmd[i - 4:i] == ["-hwaccel", "rkmpp", "-hwaccel_output_format", "drm_prime"]
graph = cmd[cmd.index("-filter_complex") + 1]
assert "scale_rkrga=w=-2:h='min(720,ih)':format=nv12[o0]" in graph and "hwdownload" not in graph
assert "fps=15" in graph and cmd.count("h264_rkmpp") == 2
with mock.patch.object(V, "_mark_filters", return_value=["drawtext=x"]):
    cmd = V.args("ffmpeg", "rkmpp", "rtsp://cam", outs, mark_files=("a", "b"),
                 mark={"on": ["480c"], "every": 15, "line1": "x", "line2": "y"})
graph = cmd[cmd.index("-filter_complex") + 1]
s480 = graph.split(";")[2]
assert "hwdownload,format=nv12" in s480 and s480.index("hwdownload") < s480.index("drawtext") \
    and s480.endswith("format=nv12[o1]"), s480
assert "hwdownload" not in graph.split(";")[1]                        # the unmarked 720p stays in hardware

# 4. ffmpeg-rockchip in /opt is found first (after a configured one) on Linux
with mock.patch.object(prereqs, "WINDOWS", False), \
        mock.patch.object(prereqs, "is_file", side_effect=lambda p: str(p) == prereqs.ROCKCHIP_FFMPEG), \
        mock.patch.object(prereqs, "which", return_value="/usr/bin/ffmpeg"), \
        mock.patch.object(prereqs, "admin_only_location", return_value=True):
    assert prereqs.find_ffmpeg() == prereqs.ROCKCHIP_FFMPEG

print("OK")
