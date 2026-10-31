"""0.17.8: remote tools (the camera's Health tab): recent log, checks, restart, update now."""
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import agent as A, tools, update  # noqa: E402

# the recent log: this camera's lines and CamAgent's own, never another camera's; passwords hidden
tools.install_ring()
logging.getLogger("cam_a.uplink").warning("starting ffmpeg -> srt://h:8890?streamid=publish:cam_a:cam_a:SECRETPW&passphrase=PHRASE")
logging.getLogger("cam_b.uplink").warning("the other camera")
logging.getLogger("camagent").warning("camagent 0.17.8 starting")
out = tools.recent_log("cam_a")
assert "starting ffmpeg" in out and "the other camera" not in out and "camagent 0.17.8 starting" in out
assert "SECRETPW" not in out and "PHRASE" not in out
assert len(tools._cap("x" * 100000).encode()) <= tools.MAX_OUTPUT + 40

# update now: a flag the administrator job acts on; nothing happens without it
with tempfile.TemporaryDirectory() as d, mock.patch.object(tools, "state_dir", return_value=Path(d)):
    called = mock.Mock()
    with mock.patch.object(update.service, "_require_admin", side_effect=called):
        update.run(if_requested=True)                       # no flag: returns at once, checks nothing
    called.assert_not_called()
    assert tools.request_update() == {"requested": True} and tools.update_requested()
    with mock.patch.object(update.service, "_require_admin"), \
            mock.patch.object(update.config, "load_or_defaults", return_value={"update": {"repo": "https://github.com/x/y", "auto": False}}), \
            mock.patch.object(update, "latest_release", return_value="v0.0.1") as latest, \
            mock.patch.object(update, "_log"):
        update.run(if_requested=True)                       # the flag: checks now, even with automatic updates off
    latest.assert_called_once()
    assert not tools.update_requested()                     # used up

# the commands: a fixed list; the checks answer at once and report later
sent = []
fake = NS(cam_id="cam_a", main_path=None, topics={"telemetry": "cam/cam_a/telemetry"},
          mq=NS(publish=lambda t, p, qos=0: sent.append(p)), _info=lambda: {"version": "x"})
with mock.patch.object(tools, "run_doctor", return_value="[ OK ] all good"):
    assert A.CameraAgent._tool(fake, "tool_log", "r1")["output"]
    assert A.CameraAgent._tool(fake, "tool_doctor", "r2") == {"started": True}
    import time
    for _ in range(50):
        if sent:
            break
        time.sleep(0.05)
assert sent and '"tool_doctor"' in sent[0] and "all good" in sent[0] and '"req": "r2"' in sent[0]
with mock.patch.object(tools, "threading") as th:
    assert tools.restart_soon() == {"restarting": True}
    th.Thread.assert_called_once()
print("v0.17.8 tools OK")
