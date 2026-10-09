"""Interactive setup: find the camera, pick a stream, enter server details, test, save."""
import getpass
import json
import os
import time
import threading
from pathlib import Path

from . import camera, config, guide, service


# ---------- prompt helpers ----------

def ask(prompt, default=""):
    shown = f" [{default}]" if default not in ("", None) else ""
    value = input(f"{prompt}{shown}: ").strip()
    return value if value else default


def ask_secret(prompt, existing=""):
    hint = " (Enter to keep current)" if existing else ""
    value = getpass.getpass(f"{prompt}{hint}: ")
    return value if value else existing


def yes(prompt, default=True):
    d = "Y/n" if default else "y/N"
    v = input(f"{prompt} ({d}): ").strip().lower()
    return default if not v else v in ("y", "yes")


def choose(items, label, allow_none_text=None):
    for i, item in enumerate(items, 1):
        print(f"  {i}. {label(item)}")
    if allow_none_text:
        print(f"  0. {allow_none_text}")
    while True:
        v = input("Choose a number: ").strip()
        if allow_none_text and v == "0":
            return None
        if v.isdigit() and 1 <= int(v) <= len(items):
            return items[int(v) - 1]
        print("  Not a valid choice.")


def heading(text):
    print(f"\n=== {text} ===")


# ---------- steps ----------

def step_find_camera(cfg):
    cam = cfg["camera"]
    heading("Camera")
    guide.show(guide.CAMERA)
    if yes("Search the network for ONVIF cameras?", default=not cam["host"]):
        print("Searching (3 seconds)...")
        try:
            found = camera.discover()
        except OSError as e:
            found = []
            print(f"  Search failed: {e}")
        if found:
            pick = choose(found, lambda c: f"{c['host']}:{c['port']}  {c['name']} {c['hardware']}".rstrip(),
                          allow_none_text="None of these; enter the address myself")
            if pick:
                cam["host"], cam["onvif_port"] = pick["host"], pick["port"]
        else:
            print("  No ONVIF cameras answered. Check that ONVIF is enabled on the camera,")
            print("  and that this computer is on the same network. You can still enter the address.")
    cam["host"] = ask("Camera IP address", cam["host"])
    cam["onvif_port"] = int(ask("ONVIF port", cam["onvif_port"]))
    cam["username"] = ask("Camera username", cam["username"])
    cam["password"] = ask_secret("Camera password", cam["password"])


RETRY_CAMERA = "Re-enter the camera's address and login, and try again"


def _is_h264(encoding):
    return str(encoding or "").upper().replace(".", "").replace("-", "") in ("H264", "AVC")


def _codec_flag(encoding):
    e = str(encoding or "").upper().replace(".", "").replace("-", "")
    if not e or e in ("H264", "AVC"):
        return ""            # blank = unknown; the stream check after picking will tell
    if e in ("H265", "HEVC"):
        return "  [H.265: won't play in most browsers]"
    return "  [won't play in browsers]"
NO_ONVIF = "Continue without ONVIF (find the stream another way; PTZ will be off)"


def step_pick_stream(cfg, ffprobe):
    """Returns False if the user wants to go back and re-enter the camera's details."""
    cam = cfg["camera"]
    heading("Stream")
    guide.show(guide.STREAM)
    print("Asking the camera for its streams over ONVIF...")
    try:
        info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
    except Exception as e:
        info = None
        msg = camera.friendly_error(e)
        print(f"  ONVIF didn't work: {msg}")
        if "login rejected" in msg:
            print("  Many cameras need a separate ONVIF user, created in the camera's own web page.")
            guide.show(guide.ONVIF_USERS)
        else:
            print("  (Common causes: wrong IP or ONVIF port, ONVIF turned off, or the camera's clock is off.)")
            guide.show(guide.ONVIF_USERS)
        if choose([RETRY_CAMERA, NO_ONVIF], str) == RETRY_CAMERA:
            return False

    if info and info["profiles"]:
        print(f"  Found: {info['manufacturer']} {info['model']} (firmware {info['firmware']})")
        profiles = [p for p in info["profiles"] if p["rtsp_url"]]
        profiles.sort(key=lambda p: not _is_h264(p["encoding"]))      # browser-friendly streams first
        if profiles:
            print("Which stream should be sent? (Pick an H.264 one: browsers can't play the others.)")
            pick = choose(profiles, lambda p: (
                f"{p['name']}: {p['encoding'] or '?'} {p['width']}x{p['height']}"
                f"{' @ ' + str(p['fps']) + ' fps' if p['fps'] else ''}"
                f"{'  [PTZ]' if p['ptz'] else ''}{_codec_flag(p['encoding'])}\n       {p['rtsp_url']}"),
                allow_none_text="None of these; I'll enter an RTSP URL")
            if pick:
                cam["rtsp_url"] = pick["rtsp_url"]
                cam["fps_configured"] = pick.get("fps") or 0
            has_ptz = any(p["ptz"] for p in info["profiles"])
            ptz_profile = next((p["token"] for p in info["profiles"] if p["ptz"]), "")
            if has_ptz:
                cam["ptz"] = yes("This camera supports PTZ. Enable PTZ control?", default=True)
                cam["ptz_profile"] = ptz_profile if cam["ptz"] else ""
            else:
                print("  No PTZ found on this camera; PTZ control will be off.")
                cam["ptz"], cam["ptz_profile"] = False, ""
            if pick:
                return True

    if ffprobe and yes("Try common RTSP addresses for popular camera brands?", default=True):
        def progress(vendor, path):
            print(f"  trying {vendor:<18} {path}")
        found = camera.scan_rtsp_paths(ffprobe, cam["host"], cam["username"], cam["password"], progress=progress)
        if found:
            pick = choose(found, lambda f: f"{f['rtsp_url']}  ({camera.describe(f['streams'])})",
                          allow_none_text="None of these")
            if pick:
                cam["rtsp_url"] = pick["rtsp_url"]
                if not info:
                    cam["ptz"] = False
                return True
        else:
            print("  None of the common addresses answered.")

    guide.show(guide.RTSP_PATHS)
    cam["rtsp_url"] = config.strip_auth(ask("RTSP URL (credentials are added automatically)", cam["rtsp_url"]))
    if not info:
        cam["ptz"] = yes("Does this camera have PTZ (and ONVIF working)?", default=False)
    return True


def step_check_stream(cfg, ffprobe):
    """Returns "ok", "camera" (go back to the camera's address/login) or "stream" (pick another stream)."""
    cam, s = cfg["camera"], cfg["stream"]
    if not ffprobe:
        print("\n(ffprobe not found, so the stream can't be checked. Install ffmpeg to enable this.)")
        return "ok"
    while True:
        print("\nChecking the stream...")
        streams = camera.probe(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
        if not streams:
            print(f"  Couldn't read {cam['rtsp_url'] or '(no RTSP URL)'}.")
            print("  Check the URL, and that the camera login is allowed to view RTSP.")
            other_url = "Enter a different RTSP URL"
            keep = "Keep it anyway (it won't stream until this is fixed)"
            pick = choose([other_url, RETRY_CAMERA, keep], str)
            if pick == other_url:
                cam["rtsp_url"] = config.strip_auth(ask("RTSP URL", cam["rtsp_url"]))
                continue
            return "camera" if pick == RETRY_CAMERA else "ok"

        print(f"  Read it: {camera.describe(streams)}")
        video = next((x for x in streams if x.get("codec_type") == "video"), None)
        codec = (video or {}).get("codec_name", "")
        if video and codec != "h264":
            name = {"hevc": "H.265 (HEVC)"}.get(codec, codec.upper())
            print(f"\n  This stream is {name}. Most viewers' browsers can't play it, so they'd see a black player.")
            print("  Set this stream to H.264 in the camera's web page (Video or Encode settings; also turn off")
            print("  any 'smart codec' or H.264+ option), or pick another stream such as the camera's sub-stream.")
            recheck, other = "I've changed the camera to H.264: check again", "Pick a different stream"
            keep = "Keep it anyway (viewers won't see video until it's H.264)"
            pick = choose([recheck, other, keep], str)
            if pick == recheck:
                continue
            if pick == other:
                return "stream"
        break

    print("  Measuring what the camera actually sends (a few seconds)...")
    m = camera.measure_stream(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
    cfg["_measure"] = m
    tips = camera.stream_advice(m)
    if m.get("actual_fps"):
        print(f"  Your camera: {camera.stream_summary(m)}." + ("" if tips else " Meets the requirements."))
    for tip in tips:
        print(f"  Note: {tip}")

    has_audio = any(x.get("codec_type") == "audio" for x in streams)
    if has_audio:
        s["audio"] = yes("The camera sends audio. Include it in the stream?", default=s["audio"])
    else:
        s["audio"] = False
    return "ok"


def step_platform(cfg):
    p, s = cfg["platform"], cfg["stream"]
    heading("Server")
    guide.show(guide.SERVER)
    print("Paste the settings block now, then press Enter")
    print("on an empty line. Or just press Enter now to type the values one by one.")
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        lines.append(line)
    if lines:
        parsed = config.parse_settings_block("\n".join(lines))
        latency = parsed.pop("_srt_latency_ms", None)
        p.update(parsed)
        if latency:
            s["srt_latency_ms"] = latency
        print(f"  Read settings for camera '{p['camera_id']}'.")
    else:
        p["camera_id"] = ask("Stream / camera ID", p["camera_id"])
        p["ingest_host"] = ask("Ingest host", p["ingest_host"])
        p["srt_password"] = ask_secret("SRT publish password", p["srt_password"])
        p["srt_passphrase"] = ask_secret("SRT passphrase", p["srt_passphrase"])
        p["mqtt_host"] = ask("MQTT host", p["mqtt_host"] or p["ingest_host"])
        p["mqtt_password"] = ask_secret("MQTT password", p["mqtt_password"])
    p["mqtt_port"] = int(ask("MQTT port", p["mqtt_port"]))


def test_mqtt(cfg):
    """Try logging in to the broker. Returns (ok, message)."""
    import paho.mqtt.client as mqtt
    p = cfg["platform"]
    done, result = threading.Event(), {}

    def on_connect(c, u, f, rc, props=None):
        result["rc"] = rc
        if not rc.is_failure:
            c.subscribe(f"cam/{p['camera_id']}/config", qos=1)     # the server's settings for this camera
        done.set()

    def on_message(c, u, msg):
        try:
            result["server"] = json.loads(msg.payload or b"{}")
        except ValueError:
            pass

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{p['camera_id']}-test")
    c.username_pw_set(p["camera_id"], p["mqtt_password"])
    if p.get("mqtt_tls"):
        c.tls_set()
    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(p["mqtt_host"], int(p["mqtt_port"]), keepalive=10)
    except Exception as e:
        return False, f"can't reach {p['mqtt_host']}:{p['mqtt_port']} ({e})"
    c.loop_start()
    done.wait(8)
    if result.get("rc") is not None and not result["rc"].is_failure:
        deadline = time.time() + 2.5                   # the server's settings arrive right after subscribing
        while "server" not in result and time.time() < deadline:
            time.sleep(0.1)
    cfg["_server"] = result.get("server") or {}
    c.disconnect()
    c.loop_stop()
    rc = result.get("rc")
    if rc is None:
        return False, "no answer from the broker"
    if rc.is_failure:
        return False, f"broker refused the login ({rc}); check the camera ID and MQTT password"
    return True, "connected and logged in"


def _rewalk_stream(cam):
    """Choose a stream again without re-entering the camera's login or the settings block."""
    ffmpeg = camera.find_ffmpeg(cam["stream"].get("ffmpeg", ""))
    ffprobe = camera.find_ffprobe(ffmpeg)
    while True:
        if not step_pick_stream(cam, ffprobe):
            step_find_camera(cam)
            continue
        if step_check_stream(cam, ffprobe) in ("camera", "stream"):
            continue
        return


def _check_writable(path: Path):
    target = path if path.exists() else path.parent
    if (target.exists() and not os.access(target, os.W_OK)) or (not target.exists() and not service.is_admin()):
        hint = "an administrator Command Prompt or PowerShell" if os.name == "nt" else "sudo"
        raise SystemExit(f"Can't write {path}. Run this from {hint}.")


def _tools(cfg):
    from . import prereqs
    return prereqs.check_and_fix(yes, cfg["stream"].get("ffmpeg", ""))


def _walk(cam, ffmpeg, ffprobe):
    done = False
    while not done:                               # camera details -> stream; go back on failure
        step_find_camera(cam)
        while True:
            if not step_pick_stream(cam, ffprobe):
                break                             # back to the camera's address and login
            result = step_check_stream(cam, ffprobe)
            if result == "camera":
                break
            if result == "stream":
                continue                          # pick another stream from the same camera
            done = True
            break
    step_platform(cam)
    if ffmpeg:
        cam["stream"]["ffmpeg"] = ffmpeg           # full path, so the service finds it too
    if not cam["platform"].get("camera_id"):
        raise SystemExit("No camera ID was given, so nothing was saved. Paste the settings block and try again.")


def _finish(cam, main_path, others):
    cid = cam["platform"]["camera_id"]
    clash = next((o for o in others if o["platform"]["camera_id"] == cid), None)
    if clash and not yes(f"Camera '{cid}' is already set up on this machine. Replace its settings?", default=False):
        raise SystemExit("Nothing saved.")

    while True:
        heading("Testing")
        guide.show(guide.TEST)
        ok, msg = test_mqtt(cam)
        print(f"  Server login: {msg}")
        if ok:
            limit = (cam.get("_server") or {}).get("max_height")
            height = (cam.get("_measure") or {}).get("height")
            if limit and height and height > limit:
                print(f"\n  This stream is {height}p, but YonderView's limit for this camera is {limit}p,")
                print("  so it would be declined as soon as it connects. Set the camera's stream to "
                      f"{limit}p or lower, or pick its sub-stream.")
                other, recheck = "Pick a different stream", "I've changed the camera: check again"
                keep = "Save anyway (it will be declined until it's fixed)"
                pick = choose([other, recheck, keep], str)
                if pick == other:
                    _rewalk_stream(cam)
                    continue
                if pick == recheck:
                    cam["_measure"] = camera.measure_stream(
                        camera.find_ffprobe(camera.find_ffmpeg(cam["stream"].get("ffmpeg", ""))),
                        config.rtsp_with_auth(cam["camera"]["rtsp_url"], cam["camera"]["username"],
                                              cam["camera"]["password"]))
                    continue
            break
        again, anyway = "Paste the settings block again", "Save anyway (it won't connect until this is fixed)"
        pick = choose([again, anyway, "Cancel without saving"], str)
        if pick == again:
            step_platform(cam)
            continue
        if pick == anyway:
            break
        raise SystemExit("Nothing saved.")

    if clash and Path(clash["_source"]) != Path(cam.get("_source") or ""):
        config.remove_camera(clash, main_path)
    saved = config.save_camera(cam, main_path)
    print(f"\nSaved camera '{cid}' to {saved}")
    _apply(main_path)
    guide.show(guide.DONE)


def _apply(main_path):
    _ask_auto_update(main_path)
    if service.is_installed():
        if yes("Restart the service to apply the change?", default=True):
            service.restart()
    elif yes("Install and start camagent as a service now?", default=True):
        service.install(main_path)
    else:
        print("Start it later with: camagent install-service   (or test it with: camagent run)")


def _ask_auto_update(main_path):
    """Asked once per computer. Automatic updates install new releases nightly and roll back if one fails."""
    main = config.load_or_defaults(main_path)
    if main["update"].get("asked"):
        return
    print("\nAutomatic updates install new camagent releases overnight (around 3 AM), check the cameras")
    print("come back, and go back to the previous version if they don't.")
    main["update"]["auto"] = yes("Keep camagent up to date automatically?", default=True)
    main["update"]["asked"] = True
    config.save_main(main, main_path)
    if not main["update"]["auto"] and service.update_schedule_installed():
        service.remove_update_schedule()
    elif main["update"]["auto"] and service.is_installed() and not service.update_schedule_installed():
        service.install_update_schedule(main_path)


def toggle_auto_update(main_path=None):
    main = config.load_or_defaults(main_path)
    main["update"]["auto"] = not main["update"].get("auto", True)
    main["update"]["asked"] = True
    config.save_main(main, main_path)
    if main["update"]["auto"]:
        service.install_update_schedule(main_path)
    elif service.update_schedule_installed():
        service.remove_update_schedule()


def _pick(cams, camera_id):
    if camera_id:
        cam = next((c for c in cams if c["platform"]["camera_id"] == camera_id), None)
        if not cam:
            raise SystemExit(f"No camera '{camera_id}' here. See them with: camagent list")
        return cam
    if len(cams) == 1:
        return cams[0]
    print("Which camera?")
    return choose(cams, lambda c: c["platform"]["camera_id"], allow_none_text="Add a new camera")


def run(config_path=None, camera_id=None):
    """camagent configure [camera]: change a camera's settings (or set up the first one)."""
    main_path = Path(config_path or config.default_config_path())
    _check_writable(main_path)
    main, cams, problems = config.load_all(main_path)
    for p in problems:
        print(f"Note: {p}")
    if not cams:
        return add(config_path)
    cam = _pick(cams, camera_id)
    if cam is None:
        return add(config_path)
    print(f"camagent setup for camera '{cam['platform']['camera_id']}'. "
          "Press Enter to keep the value shown in [brackets].")
    original_id = cam["platform"]["camera_id"]
    ffmpeg, ffprobe = _tools(cam)
    _walk(cam, ffmpeg, ffprobe)
    others = [c for c in cams if c["platform"]["camera_id"] != original_id]
    _finish(cam, main_path, others)


def add(config_path=None):
    """camagent add: set up another camera on this machine."""
    import copy
    main_path = Path(config_path or config.default_config_path())
    _check_writable(main_path)
    main, cams, _ = config.load_all(main_path)
    cam = {s: copy.deepcopy(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
    for s in config.MAIN_SECTIONS:
        cam[s] = copy.deepcopy(main[s])
    # Reuse the server address and ffmpeg path from a camera that's already set up
    if cams:
        for k in ("ingest_host", "ingest_port", "mqtt_host", "mqtt_port", "mqtt_tls"):
            cam["platform"][k] = cams[0]["platform"][k]
        cam["stream"]["ffmpeg"] = cams[0]["stream"].get("ffmpeg", "")
    n = len(cams) + 1
    if n == 1:
        guide.show(guide.INTRO)
    else:
        print(f"Adding camera #{n} on this machine. Press Enter to keep the value shown in [brackets].")
        print("You'll need this camera's IP address, its login, and its settings block from yonderview.net.")
    ffmpeg, ffprobe = _tools(cam)
    _walk(cam, ffmpeg, ffprobe)
    _finish(cam, main_path, cams)


def remove(config_path=None, camera_id=None):
    """camagent remove <camera>: stop streaming a camera from this machine and delete its settings."""
    main_path = Path(config_path or config.default_config_path())
    _check_writable(main_path)
    _, cams, _ = config.load_all(main_path)
    if not cams:
        raise SystemExit("No cameras are set up here.")
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    cid = cam["platform"]["camera_id"]
    if not yes(f"Remove camera '{cid}' from this machine? Its settings will be deleted.", default=False):
        raise SystemExit("Nothing removed.")
    config.remove_camera(cam, main_path)
    print(f"Removed '{cid}'. (It still exists on yonderview.net; this only stops sending it from here.)")
    if service.is_installed() and len(cams) > 1:
        if yes("Restart the service so it stops?", default=True):
            service.restart()
    elif service.is_installed():
        print("That was the last camera. Stop the service with: camagent uninstall-service")


def list_cameras(config_path=None):
    """camagent list: every camera on this machine, with live status when the service is running."""
    import json
    import time
    main_path = Path(config_path or config.default_config_path())
    try:
        _, cams, problems = config.load_all(main_path)
    except PermissionError:
        hint = "an administrator Command Prompt or PowerShell" if os.name == "nt" else "sudo"
        raise SystemExit(f"Can't read the config. Run this from {hint}.")
    live = {}
    try:
        data = json.loads(config.status_path().read_text(encoding="utf-8"))
        if time.time() - data.get("updated", 0) < 60:
            live = data.get("cameras", {})
    except (OSError, ValueError):
        pass
    if not cams:
        print("No cameras are set up here. Add one with: camagent add")
    for c in cams:
        cid = c["platform"]["camera_id"]
        where = config.strip_auth(c["camera"].get("rtsp_url", "")) or c["camera"].get("host", "")
        print(f"{cid}")
        print(f"    camera: {where or '(not set)'}{'  [PTZ]' if c['camera'].get('ptz') else ''}"
              f"{'  [stream off]' if not c['stream'].get('enabled', True) else ''}")
        st = live.get(cid)
        if st is None:
            print("    status: " + ("service not running" if not live else "not running (check its settings)"))
        else:
            video = (f"streaming {st['fps']:.0f} fps, {st['bitrate_kbps']:.0f} kbps" if st.get("streaming")
                     else "NOT streaming" + (f" ({st['last_error'][:80]})" if st.get("last_error") else ""))
            parts = [video, "server connected" if st.get("mqtt") else "server NOT connected"]
            if st.get("ptz"):
                parts.append(f"PTZ {st['ptz']}")
            print("    status: " + "; ".join(parts))
        if st and st.get("declined"):
            print(f"    DECLINED by YonderView: {st['declined']}")
        if c.get("_legacy"):
            print("    (stored in the old single-camera format; `camagent configure` will move it)")
    for p in problems:
        print(f"PROBLEM: {p}")
