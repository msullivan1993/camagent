"""Run with: python tests/test_menu.py  (from the repo folder)."""
import builtins, sys, tempfile
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, menu, __main__ as cli

d = Path(tempfile.mkdtemp()); main = d / "camagent.toml"
cam = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
cam["camera"].update(host="10.0.0.5", rtsp_url="rtsp://10.0.0.5/live", ptz=False)
cam["platform"].update(camera_id="menu_0001", ingest_host="i", srt_password="s", mqtt_host="m", mqtt_password="p")
config.save_camera(cam, main)

answers = iter(["1", "", "x", "4", "n", "", "0"])     # list, Enter; bad choice; remove -> say no, Enter; quit
with mock.patch.object(builtins, "input", lambda *a: next(answers)), \
     mock.patch.object(menu.service, "is_admin", lambda: True), \
     mock.patch.object(menu, "_service_state", lambda: "not installed"), \
     mock.patch.object(config, "status_path", lambda: d / "nostatus.json"):
    cli.main(["--config", str(main)])
_, cams, _ = config.load_all(main)
assert len(cams) == 1                       # remove was declined
with mock.patch.object(menu.service, "is_admin", lambda: False):
    menu.run(main)                           # prints the sudo hint and returns
print("MENU OK")
