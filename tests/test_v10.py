"""Run with: python tests/test_v10.py  (from the repo folder).
Sign in to YonderView (device sign-in) and set a camera up from the account, against a fake site."""
import builtins, json, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, configure, link

BLOCK = "\n".join(["CAMERA=ridge_0001",
                   "SRT_URL=srt://ingest.yonderview.net:8890?streamid=publish:ridge_0001:ridge_0001:PW&latency=400000"
                   "&pkt_size=1316&passphrase=PHRASE&pbkeylen=16",
                   "MQTT_HOST=mqtt.yonderview.net", "MQTT_PORT=8883", "MQTT_TLS=true", "MQTT_USER=ridge_0001",
                   "MQTT_PASS=MQ"])
state = {"polls": 0, "valid": {"yva_good"}, "has_credentials": True, "calls": []}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return self.headers.get("Authorization", "").removeprefix("Bearer ") in state["valid"]

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        state["calls"].append((self.path, data))
        if self.path == "/api/agent/link/start":
            assert data["name"]                                   # the computer's name, shown on the approval page
            return self.reply(200, {"device_code": "DEV", "user_code": "BCDF-2345", "interval": 1, "expires_in": 60,
                                    "verification_uri": "https://example/link/",
                                    "verification_uri_complete": "https://example/link/?code=BCDF-2345"})
        if self.path == "/api/agent/link/poll":
            state["polls"] += 1
            if state["polls"] < 3:
                return self.reply(202, {"status": "pending"})
            return self.reply(200, {"status": "approved", "token": "yva_good", "account": "Mike S"})
        if self.path == "/api/agent/cameras/ridge_0001/connect":
            if not self.authed():
                return self.reply(401, {"error": "Sign in again."})
            if state["has_credentials"] and not data.get("replace"):
                return self.reply(409, {"needs_confirm": True, "error": "\"Ridge\" already has connection settings."})
            return self.reply(200, {"settings_block": BLOCK, "camera": {"slug": "ridge_0001", "max_height": 1080}})
        self.reply(404, {})

    def do_GET(self):
        if self.path == "/api/agent/cameras":
            if not self.authed():
                return self.reply(401, {"error": "Sign in again."})
            return self.reply(200, {"account": "Mike S", "cameras": [
                {"slug": "ridge_0001", "name": "Ridge", "place": "Ashland, KY", "has_credentials": True,
                 "enabled": True, "max_height": 1080}]})
        self.reply(404, {})


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
SITE = f"http://127.0.0.1:{srv.server_port}"

# 1. The sign-in itself: shows the code, waits for approval, returns the token
shown = []
token, account = link.sign_in(SITE, name="OFFICE-PC", show=shown.append, sleep=lambda s: None)
assert (token, account) == ("yva_good", "Mike S")
assert "BCDF-2345" in "\n".join(shown) and state["polls"] == 3
assert link.cameras(SITE, token)[0]["slug"] == "ridge_0001"
assert link.connect(SITE, token, "ridge_0001")[0] == "confirm"                 # has settings: asks first
assert link.connect(SITE, token, "ridge_0001", replace=True)[0] == "ok"
try:
    link.cameras(SITE, "yva_unlinked")
    raise AssertionError("expected NotSignedIn")
except link.NotSignedIn:
    pass
try:
    link.cameras("http://127.0.0.1:9", "x")                                    # nothing listening: plain words
    raise AssertionError("expected LinkError")
except link.LinkError as e:
    assert "Couldn't reach" in str(e)

# 2. Setting a camera up by signing in: Server step -> sign in -> pick the camera -> confirm -> saved
d = Path(tempfile.mkdtemp()); main = d / "camagent.toml"
m = config.load_or_defaults(main); m["account"]["site"] = SITE; config.save_main(m, main)
state["polls"] = 0
answers = iter(["n", "10.0.0.9", "", "admin", "2",     # no search, address, port, user; ONVIF fails -> go on without
                "n", "rtsp://10.0.0.9/main", "n",       # no suggestions, RTSP URL, no PTZ
                "1",                                     # Server: sign in to YonderView
                "1",                                     # which camera: Ridge
                "y",                                     # make new connection settings for it
                "n"])                                    # don't install the service now
patches = [mock.patch.object(builtins, "input", lambda *a: next(answers)),
           mock.patch("getpass.getpass", lambda *a: ""),
           mock.patch("time.sleep", lambda s: None),
           mock.patch.object(configure.camera, "find_ffmpeg", lambda *a: "/usr/bin/ffmpeg"),
           mock.patch.object(configure.camera, "find_ffprobe", lambda *a: "/usr/bin/ffprobe"),
           mock.patch.object(configure.camera, "probe", lambda *a: [{"codec_type": "video", "codec_name": "h264", "height": 1080}]),
           mock.patch.object(configure.camera, "measure_stream", lambda *a, **k: {"height": 1080, "actual_fps": 20}),
           mock.patch.object(configure.camera, "onvif_inspect", side_effect=OSError("no onvif")),
           mock.patch.object(configure, "test_mqtt", lambda c: (True, "ok")),
           mock.patch.object(configure.service, "is_installed", lambda: False),
           mock.patch.object(configure, "_ask_auto_update", lambda *a: None),
           mock.patch.object(configure.service, "is_admin", lambda: True)]
for p in patches: p.start()
configure.add(main)
for p in patches: p.stop()
_, cams, _ = config.load_all(main)
p = cams[0]["platform"]
assert (p["camera_id"], p["srt_password"], p["srt_passphrase"], p["mqtt_password"], p["mqtt_port"], p["mqtt_tls"]) == \
    ("ridge_0001", "PW", "PHRASE", "MQ", 8883, True)
saved = config.load(main)["account"]
assert (saved["token"], saved["name"]) == ("yva_good", "Mike S")                # remembered for next time
assert "yva_good" not in (d / "cameras" / "ridge_0001.toml").read_text()        # only in the shared file

# 3. Next time: already signed in, no new code; an unlinked token signs in again
starts = sum(1 for path, _ in state["calls"] if path == "/api/agent/link/start")
assert configure.account_token(main)[1] == "yva_good"
assert sum(1 for path, _ in state["calls"] if path == "/api/agent/link/start") == starts
m = config.load(main); m["account"]["token"] = "yva_unlinked"; config.save_main(m, main)
state["polls"] = 0
cfg = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
answers = iter(["1", "y"])
with mock.patch.object(builtins, "input", lambda *a: next(answers)), mock.patch("time.sleep", lambda s: None):
    assert configure._from_account(cfg, main)
assert config.load(main)["account"]["token"] == "yva_good"
assert cfg["platform"]["camera_id"] == "ridge_0001" and cfg["_server"]["max_height"] == 1080

# 4. Sign out forgets it on this computer
with mock.patch.object(builtins, "print", lambda *a, **k: None):
    configure.sign_out(main)
assert config.load(main)["account"]["token"] == ""
srv.shutdown()
print("v0.10 sign-in OK")
