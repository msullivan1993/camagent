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
    hint = " (hidden as you type; press Enter to keep the current one)" if existing else " (hidden as you type)"
    value = getpass.getpass(f"{prompt}{hint}: ")
    return value if value else existing


def ask_int(prompt, default):
    """A number (a port): asks again instead of failing on a typo."""
    while True:
        value = ask(prompt, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            print("  Please type a number, like 80, or press Enter to keep the one in [brackets].")


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
        v = input("Type a number and press Enter: ").strip()
        if allow_none_text and v == "0":
            return None
        if v.isdigit() and 1 <= int(v) <= len(items):
            return items[int(v) - 1]
        print(f"  Please type a number from 1 to {len(items)}" + (", or 0." if allow_none_text else "."))


def _fix_stream(cam, max_height=1080):
    """Change the camera's main stream over ONVIF (H.264, size limit, 2 s keyframes). Reports what happened."""
    from .controls import Controls, NotAllowed, OnvifSession
    had_sound = camera.sound_check(cam) if cam["stream"].get("audio") else None
    try:
        s = OnvifSession(cam)
        s._connect()
        changes = Controls(s).stream_fixes(apply=True, max_height=max_height)
    except NotAllowed as e:
        print(f"  {e}.")
        print("  Change it in the camera's own web page instead (Video or Encode settings), then check again.")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  CamAgent couldn't change the camera's settings ({e}).")
        print("  Change them in the camera's own web page instead (Video or Encode settings).")
        return False
    print("  Changed on the camera: " + ("; ".join(changes) if changes else "nothing needed changing")
          + ". Waiting a few seconds for its video to restart...")
    time.sleep(8)
    if had_sound and changes and camera.sound_check(cam) is False:
        print(camera.AUDIO_LOST)
    return True


def heading(text):
    print(f"\n=== {text} ===")


# ---------- steps ----------

def step_find_camera(cfg):
    cam = cfg["camera"]
    heading("Step 1 of 4: Your camera")
    guide.show(guide.CAMERA)
    if yes("Let CamAgent search this network for cameras? (Easiest; takes 3 seconds)", default=not cam["host"]):
        print("Searching this network (3 seconds)...")
        try:
            found = camera.discover()
        except OSError as e:
            found = []
            print(f"  The search didn't work ({e}). You can type the camera's address below instead.")
        if found:
            print("Cameras found (IP address:port, then the camera's name). Which one is yours?")
            pick = choose(found, lambda c: f"{c['host']}:{c['port']}  {c['name']} {c['hardware']}".rstrip(),
                          allow_none_text="None of these; enter the address myself")
            if pick:
                cam["host"], cam["onvif_port"] = pick["host"], pick["port"]
        else:
            print("  No cameras answered. Check that the camera is on, on the same network as this computer,")
            print("  and has ONVIF turned on. If your Wi-Fi has a guest network, both must be on the same one.")
            print("  You can still type the camera's IP address below.")
    cam["host"] = ask("Camera's IP address (four numbers with dots, like 192.168.1.64)", cam["host"])
    cam["onvif_port"] = ask_int("Camera's ONVIF port (not sure? press Enter; Reolink cameras use 8000)", cam["onvif_port"])
    print("  Next, the camera's own login: the one for its app or web page (not your YonderView login).")
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
NO_ONVIF = "Continue without ONVIF (CamAgent tries to find the camera's video another way)"


def step_pick_stream(cfg, ffprobe):
    """Returns False if the user wants to go back and re-enter the camera's details."""
    cam = cfg["camera"]
    heading("Step 2 of 4: The camera's video")
    guide.show(guide.STREAM)
    print("Asking the camera what video it can send (this can take up to a minute)...")
    try:
        info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
    except Exception as e:
        info = None
        msg = camera.friendly_error(e)
        if "login rejected" in msg:
            print("  The camera refused the username or password. Try signing in to the camera's web page with")
            print("  them to be sure. Many cameras also need a separate ONVIF user, made in that web page:")
        else:
            print("  CamAgent couldn't talk to the camera. Check that:")
            print("    1. it's powered on and on the same network as this computer,")
            print("    2. the address (and port) you typed are right,")
            print("    3. ONVIF is turned on in the camera's web page:")
        guide.show(guide.ONVIF_USERS)
        if choose([RETRY_CAMERA, NO_ONVIF], str) == RETRY_CAMERA:
            return False

    if info and info["profiles"]:
        print(f"  Connected to your {info['manufacturer']} {info['model']}.".replace("  .", "."))
        profiles = [p for p in info["profiles"] if p["rtsp_url"]]
        profiles.sort(key=lambda p: not _is_h264(p["encoding"]))      # browser-friendly streams first
        if profiles:
            print("Which of the camera's videos should be sent? Choose the biggest picture marked H.264 (usually")
            print("the first, often called \"main\", with a size like 1920x1080). Skip any marked \"won't play\".")
            pick = choose(profiles, lambda p: (
                f"{p['name']}: {p['encoding'] or '?'} {p['width']}x{p['height']}"
                f"{' @ ' + str(p['fps']) + ' fps' if p['fps'] else ''}"
                f"{'  [PTZ]' if p['ptz'] else ''}{_codec_flag(p['encoding'])}\n       {p['rtsp_url']}"),
                allow_none_text="None of these: I'll type the video address myself")
            if pick:
                cam["rtsp_url"] = pick["rtsp_url"]
                cam["fps_configured"] = pick.get("fps") or 0
            has_ptz = any(p["ptz"] for p in info["profiles"])
            ptz_profile = next((p["token"] for p in info["profiles"] if p["ptz"]), "")
            if has_ptz:
                cam["ptz"] = yes("This camera can turn and zoom by remote control (PTZ). Allow that on YonderView? Only people\n  you give control to (and YonderView staff) can move it.", default=True)
                cam["ptz_profile"] = ptz_profile if cam["ptz"] else ""
            else:
                print("  This camera can't turn or zoom by remote (no PTZ), so those controls stay off.")
                cam["ptz"], cam["ptz_profile"] = False, ""
            if pick:
                return True

    if ffprobe and yes("Let CamAgent try the usual video addresses for popular brands? (About a minute; recommended)", default=True):
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
            print("  None of the usual addresses worked. You can type the camera's video address below.")

    guide.show(guide.RTSP_PATHS.format(ip=cam["host"] or "IP"))
    cam["rtsp_url"] = config.strip_auth(ask("Video address, starting rtsp:// (leave out the login)", cam["rtsp_url"]))
    if not info:
        cam["ptz"] = yes("Does this camera turn and zoom by remote control (PTZ)?", default=False)
    return True


def step_check_stream(cfg, ffprobe):
    """Returns "ok", "camera" (go back to the camera's address/login) or "stream" (pick another stream)."""
    cam, s = cfg["camera"], cfg["stream"]
    if not ffprobe:
        print("\n(ffprobe, part of the free video tool ffmpeg, wasn't found, so the video can't be checked now.")
        print(" Install or reinstall ffmpeg, then change this camera's settings again to check it.)")
        return "ok"
    while True:
        print("\nChecking the camera's video (up to 30 seconds)...")
        streams = camera.probe(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
        if not streams:
            print(f"  CamAgent couldn't get video from {config.redact(cam['rtsp_url']) if cam['rtsp_url'] else '(no video address)'}.")
            print("  Check the video address, and that this camera login is allowed to watch the video.")
            guide.show(guide.STREAM_DETAILS)
            other_url = "Type a different video address"
            keep = "Keep it anyway (not recommended: it won't stream until this is fixed)"
            pick = choose([other_url, RETRY_CAMERA, keep], str)
            if pick == other_url:
                cam["rtsp_url"] = config.strip_auth(ask("Video address (rtsp://...)", cam["rtsp_url"]))
                continue
            return "camera" if pick == RETRY_CAMERA else "ok"

        print(f"  Got the video: {camera.describe(streams)}")
        video = next((x for x in streams if x.get("codec_type") == "video"), None)
        codec = (video or {}).get("codec_name", "")
        if video and codec != "h264":
            name = {"hevc": "H.265 (HEVC)"}.get(codec, codec.upper())
            print(f"\n  This stream is {name}. Most viewers' browsers can't play it, so they'd see a black player.")
            print("  Fix: set this video to H.264 in the camera's web page (Video or Encode settings; also turn off")
            print("  any 'smart codec' or H.264+ option), or let CamAgent do it. For most people: choose 4, or 5 if")
            print("  the camera's setting can't be changed.")
            recheck, other = "I've changed the camera to H.264: check again", "Pick a different stream"
            keep = "Keep it anyway (not recommended: viewers won't see video until it's H.264)"
            auto = "Let CamAgent change the camera's video setting (recommended; needs the camera's admin login)"
            here = "Convert it on this computer (works with any camera; uses more of this computer's power)"
            pick = choose([recheck, other, keep, auto, here], str)
            if pick == here:
                cfg["_convert_codec"] = True
                break
            if pick == auto:
                _fix_stream(cam)
                continue
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

    cfg["_codec"] = codec
    has_audio = any(x.get("codec_type") == "audio" for x in streams)
    if has_audio and camera.audio_arrives(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"],
                                                                         cam["password"])) is False:
        print("  The camera says it has sound but isn't sending any (audio may be switched off in its")
        print("  settings). Sending video only for now. To add sound: turn the camera's audio on in its web")
        print("  page, then change this camera's settings in CamAgent again.")
        has_audio = False
    if has_audio:
        s["audio"] = yes("The camera also sends sound. Include it? Viewers will hear what the camera hears, and you're\n  responsible for what can be heard.", default=s["audio"])
    else:
        s["audio"] = False
    return "ok"


SIGN_IN = "Sign in to YonderView and pick the camera (easiest)"
PASTE = "Paste the settings block from the camera's Connection page"


def _use_block(cfg, block):
    p, s = cfg["platform"], cfg["stream"]
    parsed = config.parse_settings_block(block)
    latency = parsed.pop("_srt_latency_ms", None)
    p.update(parsed)
    if latency:
        s["srt_latency_ms"] = latency


def account_token(main_path=None, sign_in_if_needed=True):
    """(site, token, account name) for this computer's YonderView sign-in, signing in first if needed (and
    remembering it in camagent.toml). (site, None, "") if the person stops."""
    from . import link
    main = config.load_or_defaults(main_path)
    acct = main["account"]
    site = acct.get("site") or link.DEFAULT_SITE
    if acct.get("token"):
        return site, acct["token"], acct.get("name", "")
    if not sign_in_if_needed:
        return site, None, ""
    print("\nSigning in links this computer to your YonderView account, so CamAgent can fetch your")
    print("cameras' connection settings. You sign in on the website; CamAgent never sees your password.")
    token, name = link.sign_in(site)
    acct.update(token=token, name=name)
    config.save_main(main, main_path)
    print(f"  Signed in as {name or 'your account'}. (Unlink this computer any time from your profile on the website.)")
    return site, token, name


def sign_out(main_path=None):
    main = config.load_or_defaults(main_path)
    if not main["account"].get("token"):
        print("This computer isn't signed in.")
        return
    main["account"].update(token="", name="")
    config.save_main(main, main_path)
    print("Signed out on this computer. To remove it from your account completely, also unlink it on")
    print("yonderview.net: your profile > CamAgent computers.")


def _from_account(cfg, main_path=None):
    """Sign in (if needed), pick one of the account's cameras, and fill in its connection settings.
    True if done; False to fall back to pasting the settings block."""
    from . import link
    try:
        site, token, _ = account_token(main_path)
        if not token:
            return False
        try:
            cams = link.cameras(site, token)
        except link.NotSignedIn:
            print("  This computer's sign-in was unlinked or expired. Signing in again.")
            main = config.load_or_defaults(main_path)
            main["account"].update(token="", name="")
            config.save_main(main, main_path)
            site, token, _ = account_token(main_path)
            cams = link.cameras(site, token)
    except link.LinkError as e:
        print(f"  {e}")
        return False
    if not cams:
        print("  There are no cameras on your account yet. Add the camera on yonderview.net first (Host a")
        print("  camera) and wait for it to be approved, then choose Add a camera here again.")
        return False
    print("Which camera is this?")
    pick = choose(cams, lambda c: (f"{c['name']}" + (f" ({c['place']})" if c.get("place") else "")
                                   + (f"  - {c['org']}" if c.get("org") else "")
                                   + f"  [{c['slug']}]" + ("" if c.get("enabled", True) else "  (disabled)")),
                  allow_none_text="None of these: paste a settings block instead")
    if not pick:
        return False
    try:
        state, block, info = link.connect(site, token, pick["slug"])
        if state == "confirm":
            print(f"\n  {block}")
            print("  Only do this if this computer replaces the one sending it now (or that one is gone).")
            if not yes("Make new connection settings? The computer sending this camera now will stop.", default=False):
                return False
            state, block, info = link.connect(site, token, pick["slug"], replace=True)
    except link.LinkError as e:
        print(f"  {e}")
        return False
    _use_block(cfg, block)
    if info.get("max_height"):
        cfg["_server"] = {**(cfg.get("_server") or {}), "max_height": info["max_height"]}
    print(f"  Got the connection settings for '{pick['name']}' ({pick['slug']}).")
    return True


def step_convert(cfg, ffmpeg):
    """A camera above 1080p (or sending H.265): convert to 1080p H.264 here, or send it as it is."""
    from . import convert
    s, cam = cfg["stream"], cfg["camera"]
    height = int((cfg.get("_measure") or {}).get("height") or 0)
    h265 = bool(cfg.get("_convert_codec")) or cfg.get("_codec") not in (None, "", "h264")
    if height <= convert.TARGET_HEIGHT and not h265:
        s["convert"] = "off"
        return
    heading("Optional: making it 1080p")
    what = f"{height}p" if height else "an unknown size"
    print(f"This camera sends {what}{' in H.265' if h265 else ''}. YonderView takes 1080p in H.264 (bigger is a paid")
    print("option YonderView must approve). CamAgent can convert the video to 1080p H.264 on this computer before")
    print("sending it; the camera itself isn't changed. A mini PC with an Intel graphics chip does this easily;")
    print("otherwise it takes a strong processor.")
    here = "Convert to 1080p H.264 on this computer (recommended)"
    source = f"Send the camera's own {what} (only if YonderView approved it for this camera)"
    camera_side = "Change the camera's own setting to 1080p H.264 instead (CamAgent can do it for you)"
    options = [here, camera_side] + ([] if h265 else [source])
    pick = choose(options, str)
    if pick == source:
        s["convert"] = "off"
        return
    if pick == camera_side:
        s["convert"] = "off"
        _fix_stream(cam)
        return
    if not ffmpeg:
        print("  ffmpeg (the video tool CamAgent uses) isn't installed, so CamAgent can't convert. Install it,")
        print("  then change this camera's settings again.")
        s["convert"] = "off"
        return
    print("\nChecking what this computer can use to convert video (up to a minute)...")
    results = convert.detect(ffmpeg, show=print)
    method = convert.best(results)
    m = cfg.get("_measure") or {}
    width = int(m.get("width") or (height * 16 // 9 if height else 3840))
    fps = float(m.get("actual_fps") or m.get("declared_fps") or 15)
    room = convert.headroom(dict(results).get(method), width, height or 2160, fps)
    url = config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"])
    scale = not height or height > convert.TARGET_HEIGHT
    print(f"\nUsing {convert.LABELS[method]}. Trying it on this camera's video for a few seconds...")
    speed = convert.speed_on_camera(ffmpeg, url, method, s.get("bitrate_kbps"), scale=scale)
    if speed is None and method != "x264":
        print(f"  {convert.LABELS[method]} couldn't convert this camera's video; trying the processor (CPU) instead.")
        method = "x264"
        room = convert.headroom(dict(results).get("x264"), width, height or 2160, fps)
        speed = convert.speed_on_camera(ffmpeg, url, method, s.get("bitrate_kbps"), scale=scale)
    if speed is None:
        print("  Converting didn't work on this camera's video, so it will be sent as it is for now.")
        print('  To try again later: open CamAgent > Change or remove a camera > Change a camera\'s settings.')
        s["convert"] = "off"
        return
    if speed >= 0.95 and room >= 1.5:
        print(f"  It works, with room to spare (about {room}x what this camera needs).")
    else:
        why = (f"it couldn't keep up with the camera ({speed}x real time; 1x is needed)" if speed < 0.95
               else f"it has little spare capacity (about {room}x what this camera needs; 1.5x or more is comfortable)")
        print(f"  This computer may struggle: {why}. Video could stutter on busy scenes (rain, wind in trees).")
        print("  A mini PC with an Intel graphics chip handles this easily. Or set the camera itself to 1080p.")
        if not yes("Convert here anyway?", default=False):
            s["convert"] = "off"
            return
    s.update(convert="1080p", encoder=method, source_height=height)
    print(f"  Saved: converting to 1080p with {convert.LABELS[method]}.")


def step_platform(cfg, main_path=None):
    p, s = cfg["platform"], cfg["stream"]
    heading("Step 3 of 4: Your YonderView account")
    guide.show(guide.SERVER)
    typed = "Type the values one by one (only if support asks you to)"
    how = choose([SIGN_IN, PASTE, typed], str)
    if how == SIGN_IN and _from_account(cfg, main_path):
        p["mqtt_port"] = int(p["mqtt_port"])
        return
    lines = []
    if how != typed:
        print("Paste the settings block you copied from yonderview.net (in a Windows window, right-click to paste;")
        print("in a Linux terminal, Ctrl+Shift+V). Then press Enter once more on the empty line.")
        while True:
            line = input()
            if not line.strip():
                break
            lines.append(line)
    if lines:
        _use_block(cfg, "\n".join(lines))
        print(f"  Got the settings for camera '{p['camera_id']}'.")
    else:
        print("Copy each value from the settings block on your camera's page on yonderview.net.")
        p["camera_id"] = ask("Camera ID", p["camera_id"])
        p["ingest_host"] = ask("Video server (ingest host)", p["ingest_host"])
        p["srt_password"] = ask_secret("Video password (SRT password)", p["srt_password"])
        p["srt_passphrase"] = ask_secret("Video encryption key (SRT passphrase)", p["srt_passphrase"])
        p["mqtt_host"] = ask("Camera-controls server (MQTT host)", p["mqtt_host"] or p["ingest_host"])
        p["mqtt_password"] = ask_secret("Camera-controls password (MQTT password)", p["mqtt_password"])
    p["mqtt_port"] = ask_int("Camera-controls port (MQTT port)", p["mqtt_port"])


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
    if not p.get("mqtt_tls") and p.get("mqtt_host") not in ("localhost", "127.0.0.1", "::1"):
        return False, ("these settings would connect without encryption (mqtt_tls is off), so CamAgent won't send its "
                       "password. Get fresh settings from yonderview.net (sign in again, or make a new settings block)")
    c.username_pw_set(p["camera_id"], p["mqtt_password"])
    if p.get("mqtt_tls"):
        c.tls_set()
    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(p["mqtt_host"], int(p["mqtt_port"]), keepalive=10)
    except Exception as e:
        return False, (f"can't reach YonderView at {p['mqtt_host']}:{p['mqtt_port']} ({e}). Check this computer's "
                       f"internet connection; a firewall may be blocking port {p['mqtt_port']}")
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
        return False, "no answer from YonderView (check this computer's internet connection)"
    if rc.is_failure:
        return False, (f"YonderView refused this computer's login ({rc}). Sign in again, or make new "
                       "connection settings on the camera's page")
    return True, "connected"


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
        hint = ("from an administrator Command Prompt or PowerShell (or the CamAgent shortcut in the Start menu)"
                if os.name == "nt" else "with sudo in front (for example: sudo camagent)")
        raise SystemExit(f"CamAgent can't save its settings ({path}) without administrator rights. Run it again {hint}.")


def _tools(cfg):
    from . import prereqs
    return prereqs.check_and_fix(yes, cfg["stream"].get("ffmpeg", ""))


def _walk(cam, ffmpeg, ffprobe, main_path=None):
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
    step_convert(cam, ffmpeg)
    step_platform(cam, main_path)
    if ffmpeg:
        cam["stream"]["ffmpeg"] = ffmpeg           # full path, so the service finds it too
    if not cam["platform"].get("camera_id"):
        raise SystemExit("No camera ID was given, so nothing was saved. Try again, and sign in or paste the settings "
                         "block from yonderview.net when asked.")


def _finish(cam, main_path, others):
    cid = cam["platform"]["camera_id"]
    clash = next((o for o in others if o["platform"]["camera_id"] == cid), None)
    if clash and not yes(f"Camera '{cid}' is already set up on this computer. Replace its settings?", default=False):
        raise SystemExit("Cancelled. Nothing was saved.")

    while True:
        heading("Step 4 of 4: Testing the connection")
        ok, msg = test_mqtt(cam)
        print(f"  YonderView: {msg}")
        if ok:
            limit = (cam.get("_server") or {}).get("max_height")
            height = (cam.get("_measure") or {}).get("height")
            if cam["stream"].get("convert", "off") != "off":
                height = min(height or 1080, 1080)                # what's sent: the converted 1080p
            if limit and height and height > limit:
                print(f"\n  This video is {height}p, but YonderView's limit for this camera is {limit}p,")
                print("  so YonderView would decline (refuse) it as soon as it connects. Set the camera's video to "
                      f"{limit}p or lower in its web page, or let CamAgent convert it to 1080p on this computer.")
                other, recheck = "Pick a different video from the camera", "I've changed the camera: check again"
                keep = "Save anyway (not recommended: YonderView will decline it until it's fixed)"
                auto = "Let CamAgent change the camera's setting (needs the camera's admin login)"
                pick = choose([other, recheck, keep, auto], str)
                if pick == auto:
                    _fix_stream(cam, max_height=limit)
                    pick = recheck
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
        print("  Check this computer's internet connection first. If that's fine, get the YonderView settings again.")
        again, anyway = "Enter the YonderView details again", "Save anyway (not recommended: it won't connect until this is fixed)"
        pick = choose([again, anyway, "Cancel without saving"], str)
        if pick == again:
            step_platform(cam, main_path)
            continue
        if pick == anyway:
            break
        raise SystemExit("Cancelled. Nothing was saved.")

    if clash and Path(clash["_source"]) != Path(cam.get("_source") or ""):
        config.remove_camera(clash, main_path)
    saved = config.save_camera(cam, main_path)
    print(f"\nSaved camera '{cid}'.  ({saved})")
    _apply(main_path)
    guide.show(guide.DONE)


def _apply(main_path):
    _ask_auto_update(main_path)
    if service.is_installed():
        if yes("Restart CamAgent to use the new settings?", default=True):
            service.restart()
    elif yes("Start CamAgent now? It keeps sending your camera in the background and starts by itself\n  whenever this computer turns on.", default=True):
        service.install(main_path)
    else:
        print('To start it later: open CamAgent (Windows: the CamAgent shortcut in the Start menu; Linux: type')
        print('sudo camagent) and choose "Start CamAgent". Until then, the camera isn\'t sent to YonderView.')


def _ask_auto_update(main_path):
    """Asked once per computer. Automatic updates install new releases nightly and roll back if one fails."""
    main = config.load_or_defaults(main_path)
    if main["update"].get("asked"):
        return
    print("\nAutomatic updates install new CamAgent versions overnight (around 3 AM), check the cameras come")
    print("back, and return to the previous version if they don't.")
    main["update"]["auto"] = yes("Keep CamAgent up to date automatically?", default=True)
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
            raise SystemExit(f"There's no camera called '{camera_id}' on this computer. To see their names, type: camagent list")
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
    print(f"CamAgent setup for camera '{cam['platform']['camera_id']}'. "
          "Press Enter to keep the value shown in [brackets].")
    original_id = cam["platform"]["camera_id"]
    ffmpeg, ffprobe = _tools(cam)
    _walk(cam, ffmpeg, ffprobe, main_path)
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
        print(f"Adding camera #{n} on this computer. Press Enter to keep a value shown in [brackets].")
        print("You'll need this camera's IP address and login, and to sign in to YonderView (or its settings block).")
    ffmpeg, ffprobe = _tools(cam)
    _walk(cam, ffmpeg, ffprobe, main_path)
    _finish(cam, main_path, cams)


def remove(config_path=None, camera_id=None):
    """camagent remove <camera>: stop streaming a camera from this machine and delete its settings."""
    main_path = Path(config_path or config.default_config_path())
    _check_writable(main_path)
    _, cams, _ = config.load_all(main_path)
    if not cams:
        raise SystemExit("No cameras are set up on this computer, so there's nothing to remove.")
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    cid = cam["platform"]["camera_id"]
    if not yes(f"Remove camera '{cid}' from this computer? Its settings here will be deleted.", default=False):
        raise SystemExit("Cancelled. Nothing was removed.")
    config.remove_camera(cam, main_path)
    print(f"Removed '{cid}'. It stays on yonderview.net; this computer just stops sending it.")
    if service.is_installed() and len(cams) > 1:
        if yes("Restart CamAgent so it stops sending it now?", default=True):
            service.restart()
    elif service.is_installed():
        print("That was the last camera on this computer. CamAgent still runs in the background (with nothing to")
        print("send). To stop it completely, type this as administrator: camagent uninstall-service")


def list_cameras(config_path=None):
    """camagent list: every camera on this machine, with live status when the service is running."""
    import json
    import time
    main_path = Path(config_path or config.default_config_path())
    try:
        _, cams, problems = config.load_all(main_path)
    except PermissionError:
        hint = ("from an administrator Command Prompt or PowerShell (or the CamAgent shortcut in the Start menu)"
                if os.name == "nt" else "with sudo in front (for example: sudo camagent list)")
        raise SystemExit(f"CamAgent can't read its settings without administrator rights. Run it again {hint}.")
    live = {}
    try:
        data = json.loads(config.status_path().read_text(encoding="utf-8"))
        if time.time() - data.get("updated", 0) < 60:
            live = data.get("cameras", {})
    except (OSError, ValueError):
        pass
    if not cams:
        print('No cameras are set up on this computer yet. Choose "Add a camera" in the menu.')
    for c in cams:
        cid = c["platform"]["camera_id"]
        where = config.strip_auth(c["camera"].get("rtsp_url", "")) or c["camera"].get("host", "")
        print(f"{cid}")
        print(f"    camera: {where or '(not set)'}{'  [PTZ]' if c['camera'].get('ptz') else ''}"
              f"{'  [sending turned off]' if not c['stream'].get('enabled', True) else ''}")
        st = live.get(cid)
        if st is None:
            print("    status: " + ("CamAgent isn't running, so nothing is being sent (choose Start or Restart CamAgent)"
                                 if not live else "not sending (\"Check for problems\" shows why)"))
        else:
            video = (f"streaming {st['fps']:.0f} fps, {st['bitrate_kbps']:.0f} kbps" if st.get("streaming")
                     else "NOT streaming" + (f" ({st['last_error'][:80]})" if st.get("last_error") else ""))
            parts = [video, "connected to YonderView" if st.get("mqtt") else "NOT connected to YonderView"]
            if st.get("ptz"):
                parts.append(f"PTZ {st['ptz']}")
            if st.get("position") in ("real", "fake"):
                parts.append("PTZ position " + ("reported" if st["position"] == "real"
                                                else "not reported (the camera doesn't say where it's pointing)"))
            wx = st.get("weather")
            if wx:
                parts.append(f"weather {wx['kind']}: " + (f"last reading {wx['age_s']}s ago" if wx.get("age_s") is not None
                                                          else "no reading yet"))
            print("    status: " + "; ".join(parts))
        if st and st.get("declined"):
            print(f"    DECLINED by YonderView: {st['declined']}")
        if c.get("_legacy"):
            print("    (saved in an older format; it works, and changing its settings updates it)")
    for p in problems:
        print(f"PROBLEM: {p}")


# ---------- weather station (beta) ----------

def _summary(r):
    bits = []
    if r.get("temp_c") is not None:
        bits.append(f"{r['temp_c'] * 9 / 5 + 32:.0f}°F")
    if r.get("wind_ms") is not None:
        bits.append(f"wind {r['wind_ms'] * 2.23694:.0f} mph")
    if r.get("humidity") is not None:
        bits.append(f"{r['humidity']:.0f}% humidity")
    if r.get("pressure_hpa") is not None:
        bits.append(f"{r['pressure_hpa'] * 0.02953:.2f} inHg")
    return ", ".join(bits) or "a reading with no common values"


def weather_station(config_path=None, camera_id=None):
    """Add, change or remove the local weather station for one camera (BETA)."""
    from . import weather
    main_path = Path(config_path or config.default_config_path())
    _check_writable(main_path)
    _, cams, _ = config.load_all(main_path)
    if not cams:
        raise SystemExit("No cameras are set up on this computer yet. Add a camera first, then its weather station.")
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    w = cam["weather"]
    heading("Weather station (beta)")
    print("CamAgent can read a weather station on this network and show its conditions on the camera's page.")
    print("Supported: Tempest, Davis WeatherLink Live, Ecowitt gateways (GW1100, GW2000 and similar).")
    if w.get("kind"):
        print(f"\nThis camera uses: {w['kind']} {w.get('host') or w.get('serial') or ''}".rstrip())
        if yes("Remove it?", default=False):
            cam["weather"] = dict(config.DEFAULTS["weather"])
            config.save_camera(cam, main_path)
            print("Weather station removed from this camera.")
            _apply(main_path)
            return
    kinds = [("tempest", "Tempest"), ("weatherlink", "Davis WeatherLink Live"), ("ecowitt", "Ecowitt gateway")]
    kind = choose(kinds, lambda k: k[1], allow_none_text="Cancel")
    if kind is None:
        return
    kind = kind[0]
    new = dict(config.DEFAULTS["weather"], kind=kind)
    if kind == "tempest":
        print("Listening for Tempest stations on this network (15 seconds)...")
        try:
            found = weather.tempest_discover(15)
        except RuntimeError as e:
            raise SystemExit(str(e))
        if not found:
            raise SystemExit("No Tempest was heard. Its hub must be on the same network as this computer: check the hub's "
                             "Wi-Fi in the Tempest app, then try again.")
        serials = sorted(found)
        new["serial"] = serials[0] if len(serials) == 1 else choose(serials, lambda s: f"Station {s}")
        print(f"Found Tempest {new['serial']}.")
        elev = ask("The station's elevation in feet, used to show sea-level pressure (press Enter to leave pressure out)", "")
        try:
            new["elevation_m"] = round(float(elev) * 0.3048) if elev else 0
        except ValueError:
            new["elevation_m"] = 0
    else:
        new["host"] = ask("The station's IP address on this network (shown in its app or your router's list of devices)",
                          w.get("host", ""))
        if not new["host"]:
            raise SystemExit("No address given, so nothing was changed.")
    print("Reading the station (up to a minute for Tempest)...")
    reader = weather.Reader(new)
    try:
        r = reader.read(wait=70)
        uid = reader.station_id()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Couldn't read the station: {e}. Check its IP address, and that it's on this network.")
    finally:
        reader.close()
    if not r:
        raise SystemExit("No reading arrived from the station. Check it's on and on this network, then try again.")
    print(f"Got it ({uid}): {_summary(r)}")
    new["name"] = ask("A name for this station (shown on the camera page)", w.get("name") or "Weather station")
    cam["weather"] = new
    config.save_camera(cam, main_path)
    print("Saved. Conditions appear on the camera's page within a minute after CamAgent restarts.")
    _apply(main_path)
