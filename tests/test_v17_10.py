"""0.17.10: exact degrees from Dahua-family cameras (exact.py); setup and the checks say whether PTZ position works."""
import http.server, sys, threading
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import camera, exact

# what an Amcrest IP4M-1063 answered (zoomed in to 6.3x)
ANSWER = """status.AbsPosition[0]=20669
status.AbsPosition[1]=515
status.AbsPosition[2]=630
status.MoveStatus=Idle
status.Postion[0]=206.690000
status.Postion[1]=5.150000
status.Postion[2]=32.256000
status.ZoomMapValue=10763
status.ZoomValue=400
"""
assert exact.parse(ANSWER) == {"pan": 206.69, "tilt": 5.15, "zoom": 6.3}, exact.parse(ANSWER)
assert exact.parse("status.Postion[0]=nan\nstatus.Postion[1]=1") is None
assert exact.parse("Error\nBad Request!") is None
assert exact.parse("status.Postion[0]=10\nstatus.Postion[1]=120") is None          # tilt out of range
assert exact.is_dahua("Amcrest") and exact.is_dahua("Dahua Technology") and not exact.is_dahua("HIKVISION")

# a refused login is tried once, not six times (cameras count each toward their lockout)
hits = []
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(bool(self.headers.get("Authorization")))
        if self.server.ok and self.headers.get("Authorization"):
            body = ANSWER.encode(); self.send_response(200); self.end_headers(); self.wfile.write(body); return
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Digest realm="Login to cam", qop="auth", nonce="abc123", opaque="x"')
        self.end_headers()
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), H); srv.ok = False
threading.Thread(target=srv.serve_forever, daemon=True).start()
r = exact.Reader("127.0.0.1", "u", "wrong", port=srv.server_port)
assert r.read() is None and r.off == exact.REFUSED_NOTE
assert hits.count(True) == 1, hits                     # one try with the password
assert r.read() is None and hits.count(True) == 1      # and it stops asking
srv.ok = True; hits.clear()
r = exact.Reader("127.0.0.1", "u", "right", port=srv.server_port)
assert r.read()["pan"] == 206.69 and r.read()["zoom"] == 6.3 and not r.off
srv.shutdown()

# describe_position: what setup and the checks print
ok, text = camera.describe_position({"onvif": {"pan": -0.1483, "tilt": 0.6162, "zoom": 0.252}, "error": "",
                                     "exact": {"pan": 206.69, "tilt": 5.15, "zoom": 6.3}, "exact_note": ""})
assert ok and "206.7° across" in text and "5.2° down" in text and "6.3x" in text, text
ok, text = camera.describe_position({"onvif": None, "error": "timed out", "exact": None, "exact_note": ""})
assert ok is False and "timed out" in text and "presets" in text
assert camera.describe_position(None) == (None, "")

# ptz_position_check: GetStatus answered; a non-Dahua camera isn't asked for degrees
pt = NS(x=-0.5, y=0.25); st = NS(Position=NS(PanTilt=pt, Zoom=NS(x=0.1)))
cam = NS(create_ptz_service=lambda: NS(GetStatus=lambda a: st))
media = NS(GetProfiles=lambda: [NS(token="p0"), NS(token="p1", PTZConfiguration=object())])
got = camera.ptz_position_check(cam, media, "10.0.0.9", "u", "p", "HIKVISION")
assert got == {"onvif": {"pan": -0.5, "tilt": 0.25, "zoom": 0.1}, "error": "", "exact": None, "exact_note": ""}, got
assert camera.ptz_position_check(cam, NS(GetProfiles=lambda: [NS(token="p0")]), "h", "u", "p") is None
def boom(a): raise RuntimeError("no PTZ status")
got = camera.ptz_position_check(NS(create_ptz_service=lambda: NS(GetStatus=boom)), media, "h", "u", "p", "X")
assert got["onvif"] is None and got["error"]

# the agent's PTZ: degrees ride along with the position; a refused login shows on the Health tab
from camagent.ptz import PTZ
p = PTZ({"platform": {"camera_id": "c"}, "agent": {}, "camera": {}})
p.connected, p.token, p.ptz = True, "p1", NS(GetStatus=lambda a: st)
p.exact = NS(read=lambda: {"pan": 90.0, "tilt": 10.0, "zoom": 2.0}, off="")
assert p.position()["deg"] == {"pan": 90.0, "tilt": 10.0, "zoom": 2.0}
p.exact = NS(read=lambda: None, off=exact.REFUSED_NOTE)
assert "deg" not in p.position() and p.problem() == exact.REFUSED_NOTE
print("v0.17.10 OK")


# ---- a login just for YonderView ----
from unittest import mock
import builtins
from camagent import configure
pw = camera.new_password()
assert len(pw) == 16 and pw.isalnum() and any(c.isdigit() for c in pw) and not set(pw) & set("0O1lI")

# setup shows a made-up login, waits while the host adds it, checks it once per "check again"
logins = []
def connect(host, port, user, password):
    logins.append((user, password))
    if len(logins) < 2:
        raise Exception("401 Unauthorized")
    return NS(devicemgmt=NS(GetDeviceInformation=lambda: NS(Manufacturer="X")))
cam = {"host": "10.0.0.5", "onvif_port": 80, "username": "", "password": ""}
answers = iter(["", "1", ""])               # Enter (not added yet) -> "check again" -> Enter
import io, contextlib
out = io.StringIO()
with mock.patch.object(builtins, "input", lambda *a: next(answers)), mock.patch.object(camera, "onvif_connect", connect), \
        mock.patch.object(configure, "_exact_needs_web_user", lambda *a: None), contextlib.redirect_stdout(out):
    assert configure._make_login(cam)
assert cam["username"] == "yonderview" and len(cam["password"]) == 16 and cam["password"] in out.getvalue()
assert len(logins) == 2 and all(u == "yonderview" for u, _ in logins)     # never the admin login
assert "admin" not in " ".join(u for u, _ in logins) and "Not Administrator" in out.getvalue()
# giving up on it: enter a different login
cam = {"host": "10.0.0.5", "onvif_port": 80, "username": "", "password": ""}
logins.clear(); answers = iter(["", "2"])
with mock.patch.object(builtins, "input", lambda *a: next(answers)), \
        mock.patch.object(camera, "onvif_connect", lambda *a: (_ for _ in ()).throw(Exception("401 Unauthorized"))), \
        contextlib.redirect_stdout(io.StringIO()):
    assert configure._make_login(cam) is False and cam["username"] == ""

# ---- the move-and-read test ----
class FakePTZ:
    def __init__(self, follows=True, absolute=True):
        self.x, self.follows, self.absolute, self.calls = 0.2, follows, absolute, []
    def GetStatus(self, a): return NS(Position=NS(PanTilt=NS(x=self.x, y=0.1), Zoom=NS(x=0.0)))
    def ContinuousMove(self, a):
        self.calls.append(("move", a["Velocity"]["PanTilt"]["x"]))
        if self.follows: self.x += 0.02 if a["Velocity"]["PanTilt"]["x"] > 0 else -0.02
    def Stop(self, a): self.calls.append(("stop",))
    def AbsoluteMove(self, a):
        if not self.absolute: raise Exception("not supported")
        self.calls.append(("abs", a["Position"]["PanTilt"]["x"])); self.x = a["Position"]["PanTilt"]["x"]
for follows, absolute in ((True, True), (False, True), (True, False)):
    f = FakePTZ(follows, absolute)
    with mock.patch.object(camera, "onvif_connect", lambda *a: NS(create_ptz_service=lambda: f)), \
            mock.patch.object(camera.time, "sleep", lambda s: None):
        r = camera.ptz_move_test("h", 80, "u", "p", token="p1")
    assert r["follows"] is follows and r["back"] and abs(f.x - 0.2) < 1e-9, (follows, absolute, r, f.calls)
    assert ("abs", 0.2) in f.calls if absolute else ("move", -0.3) in f.calls
print("v0.17.10 login + move test OK")

# ---- a refused ONVIF login is tried once, not twice ----
import camagent._onvif as onvif_pkg
tries = []
def refuse(*a, **k):
    tries.append(k.get("adjust_time")); raise Exception("Sender not Authorized. Invalid username or password!")
with mock.patch.object(onvif_pkg, "ONVIFCamera", refuse):
    try:
        camera.onvif_connect("h", 80, "u", "bad"); raise AssertionError("should refuse")
    except Exception as e:
        assert "Authorized" in str(e)
assert tries == [True], tries
tries.clear()
def timeout_first(*a, **k):
    tries.append(k.get("adjust_time"))
    if k.get("adjust_time"): raise Exception("timed out asking the time")
    return "cam"
with mock.patch.object(onvif_pkg, "ONVIFCamera", timeout_first):
    assert camera.onvif_connect("h", 80, "u", "p") == "cam" and tries == [True, None]
print("v0.17.10 one try OK")

# ---- Amcrest: the new login refused for exact degrees -> the steps to add it as a normal user ----
dev = lambda maker: NS(devicemgmt=NS(GetDeviceInformation=lambda: NS(Manufacturer=maker)))
for maker, read, shown in (("Amcrest", (None, exact.REFUSED_NOTE), True), ("Amcrest", ({"pan": 1.0}, ""), False),
                           ("HIKVISION", (None, exact.REFUSED_NOTE), False)):
    class R:
        def __init__(self, *a, **k): self.off = ""
        def read(self): self.off = read[1]; return read[0]
    out = io.StringIO()
    with mock.patch.object(camera, "onvif_connect", lambda *a: dev(maker)), mock.patch.object(exact, "Reader", R), \
            contextlib.redirect_stdout(out):
        configure._exact_needs_web_user({"host": "h", "onvif_port": 80}, "yonderview", "Abcdefgh23456789")
    assert ("Abcdefgh23456789" in out.getvalue()) is shown, (maker, read, out.getvalue())
print("v0.17.10 web user note OK")
