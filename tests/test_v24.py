"""0.24: CamAgent's wording can come from the website (Staff > Wording), checked before use; the camera's alarm
sign-ups are cancelled (no sign-up on every connection, one at a time, none left behind); the service waits quietly
when no camera is set up instead of restarting every 5 seconds."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import agent, config, controls, link, remote, service, text  # noqa: E402
from camagent._onvif import client as onvif_client  # noqa: E402

tmp = Path(tempfile.mkdtemp())

# 1. Which website texts CamAgent accepts
assert text.usable("MODE_WELCOME", "Hi! This is CamAgent.")
assert text.usable("MENU_PICK_AGAIN", "Type a number from 1 to {n}.")                      # dropped {back}: fine
assert text.usable("MENU_PICK_AGAIN", "Pick again ({back}), 1 to {n}. Braces: {{ }}")       # moved, typed braces
assert not text.usable("MENU_PICK_AGAIN", "Type {count}")                                   # a new name
assert not text.usable("MENU_PICK_AGAIN", "Type 1 to {n")                                   # a lone brace
assert not text.usable("MENU_PICK_AGAIN", "Type {n.__class__}")                             # attribute access
assert not text.usable("MENU_PICK_AGAIN", "Type {n!r}")
assert not text.usable("MODE_WELCOME", "Hi\x1b[2J")                                         # terminal codes
assert not text.usable("MODE_WELCOME", "   ")
assert not text.usable("MODE_WELCOME", 7)
assert not text.usable("WORDING_FILE", "x.json")                                            # settings aren't wording
assert not text.usable("NO_SUCH_TEXT", "Hello")
assert not text.usable("MODE_WELCOME", "x" * 5000)

# 2. Using the saved file: good texts replace the built-in ones, bad ones and a bad file change nothing
built_in = text.MODE_WELCOME
f = tmp / "wording.json"
f.write_text(json.dumps({"v": "abc", "texts": {"MODE_WELCOME": "Welcome!", "MENU_PICK_AGAIN": "Type {oops}"}}))
assert text.use_website_wording(f) == 1
assert text.MODE_WELCOME == "Welcome!" and "{oops}" not in text.MENU_PICK_AGAIN
text.MENU_PICK_AGAIN.format(n=3, back="Back")                                               # still formats
f.write_text("not json")
assert text.use_website_wording(f) == 0
assert text.use_website_wording(tmp / "missing.json") == 0
text.MODE_WELCOME = built_in

# 3. The helper saves the website's wording when the check-in's fingerprint changes (and only then)
with mock.patch.object(config, "remote_dir", return_value=tmp), mock.patch.object(config, "ensure_remote_dir"), \
        mock.patch.object(link, "wording", return_value={"v": "f1", "texts": {"MODE_WELCOME": "Hello there.",
                                                                              "MENU_PICK_AGAIN": "{nope}"}}) as fetch:
    f.unlink(missing_ok=True)
    remote.sync_wording("https://yonderview.net", "tok", None)                               # older website
    assert not fetch.called and not f.exists()
    remote.sync_wording("https://yonderview.net", "tok", "f1")
    saved = json.loads(f.read_text(encoding="utf-8"))
    assert saved == {"v": "f1", "texts": {"MODE_WELCOME": "Hello there."}}, saved              # the bad one dropped
    remote.sync_wording("https://yonderview.net", "tok", "f1")
    assert fetch.call_count == 1                                                              # same fingerprint
    fetch.side_effect = link.LinkError("offline")
    remote.sync_wording("https://yonderview.net", "tok", "f2")                                # never raises
    assert json.loads(f.read_text(encoding="utf-8"))["v"] == "f1"
    remote.sync_wording("https://yonderview.net", "tok", "")                                  # back to built-in
    assert not f.exists()

# 4. Connecting to a camera no longer signs up for its alarms
cam = onvif_client.ONVIFCamera.__new__(onvif_client.ONVIFCamera)
events = mock.Mock()
cam.create_devicemgmt_service = mock.Mock(return_value=mock.Mock(GetCapabilities=mock.Mock(return_value={})))
cam.create_events_service = mock.Mock(return_value=events)
cam.adjust_time, cam.services_lock = False, threading.Lock()
cam.update_xaddrs()
assert not events.CreatePullPointSubscription.called

# 5. Signing up gives a cancel; the alarm loop cancels each failed sign-up and the last one when it stops
sub = mock.Mock()
sub.SubscriptionReference.Address._value_1 = "http://cam/onvif/Subscription?Idx=7"
events.CreatePullPointSubscription.return_value = sub
cam.xaddrs = {}
made = []
cam.create_onvif_service = mock.Mock(side_effect=lambda name, *a, **k: made.append((name, k.get("portType"))) or mock.Mock())
pull, cancel = cam.subscribe_pullpoint()
assert cam.xaddrs[cam.PULLPOINT_NS] == "http://cam/onvif/Subscription?Idx=7"
cancel()
assert ("subscription", "SubscriptionManager") in made

cancels = []
stop = threading.Event()
pull = mock.Mock()
pull.PullMessages.side_effect = lambda *_: (stop.set(), mock.Mock(NotificationMessage=[]))[1]
onvif = mock.Mock(subscribe_pullpoint=mock.Mock(return_value=(pull, lambda: cancels.append(1))))
c = controls.Controls.__new__(controls.Controls)
c.p, c.caps, c.log = mock.Mock(onvif=onvif), {}, mock.Mock()
c.watch_events(stop, lambda e: None)
assert cancels == [1] and onvif.subscribe_pullpoint.call_count == 1                         # stopped: cancelled

# 6. The service with no camera waits and looks again, instead of quitting
calls = []


def fake_supervisor(path):
    calls.append(path)
    if len(calls) < 3:
        raise agent.NoCameras(agent.NO_CAMERAS)
    return "running"


with mock.patch.object(agent, "Supervisor", side_effect=fake_supervisor):
    gate = mock.Mock(wait=mock.Mock(return_value=False))
    assert agent.wait_for_cameras("cfg", every=0, stop=gate) == "running" and len(calls) == 3
    gate.wait.return_value = True                                                             # stopping
    assert agent.wait_for_cameras("cfg", every=0, stop=gate) is None
assert issubclass(agent.NoCameras, SystemExit)                                                # `camagent run` by hand

# 7. Windows restart (0.24.1): stop, wait until stopped, then start; a refused start is tried again
ran, states = [], iter(["STOP_PENDING", "STOP_PENDING", "STOPPED", "STOPPED"])
with mock.patch.object(service, "_win_paths", return_value=("svc.exe", "svc.xml")), \
        mock.patch.object(service, "_win_state", side_effect=lambda: next(states, "RUNNING")), \
        mock.patch.object(service, "_run", side_effect=lambda cmd, check=True: ran.append(cmd[1]) or
                          mock.Mock(returncode=1056 if ran.count("start") == 1 else 0)), \
        mock.patch("time.sleep"):
    service._win_restart()
assert ran == ["stop", "start", "start"], ran                     # never WinSW's "restart" (start came too soon)

print("OK")
