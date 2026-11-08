"""Interactive setup: find the camera, pick a stream, enter server details, test, save."""
import getpass
import json
import os
import subprocess
import time
import threading
from pathlib import Path

from . import camera, config, guide, service
from . import text as words


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
            print("  Type a number, like 80. Or press Enter to keep the number in [brackets].")


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
        v = input(words.P_TYPE_NUMBER).strip()
        if allow_none_text and v == "0":
            return None
        if v.isdigit() and 1 <= int(v) <= len(items):
            return items[int(v) - 1]
        print(f"  Type a number from 1 to {len(items)}" + (", or 0." if allow_none_text else "."))


TRY_FIX = words.TRY_FIX


def _try_fix_stream(cam, max_height=1080):
    """Change the camera's video over ONVIF (H.264, at most max_height, 2-second keyframes) with the login CamAgent
    already uses. CamAgent never asks for the camera's admin login: most cameras only let an administrator change
    this, so it often doesn't work, and then the person changes it in the camera's web page. True if it changed."""
    from .controls import Controls, NotAllowed, OnvifSession
    print("  CamAgent is asking your camera to change its video setting...")
    had_sound = camera.sound_check(cam) if cam["stream"].get("audio") else None
    try:
        s = OnvifSession(cam)
        s._connect()
        changes = Controls(s).stream_fixes(apply=True, max_height=max_height)
    except NotAllowed:
        print("  Your camera didn't allow it. Most cameras only let an administrator change the video setting,")
        print("  and CamAgent never uses the camera's admin login. Change it yourself in the camera's web page.")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  CamAgent couldn't change the camera's video setting ({camera.friendly_error(e)}).")
        print("  Change it yourself in the camera's web page.")
        return False
    if not changes:
        print("  Your camera didn't need any change.")
        return True
    print("  Your camera changed: " + "; ".join(changes) + ".")
    print("  CamAgent is waiting a few seconds for the camera's video to restart...")
    time.sleep(8)
    if had_sound and camera.sound_check(cam) is False:
        print(camera.AUDIO_LOST)
    return True


def _measure_again(cam):
    cam["_measure"] = camera.measure_stream(
        camera.find_ffprobe(camera.find_ffmpeg(cam["stream"].get("ffmpeg", ""))),
        config.rtsp_with_auth(cam["camera"]["rtsp_url"], cam["camera"]["username"], cam["camera"]["password"]))


RULE = "-" * 64            # plain dashes: they show the same in every Windows and Linux window


MAX_LOGIN_TRIES = 3
_login_state = {"tries": 0, "warned": False}


def _reset_login_tries():
    """A new setup run starts counting the camera's rejected logins from zero."""
    _login_state.update(tries=0, warned=False)


def _warn_lockout():
    """One line before the first login try of a setup run: cameras lock people out after a few wrong tries."""
    if not _login_state["warned"]:
        _login_state["warned"] = True
        print("  Careful: cameras lock you out after a few wrong tries. Type the camera's login carefully.")


def _login_rejected():
    """Count one rejected login. After MAX_LOGIN_TRIES in one setup run, CamAgent stops (no more tries)."""
    _login_state["tries"] += 1
    if _login_state["tries"] >= MAX_LOGIN_TRIES:
        _stop_for_lockout()


def _stop_for_lockout():
    raise SystemExit(f"The camera has refused this login {MAX_LOGIN_TRIES} times. CamAgent stops trying so your "
                     "camera doesn't lock you out. Check the username and password in the camera's web page. "
                     "Then run CamAgent setup again later. Some cameras let you try again after a few minutes.")


def heading(text):
    """A new section of setup: a blank line and a line of dashes above and below, so each step stands apart."""
    print(f"\n\n{RULE}\n  {text}\n{RULE}")


# ---------- steps ----------

def step_find_camera(cfg):
    cam = cfg["camera"]
    heading(words.H_STEP1)
    guide.show(guide.CAMERA)
    if yes(words.P_SEARCH, default=not cam["host"]):
        print(words.P_SEARCHING)
        try:
            found = camera.discover()
        except OSError as e:
            found = []
            print(f"  The search didn't work ({e}). Type the camera's address below instead.")
        if found:
            print(words.P_FOUND)
            pick = choose(found, lambda c: f"{c['host']}:{c['port']}  {c['name']} {c['hardware']}".rstrip(),
                          allow_none_text=words.P_NONE_OF_THESE)
            if pick:
                cam["host"], cam["onvif_port"] = pick["host"], pick["port"]
        else:
            print("  No cameras answered. Check that your camera is on and on the same network as this computer.")
            print("  Also check that ONVIF (the camera's control and setup standard) is turned on in the camera.")
            print("  If your Wi-Fi has a guest network, put the camera and this computer on the same one.")
            print("  You can still type the camera's IP address below.")
    cam["host"] = ask(words.P_IP, cam["host"])
    cam["onvif_port"] = ask_int(words.P_ONVIF_PORT, cam["onvif_port"])
    print(words.P_LOGIN_INTRO_1)
    print(words.P_LOGIN_INTRO_2)
    print(words.P_LOGIN_INTRO_3)
    keep = words.P_KEEP_LOGIN.format(username=cam["username"]) if cam.get("username") else None
    how = choose([o for o in (keep, MAKE_LOGIN, OWN_LOGIN) if o], str)
    if how == keep:
        return
    if how == MAKE_LOGIN and _make_login(cam):
        return
    print("  Enter the login you use for the camera's app or web page. Some cameras also need a separate")
    print("  user for ONVIF (the camera's control and setup standard).")
    cam["username"] = ask(words.P_USERNAME, cam["username"])
    cam["password"] = ask_secret(words.P_PASSWORD, cam["password"])


MAKE_LOGIN = words.MAKE_LOGIN.format(login_name=camera.LOGIN_NAME)
OWN_LOGIN = words.OWN_LOGIN
ADD_LOGIN_STEPS = words.ADD_LOGIN_STEPS


def _make_login(cam):
    """Setup's "Make up a login": CamAgent picks the name and a new 16-character password and shows them; the host
    adds them on the camera (CamAgent never uses the camera's admin login). Returns False to enter one instead."""
    name, pw = camera.LOGIN_NAME, camera.new_password()
    print()
    print(f"    Username:  {name}")
    print(f"    Password:  {pw}")
    print()
    print(ADD_LOGIN_STEPS)
    print("Type them exactly (capital letters matter). CamAgent saves them on this computer.")
    print("You don't need to keep a copy.")
    _warn_lockout()
    while True:
        input("Press Enter after you add the login on the camera...")
        print("  CamAgent is checking the login...")
        try:
            camera.onvif_connect(cam["host"], cam["onvif_port"], name, pw).devicemgmt.GetDeviceInformation()
            break
        except Exception as e:  # noqa: BLE001
            print(f"  The camera didn't accept it yet: {camera.friendly_error(e)}")
            print("  (Each try counts toward the camera's lockout. Check that the camera saved the login first.)")
            if "login rejected" in camera.friendly_error(e):
                _login_rejected()
            fixed = "I've fixed it. Check again"
            if choose([fixed, "Enter a different login instead"], str) != fixed:
                return False
    cam["username"], cam["password"] = name, pw
    print(f"  The login works. CamAgent signs in to your camera as \"{name}\".")
    _exact_needs_web_user(cam, name, pw)
    return True


def _exact_needs_web_user(cam, name, pw):
    """Amcrest and Dahua keep ONVIF users apart from their normal ones; the exact degrees (exact.py) need a normal
    user. If the new login is refused there, say so (with the login again) so the host can add it."""
    from . import exact
    try:
        maker = camera.onvif_connect(cam["host"], cam["onvif_port"], name, pw).devicemgmt.GetDeviceInformation()
        if not exact.is_dahua(getattr(maker, "Manufacturer", "")):
            return
    except Exception:  # noqa: BLE001
        return
    r = exact.Reader(cam["host"], name, pw)
    if r.read() is not None or r.off != exact.REFUSED_NOTE:
        return
    print("  Optional: YonderView can show exactly which way your camera points on the map. To turn that on,")
    print("  add the same login as a regular user in the camera's web page (Setup > System > Account > Users > Add):")
    print(f"    username {name}   password {pw}   (rights: Operator, or a User with PTZ allowed)")
    print("  You can skip this. Everything else works, and the map shows a close guess of the direction.")


def _move_test(cam):
    """Setup's move-and-read test: turns the camera a little and back, and says whether its position follows."""
    if not yes("Test the controls now? Your camera will turn a little and back (about 5 seconds)", default=True):
        return
    try:
        r = camera.ptz_move_test(cam["host"], cam["onvif_port"], cam["username"], cam["password"],
                                 cam.get("ptz_profile", ""))
    except Exception as e:  # noqa: BLE001
        print(f"  The camera didn't accept the move: {camera.friendly_error(e)}")
        print("  If the camera's login is limited, it may need Operator rights to turn. You can change that later.")
        return
    if r["follows"]:
        b, a = r["before"], r["after"]
        print(f"  It works. The camera turned (pan {b['pan']:+.3f} to {a['pan']:+.3f}) and went back. YonderView can")
        print("  show which way the camera points on the map.")
    elif r["before"] is None or r["after"] is None:
        print("  The camera took the move, but it didn't say where it points. The map shows the camera's")
        print("  direction from presets you pick on YonderView instead.")
    else:
        print("  The camera took the move, but reported the same position before and after. Did it turn?")
        print("  If it did, the camera reports a fixed position. The map shows its direction from presets")
        print("  you pick on YonderView instead.")


RETRY_CAMERA = "Enter the camera's address and login again, then try again"


def _is_h264(encoding):
    return str(encoding or "").upper().replace(".", "").replace("-", "") in ("H264", "AVC")


def _codec_flag(encoding):
    e = str(encoding or "").upper().replace(".", "").replace("-", "")
    if not e or e in ("H264", "AVC"):
        return ""            # blank = unknown; the stream check after picking will tell
    if e in ("H265", "HEVC"):
        return "  [H.265: won't play in most browsers]"
    return "  [won't play in browsers]"
NO_ONVIF = "Continue without ONVIF (CamAgent tries to find your camera's video another way)"


def step_pick_stream(cfg, ffprobe):
    """Returns False if the user wants to go back and re-enter the camera's details."""
    cam = cfg["camera"]
    heading(words.H_STEP2)
    guide.show(guide.STREAM)
    if _login_state["tries"] >= MAX_LOGIN_TRIES:
        _stop_for_lockout()
    _warn_lockout()
    print(words.P_ASKING_CAMERA)
    try:
        info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
        if info.get("login_level") == "Administrator":
            print("  (This is an administrator login on the camera. It works. A separate Operator login for CamAgent "
                  "is safer, and you can switch to one later.)")
    except Exception as e:
        info = None
        msg = camera.friendly_error(e)
        if "login rejected" in msg:
            _login_rejected()
            print("  The camera refused the username or password. Try signing in to the camera's web page with")
            print("  them to be sure. Many cameras also need a separate user for ONVIF (the camera's control and")
            print("  setup standard). Make one in that web page:")
        else:
            print("  CamAgent couldn't talk to the camera. Check that:")
            print("    1. The camera is powered on and on the same network as this computer.")
            print("    2. The address (and port) you typed are right.")
            print("    3. ONVIF is turned on in the camera's web page:")
        guide.show(guide.ONVIF_USERS)
        if choose([RETRY_CAMERA, NO_ONVIF], str) == RETRY_CAMERA:
            return False

    if info and info["profiles"]:
        print(f"  Connected to your {info['manufacturer']} {info['model']}.".replace("  .", "."))
        profiles = [p for p in info["profiles"] if p["rtsp_url"]]
        profiles.sort(key=lambda p: not _is_h264(p["encoding"]))      # browser-friendly streams first
        if profiles:
            print("Which of the camera's videos do you want to send? Choose the biggest one marked H.264.")
            print("It is usually the first, often called \"main\", with a size like 1920x1080.")
            print("Skip any marked \"won't play\".")
            pick = choose(profiles, lambda p: (
                f"{p['name']}: {p['encoding'] or '?'} {p['width']}x{p['height']}"
                f"{' @ ' + str(p['fps']) + ' fps' if p['fps'] else ''}"
                f"{'  [PTZ]' if p['ptz'] else ''}{_codec_flag(p['encoding'])}\n       {p['rtsp_url']}"),
                allow_none_text="None of these. I'll type the video address myself")
            if pick:
                cam["rtsp_url"] = pick["rtsp_url"]
                cam["fps_configured"] = pick.get("fps") or 0
            has_ptz = any(p["ptz"] for p in info["profiles"])
            ptz_profile = next((p["token"] for p in info["profiles"] if p["ptz"]), "")
            if has_ptz:
                cam["ptz"] = yes("Your camera can turn and zoom by remote control (PTZ). Allow that on YonderView?\n"
                                 "  Only people you give control to (and YonderView staff) can move it.", default=True)
                cam["ptz_profile"] = ptz_profile if cam["ptz"] else ""
                ok, text = camera.describe_position(info.get("position"))
                if cam["ptz"] and ok is not None:
                    print(("  Position: " if ok else "  Note: ") + text)
                if cam["ptz"]:
                    _move_test(cam)
            else:
                print("  Your camera can't turn or zoom by remote control (no PTZ), so those controls stay off.")
                cam["ptz"], cam["ptz_profile"] = False, ""
            if pick:
                return True

    if ffprobe and yes("Do you want CamAgent to try the usual video addresses for popular camera brands?\n"
                       "  (About a minute; recommended)", default=True):
        def progress(vendor, path):
            print(f"  Trying {vendor:<18} {path}")
        found = camera.scan_rtsp_paths(ffprobe, cam["host"], cam["username"], cam["password"], progress=progress)
        if found:
            pick = choose(found, lambda f: f"{f['rtsp_url']}  ({camera.describe(f['streams'])})",
                          allow_none_text="None of these. I'll type the video address myself")
            if pick:
                cam["rtsp_url"] = pick["rtsp_url"]
                if not info:
                    cam["ptz"] = False
                return True
        else:
            print("  None of the usual addresses worked. Type your camera's video address below.")

    guide.show(guide.RTSP_PATHS.format(ip=cam["host"] or "IP"))
    cam["rtsp_url"] = config.strip_auth(
        ask("The camera's video address. It starts with rtsp://. Leave out the username and password; CamAgent adds them.", cam["rtsp_url"]))
    if not info:
        cam["ptz"] = yes("Can your camera turn and zoom by remote control (PTZ)?", default=False)
    return True


def step_check_stream(cfg, ffprobe):
    """Returns "ok", "camera" (go back to the camera's address/login) or "stream" (pick another stream)."""
    cam, s = cfg["camera"], cfg["stream"]
    if not ffprobe:
        print("\n(Part of the video tool ffmpeg (called ffprobe) is missing, so CamAgent can't check the video now.")
        print(" Install or reinstall ffmpeg. Then change this camera's settings again to check the video.)")
        return "ok"
    while True:
        print("\nCamAgent is checking your camera's video (up to 30 seconds)...")
        streams = camera.probe(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
        if not streams:
            print(f"  CamAgent couldn't get video from {config.redact(cam['rtsp_url']) if cam['rtsp_url'] else '(no video address)'}.")
            print("  Check the video address. Also check that the camera's login is allowed to watch the video.")
            guide.show(guide.STREAM_DETAILS)
            other_url = "Type a different video address"
            keep = "Keep it anyway (not recommended: CamAgent can't send video until you fix this)"
            pick = choose([other_url, RETRY_CAMERA, keep], str)
            if pick == other_url:
                cam["rtsp_url"] = config.strip_auth(ask("Camera's video address (rtsp://...)", cam["rtsp_url"]))
                continue
            return "camera" if pick == RETRY_CAMERA else "ok"

        print(f"  CamAgent got the video: {camera.describe(streams)}")
        video = next((x for x in streams if x.get("codec_type") == "video"), None)
        codec = (video or {}).get("codec_name", "")
        if video and codec != "h264":
            name = {"hevc": "H.265 (HEVC)"}.get(codec, codec.upper())
            print(f"\n  This video is {name}. Most viewers' browsers can't play it, so they would see a black player.")
            recheck, other = "I've changed the camera to H.264. Check again", "Pick a different video from the camera"
            keep = "Keep it anyway (not recommended: viewers see no video until it's H.264)"
            here = "Convert the video on this computer (works with any camera; uses more of this computer's power)"
            print("  Change it yourself in the camera's web page: find the Video (or Encode) settings and set")
            print("  the encoding (Encode Mode) to H.264. Save. Also turn off any 'smart codec' or H.264+ option.")
            print(f"  Then choose \"{recheck}\".")
            print(f"  If you can't change the camera's setting, choose \"{here}\".")
            pick = choose([recheck, other, keep, here, TRY_FIX], str)
            if pick == TRY_FIX:
                _try_fix_stream(cfg)
                continue                                          # check the video again either way
            if pick == here:
                cfg["_convert_codec"] = True
                break
            if pick == recheck:
                continue
            if pick == other:
                return "stream"
        break

    print("  CamAgent is measuring what your camera actually sends (a few seconds)...")
    m = camera.measure_stream(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
    cfg["_measure"] = m
    tips = camera.stream_advice(m)
    if m.get("actual_fps"):
        print(f"  Your camera: {camera.stream_summary(m)}." + ("" if tips else " That meets the requirements."))
    for tip in tips:
        print(f"  Note: {tip}")

    cfg["_codec"] = codec
    has_audio = any(x.get("codec_type") == "audio" for x in streams)
    if has_audio and camera.audio_arrives(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"],
                                                                         cam["password"])) is False:
        print("  The camera says it has sound, but it isn't sending any. Audio may be turned off in its")
        print("  settings. CamAgent will send video only for now. To add sound, turn on the camera's audio")
        print("  in its web page. Then change this camera's settings in CamAgent again.")
        has_audio = False
    if has_audio:
        s["audio"] = yes("Your camera also sends sound. Do you want to include it? Viewers will hear what the camera\n"
                         "  hears, and you're responsible for what they can hear.", default=s["audio"])
    else:
        s["audio"] = False
    return "ok"


SIGN_IN = words.SIGN_IN
PASTE = words.PASTE


def _use_block(cfg, block):
    """Fill in the camera's connection settings from a settings block. ValueError (plain words) if it's refused."""
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
    print(words.SIGNIN_INTRO_1)
    print(words.SIGNIN_INTRO_2)
    token, name = link.sign_in(site)
    acct.update(token=token, name=name)
    config.save_main(main, main_path)
    print(words.SIGNIN_DONE_1.format(name=name or words.S_ACCOUNT_FALLBACK))
    print(words.SIGNIN_DONE_2)
    return site, token, name


def sign_out(main_path=None):
    """Revoke the sign-in on the website (0.17), then forget it here (also when the website can't be reached)."""
    from . import link
    main = config.load_or_defaults(main_path)
    acct = main["account"]
    if not acct.get("token"):
        print(words.NOT_SIGNED_IN)
        return
    site = acct.get("site") or link.DEFAULT_SITE
    revoked = link.sign_out(site, acct["token"])
    acct.update(token="", name="")
    config.save_main(main, main_path)
    if revoked:
        print(words.SIGNOUT_DONE)
    else:
        print("You're signed out on this computer, but CamAgent couldn't reach YonderView to cancel the sign-in there.")
        print(f"To be sure, open {site.rstrip('/')}/link. Unlink this computer in the \"Your computers\" list.")


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
            print("  This computer was signed out (you unlinked it on the website, or it went unused for 90 days).")
            print("  You need to sign in again.")
            main = config.load_or_defaults(main_path)
            main["account"].update(token="", name="")
            config.save_main(main, main_path)
            site, token, _ = account_token(main_path)
            cams = link.cameras(site, token)
    except link.LinkError as e:
        print(f"  {e}")
        return False
    if not cams:
        print("  Your account has no approved cameras yet. CamAgent can only connect a camera after YonderView")
        print("  approves it.")
        print("  To add a new camera, go to yonderview.net and send a camera request (Host a camera).")
        print('  On the request\'s page, click "Add the camera now". CamAgent on this computer checks the camera')
        print("  from there. CamAgent connects the camera by itself after YonderView approves the request.")
        return False
    print("Which camera do you want to connect?")
    pick = choose(cams, lambda c: (f"{c['name']}" + (f" ({c['place']})" if c.get("place") else "")
                                   + (f"  - {c['org']}" if c.get("org") else "")
                                   + f"  [{c['slug']}]" + ("" if c.get("enabled", True) else "  (disabled)")
                                   + ("" if c.get("allowed", True) else "  (needs your OK on the website first)")),
                  allow_none_text="None of these. I'll paste the connection settings instead")
    if not pick:
        return False
    try:
        replace = False
        while True:
            state, block, info = link.connect(site, token, pick["slug"], replace=replace)
            if state == "confirm":
                print(f"\n  {block}")
                print("  Only do this if this computer replaces the one sending the camera now (or that one is gone).")
                if not yes("Make new connection settings? The computer sending this camera now will stop.",
                           default=False):
                    return False
                replace = True
                continue
            if state == "approve":
                print(f"\n  You haven't allowed this computer to set up '{pick['name']}' yet.")
                print("  On your phone or another computer, open this address, sign in, and allow it:")
                print(f"      {block}")
                again = input("  Then press Enter here to try again (or type q and press Enter to stop): ")
                if again.strip().lower() in ("q", "quit", "n", "no"):
                    return False
                continue
            break
    except link.LinkError as e:
        print(f"  {e}")
        return False
    try:
        _use_block(cfg, block)
    except ValueError as e:
        print(f"  {e}")
        return False
    if info.get("max_height"):
        cfg["_server"] = {**(cfg.get("_server") or {}), "max_height": info["max_height"]}
    print(f"  CamAgent got the connection settings for '{pick['name']}' ({pick['slug']}).")
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
    heading(words.H_CONVERT)
    what = f"{height}p" if height else "an unknown size"
    print(words.C_YOUR_CAMERA.format(what=what, codec=words.C_H265 if h265 else ""))
    print(words.C_INFO_1)
    print(words.C_INFO_2)
    print(words.C_INFO_3)
    here = words.C_HERE
    source = words.C_SOURCE.format(what=what)
    camera_side = words.C_CAMERA_SIDE
    options = [here, camera_side] + ([] if h265 else [source]) + [TRY_FIX]
    if not h265:
        print(words.C_BANDWIDTH)                                   # about "Send your camera's own ..."
    pick = choose(options, str)
    if pick == TRY_FIX:
        if _try_fix_stream(cfg, max_height=convert.TARGET_HEIGHT):
            s["convert"] = "off"
            _measure_again(cfg)
            return
        pick = camera_side                                        # it didn't work: the do-it-yourself steps
    if pick == source:
        s["convert"] = "off"
        return
    if pick == camera_side:
        s["convert"] = "off"
        print("  Change it in the camera's web page: find the Video (or Encode) settings, set Encode Mode to")
        print("  H.264 and Resolution to 1920x1080 (1080p), and save. CamAgent checks the video again next.")
        return
    if not ffmpeg:
        print("  CamAgent can't convert the video because ffmpeg (the video tool CamAgent uses) isn't installed.")
        print("  Install ffmpeg. Then change this camera's settings again.")
        s["convert"] = "off"
        return
    print("\nCamAgent is checking what this computer can use to convert video (up to a minute)...")
    results = convert.detect(ffmpeg, show=print)
    cfg["_detected"] = results                    # step_versions reuses it
    method = convert.best(results)
    m = cfg.get("_measure") or {}
    width = int(m.get("width") or (height * 16 // 9 if height else 3840))
    fps = float(m.get("actual_fps") or m.get("declared_fps") or 15)
    room = convert.headroom(dict(results).get(method), width, height or 2160, fps)
    url = config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"])
    scale = not height or height > convert.TARGET_HEIGHT
    print(f"\nCamAgent is using {convert.LABELS[method]}. It will try it on your camera's video for a few seconds...")
    speed = convert.speed_on_camera(ffmpeg, url, method, s.get("bitrate_kbps"), scale=scale)
    if speed is None and method != "x264":
        print(f"  {convert.LABELS[method]} couldn't convert your camera's video.")
        print("  CamAgent will try the processor (CPU) instead.")
        method = "x264"
        room = convert.headroom(dict(results).get("x264"), width, height or 2160, fps)
        speed = convert.speed_on_camera(ffmpeg, url, method, s.get("bitrate_kbps"), scale=scale)
    if speed is None:
        print("  Converting didn't work on your camera's video, so CamAgent will send the video as it is for now.")
        print('  To try again later, open CamAgent > Change or remove a camera > Change a camera\'s settings.')
        s["convert"] = "off"
        return
    if speed >= 0.95 and room >= 1.5:
        print(f"  Converting works, with room to spare (about {room}x what your camera needs).")
    else:
        why = (f"it couldn't keep up with the camera ({speed}x real time; 1x is needed)" if speed < 0.95
               else f"it has little spare capacity (about {room}x what your camera needs; 1.5x or more is comfortable)")
        print(f"  This computer may struggle: {why}. Video could stutter on busy scenes (rain, wind in trees).")
        print("  A mini PC with an Intel graphics chip handles this easily. Or set your camera to 1080p.")
        if not yes("Convert on this computer anyway?", default=False):
            s["convert"] = "off"
            return
    s.update(convert="1080p", encoder=method, source_height=height)
    print(f"  Saved. CamAgent converts the video to 1080p with {convert.LABELS[method]}.")


def step_versions(cfg, ffmpeg):
    """The 720p and 480p: made on this computer (YonderView's preferred way: the server stays free for everyone) when
    its graphics can, else by YonderView's server. Says which, plainly; nothing to choose unless there's no GPU."""
    from . import convert
    from .versions import GPU_METHODS
    s = cfg["stream"]
    if s.get("versions") == "off" or not ffmpeg:
        return
    heading(words.H_VERSIONS)
    print("Viewers on phones and slow connections watch a 720p or 480p copy of your video.")
    print("YonderView prefers that this computer makes them. Then your internet sends about 3 Mbps more (fine on cable or fiber; skip it on satellite or capped internet).")
    print("Otherwise YonderView's server makes them.")
    results = cfg.get("_detected")
    if results is None:
        print("CamAgent is checking this computer's graphics (up to a minute)...")
        try:
            results = convert.detect(ffmpeg, methods=[m for m in convert.candidates() if m in GPU_METHODS],
                                     size="1920x1080", timeout=20)
        except (OSError, subprocess.SubprocessError):     # ffmpeg missing or broken: as no graphics chip
            results = []
    gpu = next((m for m, speed in results if speed and m in GPU_METHODS), None)
    if gpu:
        s["versions"] = "auto"
        print(f"  This computer makes them itself, with {convert.LABELS.get(gpu, gpu)}. You have nothing to do.")
        return
    print("  This computer has no graphics chip CamAgent can use, so YonderView's server makes them for now.")
    print("  (A mini PC with an Intel graphics chip makes them easily.)")
    if yes("Make them with this computer's processor instead? Say yes only for a strong processor", default=False):
        s["versions"] = "cpu"
        print("  Saved. This computer's processor makes them.")
        print("  If it can't keep up, YonderView's server takes over by itself.")
    else:
        s["versions"] = "auto"


def step_platform(cfg, main_path=None):
    p, s = cfg["platform"], cfg["stream"]
    heading(words.H_STEP3)
    guide.show(guide.SERVER)
    typed = words.TYPED
    how = choose([SIGN_IN, PASTE, typed], str)
    if how == SIGN_IN and _from_account(cfg, main_path):
        p["mqtt_port"] = int(p["mqtt_port"])
        return
    lines = []
    if how != typed:
        print("Paste the connection settings you copied from yonderview.net.")
        print("In a Windows window, right-click to paste. In a Linux terminal, press Ctrl+Shift+V.")
        print("Then press Enter once more on the empty line.")
        while True:
            line = input()
            if not line.strip():
                break
            lines.append(line)
    if lines:
        try:
            _use_block(cfg, "\n".join(lines))
        except ValueError as e:
            print(f"  {e}")
            return step_platform(cfg, main_path)
        print(f"  CamAgent got the settings for camera '{p['camera_id']}'.")
    else:
        print("Copy each value from the connection settings on your camera's page on yonderview.net.")
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
        return False, ("these settings would connect without encryption (mqtt_tls is off). CamAgent won't send its "
                       "password that way. Get fresh settings from yonderview.net (sign in again, or make new "
                       "connection settings)")
    c.username_pw_set(p["camera_id"], p["mqtt_password"])
    if p.get("mqtt_tls"):
        c.tls_set()
    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(p["mqtt_host"], int(p["mqtt_port"]), keepalive=10)
    except Exception as e:
        return False, (f"CamAgent can't reach YonderView at {p['mqtt_host']}:{p['mqtt_port']} ({e}). Check this "
                       f"computer's internet connection. A firewall may be blocking port {p['mqtt_port']}")
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
        return False, "YonderView didn't answer (check this computer's internet connection)"
    if rc.is_failure:
        return False, (f"YonderView refused this computer's login ({rc}). Sign in again, or make new "
                       "connection settings on your camera's page")
    return True, "connected"


def _rewalk_stream(cam):
    """Choose a stream again without re-entering the camera's login or the settings block."""
    ffmpeg = camera.find_ffmpeg(cam["stream"].get("ffmpeg", ""))
    ffprobe = camera.find_ffprobe(ffmpeg)
    _reset_login_tries()
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
        raise SystemExit(f"CamAgent can't save its settings ({path}) without administrator rights. "
                         f"Run CamAgent again {hint}.")


def _tools(cfg):
    from . import prereqs
    return prereqs.check_and_fix(yes, cfg["stream"].get("ffmpeg", ""))


def _walk(cam, ffmpeg, ffprobe, main_path=None):
    _reset_login_tries()
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
    step_versions(cam, ffmpeg)
    step_platform(cam, main_path)
    if ffmpeg:
        cam["stream"]["ffmpeg"] = ffmpeg           # full path, so the service finds it too
    if not cam["platform"].get("camera_id"):
        raise SystemExit("You didn't give a camera ID, so CamAgent saved nothing. Try again. When CamAgent asks, "
                         "sign in or paste the connection settings from yonderview.net.")


def _finish(cam, main_path, others):
    cid = cam["platform"]["camera_id"]
    clash = next((o for o in others if o["platform"]["camera_id"] == cid), None)
    if clash and not yes(words.F_REPLACE.format(cid=cid), default=False):
        raise SystemExit(words.F_CANCELED)

    while True:
        heading(words.H_STEP4)
        ok, msg = test_mqtt(cam)
        print(words.F_YV.format(msg=msg))
        if ok:
            limit = (cam.get("_server") or {}).get("max_height")
            height = (cam.get("_measure") or {}).get("height")
            if cam["stream"].get("convert", "off") != "off":
                height = min(height or 1080, 1080)                # what's sent: the converted 1080p
            if limit and height and height > limit:
                print(words.F_OVER_LIMIT_1.format(height=height, limit=limit))
                print(words.F_OVER_LIMIT_2)
                print(words.F_OVER_LIMIT_5.format(limit=limit))
                print(words.F_OVER_LIMIT_3.format(limit=limit))
                print(words.F_OVER_LIMIT_4)
                other, recheck = words.F_OTHER, words.F_RECHECK
                keep = words.F_KEEP
                pick = choose([other, recheck, keep, TRY_FIX], str)
                if pick == TRY_FIX:
                    _try_fix_stream(cam, max_height=limit)
                    pick = recheck
                if pick == other:
                    _rewalk_stream(cam)
                    continue
                if pick == recheck:
                    _measure_again(cam)
                    continue
            break
        print(words.F_CHECK_NET)
        again = words.F_AGAIN
        anyway = words.F_ANYWAY
        pick = choose([again, anyway, words.F_CANCEL], str)
        if pick == again:
            step_platform(cam, main_path)
            continue
        if pick == anyway:
            break
        raise SystemExit(words.F_CANCELED)

    if clash and Path(clash["_source"]) != Path(cam.get("_source") or ""):
        config.remove_camera(clash, main_path)
    saved = config.save_camera(cam, main_path)
    print(words.F_SAVED.format(cid=cid, saved=saved))
    _apply(main_path)
    guide.show(guide.DONE)


def _apply(main_path):
    _ask_auto_update(main_path)
    if service.is_installed():
        if yes("Restart CamAgent to use the new settings?", default=True):
            service.restart()
        else:
            print("CamAgent keeps using the old settings until it restarts. To restart it, choose \"Restart CamAgent\"")
            print("in the menu, or restart this computer.")
    elif yes("Start CamAgent now? CamAgent keeps sending your camera's video in the background. It starts by\n"
             "  itself whenever this computer turns on.", default=True):
        service.install(main_path)
    else:
        print('To start CamAgent later, open it (Windows: the CamAgent shortcut in the Start menu; Linux: type')
        print('sudo camagent) and choose "Start CamAgent".')
        print("Until then, CamAgent doesn't send your camera to YonderView.")


def _ask_auto_update(main_path):
    """Asked once per computer. Automatic updates install new releases nightly and roll back if one fails."""
    main = config.load_or_defaults(main_path)
    if main["update"].get("asked"):
        return
    print("\nAutomatic updates install new CamAgent versions overnight (around 3 AM). CamAgent then checks that")
    print("your cameras come back. If they don't, CamAgent goes back to the previous version.")
    main["update"]["auto"] = yes("Do you want CamAgent to keep itself up to date automatically?", default=True)
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
            raise SystemExit(f"This computer has no camera called '{camera_id}'. "
                             "To see the names of your cameras, type: camagent list")
        return cam
    if len(cams) == 1:
        return cams[0]
    print("Which camera do you want to change?")
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
          "To keep a value shown in [brackets], press Enter.")
    original_id = cam["platform"]["camera_id"]
    ffmpeg, ffprobe = _tools(cam)
    _walk(cam, ffmpeg, ffprobe, main_path)
    others = [c for c in cams if c["platform"]["camera_id"] != original_id]
    _finish(cam, main_path, others)


def _check_clock():
    """A computer clock far off (a new PC that hasn't synced yet) makes camera logins fail in confusing ways: say
    so first, and on Windows offer to sync it."""
    from . import link
    problem = link.clock_problem(link.clock_offset())
    if not problem:
        return
    print(f"\n  Note: {problem}.")
    if os.name == "nt" and yes("Do you want to sync this computer's clock now?", default=True):
        for cmd in (["net", "start", "w32time"], ["w32tm", "/resync", "/force"]):
            subprocess.run(cmd, capture_output=True, text=True)
        still = link.clock_problem(link.clock_offset())
        print("  This computer's clock is right now." if not still else f"  {still}.")
    print()


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
        print(words.ADD_N_1.format(n=n))
        print(words.ADD_N_2)
        print(words.ADD_N_3)
    _check_clock()
    ffmpeg, ffprobe = _tools(cam)
    _walk(cam, ffmpeg, ffprobe, main_path)
    _finish(cam, main_path, cams)


def remove(config_path=None, camera_id=None):
    """camagent remove <camera>: stop streaming a camera from this machine and delete its settings."""
    main_path = Path(config_path or config.default_config_path())
    _check_writable(main_path)
    _, cams, _ = config.load_all(main_path)
    if not cams:
        raise SystemExit("You haven't set up any cameras on this computer, so there's nothing to remove.")
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    cid = cam["platform"]["camera_id"]
    if not yes(f"Remove camera '{cid}' from this computer? CamAgent will delete its settings here.", default=False):
        raise SystemExit("Canceled. CamAgent removed nothing.")
    config.remove_camera(cam, main_path)
    print(f"Removed '{cid}'. The camera stays on yonderview.net. This computer just stops sending its video.")
    if service.is_installed():                       # the running CamAgent still has it loaded until it restarts
        print("CamAgent is restarting so it stops sending the camera now...")
        service.restart()
        if len(cams) == 1:
            print("That was the last camera on this computer. CamAgent still runs in the background, with nothing to")
            print("send. To stop CamAgent completely, type this as administrator: camagent uninstall-service")


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
        raise SystemExit(f"CamAgent can't read its settings without administrator rights. Run CamAgent again {hint}.")
    live = {}
    try:
        data = json.loads(config.status_path().read_text(encoding="utf-8"))
        if time.time() - data.get("updated", 0) < 60:
            live = data.get("cameras", {})
    except (OSError, ValueError):
        pass
    if not cams:
        print('You haven\'t set up any cameras on this computer yet. Choose "Add a camera" in the menu.')
    for c in cams:
        cid = c["platform"]["camera_id"]
        where = config.strip_auth(c["camera"].get("rtsp_url", "")) or c["camera"].get("host", "")
        print(f"{cid}")
        print(f"    camera: {where or '(not set)'}{'  [PTZ]' if c['camera'].get('ptz') else ''}"
              f"{'  [sending turned off]' if not c['stream'].get('enabled', True) else ''}")
        st = live.get(cid)
        if st is None:
            print("    status: " + ("CamAgent isn't running, so it isn't sending anything (choose Start CamAgent or Restart CamAgent)"
                                 if not live else "not sending (choose \"Check for problems\" to see why)"))
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
            print("    (saved in an older format. It works, and changing its settings updates it)")
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
        raise SystemExit("You haven't set up any cameras on this computer yet. "
                         "Add a camera first, then add its weather station.")
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    w = cam["weather"]
    heading(words.H_WEATHER)
    print("CamAgent can read a weather station on this network and show its conditions on your camera's page.")
    print("CamAgent works with Tempest, Davis WeatherLink Live and Ecowitt gateways (GW1100, GW2000 and similar).")
    if w.get("kind"):
        print(f"\nThis camera uses: {w['kind']} {w.get('host') or w.get('serial') or ''}".rstrip())
        if yes("Remove it?", default=False):
            cam["weather"] = dict(config.DEFAULTS["weather"])
            config.save_camera(cam, main_path)
            print("CamAgent removed the weather station from this camera.")
            _apply(main_path)
            return
    kinds = [("tempest", "Tempest"), ("weatherlink", "Davis WeatherLink Live"), ("ecowitt", "Ecowitt gateway")]
    kind = choose(kinds, lambda k: k[1], allow_none_text="Cancel")
    if kind is None:
        return
    kind = kind[0]
    new = dict(config.DEFAULTS["weather"], kind=kind)
    if kind == "tempest":
        print("CamAgent is listening for Tempest stations on this network (15 seconds)...")
        try:
            found = weather.tempest_discover(15)
        except RuntimeError as e:
            raise SystemExit(str(e))
        if not found:
            raise SystemExit("CamAgent didn't hear a Tempest. The Tempest hub must be on the same network as this "
                             "computer. Check the hub's Wi-Fi in the Tempest app, then try again.")
        serials = sorted(found)
        new["serial"] = serials[0] if len(serials) == 1 else choose(serials, lambda s: f"Station {s}")
        print(f"CamAgent found Tempest {new['serial']}.")
        elev = ask("Station's elevation in feet, to show sea-level pressure (press Enter to leave pressure out)", "")
        try:
            new["elevation_m"] = round(float(elev) * 0.3048) if elev else 0
        except ValueError:
            new["elevation_m"] = 0
    else:
        new["host"] = ask("Station's IP address on this network (find it in its app or your router's list of devices)",
                          w.get("host", ""))
        if not new["host"]:
            raise SystemExit("You didn't give an address, so CamAgent changed nothing.")
    print("CamAgent is reading the station (up to a minute for Tempest)...")
    reader = weather.Reader(new)
    try:
        r = reader.read(wait=70)
        uid = reader.station_id()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"CamAgent couldn't read the station: {e}. "
                         "Check its IP address, and check that it's on this network.")
    finally:
        reader.close()
    if not r:
        raise SystemExit("CamAgent got no reading from the station. "
                         "Check that it's on and on this network, then try again.")
    print(f"CamAgent got a reading ({uid}): {_summary(r)}")
    new["name"] = ask("Name for this station (shown on your camera's page)", w.get("name") or "Weather station")
    cam["weather"] = new
    config.save_camera(cam, main_path)
    print("Saved. Conditions appear on your camera's page within a minute after CamAgent restarts.")
    _apply(main_path)
