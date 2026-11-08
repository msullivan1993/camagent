"""0.22: two ways to set up (on the website, or here on this computer), the wording in one file (text.py), and
"send your camera's own size" as a normal choice with a bandwidth note."""
import builtins
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import config, configure, guide, menu, service, text  # noqa: E402


def make_dir():
    d = Path(tempfile.mkdtemp())
    return d, d / "camagent.toml"


def run_menu(main, inputs, state="running"):
    """Run the menu with scripted input; returns everything it printed."""
    answers = iter(inputs)
    buf = io.StringIO()
    with redirect_stdout(buf), mock.patch.object(builtins, "input", lambda *a: next(answers)), \
            mock.patch.object(menu.service, "is_admin", lambda: True), \
            mock.patch.object(menu, "_service_state", lambda: state), \
            mock.patch.object(menu, "_website_ready") as ready, \
            mock.patch.object(config, "status_path", lambda: main.parent / "nostatus.json"):
        menu.run(main)
    return buf.getvalue(), ready


# ---- 1. first run: the question stores the mode ----
d, main = make_dir()
assert menu.get_mode(main) == ""
out, ready = run_menu(main, ["1", "0"])                  # website, then quit
assert text.MODE_QUESTION in out and text.MODE_WEBSITE in out and text.MODE_LOCAL in out
assert text.MODE_WEBSITE_ABOUT in out and text.MODE_LOCAL_ABOUT in out
assert config.load(main)["agent"]["mode"] == "website"
assert ready.called                                      # signs in and installs what's missing
d, main = make_dir()
out, ready = run_menu(main, ["x", "2", "0"])             # a wrong key asks again; then local, then quit
assert text.MODE_RETRY in out
assert config.load(main)["agent"]["mode"] == "local"
assert not ready.called and text.M_ADD in out
d, main = make_dir()
out, _ = run_menu(main, ["0"])                            # quitting the question saves nothing
assert menu.get_mode(main) == "" and text.MENU_TITLE.format(version=menu.__version__) not in out

# ---- 2. a computer that already has cameras is "local": no question ----
d, main = make_dir()
cam = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
cam["camera"].update(host="10.0.0.5", rtsp_url="rtsp://10.0.0.5/live")
cam["platform"].update(camera_id="v22_0001", ingest_host="i", srt_password="s", srt_passphrase="phrase0123",
                       mqtt_host="m", mqtt_password="p")
config.save_camera(cam, main)
assert config.load_or_defaults(main)["agent"]["mode"] == ""
assert menu.get_mode(main) == "local"
out, ready = run_menu(main, ["0"])
assert text.MODE_QUESTION not in out and text.M_LIST in out and text.M_SWITCH_TO_WEBSITE in out
assert not ready.called
assert "  10. " + text.M_SWITCH_TO_WEBSITE in out                    # the last numbered item
assert out.index(text.M_SWITCH_TO_WEBSITE) > out.index(text.M_ADVANCED)

# ---- 3. website mode: link status and the switch item; switching changes the stored mode ----
m = config.load_or_defaults(main)
m["agent"]["mode"] = "website"
m["account"].update(token="yva_x", name="Mike")
config.save_main(m, main)
out, ready = run_menu(main, ["0"])
assert "signed in as Mike" in out
assert text.W_OPEN_LINK.format(address="yonderview.net/link") in out
assert text.W_SWITCH_TO_LOCAL in out and text.W_CHECK in out and text.W_LOG in out and text.W_CAMERAS in out
assert text.M_ADD not in out                             # not the local menu
assert ready.called
# open the address: it is printed
out, _ = run_menu(main, ["1", "", "0"])
assert "https://yonderview.net/link" in out
# switch to local (menu redraws as the local menu), then back to website
w_items = [text.W_OPEN_LINK, text.W_CAMERAS, text.W_CHECK, text.W_LOG, text.W_SIGN_OUT, text.W_UPDATES,
           text.W_SWITCH_TO_LOCAL]
switch_n = str(len(w_items))
out, _ = run_menu(main, [switch_n, "0"])
assert text.SWITCHED_TO_LOCAL in out and text.M_ADD in out
assert config.load(main)["agent"]["mode"] == "local"
assert len(list((main.parent / "cameras").glob("*.toml"))) == 1          # no camera removed
out, _ = run_menu(main, ["10", "0"])                                      # the last local item
assert text.SWITCHED_TO_WEBSITE in out and text.W_SWITCH_TO_LOCAL in out
assert config.load(main)["agent"]["mode"] == "website"
# not signed in: the menu says so and where to sign in
m = config.load_or_defaults(main)
m["account"].update(token="", name="")
config.save_main(m, main)
out, _ = run_menu(main, ["0"])
assert "not signed in" in out and "You haven't signed in yet" in out and text.W_SIGN_IN in out

# ---- 4. website mode gets this computer ready: file, sign-in, helper; no cameras = helper only ----
d, main = make_dir()
with mock.patch.object(configure, "account_token") as tok, mock.patch.object(service, "is_installed",
                                                                          return_value=False), \
        mock.patch.object(service, "remote_installed", return_value=False), \
        mock.patch.object(service, "install_remote") as inst_remote, mock.patch.object(service, "install") as inst:
    menu._website_ready(main)
assert main.exists() and tok.called and inst_remote.called and not inst.called
with mock.patch.object(configure, "account_token"), mock.patch.object(service, "is_installed", return_value=False), \
        mock.patch.object(service, "install") as inst:
    config.save_camera(cam, main)
    menu._website_ready(main)
assert inst.called                                                        # cameras here: the main service too

# ---- 5. wording comes from text.py ----
assert text.__doc__ and "HOW TO EDIT" in text.__doc__
assert guide.INTRO is text.GUIDE_INTRO and guide.SERVER is text.GUIDE_SERVER and guide.DONE is text.GUIDE_DONE
assert configure.SIGN_IN == text.SIGN_IN and configure.PASTE == text.PASTE and configure.TRY_FIX == text.TRY_FIX
buf = io.StringIO()
with redirect_stdout(buf):
    d, main = make_dir()
    cam2 = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
    cam2["camera"].update(host="1.2.3.4")
    answers = iter(["1", "", "", "", "", "", "", "1"])
    with mock.patch.object(builtins, "input", lambda *a: next(answers)):
        configure.heading(text.H_STEP1)
assert text.H_STEP1 in buf.getvalue()

# ---- 6. the 1080p step: "send your own" is a normal choice, with a bandwidth note ----
for n, size in ((2, "2160p"), (1, "1440p")):
    cfg = {"stream": dict(config.DEFAULTS["stream"]), "camera": dict(config.DEFAULTS["camera"]),
           "_measure": {"height": 2160 if size == "2160p" else 1440}}
    shown = []
    picks = iter(["3"])                                           # Send your camera's own size
    buf = io.StringIO()
    with redirect_stdout(buf), mock.patch.object(builtins, "input", lambda *a: next(picks)):
        configure.step_convert(cfg, "ffmpeg")
    out = buf.getvalue()
    assert text.C_SOURCE.format(what=size) in out and "approved" not in out and "paid option" not in out
    assert "must approve" not in out
    assert text.C_BANDWIDTH in out and "8 Mbps for 1440p" in out and "16 Mbps for 4K" in out
    assert "TB a month" in out and "upload speed" in out
    assert cfg["stream"]["convert"] == "off"                      # sent as it is
    first = [ln for ln in out.splitlines() if ln.strip().startswith("1. ")][0]
    assert text.C_HERE in first                                    # 1080p stays first, "(recommended)"
    assert "(recommended)" in text.C_HERE
    assert [ln.strip() for ln in out.splitlines() if ln.strip().startswith(("2. ", "3. ", "4. "))][:3] == [
        "2. " + text.C_CAMERA_SIDE, "3. " + text.C_SOURCE.format(what=size), "4. " + text.TRY_FIX]
# over the camera's limit: the message names the limit and the raise
assert "{limit}" in text.F_OVER_LIMIT_5 and "raise" in text.F_OVER_LIMIT_5

print("v22 two ways to set up, text.py and the 1080p step OK")
