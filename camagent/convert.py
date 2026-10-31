"""Converting a bigger camera (4K, 1440p...) or an H.265 camera to 1080p H.264 on this computer, before sending.

YonderView takes 1080p (above that is an approved, paid option), so a host can keep the camera's full resolution
for their own uses and still send 1080p. It's still the camera's one main stream (no substreams): camagent decodes
it, shrinks it and encodes it again. A computer's video hardware does that with little effort (Intel Quick Sync on
most mini PCs, NVIDIA, AMD; the video engine on a Raspberry Pi 3/4 or a Rockchip board); the CPU (x264) is the
fallback.

Methods, tried in order by detect() on a short 4K test clip, so a GPU that's present but unusable (drivers, BIOS)
is simply skipped. "-lite" variants decode and shrink on the CPU and only encode on the GPU, for setups where the
full GPU path doesn't work. Output for every method: H.264, no B-frames (low-delay viewing), a keyframe every 2 s,
constant-ish bitrate."""
import os
import platform
import subprocess
import tempfile
import time

TARGET_HEIGHT = 1080
DEFAULT_KBPS = 4500

LABELS = {
    "qsv": "Intel Quick Sync (GPU)", "qsv-lite": "Intel Quick Sync (GPU encoding)",
    "vaapi": "VA-API (Intel/AMD GPU)", "vaapi-lite": "VA-API (GPU encoding)",
    "nvenc": "NVIDIA (GPU)", "nvenc-lite": "NVIDIA (GPU encoding)",
    "amf": "AMD (GPU)", "amf-lite": "AMD (GPU encoding)",
    "v4l2m2m": "Raspberry Pi video engine", "v4l2m2m-lite": "Raspberry Pi video engine (encoding)",
    "rkmpp": "Rockchip video engine (encoding)",
    "x264": "the CPU (x264)",
}
VAAPI_DEVICE = "/dev/dri/renderD128"


ARM = ("aarch64", "arm64", "armv7l", "armv8l")


def raspberry_pi():
    """The Raspberry Pi model ("Raspberry Pi 3 Model B Rev 1.2"), or "" on anything else."""
    try:
        with open("/proc/device-tree/model", encoding="utf-8", errors="replace") as f:
            model = f.read().strip("\x00 \n")
    except OSError:
        return ""
    return model if model.startswith("Raspberry Pi") else ""


def candidates():
    """Methods to try on this platform, best first."""
    if os.name == "nt":
        return ["qsv", "qsv-lite", "nvenc", "nvenc-lite", "amf", "x264"]
    if platform.machine().lower() in ARM:
        # Raspberry Pi 3/4 (the Pi 5 has no video encoder), then Rockchip boards with a Rockchip build of ffmpeg
        return ["v4l2m2m", "v4l2m2m-lite", "rkmpp", "x264"]
    return ["vaapi", "vaapi-lite", "qsv", "qsv-lite", "nvenc", "nvenc-lite", "x264"]


def args(method, kbps=DEFAULT_KBPS, scale=True, height=TARGET_HEIGHT):
    """(options before -i, video options after the input map) for one method. scale=False when the camera's
    picture is already 1080p or smaller (an H.265 camera only needs re-encoding)."""
    kbps = int(kbps or DEFAULT_KBPS)
    rate = ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.25)}k", "-bufsize", f"{kbps * 2}k"]
    keys = ["-force_key_frames", "expr:gte(t,n_forced*2)", "-bf", "0"]
    sw_scale = [f"scale=-2:'min({height},ih)'"] if scale else []
    if method == "qsv":
        vf = [f"scale_qsv=w=-2:h={height}"] if scale else []
        return (["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"],
                ([ "-vf", ",".join(vf)] if vf else []) + ["-c:v", "h264_qsv", "-preset", "veryfast", *rate, *keys])
    if method == "qsv-lite":
        return ([], ["-vf", ",".join(sw_scale + ["format=nv12"]), "-c:v", "h264_qsv", "-preset", "veryfast",
                     *rate, *keys])
    if method == "vaapi":
        vf = [f"scale_vaapi=w=-2:h={height}"] if scale else []
        return (["-hwaccel", "vaapi", "-hwaccel_device", VAAPI_DEVICE, "-hwaccel_output_format", "vaapi"],
                (["-vf", ",".join(vf)] if vf else []) + ["-c:v", "h264_vaapi", *rate, *keys])
    if method == "vaapi-lite":
        return (["-vaapi_device", VAAPI_DEVICE],
                ["-vf", ",".join(sw_scale + ["format=nv12", "hwupload"]), "-c:v", "h264_vaapi", *rate, *keys])
    if method == "nvenc":
        vf = [f"scale_cuda=-2:{height}"] if scale else []
        return (["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
                (["-vf", ",".join(vf)] if vf else []) +
                ["-c:v", "h264_nvenc", "-preset", "p3", "-tune", "ll", "-rc", "cbr", *rate, *keys])
    if method == "nvenc-lite":
        return ([], (["-vf", ",".join(sw_scale)] if sw_scale else []) +
                ["-c:v", "h264_nvenc", "-preset", "p3", "-tune", "ll", "-rc", "cbr", *rate, *keys])
    if method == "amf":
        return ([], (["-vf", ",".join(sw_scale)] if sw_scale else []) +
                ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cbr", *rate, *keys])
    if method in ("v4l2m2m", "v4l2m2m-lite"):
        # the Pi's H.264 engine (up to 1080p): decode on it too, or on the CPU ("-lite", for anything it can't
        # decode, e.g. 4K); it takes yuv420p frames in system memory
        return (["-c:v", "h264_v4l2m2m"] if method == "v4l2m2m" else [],
                ["-vf", ",".join(sw_scale + ["format=yuv420p"]), "-c:v", "h264_v4l2m2m", *rate, *keys])
    if method == "rkmpp":
        # needs ffmpeg built with Rockchip MPP (e.g. the ffmpeg-rockchip packages); decoding stays on the CPU
        return ([], ["-vf", ",".join(sw_scale + ["format=nv12"]), "-c:v", "h264_rkmpp", "-rc_mode", "CBR",
                     *rate, *keys])
    # x264: the CPU
    return ([], ["-vf", ",".join(sw_scale + ["format=yuv420p"]), "-c:v", "libx264", "-preset", "veryfast",
                 "-tune", "zerolatency", "-profile:v", "high", *rate, *keys])


def _test_clip(ffmpeg, folder, size="3840x2160"):
    """A 2-second H.264 clip (made on the CPU) to try each method on: 4K for converting a bigger camera."""
    clip = os.path.join(folder, "yv-convert-test.mp4")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"testsrc2=size={size}:rate=15", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", clip], check=True, capture_output=True, timeout=120)
    return clip


LAST_ERRORS = {}          # method -> ffmpeg's last error line from the most recent try_method (for the log)


def try_method(ffmpeg, method, clip, seconds=2.0, timeout=60):
    """Convert the test clip with one method. Returns its speed (x real time), or None if it doesn't work here."""
    before, after = args(method)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *before, "-i", clip, "-map", "0:v:0", *after,
           "-f", "null", "-"]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        LAST_ERRORS[method] = str(e)
        return None
    elapsed = max(0.05, time.monotonic() - t0)
    if r.returncode != 0:
        lines = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
        LAST_ERRORS[method] = lines[-1] if lines else f"ffmpeg exited with {r.returncode}"
        return None
    return round(seconds / elapsed, 1)


def detect(ffmpeg, methods=None, show=None, size="3840x2160", timeout=60):
    """[(method, speed or None)] for every candidate on this computer, best first. size: the test clip's (1080p for
    host-made versions, so a Raspberry Pi's decoder, which stops at 1080p, can pass)."""
    out = []
    with tempfile.TemporaryDirectory() as d:
        clip = _test_clip(ffmpeg, d, size)
        for m in methods or candidates():
            speed = try_method(ffmpeg, m, clip, timeout=timeout)
            out.append((m, speed))
            if show:
                clip_name = "4K" if size == "3840x2160" else ("1080p" if size == "1920x1080" else size)
                show(f"  {LABELS[m]}: " + (f"works ({speed}x real time on a {clip_name} test clip)" if speed
                                           else "doesn't work on this computer"))
    return out


def best(results):
    return next((m for m, speed in results if speed), "x264")


def headroom(test_speed, width=3840, height=2160, fps=15):
    """How many times over this computer can convert this camera, from its speed on the 4K/15 fps test clip,
    scaled by the camera's pixels per second. Real footage (trees, rain, noise) is harder than the test pattern,
    so treat 1.5 or more as comfortable."""
    if not test_speed:
        return 0.0
    load = (max(1, width) * max(1, height) * max(1, fps or 15)) / (3840 * 2160 * 15)
    return round(test_speed / load, 1)


def speed_on_camera(ffmpeg, rtsp_url, method, kbps=DEFAULT_KBPS, scale=True, seconds=8):
    """Convert the real camera for a few seconds (to nowhere). Returns the speed ffmpeg reports, or None if it
    failed. A live camera only sends in real time, so a computer that keeps up reads about 1.0; below ~0.95 it's
    falling behind. (For how much spare capacity there is, see headroom().)"""
    before, after = args(method, kbps, scale)
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1",
           "-rtsp_transport", "tcp", *before, "-i", rtsp_url, "-t", str(seconds), "-map", "0:v:0", *after,
           "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds * 6 + 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    speeds = [ln.split("=", 1)[1].strip().rstrip("x") for ln in r.stdout.splitlines() if ln.startswith("speed=")]
    try:
        return round(float(speeds[-1]), 2) if r.returncode == 0 and speeds else None
    except ValueError:
        return None
