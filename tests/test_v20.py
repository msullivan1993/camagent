"""0.20.2: setup can let CamAgent try changing the camera's video setting with its own login (never the admin login);
when the camera refuses, the person gets the do-it-yourself steps."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import configure  # noqa: E402
from camagent.controls import NotAllowed  # noqa: E402

cam = {"camera": {"host": "192.168.1.5", "onvif_port": 80, "username": "yonderview", "password": "x",
                  "rtsp_url": "rtsp://192.168.1.5/live"}, "stream": {"audio": False}}
with mock.patch("camagent.controls.OnvifSession") as sess, mock.patch("camagent.controls.Controls") as ctl, \
        mock.patch("time.sleep"):
    ctl.return_value.stream_fixes.side_effect = NotAllowed("the camera didn't allow stream settings")
    assert configure._try_fix_stream(cam) is False
    assert sess.call_args[0][0] is cam                       # CamAgent's own login, nothing else asked for
    ctl.return_value.stream_fixes.side_effect = None
    ctl.return_value.stream_fixes.return_value = ["resolution 2560x1440 -> 1920x1080"]
    assert configure._try_fix_stream(cam, max_height=1080) is True
    assert ctl.return_value.stream_fixes.call_args[1] == {"apply": True, "max_height": 1080}
print("v0.20.2 try the camera's own setting OK")

# ---- 0.20.3: release downloads try three times, then a plain message (GitHub 504s, 2026-11-08) ----
import urllib.error  # noqa: E402
from camagent import release  # noqa: E402


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return b"ok"


calls = []
def _open(req, timeout=60):
    calls.append(1)
    if len(calls) < 3:
        raise urllib.error.HTTPError(req.full_url, 504, "Gateway Time-out", {}, None)
    return _Resp()
with mock.patch("urllib.request.OpenerDirector.open", side_effect=_open):
    assert release._get("https://github.com/x/y/releases/download/v1/SHA256SUMS", sleep=lambda s: None) == b"ok"
assert len(calls) == 3
with mock.patch("urllib.request.OpenerDirector.open",
                side_effect=urllib.error.HTTPError("u", 504, "Gateway Time-out", {}, None)):
    try:
        release._get("https://github.com/x/y/releases/download/v1/a.whl", sleep=lambda s: None)
        raise AssertionError("should have failed")
    except release.DownloadFailed as e:
        assert "504" in str(e) and "a.whl" in str(e)
with mock.patch("urllib.request.OpenerDirector.open",
                side_effect=urllib.error.HTTPError("u", 404, "Not Found", {}, None)) as op:
    try:
        release._get("https://github.com/x/y/releases/download/v1/a.whl", sleep=lambda s: None)
    except release.DownloadFailed:
        pass
    assert op.call_count == 1                      # missing file: no point trying again
print("v0.20.3 download retries OK")
