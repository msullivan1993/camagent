"""0.21.0: lockout warning and stop after 3 rejected logins, sign-in text for a computer with no screen, the website
helper retries results and survives surprises, update results on the Health tab, privacy check only for shared
computers, the Raspberry Pi 4 video test threshold."""
import io
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import __version__, camera, config, configure, doctor, link, prereqs, remote, update  # noqa: E402

assert __version__ >= "0.21"

# ---- 1. lockout warning once, stop after 3 rejected tries ----
configure._reset_login_tries()
cam = {"host": "192.168.1.5", "onvif_port": 80, "username": "", "password": ""}
buf = io.StringIO()
with redirect_stdout(buf), mock.patch("builtins.input", return_value=""), \
        mock.patch.object(camera, "onvif_connect", side_effect=Exception("Unauthorized")) as oc, \
        mock.patch.object(configure, "choose", return_value="I've fixed it. Check again"):
    try:
        configure._make_login(cam)
        raise AssertionError("should have stopped")
    except SystemExit as e:
        assert "3 times" in str(e) and "web page" in str(e) and "later" in str(e)
assert oc.call_count == 3
text = buf.getvalue()
assert text.count("lock you out after a few wrong tries") == 1
assert text.index("lock you out") < text.index("CamAgent is checking the login")
# step 2 also refuses to try once the limit is reached
configure._reset_login_tries()
configure._login_state["tries"] = 3
with mock.patch.object(camera, "onvif_inspect") as oi:
    try:
        configure.step_pick_stream({"camera": cam, "stream": {}}, "ffprobe")
        raise AssertionError("should have stopped")
    except SystemExit:
        pass
    assert not oi.called
configure._reset_login_tries()
assert configure._login_state == {"tries": 0, "warned": False}

# ---- 2. sign-in text on a computer with no screen ----
reply = {"device_code": "d", "user_code": "ABCD-1234", "verification_uri": "https://yonderview.net/link",
         "expires_in": 600, "interval": 1}
for env, screenless in (({}, True), ({"DISPLAY": ":0"}, False)):
    lines = []
    with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.platform", "linux"), \
            mock.patch.object(link, "_call", side_effect=[(200, reply), (200, {"status": "approved", "token": "t"})]), \
            mock.patch("webbrowser.open") as wb:
        assert link.sign_in(show=lines.append, sleep=lambda s: None)[0] == "t"
    out = "\n".join(lines)
    assert not wb.called
    assert "ABCD-1234" in out and "10 minutes" in out and "phone or another computer" in out
    assert ("no screen or browser" in out) == screenless

# ---- 3. website helper: result retries, survives surprises ----
site = ("https://yonderview.net", "tok")
sleeps = []
with mock.patch.object(link, "job_result", side_effect=[False, link.LinkError("x"), True]) as jr:
    assert remote._send_result(*site, 1, True, {}, sleeps.append) is True
assert jr.call_count == 3 and sleeps == [2, 5]
sleeps.clear()
with mock.patch.object(link, "job_result", return_value=False) as jr, \
        mock.patch.object(remote.log, "warning") as warn:
    assert remote._send_result(*site, 2, True, {}, sleeps.append) is False
assert jr.call_count == 3 and sleeps == [2, 5] and "giving up" in warn.call_args[0][0]
with mock.patch.object(link, "job_result", side_effect=link.NotSignedIn("out")):
    try:
        remote._send_result(*site, 3, True, {}, sleeps.append)
        raise AssertionError("NotSignedIn should pass through")
    except link.NotSignedIn:
        pass


class Stop(Exception):
    pass


waits = []


def _sleep(s):
    waits.append(s)
    if len(waits) >= 3:
        raise Stop


with mock.patch.object(remote, "_account", return_value=site), \
        mock.patch.object(remote, "_installed", return_value=__version__), \
        mock.patch.object(remote, "prune_pending"), mock.patch.object(remote, "_measure_if_needed"), \
        mock.patch.object(remote, "_info", side_effect=[RuntimeError("boom"), {}, {}]), \
        mock.patch.object(link, "checkin", return_value={"jobs": [], "next_s": 9}):
    try:
        remote.run(sleep=_sleep)
    except Stop:
        pass
assert waits == [5, 9, 9]              # backed off after the surprise, then carried on
with mock.patch.object(remote, "_account", return_value=site), \
        mock.patch.object(remote, "_info", side_effect=RuntimeError("boom")):
    assert remote.run(once=True) == "error"

# ---- 4. update results on the Health tab ----
with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "state_dir", return_value=Path(d)):
    assert prereqs.update_notice() == ""
    update._record(False, "0.99.1", "the install didn't work (disk full)")
    saved = json.loads((Path(d) / "update_result.json").read_text())
    assert saved["ok"] is False and saved["version"] == "0.99.1" and abs(saved["at"] - time.time()) < 5
    note = prereqs.update_notice()
    assert "couldn't update to 0.99.1" in note and f"stayed on {__version__}" in note and "disk full" in note
    assert "tries again tonight" in note and len(note) < 300
    assert note in prereqs.notices()
    saved["at"] -= 8 * 86400
    (Path(d) / "update_result.json").write_text(json.dumps(saved))
    assert prereqs.update_notice() == ""                          # older than 7 days
    update._record(True, "0.99.1", "updated")
    assert prereqs.update_notice() == ""
    update._record(False, "0.20.0", "x")
    assert prereqs.update_notice() == ""                          # already newer than the failed one
    # the nightly path records its outcomes
    with mock.patch.object(update.service, "_require_admin"), \
            mock.patch.object(config, "load_or_defaults",
                              return_value={"update": {"repo": "https://github.com/a/b.git", "auto": True}}), \
            mock.patch.object(update, "latest_release", return_value="v9.0.0"), \
            mock.patch.object(update, "_install", side_effect=RuntimeError("no space")), \
            mock.patch.object(update, "_log"):
        update.run(auto=True)
    r = json.loads((Path(d) / "update_result.json").read_text())
    assert r["ok"] is False and r["version"] == "9.0.0" and "no space" in r["message"]

# ---- 5. privacy warning only when more than one person logs in ----
if True:
    d = tempfile.mkdtemp()
    one, two = Path(d) / "one", Path(d) / "two"
    base = "root:x:0:0::/root:/bin/bash\ndaemon:x:1:1::/:/usr/sbin/nologin\nnobody:x:65534:65534::/:/usr/sbin/nologin\n"
    one.write_text(base + "pi:x:1000:1000::/home/pi:/bin/bash\nsvc:x:1001:1001::/:/usr/sbin/nologin\nf:x:1002:1::/:/bin/false\n")
    two.write_text(base + "pi:x:1000:1000::/home/pi:/bin/bash\nann:x:1001:1001::/home/ann:/bin/sh\n")
    assert doctor.login_accounts(str(one)) == 1 and doctor.login_accounts(str(two)) == 2
    assert doctor.login_accounts(str(Path(d) / "missing")) is None


class _R:
    def __init__(self):
        self.rows = []

    def line(self, level, name, *a):
        self.rows.append((level, name, a))


proc = "proc /proc proc rw,nosuid 0 0\n"
real_open = open


def _open(path, *a, **k):
    if str(path) == "/proc/mounts":
        return io.StringIO(proc)
    return real_open(path, *a, **k)


if os.name != "nt":
    for f, level in ((one, doctor.OK), (two, doctor.WARN)):
        r = _R()
        with mock.patch("builtins.open", _open):
            doctor._check_privacy(r, str(f))
        assert r.rows[0][0] == level and r.rows[0][1] == "Privacy from other users", r.rows
        if level == doctor.OK:
            assert "Only one person logs in to this computer" in r.rows[0][2][0]

# ---- 6. video test threshold ----
results = [("v4l2m2m", 1.0), ("x264", 0.6)]
with tempfile.TemporaryDirectory() as d, mock.patch.object(config, "state_dir", return_value=Path(d)), \
        mock.patch.object(camera, "find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
        mock.patch("camagent.convert.detect", return_value=results), \
        mock.patch.object(config, "load_all", return_value=({}, [], [])):
    out = remote.measure()
    assert out["gpu"] == "v4l2m2m" and out["max_source"] == 1080
    assert remote._info(None)["encoders"]["max_source"] == 1080
    with mock.patch("camagent.convert.detect", return_value=[("v4l2m2m", 0.9), ("x264", 0.6)]):
        out = remote.measure()
    assert out["gpu"] == "" and "max_source" not in out            # a weaker Pi doesn't count
    with mock.patch("camagent.convert.detect", return_value=[("vaapi", 1.1), ("x264", 2.0)]):
        assert remote.measure()["gpu"] == ""                       # other graphics methods still need 1.2
    with mock.patch("camagent.convert.detect", return_value=[("vaapi", 3.0), ("x264", 2.0)]):
        out = remote.measure()
    assert out["gpu"] == "vaapi" and "max_source" not in out
    assert "max_source" not in remote._info(None)["encoders"]
print("v0.21 lockout, sign-in, helper, update notice, privacy, video test OK")

# ---- 0.21.1: a camera that refuses a Stop naming zoom still stops; test moves carry a time limit ----
from types import SimpleNamespace as _NS  # noqa: E402
from camagent import camera as _camera  # noqa: E402


class _NoZoomPTZ:
    def __init__(self):
        self.calls = []

    def GetStatus(self, a):
        return _NS(Position=_NS(PanTilt=_NS(x=0.0, y=0.0), Zoom=None))       # a fixed, made-up position

    def ContinuousMove(self, a):
        self.calls.append(("move", a["Velocity"]["PanTilt"]["x"], a.get("Timeout")))

    def Stop(self, a):
        if "Zoom" in a:
            raise Exception("zoom not supported")
        self.calls.append(("stop",))

    def AbsoluteMove(self, a):
        self.calls.append(("abs",))


_f = _NoZoomPTZ()
with mock.patch.object(_camera, "onvif_connect", lambda *a: _NS(create_ptz_service=lambda: _f)), \
        mock.patch.object(_camera.time, "sleep", lambda s: None):
    _r = _camera.ptz_move_test("h", 80, "u", "p", token="p1")
assert _f.calls.count(("stop",)) == 2                     # both moves stopped, without the zoom part
assert ("abs",) not in _f.calls                           # a made-up position: never "back to" it
assert ("move", -0.3, "PT1S") in _f.calls and _r["follows"] is False
print("v0.21.1 stop without zoom, no move back to a made-up position OK")

# ---- 0.21.2: converting turned on, but the camera already sends 1080p H.264: send it as it is ----
import json as _json  # noqa: E402
import logging as _logging  # noqa: E402
from camagent import uplink as _uplink  # noqa: E402

_up = _uplink.Uplink.__new__(_uplink.Uplink)
_up.cfg = {"camera": {"rtsp_url": "rtsp://192.168.1.5/live", "username": "u", "password": "p"},
           "stream": {"convert": "1080p", "encoder": "amf", "source_height": 2160}}
_up.stats, _up._fell_to, _up._fast_fails, _up.log = {}, None, 0, _logging.getLogger("t")
for codec, height, expect in (("h264", 1080, None), ("h264", 2160, "amf"), ("hevc", 1080, "amf")):
    _up._fits = None
    out = _NS(stdout=_json.dumps({"streams": [{"codec_name": codec, "height": height}]}), returncode=0)
    with mock.patch("subprocess.run", return_value=out), mock.patch.object(_uplink.camera, "find_ffprobe",
                                                                          return_value="ffprobe"):
        assert _up.convert_method() == expect, (codec, height)
print("v0.21.2 no converting when the camera already fits OK")

# ---- 0.21.3: gpu-test makes what CamAgent really makes, and shows the real error ----
from camagent import gputest as _g  # noqa: E402
assert _g.outputs_for(1080) == ((720, "2000k"), (480, "900k"))
assert _g.outputs_for(2160)[0][0] == 1080
assert _g._error_line("x\n[scale_d3d11] Failed to create device\nConversion failed!\nbench: maxrss=1KiB\n") \
    == "[scale_d3d11] Failed to create device"
print("v0.21.3 gpu-test sizes and errors OK")

# ---- 0.22.2: scale_d3d11 takes width/height (ffmpeg 9 refused w=-2) ----
assert "scale_d3d11=width=1280:height=720" in [w[2](720) for w in _g.ways("amf")]
print("v0.22.2 scale_d3d11 options OK")
