"""Interactive setup: find the camera, pick a stream, enter server details, test, save."""
import getpass
import os
import threading
from pathlib import Path

from . import camera, config, service


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
            print("  and that this computer is on the same network.")
    cam["host"] = ask("Camera IP address", cam["host"])
    cam["onvif_port"] = int(ask("ONVIF port", cam["onvif_port"]))
    cam["username"] = ask("Camera username", cam["username"])
    cam["password"] = ask_secret("Camera password", cam["password"])


def step_pick_stream(cfg, ffprobe):
    cam = cfg["camera"]
    heading("Stream")
    print("Asking the camera for its streams over ONVIF...")
    try:
        info = camera.onvif_inspect(cam["host"], cam["onvif_port"], cam["username"], cam["password"])
    except Exception as e:
        info = None
        print(f"  ONVIF didn't work: {camera.friendly_error(e)}")
        print("  (Common causes: wrong port or password, ONVIF disabled, or the camera's clock is off.)")

    if info and info["profiles"]:
        print(f"  Found: {info['manufacturer']} {info['model']} (firmware {info['firmware']})")
        profiles = [p for p in info["profiles"] if p["rtsp_url"]]
        if profiles:
            print("Which stream should be sent? (The highest quality is usually first.)")
            pick = choose(profiles, lambda p: (
                f"{p['name']}: {p['encoding']} {p['width']}x{p['height']}"
                f"{' @ ' + str(p['fps']) + ' fps' if p['fps'] else ''}"
                f"{'  [PTZ]' if p['ptz'] else ''}\n       {p['rtsp_url']}"),
                allow_none_text="None of these; I'll enter an RTSP URL")
            if pick:
                cam["rtsp_url"] = pick["rtsp_url"]
            has_ptz = any(p["ptz"] for p in info["profiles"])
            ptz_profile = next((p["token"] for p in info["profiles"] if p["ptz"]), "")
            if has_ptz:
                cam["ptz"] = yes("This camera supports PTZ. Enable PTZ control?", default=True)
                cam["ptz_profile"] = ptz_profile if cam["ptz"] else ""
            else:
                print("  No PTZ found on this camera; PTZ control will be off.")
                cam["ptz"], cam["ptz_profile"] = False, ""
            if pick:
                return

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
                return
        else:
            print("  None of the common addresses answered.")

    cam["rtsp_url"] = config.strip_auth(ask("RTSP URL (credentials are added automatically)", cam["rtsp_url"]))
    if not info:
        cam["ptz"] = yes("Does this camera have PTZ (and ONVIF working)?", default=False)


def step_check_stream(cfg, ffprobe):
    cam, s = cfg["camera"], cfg["stream"]
    if not ffprobe:
        print("\n(ffprobe not found, so the stream can't be checked. Install ffmpeg to enable this.)")
        return
    print("\nChecking the stream...")
    streams = camera.probe(ffprobe, config.rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))
    if not streams:
        print("  Couldn't read the stream. Double-check the RTSP URL and camera login.")
        return
    print(f"  OK: {camera.describe(streams)}")
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    if video and video.get("codec_name") != "h264":
        print(f"  Note: the video is {video.get('codec_name')}. Browsers play H.264 directly;")
        print("  other codecs need transcoding on the server, so H.264 is recommended for now.")
    has_audio = any(x.get("codec_type") == "audio" for x in streams)
    if has_audio:
        s["audio"] = yes("The camera sends audio. Include it in the stream?", default=s["audio"])
    else:
        s["audio"] = False


def step_platform(cfg):
    p, s = cfg["platform"], cfg["stream"]
    heading("Server")
    print("Paste the settings block from yvcam ('Show stream settings'), then press Enter")
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
        done.set()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{p['camera_id']}-test")
    c.username_pw_set(p["camera_id"], p["mqtt_password"])
    if p.get("mqtt_tls"):
        c.tls_set()
    c.on_connect = on_connect
    try:
        c.connect(p["mqtt_host"], int(p["mqtt_port"]), keepalive=10)
    except Exception as e:
        return False, f"can't reach {p['mqtt_host']}:{p['mqtt_port']} ({e})"
    c.loop_start()
    done.wait(8)
    c.disconnect()
    c.loop_stop()
    rc = result.get("rc")
    if rc is None:
        return False, "no answer from the broker"
    if rc.is_failure:
        return False, f"broker refused the login ({rc}); check the camera ID and MQTT password"
    return True, "connected and logged in"


def run(config_path=None):
    path = Path(config_path or config.default_config_path())
    if path.exists() and not os.access(path, os.W_OK) or (not path.exists() and not service.is_admin()):
        hint = "an administrator Command Prompt or PowerShell" if os.name == "nt" else "sudo"
        raise SystemExit(f"Can't write {path}. Run this from {hint}.")

    cfg = config.load_or_defaults(path)
    print("camagent setup. Press Enter to keep the value shown in [brackets].")

    ffmpeg = camera.find_ffmpeg(cfg["stream"].get("ffmpeg", ""))
    if not ffmpeg:
        print("\nffmpeg wasn't found. camagent needs it to send video. Install it with:")
        print(f"    {camera.ffmpeg_install_hint()}")
        if not yes("Continue setup anyway? (You can install ffmpeg afterward.)", default=False):
            raise SystemExit("Setup stopped. Install ffmpeg, then run: camagent configure")
    ffprobe = camera.find_ffprobe(ffmpeg)

    step_find_camera(cfg)
    step_pick_stream(cfg, ffprobe)
    step_check_stream(cfg, ffprobe)
    step_platform(cfg)
    if ffmpeg:
        cfg["stream"]["ffmpeg"] = ffmpeg           # full path, so the service finds it too

    heading("Testing")
    ok, msg = test_mqtt(cfg)
    print(f"  MQTT: {msg}")

    saved = config.save(cfg, path)
    print(f"\nSaved to {saved}")

    if service.is_installed():
        if yes("Restart the service to use the new settings?", default=True):
            service.restart()
    elif yes("Install and start camagent as a service now?", default=True):
        service.install(saved)
    else:
        print("Start it later with: camagent install-service   (or test it with: camagent run)")
