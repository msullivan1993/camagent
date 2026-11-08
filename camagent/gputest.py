"""camagent gpu-test (0.17.14): how this PC copes with a camera's whole job, done different ways, so we can pick
the lightest one that keeps up. Each way reads the camera once and makes 1080p, 720p and 480p for 15 seconds
(nothing is sent anywhere); ffmpeg reports its speed (1.0x or more keeps up) and the processor time it used.

Uses the camera's saved settings (its login never has to be typed). Runs beside CamAgent: the numbers are this
test's own, but stop the service first if the PC is already struggling."""
import os
import re
import subprocess
import time

from . import config
from .camera import find_ffmpeg

SECONDS = 15
OUTPUTS = ((1080, "4500k"), (720, "2000k"), (480, "900k"))


def ways(method):
    """[(name, options before -i, filter chain per output -> encoder options)] worth comparing for this encoder."""
    if method not in ("amf", "amf-lite"):
        return []
    enc = ["-c:v", "h264_amf", "-quality", "speed"]
    return [
        ("as CamAgent does it today (frames copied to the processor and resized there)",
         ["-hwaccel", "d3d11va"], lambda h: f"scale=-2:{h},format=nv12", enc),
        ("all on the graphics chip, AMD's resizer (vpp_amf)",
         ["-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11"], lambda h: f"vpp_amf=w=-2:h={h}", enc),
        ("all on the graphics chip, the Windows resizer (scale_d3d11)",
         ["-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11"], lambda h: f"scale_d3d11=w=-2:h={h}", enc),
    ]


def outputs_for(source_height):
    """The versions CamAgent really makes for this camera: 720p and 480p, plus 1080p only when it sends more."""
    return tuple(o for o in OUTPUTS if o[0] <= 720 or (source_height and source_height > o[0]))


def _camera_height(ffmpeg, url):
    """The camera's picture height right now (its setting may have changed since setup), or 0."""
    import json
    from .camera import find_ffprobe
    ffprobe = find_ffprobe(ffmpeg)
    if not ffprobe:
        return 0
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
                            "-show_entries", "stream=height", "-of", "json", url],
                           capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
        return int(((json.loads(r.stdout or "{}").get("streams") or [{}])[0]).get("height") or 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def _error_line(text):
    """The line that says what went wrong (not ffmpeg's timing line or its generic last words)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and "frame=" not in ln
             and not ln.strip().startswith("bench:")]
    useful = [ln for ln in lines if ln != "Conversion failed!" and re.search(
        r"error|fail|not supported|invalid|unable|cannot|could not|no such|terminat|not found|denied", ln, re.I)]
    return (useful or lines or ["no output"])[-1]


def _save_output(name, text, n):
    """Keep ffmpeg's whole answer for a way that failed, so support can see exactly why. The camera's address is
    removed. Returns the file's path, or ""."""
    try:
        folder = config.base_dir() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"gpu-test-{n}.txt"
        path.write_text(f"{name}\n\n" + config.scrub(text)[-20000:], encoding="utf-8")
        return str(path)
    except OSError:
        return ""


def command(ffmpeg, url, pre, chain, enc, outputs=OUTPUTS):
    graph = [f"[0:v]split={len(outputs)}" + "".join(f"[s{i}]" for i in range(len(outputs)))]
    graph += [f"[s{i}]{chain(h)}[o{i}]" for i, (h, _) in enumerate(outputs)]
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-benchmark", "-stats",
           "-rtsp_transport", "tcp", *pre, "-t", str(SECONDS), "-i", url, "-filter_complex", ";".join(graph)]
    for i, (_, rate) in enumerate(outputs):
        cmd += ["-map", f"[o{i}]", *enc, "-b:v", rate, "-f", "null", "-"]
    return cmd


def parse(text):
    """(speed, processor seconds, wall seconds) from ffmpeg's last stats line and its -benchmark line."""
    speeds = re.findall(r"speed=\s*([\d.]+)x", text)
    bench = re.search(r"bench: utime=([\d.]+)s stime=([\d.]+)s rtime=([\d.]+)s", text)
    speed = float(speeds[-1]) if speeds else None
    if not bench:
        return speed, None, None
    return speed, float(bench.group(1)) + float(bench.group(2)), float(bench.group(3))


def run(config_path=None, camera_id=None):
    _, cams, _ = config.load_all(config_path or config.default_config_path())
    cams = [c for c in cams if not camera_id or c["platform"].get("camera_id") == camera_id]
    if not cams:
        print("That camera isn't set up on this computer." if camera_id else
              "You haven't set up any cameras on this computer.")
        return 1
    cfg = cams[0]
    cam, s = cfg["camera"], cfg["stream"]
    ffmpeg = find_ffmpeg(s.get("ffmpeg", ""))
    if not ffmpeg:
        print("ffmpeg isn't installed on this computer.")
        return 1
    method = s.get("encoder") or ""
    tests = ways(method)
    if not tests:
        print(f"Nothing to compare for your camera's encoder ({method or 'none set'}). This test is for AMD graphics.")
        return 0
    url = config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"])
    cores = os.cpu_count() or 1
    height = _camera_height(ffmpeg, url)
    outputs = outputs_for(height)
    sizes = ", ".join(f"{h}p" for h, _ in outputs)
    print("This test checks how well this computer makes the smaller copies of your camera's video (720p and 480p).")
    print("It takes about a minute, and this computer works hard while it runs. Nothing is sent anywhere.")
    print(f"Camera {cfg['platform'].get('camera_id')} ({height or '?'}p): CamAgent makes {sizes} for {SECONDS} seconds, "
          f"in {len(tests)} ways ({cores} processor threads).\n"
          "Optional: watch the GPU (graphics chip) in Task Manager while it runs.\n")
    results = []
    for n, (name, pre, chain, enc) in enumerate(tests, 1):
        print(f"- {name}: ", end="", flush=True)
        t0 = time.monotonic()
        try:
            r = subprocess.run(command(ffmpeg, url, pre, chain, enc, outputs), capture_output=True, text=True,
                               timeout=SECONDS + 60, stdin=subprocess.DEVNULL)
            text = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            print("didn't finish, so CamAgent stopped it")
            continue
        speed, cpu_s, wall = parse(text)
        if r.returncode or speed is None:
            saved = _save_output(name, text, n)
            print(f"doesn't work here (ffmpeg stopped with code {r.returncode}): "
                  + config.scrub(_error_line(text))[:160])
            if saved:
                print(f"    ffmpeg's full answer is in {saved}")
            continue
        share = round(100 * cpu_s / (wall * cores)) if cpu_s and wall else None
        results.append((name, speed, share))
        print(f"speed {speed:.2f}x, processor {share if share is not None else '?'}% of this computer "
              f"({time.monotonic() - t0:.0f} s)")
    print()
    good = [r for r in results if r[1] >= 0.97]
    if good:
        best = min(good, key=lambda r: r[2] if r[2] is not None else 999)
        print(f"Lightest way that keeps up: {best[0]} ({best[2]}% processor). Send these lines to YonderView support.")
    else:
        print("None of the ways kept up (speed under 1.0x). Send these lines to YonderView support.")
    return 0
