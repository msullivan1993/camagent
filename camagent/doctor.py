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
            print(f"       fix: {fix}")
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
    print(f"camagent {__version__} health check\n")

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
        r.line(FAIL, "Config", f"can't read {path}", f"run this from {admin_hint}")
        return _summary(r)
    except Exception as e:
        r.line(FAIL, "Config", f"can't parse {path} ({e})", "fix the file, or run: camagent configure")
        return _summary(r)
    for p in problems:
        r.line(FAIL, "Camera file", p, "fix or remove that file, or run: camagent configure")
    if not cams:
        r.line(FAIL, "Cameras", "none set up", "camagent add")
        return _summary(r)
    if camera_id:
        cams = [c for c in cams if c["platform"]["camera_id"] == camera_id]
        if not cams:
            r.line(FAIL, "Camera", f"'{camera_id}' isn't set up here", "camagent list")
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
    else:
        r.line(FAIL, "ffmpeg", "not found", camera.ffmpeg_install_hint())
    ffprobe = camera.find_ffprobe(ffmpeg)

    for cfg in cams:
        _check_camera(r, cfg, ffprobe)

    # --- service ---
    print()
    state = _service_state()
    if state == "running":
        r.line(OK, "Service", "running")
    elif state == "not installed":
        r.line(WARN, "Service", "not installed", "camagent install-service")
    else:
        r.line(FAIL, "Service", state, "camagent restart, then check the logs")

    return _summary(r)


def _check_camera(r, cfg, ffprobe):
    cam, plat = cfg["camera"], cfg["platform"]
    cid = plat.get("camera_id") or "?"
    print(f"\n--- camera {cid} ---")
    from .agent import missing_settings
    missing = missing_settings(cfg)
    if missing:
        r.line(FAIL, "Settings", "missing " + ", ".join(missing), f"camagent configure {cid}")

    if cam.get("host") and cam.get("ptz", True):
        try:
            info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
            r.line(OK, "Camera ONVIF", f"{info['manufacturer']} {info['model']} at {cam['host']}")
            if not any(p["ptz"] for p in info["profiles"]):
                r.line(WARN, "Camera PTZ", "no PTZ profile found",
                       f"set ptz = false in {cfg.get('_source')} if this is a fixed camera")
        except Exception as e:
            r.line(FAIL, "Camera ONVIF", camera.friendly_error(e),
                   "check the camera's IP, ONVIF port, login, and that its clock is set by NTP")

    if cam.get("rtsp_url"):
        if not ffprobe:
            r.line(WARN, "Camera stream", "can't check (ffprobe not found)")
        else:
            streams = camera.probe(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
            if streams:
                r.line(OK, "Camera stream", camera.describe(streams))
                video = next((x for x in streams if x.get("codec_type") == "video"), None)
                if video and video.get("codec_name") != "h264":
                    r.line(FAIL, "Video codec", f"{video.get('codec_name')}: most viewers' browsers can't play it",
                           "set this stream to H.264 in the camera's web page (and turn off 'smart codec'/H.264+)")
            else:
                r.line(FAIL, "Camera stream", f"can't read {config.strip_auth(cam['rtsp_url'])}",
                       f"check the RTSP URL and camera login (camagent configure {cid})")

    host = plat.get("ingest_host")
    if host:
        try:
            ip = socket.gethostbyname(host)
            r.line(OK, "Ingest server", f"{host} resolves to {ip}")
        except OSError:
            r.line(FAIL, "Ingest server", f"can't resolve {host}", "check the hostname and this machine's internet/DNS")

    if plat.get("mqtt_host") and plat.get("mqtt_password"):
        from .configure import test_mqtt
        ok, msg = test_mqtt(cfg)
        if ok:
            r.line(OK, "Server login", f"{plat['mqtt_host']}: {msg}")
        else:
            r.line(FAIL, "Server login", msg,
                   f"generate a new settings block on the camera's Connection page, then: camagent configure {cid}")


def _summary(r: Report):
    print()
    if r.failures:
        print(f"{r.failures} problem(s) found" + (f", {r.warnings} warning(s)" if r.warnings else "") + ".")
    elif r.warnings:
        print(f"No problems; {r.warnings} warning(s).")
    else:
        print("Everything looks good.")
    return 1 if r.failures else 0
