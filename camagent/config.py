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
        "fps_configured": 0,     # the camera's own frame-rate setting (read over ONVIF during setup)
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
        "ref": "",               # blank = the latest release
        "auto": True,            # install new releases automatically, nightly around 3 AM
        "asked": False,          # setup asked about automatic updates
    },
}

CAMERA_SECTIONS = ("camera", "stream", "platform")      # one file per camera, in cameras/
MAIN_SECTIONS = ("agent", "update")                     # shared settings, in camagent.toml

SECTION_COMMENTS = {
    "camera": "The camera on the local network",
    "stream": "Video uplink to the server (ffmpeg -> SRT)",
    "platform": "Server connection details (the settings block from the camera's Connection page)",
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


def dumps(cfg: dict, sections=None, title="camagent configuration") -> str:
    lines = [f"# {title}", "# Edit with: camagent configure", ""]
    for section in (sections or DEFAULTS):
        if SECTION_COMMENTS.get(section):
            lines.append(f"# {SECTION_COMMENTS[section]}")
        lines.append(f"[{section}]")
        for key, value in cfg.get(section, {}).items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def _secure_dir(d: Path):
    """Config folders hold passwords: on Linux, readable only by root and the camagent service account."""
    d.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            import grp
            os.chown(d, 0, grp.getgrnam("camagent").gr_gid)
            os.chmod(d, 0o750)
        except (KeyError, PermissionError):
            pass


def save(cfg: dict, path=None, sections=None, title="camagent configuration") -> Path:
    path = Path(path or default_config_path())
    _secure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".camagent.")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(cfg, sections, title))
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


# ---------- several cameras ----------
# camagent.toml holds the shared [agent] and [update] settings; each camera has its own file in
# cameras/<camera id>.toml with [camera], [stream] and [platform]. A camagent.toml from 0.1.x that
# still has a camera in it keeps working: it counts as one camera until it's edited or migrated.

def cameras_dir(main_path=None) -> Path:
    return Path(main_path or default_config_path()).parent / "cameras"


def camera_path(camera_id: str, main_path=None) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", camera_id or ""):
        raise ValueError(f"not a valid camera ID: {camera_id!r}")
    return cameras_dir(main_path) / f"{camera_id}.toml"


def _combine(main: dict, camera: dict) -> dict:
    out = copy.deepcopy(camera)
    for s in MAIN_SECTIONS:
        out[s] = copy.deepcopy(main[s])
    return out


def load_all(main_path=None):
    """Returns (main, cameras, problems). Each camera is a full config dict with "_source" (its file)
    and "_legacy" (True if it still lives in camagent.toml). A broken camera file is reported in
    problems and skipped, so the other cameras still run."""
    main_path = Path(main_path or default_config_path())
    main = load_or_defaults(main_path)
    cams, problems, seen = [], [], set()
    d = cameras_dir(main_path)
    for f in sorted(d.glob("*.toml")) if d.is_dir() else []:
        try:
            with f.open("rb") as fh:
                cam = _combine(main, _merge(DEFAULTS, tomllib.load(fh)))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{f.name}: {e}")
            continue
        cid = cam["platform"].get("camera_id") or f.stem
        cam["platform"]["camera_id"] = cid
        if cid in seen:
            problems.append(f"{f.name}: camera '{cid}' is configured twice; ignored")
            continue
        seen.add(cid)
        cam["_source"], cam["_legacy"] = f, False
        cams.append(cam)
    legacy_id = main["platform"].get("camera_id")
    if legacy_id and legacy_id not in seen:
        cam = copy.deepcopy(main)
        cam["_source"], cam["_legacy"] = main_path, True
        cams.insert(0, cam)
    return main, cams, problems


def save_camera(cam: dict, main_path=None) -> Path:
    """Write one camera to cameras/<id>.toml. If it came from an old single-camera camagent.toml,
    that file is rewritten with only the shared settings (migration)."""
    main_path = Path(main_path or default_config_path())
    cid = cam["platform"]["camera_id"]
    path = camera_path(cid, main_path)
    _secure_dir(path.parent)
    save(cam, path, CAMERA_SECTIONS, f"camagent camera: {cid}")
    old = cam.get("_source")
    if cam.get("_legacy") or not main_path.exists():
        save_main(load_or_defaults(main_path) if main_path.exists() else cam, main_path)
    elif old and Path(old) != path and Path(old).parent == path.parent:
        Path(old).unlink(missing_ok=True)             # the camera ID changed: drop the old file
    cam["_source"], cam["_legacy"] = path, False
    return path


def save_main(main: dict, main_path=None) -> Path:
    return save(main, main_path, MAIN_SECTIONS, "camagent shared settings (cameras are in the cameras folder)")


def remove_camera(cam: dict, main_path=None):
    main_path = Path(main_path or default_config_path())
    if cam.get("_legacy"):
        main = load(main_path)
        for s in CAMERA_SECTIONS:
            main[s] = copy.deepcopy(DEFAULTS[s])
        save_main(main, main_path)
    else:
        Path(cam["_source"]).unlink(missing_ok=True)


# ---------- runtime status (written by the service, read by `camagent list`) ----------

def status_path() -> Path:
    if os.name == "nt":
        return base_dir() / "status.json"
    return Path("/var/lib/camagent/status.json")


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
    if values.get("MQTT_PORT", "").isdigit():
        out["mqtt_port"] = int(values["MQTT_PORT"])
    if values.get("MQTT_TLS"):
        out["mqtt_tls"] = values["MQTT_TLS"].strip().lower() in ("1", "true", "yes", "on")
    return out
