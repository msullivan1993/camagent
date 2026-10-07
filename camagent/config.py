"""Configuration file handling (camagent.toml) and small URL helpers."""
import copy
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse, urlunparse

from . import DEFAULT_REPO

WSDL_DIR = Path(__file__).parent / "wsdl"

DEFAULTS = {
    "camera": {
        "host": "",              # camera IP or hostname on the local network
        "onvif_port": 80,
        "username": "",
        "password": "",
        "rtsp_url": "",          # without credentials; they are added at runtime
        "ptz": True,             # set false for fixed cameras
        "ptz_profile": "",       # ONVIF profile token; blank = first profile with PTZ
    },
    "stream": {
        "enabled": True,
        "audio": False,
        "srt_latency_ms": 400,
        "wallclock_timestamps": True,
        "stall_seconds": 15,     # restart ffmpeg if no new frames for this long
        "ffmpeg": "",            # full path; blank = find on PATH
    },
    "platform": {
        "camera_id": "",
        "ingest_host": "",
        "ingest_port": 8890,
        "srt_password": "",
        "srt_passphrase": "",
        "mqtt_host": "",
        "mqtt_port": 1883,
        "mqtt_tls": False,
        "mqtt_password": "",
    },
    "agent": {
        "max_move_ms": 2000,
        "telemetry_interval_s": 30,
        "stale_command_s": 5,
    },
    "update": {
        "repo": DEFAULT_REPO,
        "ref": "main",
    },
}

SECTION_COMMENTS = {
    "camera": "The camera on the local network",
    "stream": "Video uplink to the server (ffmpeg -> SRT)",
    "platform": "Server connection details (from yvcam: 'Show stream settings')",
    "agent": "Agent behavior",
    "update": "Where 'camagent update' installs from",
}


def base_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "camagent"
    return Path("/opt/camagent")


def default_config_path() -> Path:
    if os.name == "nt":
        return base_dir() / "camagent.toml"
    return Path("/etc/camagent/camagent.toml")


def _merge(defaults, loaded):
    out = copy.deepcopy(defaults)
    for section, values in (loaded or {}).items():
        if isinstance(values, dict):
            out.setdefault(section, {}).update(values)
    return out


def load(path=None) -> dict:
    path = Path(path or default_config_path())
    with path.open("rb") as f:
        return _merge(DEFAULTS, tomllib.load(f))


def load_or_defaults(path=None) -> dict:
    try:
        return load(path)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v))          # JSON string escaping is valid TOML basic-string syntax


def dumps(cfg: dict) -> str:
    lines = ["# camagent configuration", "# Edit with: camagent configure", ""]
    for section in DEFAULTS:
        if SECTION_COMMENTS.get(section):
            lines.append(f"# {SECTION_COMMENTS[section]}")
        lines.append(f"[{section}]")
        for key, value in cfg.get(section, {}).items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def save(cfg: dict, path=None) -> Path:
    path = Path(path or default_config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".camagent.")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(cfg))
    if os.name != "nt":
        # readable only by root and the service account, since it holds passwords
        try:
            import grp
            os.chown(tmp, 0, grp.getgrnam("camagent").gr_gid)
            os.chmod(tmp, 0o640)
        except (KeyError, PermissionError):
            os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


# ---------- URL helpers ----------

def rtsp_with_auth(url: str, user: str, password: str) -> str:
    """Insert credentials into an RTSP URL unless it already has some."""
    u = urlparse(url)
    if u.username or not user:
        return url
    hostport = u.netloc.split("@")[-1]
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{hostport}"
    return urlunparse((u.scheme, netloc, u.path, u.params, u.query, u.fragment))


def strip_auth(url: str) -> str:
    u = urlparse(url)
    return urlunparse((u.scheme, u.netloc.split("@")[-1], u.path, u.params, u.query, u.fragment))


def redact(url: str) -> str:
    """For logging: hide passwords in RTSP and SRT URLs."""
    u = urlparse(url)
    if u.password:
        url = url.replace(f":{u.password}@", ":***@")
    if u.scheme == "srt":
        parts = []
        for item in u.query.split("&"):
            k, _, v = item.partition("=")
            if k == "passphrase":
                v = "***"
            elif k == "streamid" and v.count(":") >= 3:
                v = ":".join(v.split(":")[:3] + ["***"])
            parts.append(f"{k}={v}")
        url = urlunparse((u.scheme, u.netloc, u.path, u.params, "&".join(parts), u.fragment))
    return url


_URL_AUTH = re.compile(r"(rtsps?|srt|https?)://[^\s/@]+@")
_SECRETS = re.compile(r"(passphrase=)[^&\s]+|(streamid=[^:&\s]*:[^:&\s]*:[^:&\s]*:)[^&\s]+")


def scrub(text: str) -> str:
    """Remove credentials from free text such as ffmpeg error messages."""
    text = _URL_AUTH.sub(lambda m: f"{m.group(1)}://***@", text)
    return _SECRETS.sub(lambda m: (m.group(1) or m.group(2)) + "***", text)


def srt_url(cfg: dict) -> str:
    p, s = cfg["platform"], cfg["stream"]
    cid = p["camera_id"]
    params = [
        f"streamid=publish:{cid}:{cid}:{p['srt_password']}",
        f"latency={int(s['srt_latency_ms']) * 1000}",
        "pkt_size=1316",
    ]
    if p.get("srt_passphrase"):
        params += [f"passphrase={p['srt_passphrase']}", "pbkeylen=16"]
    return f"srt://{p['ingest_host']}:{int(p['ingest_port'])}?" + "&".join(params)


def parse_settings_block(text: str) -> dict:
    """Parse the block printed by yvcam ('Show stream settings') into platform settings."""
    values = {}
    for line in text.splitlines():
        key, sep, val = line.strip().partition("=")
        if sep:
            values[key.strip().upper()] = val.strip()

    out = {}
    srt = values.get("SRT_URL")
    if srt:
        u = urlparse(srt)
        q = parse_qs(u.query, keep_blank_values=True)
        out["ingest_host"] = u.hostname or ""
        out["ingest_port"] = u.port or 8890
        sid = (q.get("streamid") or [""])[0].split(":")
        if len(sid) >= 4:
            out["camera_id"] = sid[1]
            out["srt_password"] = ":".join(sid[3:])
        if q.get("passphrase"):
            out["srt_passphrase"] = q["passphrase"][0]
        if q.get("latency"):
            out["_srt_latency_ms"] = int(int(q["latency"][0]) / 1000)
    if values.get("CAMERA"):
        out["camera_id"] = values["CAMERA"]
    if values.get("MQTT_HOST"):
        out["mqtt_host"] = values["MQTT_HOST"]
    if values.get("MQTT_PASS"):
        out["mqtt_password"] = values["MQTT_PASS"]
    return out
