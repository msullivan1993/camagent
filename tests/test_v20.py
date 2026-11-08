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
