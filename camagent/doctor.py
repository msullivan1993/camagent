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


def run(config_path=None):
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
        cfg = config.load(path)
        r.line(OK, "Config", str(path))
    except FileNotFoundError:
        r.line(FAIL, "Config", f"not found at {path}", "camagent configure")
        return _summary(r)
    except PermissionError:
        r.line(FAIL, "Config", f"can't read {path}", f"run this from {admin_hint}")
        return _summary(r)
    except Exception as e:
        r.line(FAIL, "Config", f"can't parse {path} ({e})", "fix the file, or run: camagent configure")
        return _summary(r)

    from .agent import REQUIRED, REQUIRED_STREAM
    needed = list(REQUIRED) + (list(REQUIRED_STREAM) if cfg["stream"].get("enabled", True) else [])
    missing = [f"[{s}] {k}" for s, k in needed if not cfg[s].get(k)]
    if missing:
        r.line(FAIL, "Config values", "missing " + ", ".join(missing), "camagent configure")

    cam, plat = cfg["camera"], cfg["platform"]

    # --- ffmpeg ---
    ffmpeg = camera.find_ffmpeg(cfg["stream"].get("ffmpeg", ""))
    if ffmpeg:
        try:
            first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
        except Exception:
            first = ffmpeg
        r.line(OK, "ffmpeg", first)
    else:
        r.line(FAIL, "ffmpeg", "not found", camera.ffmpeg_install_hint())
    ffprobe = camera.find_ffprobe(ffmpeg)

    # --- camera ---
    if cam.get("host") and cam.get("ptz", True):
        try:
            info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
            r.line(OK, "Camera ONVIF", f"{info['manufacturer']} {info['model']} at {cam['host']}")
            if not any(p["ptz"] for p in info["profiles"]):
                r.line(WARN, "Camera PTZ", "no PTZ profile found",
                       "set [camera] ptz = false if this is a fixed camera")
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
                    r.line(WARN, "Video codec", f"{video.get('codec_name')} (browsers play H.264 directly)",
                           "set the camera's main stream to H.264")
            else:
                r.line(FAIL, "Camera stream", f"can't read {config.strip_auth(cam['rtsp_url'])}",
                       "check the RTSP URL and camera login (camagent configure can search for it)")

    # --- server ---
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
            r.line(OK, "MQTT broker", f"{plat['mqtt_host']}: {msg}")
        else:
            r.line(FAIL, "MQTT broker", msg, "check [platform] mqtt_host, mqtt_port, and mqtt_password")

    # --- service ---
    state = _service_state()
    if state == "running":
        r.line(OK, "Service", "running")
    elif state == "not installed":
        r.line(WARN, "Service", "not installed", "camagent install-service")
    else:
        r.line(FAIL, "Service", state, "camagent restart, then check the logs")

    return _summary(r)


def _summary(r: Report):
    print()
    if r.failures:
        print(f"{r.failures} problem(s) found" + (f", {r.warnings} warning(s)" if r.warnings else "") + ".")
    elif r.warnings:
        print(f"No problems; {r.warnings} warning(s).")
    else:
        print("Everything looks good.")
    return 1 if r.failures else 0
