"""0.23 (security audit): root's files in a root-only folder written atomically (never through a link the service's
account planted), old files moved over only when safe, the website's camera address check refuses this computer
itself, the helper's camera-facing ffmpeg runs as the camagent account, logins in video address paths and queries
are hidden, emoji survive the settings file, and the helper refuses an ffmpeg any user can change."""
import json
import os
import stat
import sys
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import config, convert, prereqs, remote, secret, service, update  # noqa: E402

POSIX = os.name != "nt"
LOGIN = json.dumps({"username": "yonderview", "password": "Abcdefgh23456789"})

# ---- 1. root's files: a root-only folder, atomic writes, links refused ----
if POSIX:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        rd, old = t / "camagent-remote", t / "var-lib-camagent"
        old.mkdir()
        victim_dir, victim = t / "etc", t / "shadow"
        victim_dir.mkdir()
        os.chmod(victim_dir, 0o755)
        victim.write_text("root:secret\n")
        with mock.patch.object(config, "remote_dir", return_value=rd), \
                mock.patch.object(config, "state_dir", return_value=old), \
                mock.patch.object(secret, "protect", side_effect=lambda v: "sealed:x"):
            # a planted link in the service's old folder changes nothing: root writes in its own folder now
            (old / "pending").symlink_to(victim_dir)
            (old / "capability.json").symlink_to(victim)
            ok, out = remote.run_job({"kind": "stage", "secret": LOGIN,
                                      "payload": {"host": "192.168.1.5", "setup": "abc123", "rtsp_url": "rtsp://x"}})
            assert ok, out
            assert os.listdir(victim_dir) == [] and stat.S_IMODE(victim_dir.stat().st_mode) == 0o755
            saved = rd / "pending" / "abc123.json"
            assert json.loads(saved.read_text())["password"] == "sealed:x"
            assert stat.S_IMODE(saved.stat().st_mode) == 0o600
            assert stat.S_IMODE((rd / "pending").stat().st_mode) == 0o700
            assert stat.S_IMODE(rd.stat().st_mode) == 0o755          # the service reads the update result here
            with mock.patch("camagent.camera.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                    mock.patch("camagent.convert.detect", return_value=[("x264", 2.0)]):
                remote.measure()
            assert victim.read_text() == "root:secret\n"
            assert json.loads((rd / "capability.json").read_text())["cpu_speed"] == 2.0
            assert os.path.islink(old / "capability.json") and os.path.islink(old / "pending")   # ignored, kept
            assert not [n for n in os.listdir(rd) if n.endswith(".tmp")]     # no temporary files left behind

            # a link inside root's folder (it shouldn't be there) is replaced, never written through
            (rd / "capability.json").unlink()
            (rd / "capability.json").symlink_to(victim)
            with mock.patch("camagent.camera.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                    mock.patch("camagent.convert.detect", return_value=[("x264", 3.0)]):
                remote.measure()
            assert victim.read_text() == "root:secret\n" and not os.path.islink(rd / "capability.json")

            # the update result too, readable by the service
            update._record(False, "9.9.9", "the install didn't work")
            r = rd / "update_result.json"
            assert json.loads(r.read_text())["version"] == "9.9.9" and stat.S_IMODE(r.stat().st_mode) == 0o644
            assert "couldn't update to 9.9.9" in prereqs.update_notice()

            # pending/ replaced by a link: refused, nothing written through it
            os.rename(rd / "pending", t / "moved")
            (rd / "pending").symlink_to(victim_dir)
            ok, out = remote.run_job({"kind": "stage", "secret": LOGIN,
                                      "payload": {"host": "192.168.1.5", "setup": "abc124", "rtsp_url": "rtsp://x"}})
            assert not ok and "link" in out and os.listdir(victim_dir) == []
            ok, out = remote.run_job({"kind": "forget", "payload": {"setup": "abc123"}})
            assert not ok and os.listdir(victim_dir) == []
            remote.prune_pending(now=9e12)                          # logs, never deletes through the link

        # root's folder itself a link, or writable by others: refused
        with mock.patch.object(config, "state_dir", return_value=old):
            link_dir = t / "link-remote"
            link_dir.symlink_to(victim_dir)
            with mock.patch.object(config, "remote_dir", return_value=link_dir):
                try:
                    config.ensure_remote_dir()
                    raise AssertionError("a linked folder should be refused")
                except config.UnsafeFolder:
                    pass
            loose = t / "loose"
            loose.mkdir()
            os.chmod(loose, 0o777)
            with mock.patch.object(config, "remote_dir", return_value=loose):
                try:
                    config.ensure_remote_dir()
                    raise AssertionError("a folder others can write should be refused")
                except config.UnsafeFolder:
                    pass
            with mock.patch.object(config, "remote_dir", return_value=t / "notmine"), \
                    mock.patch("os.geteuid", return_value=os.geteuid() + 1):
                (t / "notmine").mkdir()
                try:
                    config.ensure_remote_dir()
                    raise AssertionError("a folder owned by another account should be refused")
                except config.UnsafeFolder:
                    pass

    # write_private never opens an existing file or link: a fresh temporary name, then a rename
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        opened = []
        real_open = os.open
        with mock.patch("os.open", side_effect=lambda p, f, *a: opened.append((p, f)) or real_open(p, f, *a)):
            config.write_private(t / "x.json", "{}")
        p, flags = opened[0]
        assert Path(p).parent == t and Path(p).name != "x.json"
        assert flags & os.O_EXCL and flags & os.O_CREAT and flags & os.O_NOFOLLOW
        assert (t / "x.json").read_text() == "{}"

    # migration: real files root made move over once; links (and files someone else made) are ignored
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        old, rd = t / "old", t / "new"
        (old / "pending").mkdir(parents=True)
        (old / "capability.json").write_text('{"gpu": "qsv"}')
        (old / "update_result.json").write_text('{"ok": true}')
        (old / "pending" / "s1.json").write_text('{"host": "192.168.1.9"}')
        (t / "elsewhere.json").write_text('{"host": "10.0.0.66"}')
        (old / "pending" / "s2.json").symlink_to(t / "elsewhere.json")
        with mock.patch.object(config, "remote_dir", return_value=rd), \
                mock.patch.object(config, "state_dir", return_value=old):
            config.ensure_remote_dir()
        assert json.loads((rd / "capability.json").read_text()) == {"gpu": "qsv"}
        assert json.loads((rd / "update_result.json").read_text()) == {"ok": True}
        assert json.loads((rd / "pending" / "s1.json").read_text())["host"] == "192.168.1.9"
        assert not (rd / "pending" / "s2.json").exists()
        assert not (old / "capability.json").exists() and not (old / "pending" / "s1.json").exists()
        assert (t / "elsewhere.json").exists()
        # a file another account made isn't trusted either
        (old / "capability.json").write_text('{"gpu": "fake"}')
        (rd / "capability.json").unlink()
        with mock.patch.object(config, "remote_dir", return_value=rd), \
                mock.patch.object(config, "state_dir", return_value=old), \
                mock.patch("os.geteuid", return_value=os.geteuid()), \
                mock.patch("os.fstat", return_value=NS(st_mode=stat.S_IFREG | 0o644, st_size=10,
                                                       st_uid=os.geteuid() + 1)):
            assert config._read_old(old / "capability.json") is None
        # an old capability.json that is a link: ignored, nothing read through it
        (old / "capability.json").unlink()
        (old / "capability.json").symlink_to(t / "elsewhere.json")
        with mock.patch.object(config, "remote_dir", return_value=rd), \
                mock.patch.object(config, "state_dir", return_value=old):
            config.ensure_remote_dir()
        assert not (rd / "capability.json").exists()

    # the helper's unit: sandboxed, still able to write the settings, its folder and the program
    with mock.patch.object(service, "_linux_seal"):
        unit = service._remote_unit(Path("/etc/camagent/camagent.toml"))
    for line in ("ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "NoNewPrivileges=yes",
                 "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectControlGroups=yes",
                 "RestrictSUIDSGID=yes", "LockPersonality=yes", "StateDirectory=camagent-remote", "Restart=always"):
        assert line in unit.splitlines(), line
    rw = next(ln for ln in unit.splitlines() if ln.startswith("ReadWritePaths="))
    for path in ("/etc/camagent", "/opt/camagent", "/etc/systemd/system"):
        assert f"-{path}" in rw.split("=", 1)[1].split(), path
    assert "User=" not in unit and "CapabilityBoundingSet" not in unit and "UMask" not in unit
    # an update rewrites an older helper unit and reloads systemd
    calls = []
    with mock.patch.object(service, "remote_installed", return_value=True), \
            mock.patch.object(service, "REMOTE_UNIT") as ru, \
            mock.patch.object(service, "_run", side_effect=lambda cmd, check=True: calls.append(cmd)):
        ru.read_text.return_value = "[Service]\nNoNewPrivileges=yes\n"
        service._refresh_remote_unit(Path("/etc/camagent/camagent.toml"))
        assert "ProtectSystem=strict" in ru.write_text.call_args[0][0]
        assert ["systemctl", "daemon-reload"] in calls
        calls.clear()
        ru.reset_mock()
        ru.read_text.return_value = service._remote_unit(Path("/etc/camagent/camagent.toml"))
        service._refresh_remote_unit(Path("/etc/camagent/camagent.toml"))
        assert not ru.write_text.called and not calls                  # already current: left alone
    # a computer with the helper alone (website setup, no camera yet) gets it on update too
    with mock.patch.object(service, "os") as fake_os, mock.patch.object(service, "UNIT_PATH") as up, \
            mock.patch.object(service, "remote_installed", return_value=True), \
            mock.patch.object(service, "_refresh_remote_unit") as rr:
        fake_os.name = "posix"
        up.exists.return_value = False
        service.refresh_unit("/etc/camagent/camagent.toml")
        assert rr.called
print("v0.23 root-only folder, atomic writes, migration, helper unit OK")

# ---- 2. camera addresses: never this computer itself ----
for host in ("0.0.0.0", "0.1.2.3", "::", "::ffff:0.0.0.0", "127.0.0.1", "::1", "::ffff:127.0.0.1",
             "255.255.255.255", "240.0.0.1", "198.18.0.1", "192.0.2.1", "224.0.0.1", "ff02::1",
             "169.254.169.254", "::ffff:169.254.169.254", "8.8.8.8", "::ffff:8.8.8.8", "2001:db8::1",
             "100.64.0.1"):
    try:
        remote._local_host({"payload": {"host": host}})
        raise AssertionError(f"{host} should be refused")
    except ValueError:
        pass
for host in ("192.168.1.10", "10.0.0.5", "172.16.20.113", "172.31.255.1", "169.254.10.20", "fd00::5",
             "fe80::1", "::ffff:192.168.1.10"):
    assert remote._local_host({"payload": {"host": host, "port": 554}}) == (host, 554), host
print("v0.23 local address check OK")

# ---- 3. the helper's camera-facing ffmpeg runs as the camagent account when root ----
if POSIX:
    import grp
    import pwd
    acct = NS(pw_uid=998, pw_gid=997)
    with mock.patch("os.geteuid", return_value=0), mock.patch.object(pwd, "getpwnam", return_value=acct), \
            mock.patch.object(grp, "getgrnam", side_effect=lambda n: NS(gr_gid={"video": 44, "render": 105}[n])):
        kw = prereqs.unprivileged()
        assert kw == {"user": 998, "group": 997, "extra_groups": [44, 105]}
        with mock.patch("camagent.camera.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
                mock.patch("subprocess.run", return_value=NS(returncode=0, stdout=b"\xff\xd8jpeg", stderr=b"")) as run:
            ok, out = remote.run_job({"kind": "snapshot", "secret": LOGIN, "payload": {
                "host": "192.168.1.10", "rtsp_url": "rtsp://192.168.1.10:554/live"}})
        assert ok and run.call_args.kwargs["user"] == 998 and run.call_args.kwargs["group"] == 997
        assert run.call_args.kwargs["extra_groups"] == [44, 105]
        runs, chowns = [], []
        with mock.patch("subprocess.run", side_effect=lambda cmd, **k: runs.append(k) or NS(returncode=0, stderr="")), \
                mock.patch("os.chown", side_effect=lambda p, u, g: chowns.append((u, g))):
            convert.detect("/usr/bin/ffmpeg", methods=["x264"], run_as=kw)
        assert chowns == [(998, 997)] and runs and all(k.get("user") == 998 for k in runs)
    # the video test: every ffmpeg as camagent (root's folder checked as this account here)
    with tempfile.TemporaryDirectory() as t, mock.patch.object(prereqs, "unprivileged", return_value=kw), \
            mock.patch.object(config, "remote_dir", return_value=Path(t) / "r"), \
            mock.patch("camagent.camera.find_ffmpeg", return_value="/usr/bin/ffmpeg"), \
            mock.patch("camagent.convert.detect", return_value=[("x264", 1.5)]) as det:
        remote.measure()
        assert det.call_args.kwargs["run_as"]["user"] == 998
    with mock.patch("os.geteuid", return_value=0), mock.patch.object(pwd, "getpwnam", side_effect=KeyError("x")):
        assert prereqs.unprivileged() == {}                 # no camagent account: as this account
    with mock.patch("os.geteuid", return_value=1000):
        assert prereqs.unprivileged() == {}                 # not root: nothing to give up
    with mock.patch("os.geteuid", return_value=1000), mock.patch("camagent.camera.find_ffmpeg", return_value="ff"), \
            mock.patch("subprocess.run", return_value=NS(returncode=0, stdout=b"\xff\xd8", stderr=b"")) as run:
        remote.run_job({"kind": "snapshot", "secret": LOGIN, "payload": {
            "host": "192.168.1.10", "rtsp_url": "rtsp://192.168.1.10:554/live"}})
        assert "user" not in run.call_args.kwargs
print("v0.23 helper ffmpeg as camagent OK")

# ---- 4. logins in a video address's path or query are hidden ----
xm = "rtsp://192.168.1.10:554/user=admin&password=Hunter22&channel=1&stream=0.sdp?real_stream"
q = "rtsp://192.168.1.11/cam/realmonitor?channel=1&subtype=0&user=admin&pwd=Hunter22"
for f in (config.scrub, config.redact, config.strip_auth):
    for u in (xm, q, "rtsp://u:pw@192.168.1.12/live?PassWord=Hunter22&x=1", "http://cam/snap.cgi?pass=Hunter22"):
        out = f(u)
        assert "Hunter22" not in out, (f.__name__, out)
    assert "channel=1" in f(xm) and "user=admin" in f(xm) and "stream=0.sdp" in f(xm)
    assert "subtype=0" in f(q)
assert config.scrub(f"Error opening input {xm}: Connection refused").endswith(": Connection refused")
assert "password=***&channel=1" in config.scrub(xm)
# what was hidden before still is, and ordinary text stays as it was
assert config.scrub("srt://h:8890?streamid=publish:a:b:SECRET&passphrase=PP123") == \
    "srt://h:8890?streamid=publish:a:b:***&passphrase=***"
assert config.scrub("passphrase=abc pbkeylen=16 bypass=1") == "passphrase=*** pbkeylen=16 bypass=1"
print("v0.23 scrub of logins in addresses OK")

# ---- 5. emoji (outside the BMP) survive the settings file ----
for v in ("pw\U0001F600", "Barn cam \U0001F42E", "café", "tab\tquote\"back\\slash", "del\x7f", "nl\nx"):
    text = config.dumps({"camera": {"username": v}}, ["camera"])
    assert tomllib.loads(text)["camera"]["username"] == v, repr(v)
assert "\\ud83d" not in config.dumps({"camera": {"username": "pw\U0001F600"}}, ["camera"])
with tempfile.TemporaryDirectory() as t, mock.patch.object(secret, "protect", side_effect=lambda v: v), \
        mock.patch.object(secret, "unprotect", side_effect=lambda v: v):
    path = Path(t) / "camagent.toml"
    cfg = config.load_or_defaults(path)
    cfg["camera"].update(host="192.168.1.5", username="\U0001F600user", rtsp_url="rtsp://192.168.1.5/\U0001F600")
    config.save(cfg, path)
    back = config.load(path)
    assert back["camera"]["username"] == "\U0001F600user" and back["camera"]["rtsp_url"].endswith("\U0001F600")
print("v0.23 TOML emoji round-trip OK")

# ---- 6. the helper refuses an ffmpeg any user can change (Windows) ----
with mock.patch.object(prereqs, "admin_only_location", return_value=False), \
        mock.patch("camagent.camera.find_ffmpeg", return_value=r"C:\ffmpeg\bin\ffmpeg.exe"), \
        mock.patch("camagent.convert.detect") as det, mock.patch("subprocess.run") as run, \
        mock.patch.object(config, "load_all", return_value=({}, [], [])):
    assert remote.measure() is None and not det.called
    ok, out = remote.run_job({"kind": "snapshot", "secret": LOGIN, "payload": {
        "host": "192.168.1.10", "rtsp_url": "rtsp://192.168.1.10:554/live"}})
    assert not ok and "any user" in out and "--scope machine" in out and not run.called
    try:
        remote._safe_ffmpeg(r"C:\ffmpeg\bin\ffmpeg.exe")
        raise AssertionError("should refuse")
    except ValueError:
        pass
with mock.patch.object(prereqs, "admin_only_location", return_value=True):
    assert remote._safe_ffmpeg(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe").endswith("ffmpeg.exe")
assert remote._safe_ffmpeg("") == ""
print("v0.23 unsafe ffmpeg refused by the helper OK")
