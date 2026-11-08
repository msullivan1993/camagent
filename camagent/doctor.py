"""camagent doctor: check everything CamAgent depends on, with a plain pass/fail and what to do."""
import os
import socket
import subprocess
import sys

from . import __version__, camera, config, service

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"
CHANGE = "open CamAgent, then choose Change or remove a camera > Change a camera's settings"


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



def _problem(text):
    """("what's wrong", "how to fix it") from a message that ends with "To fix it: ..." or "To fix this, ...";
    (text, "") when it has neither, so a reworded message never breaks the checks."""
    for sep in (". To fix it: ", ". To fix this, ", ". To fix it, ", ". Fix: "):
        if sep in text:
            what, fix = text.split(sep, 1)
            return what, fix[:1].upper() + fix[1:]
    return text, ""

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
    admin_hint = ("an administrator Command Prompt or PowerShell (or the CamAgent shortcut in the Start menu)"
                  if os.name == "nt" else "sudo (type: sudo camagent doctor)")
    print(f"CamAgent {__version__} is checking for problems")
    print("[ OK ] means fine. [FAIL] means a camera can't work until you fix it.")
    print("[WARN] means you should fix it, but it isn't urgent.\n")

    # --- software ---
    v = sys.version_info
    if v >= (3, 11):
        r.line(OK, "Python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        r.line(FAIL, "Python", f"{v.major}.{v.minor} is too old",
               "run the CamAgent installer again. It installs a newer Python")
    from .storage import emmc_wear, wear_notice      # a box's built-in flash storage (0.17.15)
    for w in emmc_wear():
        note = wear_notice()
        r.line(WARN if note else OK, "Storage", f"{w['device']}: about {min(w['used_pct'], 100)}% worn",
               note or "")

    # --- config ---
    try:
        main, cams, problems = config.load_all(path)
    except PermissionError:
        r.line(FAIL, "Settings file", f"CamAgent can't read {path} without administrator rights",
               f"run this from {admin_hint}")
        return _summary(r)
    except Exception as e:
        r.line(FAIL, "Settings file", f"CamAgent can't read {path} ({e})",
               "fix the file, or set up the camera again: " + CHANGE)
        return _summary(r)
    for p in problems:
        r.line(FAIL, "Camera settings file", p, "fix or remove that file, or set up the camera again: " + CHANGE)
    if not cams:
        r.line(FAIL, "Cameras", "you haven't added a camera on this computer yet",
               "open CamAgent and choose Add a camera")
        return _summary(r)
    if camera_id:
        cams = [c for c in cams if c["platform"]["camera_id"] == camera_id]
        if not cams:
            r.line(FAIL, "Camera", f"'{camera_id}' isn't set up on this computer",
                   "open CamAgent and choose \"See if your cameras are working\" to see their names")
            return _summary(r)
    else:
        r.line(OK, "Cameras", f"{len(cams)} set up on this computer")

    # --- ffmpeg (shared) ---
    ffmpeg = camera.find_ffmpeg(cams[0]["stream"].get("ffmpeg", ""))
    if ffmpeg:
        try:
            first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
        except Exception:
            first = ffmpeg
        r.line(OK, "ffmpeg (video tool)", first)
        from . import prereqs
        if prereqs.unsafe_ffmpeg():          # Windows: in a folder any user can change (used anyway, 0.17.3)
            r.line(WARN, "ffmpeg location", _problem(prereqs.unsafe_ffmpeg())[0],
                   "open an administrator window and type: winget install -e --id Gyan.FFmpeg --scope machine --force. "
                   "Then restart CamAgent")
        if not prereqs.admin_only_location(sys.base_prefix):
            r.line(FAIL, "Python location", f"CamAgent's Python ({sys.base_prefix}) is in a folder that any "
                   "user of this computer can change (a security risk)",
                   "run the current CamAgent installer again. It installs Python for all users")
        if prereqs.srt_supported(ffmpeg) is False:
            r.line(FAIL, "ffmpeg SRT support", "this copy of ffmpeg can't send video the way YonderView receives it "
                   "(this copy was built without SRT)",
                   "install the full version (Windows: winget install -e --id Gyan.FFmpeg --scope machine; "
                   "Linux: sudo apt install ffmpeg)")
    else:
        from . import prereqs
        r.line(FAIL, "ffmpeg (video tool)", "CamAgent can't find it, and needs it to send video",
               camera.ffmpeg_install_hint())
    ffprobe = camera.find_ffprobe(ffmpeg)
    _check_privacy(r)
    if os.name == "nt":
        _check_windows_account(r)

    for cfg in cams:
        _check_camera(r, cfg, ffprobe)

    # --- clock ---
    from . import link
    offset = link.clock_offset()
    if offset is None:
        r.line(WARN, "Clock", "CamAgent couldn't compare it with YonderView's clock (no connection?)")
    elif link.clock_problem(offset):
        what, fix = _problem(link.clock_problem(offset))
        r.line(WARN, "Clock", what, fix)
    else:
        r.line(OK, "Clock", f"correct (within {abs(offset)} seconds of YonderView's)")

    # --- power ---
    from . import prereqs
    mins = prereqs.sleep_minutes()
    if mins:
        r.line(WARN, "Sleep", f"this computer goes to sleep after {mins} minutes when plugged in. That takes "
               "your cameras offline",
               "Settings > System > Power > Screen and sleep > Sleep: Never (while plugged in);"
               " or type: powercfg /change standby-timeout-ac 0")

    # --- automatic updates ---
    try:
        auto = config.load_or_defaults(config_path)["update"].get("auto", True)
        sched = service.update_schedule_installed()
        if auto and sched:
            r.line(OK, "Automatic updates", "on (nightly)")
        elif auto:
            r.line(WARN, "Automatic updates", "on, but CamAgent hasn't scheduled the nightly check yet",
                   "open CamAgent and choose Start CamAgent (or Restart CamAgent). Already running? Choose "
                   "Updates > Update CamAgent now")
        else:
            r.line(WARN, "Automatic updates", "off", "to turn them on, open CamAgent and choose Updates")
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
        r.line(WARN, "Running in the background", "not yet, so CamAgent isn't sending any camera",
               "open CamAgent and choose Start CamAgent")
    else:
        r.line(FAIL, "Running in the background", f"no ({state}), so CamAgent isn't sending any camera",
               "open CamAgent and choose Restart CamAgent. Still failing? Choose Show the recent log and send it "
               "to support (choose Report a problem on yonderview.net)")

    return _summary(r)


def _check_privacy(r):
    """Linux: other users of this computer can see every program's command line (ps), and ffmpeg's holds the camera's
    login and the stream's secrets. hidepid=2 on /proc hides other users' programs."""
    if os.name == "nt":
        return
    try:
        opts = next((line.split()[3] for line in open("/proc/mounts", encoding="utf-8")
                     if line.split()[1:2] == ["/proc"]), "")
    except (OSError, IndexError):
        return
    if not any(o in opts.split(",") for o in ("hidepid=2", "hidepid=invisible", "hidepid=1", "hidepid=noaccess")):
        r.line(WARN, "Privacy from other users", "other users of this computer can see ffmpeg's command "
               "line. That line holds the camera's login and the video's secrets",
               "this only matters if other people have their own logins on this computer. If that's you, whoever "
               "manages the computer can hide their view of programs: add hidepid=2 to /proc in /etc/fstab "
               "(proc /proc proc defaults,hidepid=2 0 0). Also give CamAgent a camera "
               "login that can only watch (not change settings), if the camera allows it")


def _check_windows_account(r):
    """0.17: the service runs as its own limited account, not SYSTEM."""
    try:
        out = subprocess.run(["sc", "qc", service.SERVICE], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return
    m = next((line.split(":", 1)[1].strip() for line in out.splitlines() if "SERVICE_START_NAME" in line), "")
    if not m:
        return
    if m.lower() == "localsystem":
        r.line(OK, "Service account", "CamAgent runs as Windows' system account (LocalSystem), the normal setting. "
               "Leave this alone unless YonderView support asks you to change it. (For support: set "
               "[agent] windows_account = \"limited\" in camagent.toml to use a limited account.)")
    else:
        r.line(OK, "Service account", m)


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
        r.line(FAIL, "Converting to 1080p", "converting on this computer's processor (CPU) doesn't work",
               f"reinstall ffmpeg: {camera.ffmpeg_install_hint()}")
    else:
        r.line(WARN, "Converting to 1080p", f"{convert.LABELS[method]} doesn't work on this computer any more. "
               "CamAgent uses the processor instead. That works, but the video may stutter",
               f"update the graphics driver, or check again: {CHANGE}")


def _check_camera(r, cfg, ffprobe):
    cam, plat = cfg["camera"], cfg["platform"]
    cid = plat.get("camera_id") or "?"
    print(f"\n--- camera {cid} ---")
    from .agent import missing_settings
    missing = missing_settings(cfg)
    if missing:
        r.line(FAIL, "Settings", "missing " + ", ".join(missing), f"set up the camera again: {CHANGE}")

    if cam.get("host") and cam.get("ptz", True):
        try:
            info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
            r.line(OK, "Camera connection", f"{info['manufacturer']} {info['model']} at {cam['host']}")
            if info.get("login_level") == "Administrator":
                r.line(WARN, "Camera login", f"\"{cam['username']}\" is an administrator on the camera",
                       "for safety, make a login on the camera just for CamAgent, with Operator (or User) rights. "
                       "Operator rights are enough for turning, zoom and presets. Then change the camera's login "
                       "in CamAgent: " + CHANGE)
            ok, text = camera.describe_position(info.get("position"))
            if ok is not None:
                r.line(OK if ok else WARN, "PTZ position", text,
                       None if ok else "restart the camera. That sometimes fixes this. Some cameras never report it")
            if not any(p["ptz"] for p in info["profiles"]):
                r.line(WARN, "Camera controls", "remote turn and zoom (PTZ) is on, but this camera doesn't offer it",
                       f"if the camera doesn't turn or zoom, answer No to PTZ here: {CHANGE}")
        except Exception as e:
            msg = camera.friendly_error(e)
            if "login rejected" in msg:
                r.line(FAIL, "Camera connection", "the camera refused the username or password",
                       "try them in the camera's web page. Some cameras need a separate ONVIF user. Also check that "
                       f"the camera sets its clock automatically (NTP). Then: {CHANGE}")
            else:
                r.line(FAIL, "Camera connection", msg,
                       "check that the camera is on and is on this network. If your router restarted, the camera "
                       "may have a new address. To find it, choose Advanced tools > Search the network for cameras. "
                       f"Then: {CHANGE}")

    if cam.get("rtsp_url"):
        if not ffprobe:
            r.line(WARN, "Camera video", "CamAgent can't check it (ffmpeg isn't installed)",
                   camera.ffmpeg_install_hint())
        else:
            streams = camera.probe(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
            if streams:
                r.line(OK, "Camera video", camera.describe(streams))
                video = next((x for x in streams if x.get("codec_type") == "video"), None)
                converting = cfg["stream"].get("convert", "off") != "off"
                if video and video.get("codec_name") != "h264" and not converting:
                    r.line(FAIL, "Video codec", f"{video.get('codec_name')}: most viewers' browsers can't play it",
                           "set the camera's video to H.264 in the camera's web page (and turn off 'smart codec' "
                           f"and H.264+), or let CamAgent convert it: {CHANGE}")
                elif video and (video.get("height") or 0) > 1080 and not converting:
                    r.line(WARN, "Resolution", f"CamAgent sends {video.get('height')}p as it is",
                           f"that's fine if YonderView approved it for this camera. If not, let CamAgent convert it "
                           f"to 1080p: {CHANGE}")
            else:
                r.line(FAIL, "Camera video", f"CamAgent can't read {config.strip_auth(cam['rtsp_url'])}",
                       f"check the video address and camera login: {CHANGE}")

    if cfg["stream"].get("audio") and cam.get("rtsp_url") and ffprobe:
        url = config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"])
        if camera.audio_arrives(ffprobe, url) is False:
            r.line(WARN, "Audio", "audio is on, but the camera sends no sound (CamAgent sends video only)",
                   f"turn the camera's audio on in its web page, or turn sound off in CamAgent: {CHANGE}")

    _check_convert(r, cfg, cid)

    host = plat.get("ingest_host")
    if host:
        try:
            ip = socket.gethostbyname(host)
            r.line(OK, "YonderView video server", f"CamAgent found {host} ({ip})")
        except OSError:
            r.line(FAIL, "YonderView video server", f"CamAgent can't find {host}",
                   "check this computer's internet connection")

    if plat.get("mqtt_host") and plat.get("mqtt_password"):
        from .configure import test_mqtt
        ok, msg = test_mqtt(cfg)
        if ok:
            r.line(OK, "YonderView connection", f"{plat['mqtt_host']}: {msg}")
            declined = (cfg.get("_server") or {}).get("declined")
            if declined:
                r.line(FAIL, "YonderView", f"YonderView declined the video: {declined}",
                       "fix what YonderView's message describes (in the camera's web page, or here: " + CHANGE + "). "
                       "Then click 'Check again now' on the camera's page on yonderview.net")
        else:
            r.line(FAIL, "YonderView connection", msg,
                   "if this computer's internet works, get new connection settings: " + CHANGE
                   + " (sign in, or paste new connection settings from the camera's Connection page on yonderview.net)")

    if cam.get("rtsp_url") and ffprobe:
        m = camera.measure_stream(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
        if m.get("actual_fps"):
            r.line(OK, "Video measured", camera.stream_summary(m))
        limit = (cfg.get("_server") or {}).get("max_height")
        for tip in camera.stream_advice(m, limit):
            r.line(FAIL if "would be declined" in tip or "needs one" in tip else WARN, "Camera video settings", tip)


def _summary(r: Report):
    print()
    if r.failures:
        print(f"CamAgent found {r.failures} problem(s)" + (f" and {r.warnings} warning(s)" if r.warnings else "")
              + ", marked above. Fix them from the top down, then choose Check for problems again.")
        print('Stuck? Choose "Report a problem" on yonderview.net and paste this list.')
    elif r.warnings:
        print(f"CamAgent found no problems and {r.warnings} warning(s), marked [WARN] above.")
    else:
        print("CamAgent found no problems. Everything looks good.")
    return 1 if r.failures else 0
