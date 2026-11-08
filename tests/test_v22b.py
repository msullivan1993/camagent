"""Run with: python tests/test_v22b.py  (from the repo folder). Website setup helper jobs (camera_settings,
remove_camera) and the PTZ limits a camera's host sets (enforced for everyone but the camera's owners)."""
import copy
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, remote, service  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402

# ---- 1. website jobs ----
tmp = Path(tempfile.mkdtemp())
main_path = tmp / "camagent.toml"
config.save_main({s: copy.deepcopy(config.DEFAULTS[s]) for s in config.MAIN_SECTIONS}, main_path)
cam = {s: copy.deepcopy(config.DEFAULTS[s]) for s in config.DEFAULTS}
cam["platform"].update(camera_id="yard_1", ingest_host="i", srt_password="s", srt_passphrase="phrase0123",
                       mqtt_host="m", mqtt_password="p")
cam["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=True)
path = config.save_camera(cam, main_path)
assert path.exists()


def job(**p):
    return {"kind": "camera_settings", "payload": p}


assert set(remote.HANDLERS) >= {"camera_settings", "remove_camera"}
with mock.patch.object(remote, "_cfg_path", str(main_path)), mock.patch.object(service, "is_installed",
                                                                                return_value=True), \
        mock.patch.object(service, "restart") as rs:
    ok, out = remote.run_job(job(slug="yard_1", audio=True, ptz=False))
    assert ok
    assert out == {"camera": "yard_1", "audio": True, "ptz": False}, out
    rs.assert_called_once_with(remote=False)
    _, cams, _ = config.load_all(main_path)
    assert cams[0]["stream"]["audio"] is True and cams[0]["camera"]["ptz"] is False
    out = remote.HANDLERS["camera_settings"](job(slug="yard_1", audio=False))      # ptz left alone
    assert out == {"camera": "yard_1", "audio": False, "ptz": False}, out
    for bad in ("nope", "../x"):
        try:
            remote.HANDLERS["camera_settings"](job(slug=bad))
            raise AssertionError("should refuse")
        except ValueError:
            pass
    try:
        remote.HANDLERS["camera_settings"](job(slug="nope"))
    except ValueError as e:
        assert str(e) == "This camera isn't set up on this computer"
    rs.reset_mock()
    assert remote.HANDLERS["remove_camera"]({"payload": {"slug": "yard_1"}}) == {"removed": "yard_1"}
    rs.assert_called_once_with(remote=False)
    assert not path.exists()
    try:
        remote.HANDLERS["remove_camera"]({"payload": {"slug": "yard_1"}})
        raise AssertionError("already gone")
    except ValueError:
        pass
with mock.patch.object(remote, "_cfg_path", str(main_path)), mock.patch.object(service, "is_installed",
                                                                                return_value=True), \
        mock.patch.object(service, "restart", side_effect=SystemExit("needs admin")):
    config.save_camera(cam, main_path)
    try:
        remote.HANDLERS["camera_settings"](job(slug="yard_1", audio=True))
        raise AssertionError("should be an error")
    except ValueError as e:
        assert "needs admin" in str(e)

# ---- 2. limits ----
c2 = copy.deepcopy(cam)
c2["platform"]["camera_id"] = "lim_1"
a = CameraAgent(c2)
a.ptz = mock.Mock(last_activity=0.0, moves=0, max_move_ms=2000, reports_position=True)
a.ptz.settings = {"invert_pan": False, "invert_tilt": False}
a.controls = mock.Mock()
a.position_kind = "real"
LIM = {"pan": [-0.5, 0.5], "tilt": None, "zoom_max": 0.6}
a._apply_config(('{"invert_pan": false, "ptz_limits": %s}' % __import__("json").dumps(LIM)).encode())
assert a.ptz_limits == {"pan": (-0.5, 0.5), "tilt": None, "zoom_max": 0.6}, a.ptz_limits
OUT = "That's outside the area the camera's host allows."


def refused(cmd):
    try:
        a._handle(cmd)
    except RuntimeError as e:
        assert str(e) == OUT, e
        return True
    return False


# point
assert refused({"op": "point", "pan": 0.8, "tilt": 0, "zoom": 0.1, "limited": True})
assert refused({"op": "point", "pan": 0.1, "tilt": 0, "zoom": 0.9, "limited": True})
assert not a.controls.point.called
assert not refused({"op": "point", "pan": 0.3, "tilt": -0.9, "zoom": 0.5, "limited": True})
assert a.controls.point.call_count == 1
assert not refused({"op": "point", "pan": 0.9, "tilt": 0, "zoom": 0.9})                   # owner
assert not refused({"op": "point", "pan": 0.9, "tilt": 0, "zoom": 0.9, "limited": False})
assert a.controls.point.call_count == 3

# a move pushing past an edge already reached is refused; away from it is fine
a.ptz.position.return_value = {"pan": 0.5, "tilt": 0, "zoom": 0.1}
assert refused({"op": "move", "pan": 0.5, "tilt": 0, "zoom": 0, "ms": 400, "limited": True})
assert not a.ptz.move.called
a.ptz.position.return_value = {"pan": 0.7, "tilt": 0, "zoom": 0.1}
assert not refused({"op": "move", "pan": -0.5, "tilt": 0, "zoom": 0, "ms": 400, "limited": True})
assert a.ptz.move.call_count == 1
a._limit_watch += 1                                                         # stop that move's watcher
# inverted pan: moving the camera's raw pan up means asking for negative pan
a.ptz.settings["invert_pan"] = True
a.ptz.position.return_value = {"pan": 0.5, "tilt": 0, "zoom": 0.1}
assert refused({"op": "move", "pan": -0.5, "tilt": 0, "zoom": 0, "ms": 400, "limited": True})
a.ptz.settings["invert_pan"] = False
a.ptz.move.reset_mock()
a._limit_watch += 1
# owner: never restricted
assert not refused({"op": "move", "pan": 0.5, "tilt": 0, "zoom": 0, "ms": 400})
assert a.ptz.move.call_count == 1

# a limited move is stopped when the position crosses the edge
a.LIMIT_POLL_S = 0.02
a.ptz.move.reset_mock()
positions = iter([{"pan": 0.3, "tilt": 0, "zoom": 0.1}, {"pan": 0.4, "tilt": 0, "zoom": 0.1},
                  {"pan": 0.52, "tilt": 0, "zoom": 0.1}])
a.ptz.position.side_effect = lambda: next(positions, {"pan": 0.52, "tilt": 0, "zoom": 0.1})
a._handle({"op": "move", "pan": 0.5, "tilt": 0, "zoom": 0, "ms": 2000, "limited": True})
for _ in range(100):
    if a.ptz.stop.called:
        break
    time.sleep(0.02)
assert a.ptz.stop.call_count == 1
# zoom past zoom_max stops too
a.ptz.stop.reset_mock()
a.ptz.position.side_effect = [{"pan": 0, "tilt": 0, "zoom": 0.3}] + [{"pan": 0, "tilt": 0, "zoom": 0.65}] * 50
a._handle({"op": "move", "pan": 0, "tilt": 0, "zoom": 0.5, "ms": 2000, "limited": True})
for _ in range(100):
    if a.ptz.stop.called:
        break
    time.sleep(0.02)
assert a.ptz.stop.call_count == 1

# presets: outside refused, inside and unknown allowed
a._limit_watch += 1
a.ptz.presets.return_value = [{"token": "1", "pan": 0.9, "tilt": 0, "zoom": 0}, {"token": "2", "pan": 0.1, "tilt": 0},
                              {"token": "3"}]
assert refused({"op": "preset", "id": "1", "limited": True})
assert not refused({"op": "preset", "id": "2", "limited": True})
assert not refused({"op": "preset", "id": "3", "limited": True})
assert not refused({"op": "preset", "id": "1"})

# a camera that doesn't report a position: moves allowed (logged once)
a.position_kind = "fake"
a.ptz.position.side_effect = None
a.ptz.move.reset_mock()
assert not refused({"op": "move", "pan": 0.5, "tilt": 0, "zoom": 0, "ms": 400, "limited": True})
assert a.ptz.move.call_count == 1

# no limits (absent or null): nothing enforced
a.position_kind = "real"
a._apply_config(b'{"ptz_limits": null}')
assert a.ptz_limits is None
a.ptz.position.return_value = {"pan": 0.5, "tilt": 0, "zoom": 0.1}
assert not refused({"op": "point", "pan": 0.99, "tilt": 0, "zoom": 1, "limited": True})
assert not refused({"op": "move", "pan": 0.5, "tilt": 0, "zoom": 0, "ms": 400, "limited": True})
a._apply_config(b'{}')
assert a.ptz_limits is None
a._apply_config(b'{"ptz_limits": {"pan": null, "tilt": null, "zoom_max": null}}')
assert a.ptz_limits is None
a._limit_watch += 1
print("test_v22b OK")
