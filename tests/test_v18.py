"""0.18 (beta): camagent remote, the website setup helper: checks in, runs only known jobs, sends results."""
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import link, remote, service  # noqa: E402

# only the fixed list of jobs
ok, out = remote.run_job({"id": 1, "kind": "rm -rf /"})
assert not ok and "unknown job" in out
with mock.patch("camagent.camera.discover", return_value=[{"host": "172.16.20.113", "port": 80, "name": "Amcrest",
                                                           "hardware": "IP4M-1063EW"}]):
    ok, out = remote.run_job({"id": 2, "kind": "discover"})
assert ok and out["cameras"][0] == {"host": "172.16.20.113", "port": 80, "name": "Amcrest", "hardware": "IP4M-1063EW"}
with mock.patch("camagent.camera.discover", side_effect=OSError("no network")):
    ok, out = remote.run_job({"id": 3, "kind": "discover"})
assert not ok and "no network" in out

# one check-in: jobs run, results sent, info reported
sent = []
with mock.patch.object(remote, "_account", return_value=("https://yonderview.net", "yva_x")), \
        mock.patch.object(link, "checkin", return_value={"jobs": [{"id": 7, "kind": "discover"}, {"id": "bad"}],
                                                         "next_s": 2}) as ci, \
        mock.patch.object(link, "job_result", side_effect=lambda site, tok, jid, ok, out: sent.append((jid, ok))), \
        mock.patch("camagent.camera.discover", return_value=[]):
    assert remote.run(once=True) == "ok"
info = ci.call_args[0][2]
assert info["version"] and "os" in info and sent == [(7, True)]
# not linked / unlinked / offline: waits, never crashes
with mock.patch.object(remote, "_account", return_value=("https://yonderview.net", "")):
    assert remote.run(once=True) == "signed out"
with mock.patch.object(remote, "_account", return_value=("https://yonderview.net", "yva_x")), \
        mock.patch.object(link, "checkin", side_effect=link.NotSignedIn("unlinked")):
    assert remote.run(once=True) == "unlinked"
with mock.patch.object(remote, "_account", return_value=("https://yonderview.net", "yva_x")), \
        mock.patch.object(link, "checkin", side_effect=link.LinkError("offline")):
    assert remote.run(once=True) == "offline"

# the Windows task has no time limit and one copy at a time; the Linux unit restarts by itself
assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in service._REMOTE_XML
assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in service._REMOTE_XML
with mock.patch.object(service, "os") as fake_os, mock.patch.object(service, "_run") as run, \
        mock.patch.object(service, "REMOTE_UNIT") as unit:
    fake_os.name = "posix"
    service.install_remote(Path("/etc/camagent/camagent.toml"))
    text = unit.write_text.call_args[0][0]
assert "camagent remote --config /etc/camagent/camagent.toml" in text and "Restart=always" in text
print("v0.18 remote OK")

# ---- phase 2: a camera on this computer's network ----
import json
import tempfile
from types import SimpleNamespace as NS

secret_login = json.dumps({"username": "yonderview", "password": "Abcdefgh23456789"})
# only local addresses, ever
for host in ("8.8.8.8", "127.0.0.1", "224.0.0.1", "camera.example.com", ""):
    ok, out = remote.run_job({"id": 1, "kind": "inspect", "payload": {"host": host}, "secret": secret_login})
    assert not ok and ("network" in out or "address" in out), (host, out)
with mock.patch("camagent.camera.onvif_inspect", return_value={
        "manufacturer": "Amcrest", "model": "IP4M-1063EW", "firmware": "3.2", "login_level": "Operator",
        "profiles": [{"name": "main", "token": "p0", "encoding": "H264", "width": 2560, "height": 1440, "fps": 30,
                      "ptz": True, "rtsp_url": "rtsp://172.16.20.113:554/cam/realmonitor?channel=1&subtype=0"}],
        "position": {"onvif": {"pan": 0.1}, "exact": {"pan": 10}}}) as insp:
    ok, out = remote.run_job({"id": 2, "kind": "inspect", "payload": {"host": "172.16.20.113", "port": 80},
                              "secret": secret_login})
assert ok and out["model"] == "IP4M-1063EW" and out["profiles"][0]["height"] == 1440 and out["position"]["exact"]
assert insp.call_args[0] == ("172.16.20.113", 80, "yonderview", "Abcdefgh23456789")
# the preview: only the camera's own video address
ok, out = remote.run_job({"id": 3, "kind": "snapshot", "payload": {"host": "172.16.20.113",
                          "rtsp_url": "rtsp://10.0.0.9/other"}, "secret": secret_login})
assert not ok and "isn't the camera's" in out
with mock.patch("camagent.camera.find_ffmpeg", return_value="ffmpeg"), \
        mock.patch("subprocess.run", return_value=NS(returncode=0, stdout=b"\xff\xd8jpeg", stderr=b"")):
    ok, out = remote.run_job({"id": 4, "kind": "snapshot", "payload": {"host": "172.16.20.113",
                              "rtsp_url": "rtsp://172.16.20.113:554/live"}, "secret": secret_login})
assert ok and out["jpeg"]
# keeping the setup for later: the login encrypted where it can be (Windows/Linux key)
with tempfile.TemporaryDirectory() as d, mock.patch("camagent.config.state_dir", return_value=Path(d)), \
        mock.patch("camagent.secret.protect", side_effect=lambda v: "sealed:" + v[::-1]):
    ok, out = remote.run_job({"id": 5, "kind": "stage", "payload": {"host": "172.16.20.113", "setup": "abc123",
                              "rtsp_url": "rtsp://172.16.20.113:554/live", "ptz": True, "token": "p0"},
                              "secret": secret_login})
    saved = json.loads((Path(d) / "pending" / "abc123.json").read_text())
assert ok and saved["username"] == "yonderview" and saved["password"].startswith("sealed:") and saved["ptz"]
ok, out = remote.run_job({"id": 6, "kind": "stage", "payload": {"host": "172.16.20.113", "setup": "../x"},
                          "secret": secret_login})
assert not ok and "setup ID" in out
print("v0.18 phase 2 OK")

# ---- 0.18.2: after an update the helper starts again on the new version ----
with mock.patch("camagent.remote._installed", return_value="9.9.9"), \
        mock.patch("camagent.remote._account", return_value=("https://x", "tok")), \
        mock.patch("camagent.link.checkin") as ci:
    assert remote.run(once=True) == "updated" and not ci.called
calls = []
with mock.patch("camagent.service.remote_installed", return_value=True), \
        mock.patch("camagent.service._run", side_effect=lambda cmd, check=True: calls.append(cmd)), \
        mock.patch("camagent.service.os.name", "posix"):
    service.restart_remote()
assert calls == [["systemctl", "restart", "camagent-remote.service"]]
print("v0.18.2 helper restart OK")

# ---- 0.19: connect an approved camera, forget a kept setup ----
BLOCK = ("SRT_URL=srt://ingest.example:8890?streamid=publish:kyx0001:kyx0001:srtpw&passphrase=pp1234567890&pbkeylen=16\n"
         "MQTT_HOST=mqtt.example\nMQTT_PASS=mqpw\nMQTT_PORT=8883\n")
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    main_path = d / "camagent.toml"
    with mock.patch("camagent.config.state_dir", return_value=d / "state"), \
            mock.patch("camagent.secret.protect", side_effect=lambda v: v), \
            mock.patch("camagent.secret.unprotect", side_effect=lambda v: v):
        remote._cfg_path = main_path
        ok, out = remote.run_job({"id": 7, "kind": "stage", "payload": {"host": "172.16.20.113", "setup": "s1",
                                  "rtsp_url": "rtsp://172.16.20.113:554/live", "ptz": True, "token": "p0",
                                  "convert": "1080p", "source_height": 1440}, "secret": secret_login})
        assert ok
        calls = {}
        with mock.patch("camagent.remote._account", return_value=("https://yv.example", "tok")), \
                mock.patch("camagent.link.connect", side_effect=lambda s, t, slug, replace=False:
                           calls.setdefault("connect", (slug, replace)) and ("ok", BLOCK, {"max_height": 1080})), \
                mock.patch("camagent.camera.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                mock.patch("camagent.convert.detect", return_value=[("qsv", 3.0)]), \
                mock.patch("camagent.convert.best", return_value="qsv"), \
                mock.patch("camagent.configure.test_mqtt", return_value=(True, "connected")), \
                mock.patch("camagent.service.is_installed", return_value=True), \
                mock.patch("camagent.service.restart", side_effect=lambda remote=True: calls.setdefault("restart", remote)):
            ok, out = remote.run_job({"id": 8, "kind": "connect", "payload": {"slug": "kyx0001", "setup": "s1"}})
        assert ok, out
        assert out["convert"] == "1080p" and calls["connect"] == ("kyx0001", False) and calls["restart"] is False
        saved = (d / "cameras" / "kyx0001.toml").read_text()
        assert "172.16.20.113" in saved and "Abcdefgh23456789" in saved and 'convert = "1080p"' in saved
        assert not (d / "state" / "pending" / "s1.json").exists()          # the login lives in the camera file now
        # the job can't point CamAgent at another address than the camera's
        ok, out = remote.run_job({"id": 9, "kind": "connect", "payload": {"slug": "kyx0001", "host": "172.16.20.113",
                                  "rtsp_url": "rtsp://10.9.9.9/live"}, "secret": secret_login})
        assert not ok and "isn't the camera's" in out
        ok, out = remote.run_job({"id": 10, "kind": "connect", "payload": {"slug": "../etc", "setup": "s1"}})
        assert not ok
        ok, out = remote.run_job({"id": 11, "kind": "connect", "payload": {"slug": "kyx0001", "setup": "gone"}})
        assert not ok and "no longer has" in out
        # forget, and the 30-day clean-up
        remote.run_job({"id": 12, "kind": "stage", "payload": {"host": "172.16.20.113", "setup": "s2",
                        "rtsp_url": "rtsp://172.16.20.113:554/live"}, "secret": secret_login})
        ok, out = remote.run_job({"id": 13, "kind": "forget", "payload": {"setup": "s2"}})
        assert ok and out["forgotten"] and not (d / "state" / "pending" / "s2.json").exists()
        remote.run_job({"id": 14, "kind": "stage", "payload": {"host": "172.16.20.113", "setup": "s3",
                        "rtsp_url": "rtsp://172.16.20.113:554/live"}, "secret": secret_login})
        remote.prune_pending(now=time.time() + 31 * 86400)
        assert not (d / "state" / "pending" / "s3.json").exists()
        remote._cfg_path = None
print("v0.19 connect OK")

# ---- 0.20: the video test, measured once and told to the website ----
with tempfile.TemporaryDirectory() as d, mock.patch("camagent.config.state_dir", return_value=Path(d)), \
        mock.patch("camagent.camera.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
        mock.patch("camagent.convert.detect", return_value=[("vaapi", None), ("qsv", 4.2), ("x264", 0.9)]):
    cap = remote.measure()
    assert cap["gpu"] == "qsv" and cap["gpu_speed"] == 4.2 and cap["cpu_speed"] == 0.9
    info = remote._info(Path(d) / "camagent.toml")
    assert info["encoders"]["gpu"] == "qsv" and "fp" not in info["encoders"]
    with mock.patch("threading.Thread") as th:              # same hardware and ffmpeg: not tested again
        remote._measure_if_needed(Path(d) / "camagent.toml")
        assert not th.called
with tempfile.TemporaryDirectory() as d, mock.patch("camagent.config.state_dir", return_value=Path(d)), \
        mock.patch("camagent.camera.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
        mock.patch("camagent.convert.detect", return_value=[("v4l2m2m", 0.8), ("x264", 0.4)]):
    cap = remote.measure()
    assert cap["gpu"] == "" and cap["gpu_label"] == ""                 # too slow to count (a Pi)
print("v0.20 video test OK")
