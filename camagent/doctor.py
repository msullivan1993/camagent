"""camagent doctor: check everything the agent depends on, with a plain pass/fail and what to do."""
import os
import socket
import subprocess
import sys

from . import __version__, camera, config, service

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"


class Report:
    def __init__(self):
        self.failures = 0
        self.warnings = 0

    def line(self, status, what, detail="", fix=""):
        print(f"{status} {what}" + (f": {detail}" if detail else ""))
        if fix:
            print(f"       What to do: {fix}")
        if status == FAIL:
            self.failures += 1
        elif status == WARN:
            self.warnings += 1


def _service_state() -> str:
    try:
        if os.name == "nt":
            r = subprocess.run(["sc", "query", service.SERVICE], capture_output=True, text=True)
            if r.returncode != 0:
                return "not installed"
            return "running" if "RUNNING" in r.stdout else "stopped"
        if not service.UNIT_PATH.exists():
            return "not installed"
        r = subprocess.run(["systemctl", "is-active", service.SERVICE], capture_output=True, text=True)
        return "running" if r.stdout.strip() == "active" else r.stdout.strip() or "stopped"
    except OSError:
        return "unknown"


def run(config_path=None, camera_id=None):
    r = Report()
    path = config.default_config_path() if not config_path else config_path
    admin_hint = "an administrator Command Prompt or PowerShell" if os.name == "nt" else "sudo"
    print(f"CamAgent {__version__}: checking for problems")
    print("[FAIL] stops a camera working; [WARN] is worth fixing but not urgent.\n")

    # --- software ---
    v = sys.version_info
    if v >= (3, 11):
        r.line(OK, "Python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        r.line(FAIL, "Python", f"{v.major}.{v.minor} is too old", "install Python 3.11 or newer")

    # --- config ---
    try:
        main, cams, problems = config.load_all(path)
    except PermissionError:
        r.line(FAIL, "Settings file", f"can't read {path}", f"run this from {admin_hint}")
        return _summary(r)
    except Exception as e:
        r.line(FAIL, "Settings file", f"can't read {path} ({e})", "fix the file, or set the camera up again: camagent configure")
        return _summary(r)
    for p in problems:
        r.line(FAIL, "Camera settings file", p, "fix or remove that file, or set the camera up again: camagent configure")
    if not cams:
        r.line(FAIL, "Cameras", "none set up yet", "open CamAgent and choose Add a camera")
        return _summary(r)
    if camera_id:
        cams = [c for c in cams if c["platform"]["camera_id"] == camera_id]
        if not cams:
            r.line(FAIL, "Camera", f"'{camera_id}' isn't set up on this computer", "open CamAgent and choose See my cameras")
            return _summary(r)
    else:
        r.line(OK, "Cameras", f"{len(cams)} set up")

    # --- ffmpeg (shared) ---
    ffmpeg = camera.find_ffmpeg(cams[0]["stream"].get("ffmpeg", ""))
    if ffmpeg:
        try:
            first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
        except Exception:
            first = ffmpeg
        r.line(OK, "ffmpeg", first)
        from . import prereqs
        if prereqs.srt_supported(ffmpeg) is False:
            r.line(FAIL, "ffmpeg SRT support", "this ffmpeg build can't send SRT",
                   "install a full build (Windows: winget install -e --id Gyan.FFmpeg)")
    else:
        r.line(FAIL, "ffmpeg", "not found", camera.ffmpeg_install_hint())
    ffprobe = camera.find_ffprobe(ffmpeg)

    for cfg in cams:
        _check_camera(r, cfg, ffprobe)

    # --- power ---
    from . import prereqs
    mins = prereqs.sleep_minutes()
    if mins:
        r.line(WARN, "Sleep", f"this computer sleeps after {mins} minutes on AC power",
               "Settings > System > Power > Screen and sleep > Sleep: Never (while plugged in);"
               " or type: powercfg /change standby-timeout-ac 0")

    # --- automatic updates ---
    try:
        auto = config.load_or_defaults(config_path)["update"].get("auto", True)
        sched = service.update_schedule_installed()
        if auto and sched:
            r.line(OK, "Automatic updates", "on (nightly)")
        elif auto:
            r.line(WARN, "Automatic updates", "on, but not scheduled yet", "open CamAgent and choose Start CamAgent")
        else:
            r.line(WARN, "Automatic updates", "off", "open CamAgent > Updates")
        logf = config.base_dir() / "logs" / "update.log"
        if logf.exists():
            last = logf.read_text(encoding="utf-8").strip().splitlines()[-1:]
            if last:
                r.line(OK, "Last update check", last[0])
    except Exception:  # noqa: BLE001
        pass

    # --- service ---
    print()
    state = _service_state()
    if state == "running":
        r.line(OK, "Running in the background", "yes")
    elif state == "not installed":
        r.line(WARN, "Running in the background", "not yet", "open CamAgent and choose Start CamAgent")
    else:
        r.line(FAIL, "Running in the background", state, "open CamAgent, choose Restart CamAgent, then Show the recent log")

    return _summary(r)


def _check_convert(r, cfg, cid):
    """Converting to 1080p here: is the chosen method still usable on this computer (drivers change)?"""
    from . import convert
    s = cfg["stream"]
    if s.get("convert", "off") == "off":
        return
    method = s.get("encoder") or "x264"
    ffmpeg = camera.find_ffmpeg(s.get("ffmpeg", ""))
    if not ffmpeg:
        return
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as d:
            speed = convert.try_method(ffmpeg, method, convert._test_clip(ffmpeg, d))
    except Exception:  # noqa: BLE001
        speed = None
    if speed:
        r.line(OK, "Converting to 1080p", f"{convert.LABELS[method]} works ({speed}x real time on a 4K test clip)")
    elif method == "x264":
        r.line(FAIL, "Converting to 1080p", "the CPU conversion doesn't work", "reinstall ffmpeg")
    else:
        r.line(WARN, "Converting to 1080p", f"{convert.LABELS[method]} doesn't work here any more (the service "
               "falls back to the CPU)", f"update the graphics driver, or run camagent configure {cid} to check again")


def _check_camera(r, cfg, ffprobe):
    cam, plat = cfg["camera"], cfg["platform"]
    cid = plat.get("camera_id") or "?"
    print(f"\n--- camera {cid} ---")
    from .agent import missing_settings
    missing = missing_settings(cfg)
    if missing:
        r.line(FAIL, "Settings", "missing " + ", ".join(missing), "open CamAgent > Change or remove a camera > Change a camera's settings")

    if cam.get("host") and cam.get("ptz", True):
        try:
            info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
            r.line(OK, "Camera connection", f"{info['manufacturer']} {info['model']} at {cam['host']}")
            if not any(p["ptz"] for p in info["profiles"]):
                r.line(WARN, "Camera controls", "this camera doesn't offer pan/tilt/zoom (PTZ)",
                       "if it's a fixed camera, turn PTZ off: open CamAgent > Change or remove a camera > Change a camera's settings")
        except Exception as e:
            r.line(FAIL, "Camera connection", camera.friendly_error(e),
                   "check the camera's IP address, ONVIF port and login, and that its clock is set automatically (NTP)")

    if cam.get("rtsp_url"):
        if not ffprobe:
            r.line(WARN, "Camera video", "can't check it (ffmpeg isn't installed)")
        else:
            streams = camera.probe(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
            if streams:
                r.line(OK, "Camera video", camera.describe(streams))
                video = next((x for x in streams if x.get("codec_type") == "video"), None)
                converting = cfg["stream"].get("convert", "off") != "off"
                if video and video.get("codec_name") != "h264" and not converting:
                    r.line(FAIL, "Video codec", f"{video.get('codec_name')}: most viewers' browsers can't play it",
                           "set this stream to H.264 in the camera's web page (and turn off 'smart codec'/H.264+), "
                           f"or let camagent convert it (camagent configure {cid})")
                elif video and (video.get("height") or 0) > 1080 and not converting:
                    r.line(WARN, "Resolution", f"{video.get('height')}p is sent as it is",
                           f"fine if YonderView approved it for this camera; otherwise let camagent convert it to "
                           f"1080p (camagent configure {cid})")
            else:
                r.line(FAIL, "Camera video", f"can't read {config.strip_auth(cam['rtsp_url'])}",
                       f"check the RTSP URL and camera login (camagent configure {cid})")

    if cfg["stream"].get("audio") and cam.get("rtsp_url") and ffprobe:
        url = config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"])
        if camera.audio_arrives(ffprobe, url) is False:
            r.line(WARN, "Audio", "audio is on, but the camera sends no sound (the stream goes out video only)",
                   f"turn the camera's audio on in its web page, or turn audio off: camagent configure {cid}")

    _check_convert(r, cfg, cid)

    host = plat.get("ingest_host")
    if host:
        try:
            ip = socket.gethostbyname(host)
            r.line(OK, "YonderView video server", f"{host} resolves to {ip}")
        except OSError:
            r.line(FAIL, "YonderView video server", f"can't find {host}", "check this computer's internet connection")

    if plat.get("mqtt_host") and plat.get("mqtt_password"):
        from .configure import test_mqtt
        ok, msg = test_mqtt(cfg)
        if ok:
            r.line(OK, "YonderView camera controls", f"{plat['mqtt_host']}: {msg}")
            declined = (cfg.get("_server") or {}).get("declined")
            if declined:
                r.line(FAIL, "YonderView", f"video declined: {declined}",
                       "change the camera's stream, then use 'Check again now' on the camera's page")
        else:
            r.line(FAIL, "YonderView camera controls", msg,
                   "generate a new settings block on the camera's Connection page on yonderview.net, then open CamAgent > Change or remove a camera > Change a camera's settings")

    if cam.get("rtsp_url") and ffprobe:
        m = camera.measure_stream(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
        if m.get("actual_fps"):
            r.line(OK, "Video measured", camera.stream_summary(m))
        limit = (cfg.get("_server") or {}).get("max_height")
        for tip in camera.stream_advice(m, limit):
            r.line(FAIL if "would be declined" in tip or "needs one" in tip else WARN, "Stream", tip)


def _summary(r: Report):
    print()
    if r.failures:
        print(f"Found {r.failures} problem(s)" + (f" and {r.warnings} warning(s)" if r.warnings else "")
              + ", marked above. Fix them from the top down, then check again.")
        print('Stuck? Use "Report a problem" on yonderview.net and paste this list.')
    elif r.warnings:
        print(f"No problems; {r.warnings} warning(s), marked [WARN] above.")
    else:
        print("No problems found. Everything looks good.")
    return 1 if r.failures else 0
