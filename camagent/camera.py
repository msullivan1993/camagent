"""Finding cameras and their streams: ONVIF WS-Discovery, ONVIF profile lookup, RTSP probing."""
import ipaddress
import json
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse, urlunparse

from .config import WSDL_DIR, rtsp_with_auth

# Common RTSP paths by vendor, tried when ONVIF isn't available.
COMMON_RTSP_PATHS = [
    ("Amcrest / Dahua",        "/cam/realmonitor?channel=1&subtype=0"),
    ("Hikvision / Annke",      "/Streaming/Channels/101"),
    ("Reolink",                "/h264Preview_01_main"),
    ("Hanwha / Wisenet",       "/profile1/media.smp"),
    ("Axis",                   "/axis-media/media.amp"),
    ("Generic",                "/ch01/0"),
    ("Generic",                "/stream0"),
    ("Generic (XM)",           "/11"),
    ("Generic",                "/live/ch00_0"),
    ("Generic",                "/onvif1"),
    ("Generic",                "/live"),
    ("Generic",                "/0"),
]


# ---------- WS-Discovery ----------

_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<e:Header>
<w:MessageID>uuid:{id}</w:MessageID>
<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
</e:Header>
<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>"""


def _local_ipv4():
    """This computer's IPv4 addresses (every network adapter), so the search goes out on each of them. Windows with
    WSL, Hyper-V or a VPN has extra adapters, and a single probe often leaves on one with no cameras behind it."""
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    try:                                           # the adapter with the default route (no packet is sent)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith(("127.", "169.254.", "0.")))


def _parse_probe_match(text, addr):
    xaddrs = re.search(r"XAddrs>\s*([^<]+)<", text)
    scopes = re.search(r"Scopes>\s*([^<]+)<", text)
    urls = (xaddrs.group(1).split() if xaddrs else [])
    # prefer the address the reply actually came from
    url = next((x for x in urls if addr in x), urls[0] if urls else f"http://{addr}/onvif/device_service")
    u = urlparse(url)
    scope_text = scopes.group(1) if scopes else ""

    def scope(name):
        m = re.search(rf"onvif://www\.onvif\.org/{name}/(\S+)", scope_text)
        # printable text only: a reply is untrusted, and control characters could rewrite the terminal
        return "".join(ch for ch in unquote(m.group(1)) if ch.isprintable())[:80] if m else ""

    try:
        port = u.port or 80
    except ValueError:
        port = 80
    return {"host": addr, "port": port, "name": scope("name"), "hardware": scope("hardware")}


def discover(timeout: float = 3.0) -> list:
    """Send an ONVIF WS-Discovery probe on every network adapter and collect the cameras that answer."""
    import select
    socks = []
    for local in _local_ipv4() or ["0.0.0.0"]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if local != "0.0.0.0":
                sock.bind((local, 0))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
            sock.setblocking(False)
            sock.sendto(_PROBE.format(id=uuid.uuid4()).encode(), ("239.255.255.250", 3702))
            socks.append(sock)
        except OSError:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
    try:
        found, deadline = {}, time.time() + timeout
        while socks and time.time() < deadline:
            ready, _, _ = select.select(socks, [], [], max(0.05, min(0.5, deadline - time.time())))
            for sock in ready:
                try:
                    data, (addr, _) = sock.recvfrom(65535)
                except OSError:
                    continue
                if not ipaddress.ip_address(addr).is_private:      # cameras answer from the local network
                    continue
                found[addr] = _parse_probe_match(data.decode("utf-8", "ignore"), addr)
        return sorted(found.values(), key=lambda c: tuple(int(p) for p in c["host"].split(".") if p.isdigit()))
    finally:
        for sock in socks:
            sock.close()


# ---------- ONVIF ----------

def friendly_error(e) -> str:
    """Turn onvif-zeep's cryptic failures into something readable."""
    text = str(e)
    if "getroottree" in text or "Max retries" in text or "timed out" in text.lower():
        return "the camera didn't answer (the address may be wrong, the camera may be off or on another network, or ONVIF is off)"
    if "Unauthorized" in text or "not authorized" in text.lower() or "401" in text:
        return "login rejected by the camera (check the username and password, and that the camera's clock is right)"
    return text


def onvif_connect(host, port, user, password):
    from ._onvif import ONVIFCamera          # imported here so discovery works even if onvif is broken
    # no_cache: the WSDLs are bundled, and the default cache lives in the home folder, which the Linux
    # service account doesn't have (that made every ONVIF connection fail with "Permission denied")
    # adjust_time: ONVIF logins carry a timestamp, and cameras refuse ones too far from their own clock. Asking the
    # camera's time first (no login needed) and stamping with it makes a wrong clock on this PC harmless (0.17.5:
    # a new mini PC, three hours off, couldn't log in to its camera). A camera that won't tell its time without a
    # login: connect as before.
    # A refused login isn't tried the second way (0.17.10): cameras count each refusal toward their lockout.
    try:
        return ONVIFCamera(host, int(port), user, password, str(WSDL_DIR), no_cache=True, adjust_time=True)
    except Exception as e:  # noqa: BLE001
        if friendly_error(e).startswith("login rejected"):
            raise
        return ONVIFCamera(host, int(port), user, password, str(WSDL_DIR), no_cache=True)


def normalize_uri(raw: str, host: str) -> str:
    """Cameras often report a stale IP in stream URIs; keep their path, use the real host, drop credentials."""
    u = urlparse(raw)
    port = u.port or 554
    return urlunparse(("rtsp", f"{host}:{port}", u.path, u.params, u.query, u.fragment))


def login_level(cam, user):
    """The camera login's level, as ONVIF names it ("Administrator", "Operator", "User"), or "" if the camera won't
    say. Listing a camera's users usually needs an administrator, so a refusal itself suggests a limited login."""
    try:
        for u in cam.devicemgmt.GetUsers() or []:
            if str(getattr(u, "Username", "")).lower() == str(user).lower():
                return str(getattr(u, "UserLevel", "") or "")
    except Exception:  # noqa: BLE001
        return "limited?"
    return ""


def onvif_inspect(host, port, user, password) -> dict:
    """Return device info and a list of stream profiles (with RTSP URLs and PTZ flag)."""
    cam = onvif_connect(host, port, user, password)
    info = cam.devicemgmt.GetDeviceInformation()
    media = cam.create_media_service()
    profiles = []
    for p in media.GetProfiles():
        enc = getattr(p, "VideoEncoderConfiguration", None)
        res = getattr(enc, "Resolution", None) if enc else None
        rate = getattr(enc, "RateControl", None) if enc else None
        try:
            uri = media.GetStreamUri({
                "StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
                "ProfileToken": p.token,
            }).Uri
            uri = normalize_uri(uri, host)
        except Exception:
            uri = ""
        profiles.append({
            "token": p.token,
            "name": getattr(p, "Name", "") or p.token,
            "encoding": getattr(enc, "Encoding", "") if enc else "",
            "width": getattr(res, "Width", 0) if res else 0,
            "height": getattr(res, "Height", 0) if res else 0,
            "fps": getattr(rate, "FrameRateLimit", 0) if rate else 0,
            "rtsp_url": uri,
            "ptz": bool(getattr(p, "PTZConfiguration", None)),
        })
    manufacturer = getattr(info, "Manufacturer", "")
    return {
        "login_level": login_level(cam, user),
        "position": ptz_position_check(cam, media, host, user, password, manufacturer),
        "manufacturer": manufacturer,
        "model": getattr(info, "Model", ""),
        "firmware": getattr(info, "FirmwareVersion", ""),
        "serial": getattr(info, "SerialNumber", ""),
        "profiles": profiles,
    }


def ptz_position_check(cam, media, host, user, password, manufacturer=""):
    """Does the camera answer where it points (0.17.10, for setup and the checks)? {"onvif": {pan, tilt, zoom} or
    None, "error": why not, "exact": degrees on Dahua-family cameras or None, "exact_note": why not}. None for a
    camera without PTZ. Read-only: nothing moves."""
    try:
        prof = next((p for p in media.GetProfiles() if getattr(p, "PTZConfiguration", None)), None)
    except Exception:  # noqa: BLE001
        prof = None
    if prof is None:
        return None
    out = {"onvif": None, "error": "", "exact": None, "exact_note": ""}
    try:
        st = cam.create_ptz_service().GetStatus({"ProfileToken": prof.token})
        pos = getattr(st, "Position", None)
        pt = getattr(pos, "PanTilt", None) if pos else None
        zoom = getattr(getattr(pos, "Zoom", None), "x", None) if pos else None
        if pt is not None and getattr(pt, "x", None) is not None:
            out["onvif"] = {"pan": round(float(pt.x), 4), "tilt": round(float(pt.y or 0), 4),
                            "zoom": round(float(zoom), 4) if zoom is not None else None}
        else:
            out["error"] = "the camera answered, but without a position"
    except Exception as e:  # noqa: BLE001
        out["error"] = friendly_error(e)
    from . import exact
    if exact.is_dahua(manufacturer):
        r = exact.Reader(host, user, password)
        out["exact"] = r.read()
        out["exact_note"] = r.off
    return out


def describe_position(check):
    """(ok, text) about a ptz_position_check result, for people."""
    if check is None:
        return None, ""
    if not check["onvif"]:
        return False, ("the camera didn't say where it points (" + (check["error"] or "no answer") + "): the map can "
                       "show its direction only from presets picked on YonderView")
    o = check["onvif"]
    text = f"pan {o['pan']:+.3f}, tilt {o['tilt']:+.3f}" + (f", zoom {o['zoom']:.3f}" if o["zoom"] is not None else "")
    e = check.get("exact")
    if e:
        text += (f"; exact: {e['pan']:.1f}° across, {e['tilt']:.1f}° " + ("down" if e["tilt"] >= 0 else "up")
                 + (f", zoom {e['zoom']:g}x" if e.get("zoom") else "") + " (YonderView sets up tilt and zoom from these)")
    elif check.get("exact_note"):
        text += "; exact degrees: " + check["exact_note"]
    return True, text


# ---------- a login just for YonderView (0.17.10) ----------

LOGIN_NAME = "yonderview"
_PW_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"   # no 0/O, 1/l/I: easy to type if needed


def new_password(n=16):
    """Letters and digits only (some cameras refuse symbols), at least one of each kind."""
    import secrets
    while True:
        pw = "".join(secrets.choice(_PW_CHARS) for _ in range(n))
        if any(c.isupper() for c in pw) and any(c.islower() for c in pw) and any(c.isdigit() for c in pw):
            return pw


class LoginExists(Exception):
    pass


def create_login(host, port, admin_user, admin_password, name=LOGIN_NAME, replace=False):
    """Make an Operator login on the camera over ONVIF (CreateUsers, the standard way), with a new password, and
    check it works. Needs an administrator login, used for this only. LoginExists if `name` is there already and
    replace is False; with replace, that login gets the new password (SetUser). Returns (name, password)."""
    admin = onvif_connect(host, port, admin_user, admin_password)
    dm = admin.devicemgmt
    try:
        users = {getattr(u, "Username", "") for u in (dm.GetUsers() or [])}
    except Exception:  # noqa: BLE001
        users = set()
    password = new_password()
    user = {"Username": name, "Password": password, "UserLevel": "Operator"}
    if name in users:
        if not replace:
            raise LoginExists(name)
        dm.SetUser({"User": [user]})
    else:
        dm.CreateUsers({"User": [user]})
    last = None
    for wait in (2, 5):                       # some cameras take a moment to start accepting a new login
        time.sleep(wait)
        try:
            onvif_connect(host, port, name, password).devicemgmt.GetDeviceInformation()
            return name, password
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"the camera made the login, but didn't accept it afterwards ({friendly_error(last)})")


# ---------- does PTZ position follow a move? (setup's test, 0.17.10) ----------

def ptz_move_test(host, port, user, password, token="", seconds=0.7):
    """Turn the camera a little, read its position, put it back. {"before", "after", "follows": bool, "back": bool}.
    Raises if the camera won't connect or move."""
    cam = onvif_connect(host, port, user, password)
    ptz = cam.create_ptz_service()
    if not token:
        media = cam.create_media_service()
        token = next(p.token for p in media.GetProfiles() if getattr(p, "PTZConfiguration", None))

    def where():
        st = ptz.GetStatus({"ProfileToken": token})
        pt = st.Position.PanTilt
        z = getattr(getattr(st.Position, "Zoom", None), "x", None)
        return {"pan": float(pt.x), "tilt": float(pt.y or 0), "zoom": None if z is None else float(z)}

    try:
        before = where()
    except Exception:  # noqa: BLE001
        before = None

    def turn(x):
        ptz.ContinuousMove({"ProfileToken": token, "Velocity": {"PanTilt": {"x": x, "y": 0.0}}})
        time.sleep(seconds)
        ptz.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": True})
        time.sleep(1.5)                                    # let it settle before asking where it is

    turn(0.3)
    try:
        after = where()
    except Exception:  # noqa: BLE001
        after = None
    back = False
    if before is not None:
        pos = {"PanTilt": {"x": before["pan"], "y": before["tilt"]}}
        if before["zoom"] is not None:
            pos["Zoom"] = {"x": before["zoom"]}
        try:
            ptz.AbsoluteMove({"ProfileToken": token, "Position": pos})
            back = True
        except Exception:  # noqa: BLE001
            pass
    if not back:
        turn(-0.3)                                         # the same move the other way
        back = True
    follows = bool(before and after and abs(after["pan"] - before["pan"]) > 0.003)
    return {"before": before, "after": after, "follows": follows, "back": back}


# ---------- ffmpeg / ffprobe ----------

def ffmpeg_install_hint() -> str:
    import os
    if os.name == "nt":
        return ("in an administrator window, type: winget install -e --id Gyan.FFmpeg --scope machine "
                "(then open CamAgent again in a new window)")
    return "type: sudo apt install ffmpeg"


def find_ffmpeg(configured: str = "") -> str:
    from .prereqs import find_ffmpeg as _find
    return _find(configured)


def find_ffprobe(ffmpeg: str = "") -> str:
    if ffmpeg:
        p = Path(ffmpeg)
        candidate = p.with_name(p.name.replace("ffmpeg", "ffprobe"))
        from .prereqs import is_file
        if is_file(candidate):
            return str(candidate)
    from .prereqs import which
    return which("ffprobe") or ""


def probe(ffprobe: str, url: str, timeout: float = 8.0):
    """Return a list of stream dicts from ffprobe, or None if the URL doesn't work."""
    if not ffprobe:
        return None
    cmd = [ffprobe, "-v", "error", "-rtsp_transport", "tcp",
           "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate",
           "-of", "json", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except ValueError:
        return None
    return streams or None


def audio_arrives(ffprobe: str, url: str, seconds: int = 4):
    """True if the camera actually sends sound (not just lists an audio track), False if not, None if unknown.
    Some cameras keep announcing audio after it's switched off, and ffmpeg then waits for it forever."""
    if not ffprobe:
        return None
    cmd = [ffprobe, "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "a:0",
           "-read_intervals", f"%+{seconds}", "-show_entries", "packet=pts_time", "-of", "csv=p=0", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 15)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return any(line.strip() for line in r.stdout.splitlines())


def sound_check(cfg):
    """Does this camera send sound right now? None if it can't be checked (no ffprobe, no URL)."""
    cam = cfg["camera"]
    ffprobe = find_ffprobe(find_ffmpeg(cfg["stream"].get("ffmpeg", "")))
    if not cam.get("rtsp_url") or not ffprobe:
        return None
    from .config import rtsp_with_auth
    return audio_arrives(ffprobe, rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]))


AUDIO_LOST = ("  Note: the camera stopped sending sound after this change (some cameras switch audio off when\n"
              "  their video settings are changed this way). CamAgent sends video only until the sound is back, then\n"
              "  adds it again by itself. Restarting the camera usually fixes it; if not, turn audio on again in the\n"
              "  camera's own web page (often \"Video & Audio\" as the stream type).")


def describe(streams) -> str:
    parts = []
    for s in streams or []:
        if s.get("codec_type") == "video":
            fps = s.get("avg_frame_rate", "0/1")
            try:
                n, d = fps.split("/")
                fps = f"{float(n) / float(d):.0f} fps" if float(d) else ""
            except ValueError:
                fps = ""
            parts.append(f"video {s.get('codec_name')} {s.get('width')}x{s.get('height')} {fps}".strip())
        elif s.get("codec_type") == "audio":
            parts.append(f"audio {s.get('codec_name')}")
    return ", ".join(parts) or "no streams"


def scan_rtsp_paths(ffprobe, host, user, password, port=554, progress=None) -> list:
    """Try common RTSP paths on a host; return the ones that answer."""
    found = []
    for vendor, path in COMMON_RTSP_PATHS:
        url = f"rtsp://{host}:{port}{path}"
        if progress:
            progress(vendor, path)
        streams = probe(ffprobe, rtsp_with_auth(url, user, password), timeout=6)
        if streams:
            found.append({"vendor": vendor, "rtsp_url": url, "streams": streams})
    return found


def measure_stream(ffprobe: str, url: str, seconds: int = 6):
    """Watch the stream for a few seconds (without decoding) and report what it really sends:
    {"declared_fps", "actual_fps", "kbps", "keyframe_s", "width", "height"}. Any value may be None."""
    if not ffprobe:
        return {}
    cmd = [ffprobe, "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
           "-read_intervals", f"%+{seconds}",
           "-show_entries", "stream=avg_frame_rate,r_frame_rate,width,height:packet=pts_time,size,flags",
           "-of", "json", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds + 15)
        data = json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return {}
    st = (data.get("streams") or [{}])[0]

    def rate(text):
        try:
            n, _, d = str(text).partition("/")
            v = float(n) / float(d or 1)
            return round(v, 2) if 1 <= v <= 120 else None
        except (ValueError, ZeroDivisionError):
            return None

    pkts = [p for p in data.get("packets", []) if p.get("pts_time") not in (None, "N/A")]
    out = {"declared_fps": rate(st.get("avg_frame_rate")) or rate(st.get("r_frame_rate")),
           "width": st.get("width"), "height": st.get("height"), "actual_fps": None, "kbps": None,
           "keyframe_s": None}
    if len(pkts) >= 2:
        t = [float(p["pts_time"]) for p in pkts]
        span = max(t) - min(t)
        if span > 0.5:
            out["actual_fps"] = round((len(pkts) - 1) / span, 1)
            out["kbps"] = round(sum(int(p.get("size", 0)) for p in pkts) * 8 / 1000 / span)
        keys = [float(p["pts_time"]) for p in pkts if "K" in str(p.get("flags", ""))]
        if len(keys) >= 2:
            gaps = [b - a for a, b in zip(keys, keys[1:])]
            out["keyframe_s"] = round(sum(gaps) / len(gaps), 1)
        elif len(keys) <= 1 and span >= seconds - 1:
            out["keyframe_s"] = float(seconds)       # at most one keyframe in the window: at least this long
    return out


def stream_advice(m: dict, max_height=None):
    """Only things that actually cause problems. Frame rate and bitrate are the host's choice."""
    tips = []
    fps, actual, gop, h = m.get("declared_fps"), m.get("actual_fps"), m.get("keyframe_s"), m.get("height")
    if max_height and h and h > max_height:
        tips.append(f"The camera sends {h}p, but this camera's limit on YonderView is {max_height}p, so its video "
                    f"would be declined. Set the camera's video to {max_height}p or lower in its web page, or let "
                    f"CamAgent convert it to 1080p on this computer (open CamAgent > Change or remove a camera > "
                    f"Change a camera's settings).")
    if fps and actual and actual < 0.85 * fps:
        tips.append(f"Only {actual:g} of {fps:g} frames per second actually arrived. In low light many cameras "
                    "slow down on their own, which is fine; otherwise the camera may be overloaded (restarting it "
                    "often helps).")
    if gop and gop > 4:
        tips.append(f"The camera sends a full picture (keyframe) only every {gop:g} seconds; YonderView needs one at "
                    "least every 4 (2 is best). In the camera's web page, set \"I-frame interval\" or \"keyframe "
                    "interval\" to about twice its frame rate (e.g. 50 at 25 fps).")
    return tips


def stream_summary(m: dict) -> str:
    """One line describing what the camera sends, e.g. '1440p, 30 fps, about 8.1 Mbps, keyframe every 2 s'."""
    if not m.get("actual_fps"):
        return ""
    return (f"{m.get('height') or '?'}p, {m['actual_fps']:g} fps"
            f"{', about ' + format(m['kbps'] / 1000, '.1f') + ' Mbps' if m.get('kbps') else ''}"
            f"{', keyframe every ' + format(m['keyframe_s'], 'g') + ' s' if m.get('keyframe_s') else ''}")
