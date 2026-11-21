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
assert "h264_rkmpp" in post and "CBR" in post and "-bf" in post
# 0.25.3: no -maxrate/-bufsize/-force_key_frames (with live timestamps they made ~150 kbps); a fixed 50-frame GOP
assert post[post.index("-g") + 1] == "50" and "-maxrate" not in post and "-force_key_frames" not in post
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
assert "-maxrate" not in cmd and "-bufsize" not in cmd                # 0.25.3: the cap made ~20 kbps copies
with mock.patch.object(V, "_mark_filters", return_value=["drawtext=x"]):
    cmd = V.args("ffmpeg", "rkmpp", "rtsp://cam", outs, mark_files=("a", "b"),
                 mark={"on": ["480c"], "every": 15, "line1": "x", "line2": "y"})
graph = cmd[cmd.index("-filter_complex") + 1]
s480 = graph.split(";")[2]
assert "hwdownload,format=nv12" in s480 and s480.index("hwdownload") < s480.index("drawtext") \
    and s480.endswith("format=nv12[o1]"), s480
assert "hwdownload" not in graph.split(";")[1]                        # the unmarked 720p stays in hardware

# 4. ffmpeg-rockchip in /opt: found first; it takes over from a regular ffmpeg saved at setup (0.25.1), not from
#    one the host put elsewhere; a build that can't send SRT is never used
def find(configured, srt=True):
    exists = {prereqs.ROCKCHIP_FFMPEG, "/usr/bin/ffmpeg", "/home/me/ffmpeg"}
    with mock.patch.object(prereqs, "WINDOWS", False), \
            mock.patch.object(prereqs, "is_file", side_effect=lambda p: str(p) in exists), \
            mock.patch.object(prereqs, "which", return_value="/usr/bin/ffmpeg"), \
            mock.patch.object(prereqs, "rockchip_ffmpeg_ok", return_value=srt), \
            mock.patch.object(prereqs, "admin_only_location", return_value=True):
        return prereqs.find_ffmpeg(configured)
assert find("") == prereqs.ROCKCHIP_FFMPEG
assert find("/usr/bin/ffmpeg") == prereqs.ROCKCHIP_FFMPEG                # saved before the build: replaced
assert find("/home/me/ffmpeg") == "/home/me/ffmpeg"                      # the host's own choice stays
assert find("/usr/bin/ffmpeg", srt=False) == "/usr/bin/ffmpeg"           # no SRT: never the Rockchip build
assert find("", srt=False) == "/usr/bin/ffmpeg"
prereqs._rockchip_checked.clear()
with mock.patch("subprocess.run", return_value=mock.Mock(stdout="Supported file protocols:\nInput:\n  rtsp\nOutput:\n  srt\n")):
    assert prereqs.rockchip_ffmpeg_ok("/x/ffmpeg")
with mock.patch("subprocess.run", return_value=mock.Mock(stdout="Output:\n  rtmp\n")):
    assert not prereqs.rockchip_ffmpeg_ok("/y/ffmpeg")

print("OK")

# 5. (0.25.2) the mark names a font file on Linux (ffmpeg builds without fontconfig can't find "DejaVu Sans")
from camagent import uplink  # noqa: E402
with mock.patch("os.name", "posix"), mock.patch("os.path.isfile", side_effect=lambda p: p == V.LINUX_FONTS[0]):
    assert V._font() == f"fontfile='{V.LINUX_FONTS[0]}'"
with mock.patch("os.name", "posix"), mock.patch("os.path.isfile", return_value=False):
    assert V._font() == "font='DejaVu Sans'"

# 6. (0.25.2) a camera set up with x264 uses the Rockchip hardware once ffmpeg-rockchip is its ffmpeg; failing, it
#    steps down to encode-only, then the CPU
u = uplink.Uplink.__new__(uplink.Uplink)
u.cfg = {"stream": {"convert": "1080p", "encoder": "x264", "ffmpeg": prereqs.ROCKCHIP_FFMPEG, "source_height": 2160}}
u.stats, u._fell_to, u._fast_fails, u.log = {}, None, 0, mock.Mock()
with mock.patch.object(convert, "rockchip", return_value=True), mock.patch.object(u, "_already_fits", return_value=False):
    assert u.convert_method() == "rkmpp"
    u._fast_fails = u.FAST_FAILS_TO_FALL_BACK
    assert u.convert_method() == "rkmpp-lite"
with mock.patch.object(convert, "rockchip", return_value=False), mock.patch.object(u, "_already_fits", return_value=False):
    u._fell_to = None; u._fast_fails = 0
    assert u.convert_method() == "x264"                                   # not a Rockchip board: as saved
print("0.25.2 OK")

# 7. (0.25.3) Rockchip: the camera's own timestamps, not the arrival clock (that made ~100-300 kbps at random)
cmd = V.args("ffmpeg", "rkmpp", "rtsp://cam", outs, wallclock=True)
assert "-use_wallclock_as_timestamps" not in cmd
cmd = V.args("ffmpeg", "qsv", "rtsp://cam", outs, wallclock=True)
assert "-use_wallclock_as_timestamps" in cmd                              # everything else unchanged
print("0.25.3 OK")
