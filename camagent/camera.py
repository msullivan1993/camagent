"""Finding cameras and their streams: ONVIF WS-Discovery, ONVIF profile lookup, RTSP probing."""
import json
import re
import shutil
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


def discover(timeout: float = 3.0) -> list:
    """Send an ONVIF WS-Discovery probe and collect responding cameras."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    try:
        sock.sendto(_PROBE.format(id=uuid.uuid4()).encode(), ("239.255.255.250", 3702))
        found, deadline = {}, time.time() + timeout
        while time.time() < deadline:
            try:
                data, (addr, _) = sock.recvfrom(65535)
            except socket.timeout:
                continue
            text = data.decode("utf-8", "ignore")
            xaddrs = re.search(r"XAddrs>\s*([^<]+)<", text)
            scopes = re.search(r"Scopes>\s*([^<]+)<", text)
            urls = (xaddrs.group(1).split() if xaddrs else [])
            # prefer the address the reply actually came from
            url = next((x for x in urls if addr in x), urls[0] if urls else f"http://{addr}/onvif/device_service")
            u = urlparse(url)
            scope_text = scopes.group(1) if scopes else ""

            def scope(name):
                m = re.search(rf"onvif://www\.onvif\.org/{name}/(\S+)", scope_text)
                return unquote(m.group(1)) if m else ""

            found[addr] = {
                "host": addr,
                "port": u.port or 80,
                "name": scope("name"),
                "hardware": scope("hardware"),
            }
        return sorted(found.values(), key=lambda c: tuple(int(p) for p in c["host"].split(".") if p.isdigit()))
    finally:
        sock.close()


# ---------- ONVIF ----------

def friendly_error(e) -> str:
    """Turn onvif-zeep's cryptic failures into something readable."""
    text = str(e)
    if "getroottree" in text or "Max retries" in text or "timed out" in text.lower():
        return "no ONVIF response (camera unreachable, wrong port, or ONVIF disabled)"
    if "Unauthorized" in text or "not authorized" in text.lower() or "401" in text:
        return "login rejected (check the username/password, and that the camera's clock is correct)"
    return text


def onvif_connect(host, port, user, password):
    from onvif import ONVIFCamera            # imported here so discovery works even if onvif is broken
    return ONVIFCamera(host, int(port), user, password, str(WSDL_DIR))


def normalize_uri(raw: str, host: str) -> str:
    """Cameras often report a stale IP in stream URIs; keep their path, use the real host, drop credentials."""
    u = urlparse(raw)
    port = u.port or 554
    return urlunparse(("rtsp", f"{host}:{port}", u.path, u.params, u.query, u.fragment))


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
    return {
        "manufacturer": getattr(info, "Manufacturer", ""),
        "model": getattr(info, "Model", ""),
        "firmware": getattr(info, "FirmwareVersion", ""),
        "serial": getattr(info, "SerialNumber", ""),
        "profiles": profiles,
    }


# ---------- ffmpeg / ffprobe ----------

def ffmpeg_install_hint() -> str:
    import os
    if os.name == "nt":
        return "winget install -e --id Gyan.FFmpeg   (then open a new Command Prompt)"
    return "sudo apt install ffmpeg"


def find_ffmpeg(configured: str = "") -> str:
    from .prereqs import find_ffmpeg as _find
    return _find(configured)


def find_ffprobe(ffmpeg: str = "") -> str:
    if ffmpeg:
        p = Path(ffmpeg)
        candidate = p.with_name(p.name.replace("ffmpeg", "ffprobe"))
        if candidate.exists():
            return str(candidate)
    return shutil.which("ffprobe") or ""


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
    {"declared_fps", "actual_fps", "kbps", "keyframe_s", "height"}. Any value may be None."""
    if not ffprobe:
        return {}
    cmd = [ffprobe, "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
           "-read_intervals", f"%+{seconds}",
           "-show_entries", "stream=avg_frame_rate,r_frame_rate,height:packet=pts_time,size,flags",
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
           "height": st.get("height"), "actual_fps": None, "kbps": None, "keyframe_s": None}
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
    """Plain-language suggestions from measure_stream()."""
    tips = []
    fps, actual, kbps, gop, h = (m.get("declared_fps"), m.get("actual_fps"), m.get("kbps"),
                                 m.get("keyframe_s"), m.get("height"))
    if max_height and h and h > max_height:
        tips.append(f"The stream is {h}p but this camera's limit on YonderView is {max_height}p: it would be "
                    f"declined. Set the camera's stream to {max_height}p or lower, or pick its sub-stream.")
    if fps and fps > 20.5:
        tips.append(f"It runs at {fps:g} fps. Weather looks just as good at 15-20 fps, and the saved upload "
                    "goes into picture quality instead.")
    if fps and actual and actual < 0.85 * fps:
        tips.append(f"Only {actual:g} of {fps:g} frames per second actually arrived. In low light many cameras "
                    "slow down on their own; otherwise the camera may be overloaded.")
    if kbps and kbps > 8000:
        tips.append(f"It's using about {kbps / 1000:.1f} Mbps. Around 4 Mbps (capped VBR, max about 6) is plenty "
                    "for 1080p and much easier on the upload.")
    elif kbps and h and h >= 1080 and kbps < 1500:
        tips.append(f"It's only about {kbps / 1000:.1f} Mbps at {h}p, which can look soft in rain or wind. "
                    "Around 4 Mbps (capped VBR) is a good target, if the upload allows it.")
    if gop and gop > 3:
        tips.append(f"Keyframes come about every {gop:g} seconds. Set the camera's I-frame interval to about "
                    "2 seconds (twice the frame rate) so the video starts faster and plays smoother.")
    return tips
