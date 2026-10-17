"""Run with: python tests/test_v12.py  (from the repo folder).
Security fixes from the 2026-10-16 review."""
import json, os, stat, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, link, prereqs, service
from camagent.agent import CameraAgent

# 1. ffmpeg is never taken from the current folder or a relative PATH entry (it runs as SYSTEM/root)
d = Path(tempfile.mkdtemp())
planted = d / "ffmpeg"
planted.write_text("#!/bin/sh\n"); planted.chmod(planted.stat().st_mode | stat.S_IEXEC)
real = Path(tempfile.mkdtemp()) / "ffmpeg"
real.write_text("#!/bin/sh\n"); real.chmod(real.stat().st_mode | stat.S_IEXEC)
cwd = os.getcwd(); os.chdir(d)
try:
    with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([".", "", "relative/bin", str(real.parent)])}):
        assert prereqs.which("ffmpeg") == str(real), prereqs.which("ffmpeg")
    with mock.patch.dict(os.environ, {"PATH": "."}):
        assert prereqs.which("ffmpeg") is None
finally:
    os.chdir(cwd)
assert "C:\\\\" not in Path(prereqs.__file__).read_text().split("def _windows_ffmpeg_candidates")[1].split("def ")[0].split('"""')[2]

# 2. a save that fails leaves no copy of the passwords behind
cfgdir = Path(tempfile.mkdtemp())
with mock.patch.object(config, "dumps", side_effect=RuntimeError("disk full")):
    try:
        config.save({}, cfgdir / "camagent.toml")
        raise AssertionError("expected the error")
    except RuntimeError:
        pass
assert not list(cfgdir.glob(".camagent.*")), list(cfgdir.iterdir())

# 3. MQTT: plain (no TLS) only to this computer; the default is TLS on 8883
assert config.DEFAULTS["platform"]["mqtt_tls"] is True and config.DEFAULTS["platform"]["mqtt_port"] == 8883
def agent(**p):
    c = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
    c["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=False)
    c["platform"].update(camera_id="sec_1", ingest_host="ingest.example", srt_password="s", mqtt_password="m", **p)
    return CameraAgent(c)
assert agent(mqtt_host="mqtt.example", mqtt_tls=False).mqtt_refused
assert not agent(mqtt_host="mqtt.example", mqtt_tls=True).mqtt_refused
assert not agent(mqtt_host="127.0.0.1", mqtt_tls=False, mqtt_port=1883).mqtt_refused

# 4. a bad latency value from the server doesn't break MQTT
a = agent(mqtt_host="mqtt.example")
a._apply_config(json.dumps({"latency_ms": "lots"}).encode())

# 5. sign-in: https only (plain http just for this computer), and the token never follows a redirect
for bad in ("http://yonderview.net", "ftp://x", "yonderview.net"):
    try:
        link.cameras(bad, "yva_x")
        raise AssertionError(bad)
    except link.LinkError as e:
        assert "https://" in str(e)
seen = []
class Other(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        seen.append(self.headers.get("Authorization"))
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"cameras": []}')
other = HTTPServer(("127.0.0.1", 0), Other)
class Redirect(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(302); self.send_header("Location", f"http://localhost:{other.server_port}/steal")
        self.end_headers()
site = HTTPServer(("127.0.0.1", 0), Redirect)
for s in (other, site):
    threading.Thread(target=s.serve_forever, daemon=True).start()
try:
    link.cameras(f"http://127.0.0.1:{site.server_port}", "yva_secret")
except link.LinkError:
    pass
assert seen == [], seen                                           # the token never reached the other address
site.shutdown(); other.shutdown()

# 6. the service wrapper (runs as SYSTEM) is checked before it's installed
class Resp:
    def __init__(self, data): self.data = data
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): pass
target = Path(tempfile.mkdtemp()) / "svc.exe"
with mock.patch("urllib.request.urlopen", lambda *a, **k: Resp(b"not winsw")):
    try:
        service._download_checked(service.WINSW_URL, service.WINSW_SHA256, target)
        raise AssertionError("expected refusal")
    except SystemExit as e:
        assert "checksum" in str(e)
assert not target.exists()
print("v0.12 security OK")
