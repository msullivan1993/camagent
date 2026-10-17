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
m = config.load_or_defaults(main); m["account"].update(token="yva_x", name="Mike"); config.save_main(m, main)

answers = iter(["1", "",                  # show cameras, Enter
                "x",                      # not a choice: asked again
                "3", "0",                 # Change a camera... then Back (straight back to the main menu)
                "3", "3", "n", "",        # Change a camera > Remove it -> say no, Enter
                "7", "1", "",             # YonderView account > Sign out (signed in), Enter
                "0"])                     # quit
with mock.patch.object(builtins, "input", lambda *a: next(answers)), \
     mock.patch.object(menu.service, "is_admin", lambda: True), \
     mock.patch.object(menu, "_service_state", lambda: "not installed"), \
     mock.patch.object(config, "status_path", lambda: d / "nostatus.json"):
    cli.main(["--config", str(main)])
_, cams, _ = config.load_all(main)
assert len(cams) == 1                       # remove was declined
assert config.load(main)["account"]["token"] == ""        # signed out from the account menu
with mock.patch.object(menu.service, "is_admin", lambda: False):
    menu.run(main)                           # prints the sudo hint and returns

# Updated while the menu was open (nightly auto-update, or another window): it reopens on the new version
assert menu.disk_version() == menu.__version__
reopened = []
answers = iter(["4"])                                       # picks something; the update is noticed first
with mock.patch.object(builtins, "input", lambda *a: next(answers)), \
     mock.patch.object(menu.service, "is_admin", lambda: True), \
     mock.patch.object(menu, "_service_state", lambda: "running"), \
     mock.patch.object(menu, "disk_version", lambda: "99.0.0"), \
     mock.patch.object(menu, "relaunch", lambda cp=None: (reopened.append(cp), (_ for _ in ()).throw(SystemExit(0)))), \
     mock.patch.object(config, "status_path", lambda: d / "nostatus.json"):
    try:
        menu.run(main)
    except SystemExit:
        pass
assert reopened == [main]
print("MENU OK")
