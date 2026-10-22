"""Run with: python tests/test_v16.py  (from the repo folder).
Security (0.16): signed updates, checked server requests, the sign-in in its own file, Tempest from the hub only,
admin-only ffmpeg on Windows, and the sandboxed service."""
import base64, hashlib, json, os, sys, tempfile, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from camagent import config, prereqs, release, service, update, versions as V, weather

# 1. Signatures: a release signed with the key verifies; anything changed, or signed by another key, doesn't
key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                     serialization.PublicFormat.Raw)).decode()
wheel = b"PK fake wheel bytes"
name = release.wheel_name("0.16.0")
sums = f"{hashlib.sha256(wheel).hexdigest()}  {name}\n".encode()
sig = base64.b64encode(key.sign(sums))
release._check("0.16.0", sums, sig, wheel, keys=[pub])
for bad in [
    ("0.16.0", sums, sig, wheel + b"x"),                                   # the package was changed
    ("0.16.0", sums.replace(b"0.16.0", b"0.16.9"), sig, wheel),            # the checksums were changed
    ("0.16.0", sums, base64.b64encode(other.sign(sums)), wheel),           # signed by someone else
    ("0.16.1", sums, sig, wheel),                                          # another version's files
    ("0.16.0", sums, b"not base64!!", wheel),
]:
    try:
        release._check(*bad, keys=[pub])
        raise AssertionError(f"accepted {bad[0]}")
    except release.NotSigned:
        pass
assert release.PUBLIC_KEYS and all(len(base64.b64decode(k)) == 32 for k in release.PUBLIC_KEYS if
                                   k != "REPLACE_WITH_RELEASE_PUBLIC_KEY")

# 2. fetch(): downloads the three files, checks them, keeps them for rolling back; a release without them is refused
with tempfile.TemporaryDirectory() as d, mock.patch.object(release, "cache_dir", return_value=Path(d)):
    files = {"SHA256SUMS": sums, "SHA256SUMS.sig": sig, name: wheel}
    info = {"assets": [{"name": n, "browser_download_url": f"https://github.com/dl/{n}"} for n in files]}

    def get(url, accept="application/octet-stream"):
        if "api.github.com" in url:
            return json.dumps(info).encode()
        return files[url.rsplit("/", 1)[1]]
    with mock.patch.object(release, "_get", side_effect=get) as g:
        path = release.fetch("https://github.com/msullivan1993/camagent.git", "v0.16.0", keys=[pub])
        assert path.read_bytes() == wheel and (path.parent / "SHA256SUMS.sig").exists()
        n = g.call_count
        release.fetch("https://github.com/msullivan1993/camagent.git", "v0.16.0", keys=[pub])
        assert g.call_count == n                                            # the kept copy, no download
    info["assets"] = [a for a in info["assets"] if a["name"] != "SHA256SUMS.sig"]
    with mock.patch.object(release, "_get", side_effect=get):
        try:
            release.fetch("https://github.com/msullivan1993/camagent.git", "v0.17.0", keys=[pub])
            raise AssertionError("installed an unsigned release")
        except release.NotSigned as e:
            assert "isn't a signed release" in str(e)
    for tag in ("main", "v0.16.0;rm", "../v1"):
        try:
            release.fetch("https://github.com/msullivan1993/camagent.git", tag, keys=[pub])
            raise AssertionError(tag)
        except release.NotSigned:
            pass
try:
    release._get("http://example.com/x")
    raise AssertionError("plain http allowed")
except release.NotSigned:
    pass

# 3. The nightly update refuses an unsigned release and stays put
logs = []
with mock.patch.object(update, "latest_release", return_value="v9.0.0"), \
        mock.patch.object(update.service, "_require_admin"), \
        mock.patch.object(update, "_log", side_effect=logs.append), \
        mock.patch.object(update, "_status", return_value={}), \
        mock.patch.object(release, "fetch", side_effect=release.NotSigned("no signature")), \
        mock.patch.object(update, "_pip_install") as pip, \
        mock.patch.object(update.service, "restart") as restart:
    update.run(None, auto=True)
    assert not pip.called and not restart.called and any("NOT installing v9.0.0" in x for x in logs)
# by hand, a branch needs --unsigned
with mock.patch.object(update.service, "_require_admin"), \
        mock.patch.object(release, "fetch", side_effect=release.NotSigned("not a release version: main")):
    try:
        update.run(None, ref="main")
        raise AssertionError("installed a branch without --unsigned")
    except SystemExit as e:
        assert "--unsigned" in str(e)

# 4. What the website asks for is checked before it reaches ffmpeg
ok, mark = V.clean_request([{"suffix": "720", "height": 720, "fps": 30, "kbps": 3000},
                            {"suffix": "480", "height": "480", "kbps": 999999}],
                           {"on": ["480", "x"], "line1": "a" * 500, "every": 1})
assert ok[1]["kbps"] == 20000 and mark["on"] == ["480"] and len(mark["line1"]) == 80 and mark["every"] == 5
for bad in ([{"suffix": "720&passphrase=x", "height": 720, "kbps": 1}], [{"suffix": "999", "height": 999}],
            [{"suffix": "720", "height": 720, "kbps": 1}] * 5, "720", []):
    try:
        V.clean_request(bad, None)
        raise AssertionError(bad)
    except (ValueError, TypeError):
        pass

# 5. Linux: the sign-in is saved in account.toml (owner-only), not camagent.toml; older files move over
if os.name != "nt":
    with tempfile.TemporaryDirectory() as d:
        main = Path(d) / "camagent.toml"
        cfg = config.load_or_defaults(main)
        cfg["account"].update(token="yva_secret", name="Mike")
        config.save_main(cfg, main)
        assert "yva_secret" not in main.read_text()
        acct = config.account_path(main)
        assert "yva_secret" in acct.read_text() and (acct.stat().st_mode & 0o777) == 0o600
        assert config.load(main)["account"]["token"] == "yva_secret"
        acct.unlink()
        main.write_text(main.read_text() + '\n[account]\ntoken = "yva_old"\nname = ""\n')
        config.migrate_account(main)
        assert "yva_old" not in main.read_text() and config.load(main)["account"]["token"] == "yva_old"

# 6. Tempest: only from the local network, and only from the hub once it's known
assert weather._from_lan(("192.168.1.20", 50222)) and not weather._from_lan(("8.8.8.8", 50222))
obs = {"type": "obs_st", "serial_number": "ST-1", "obs": [[int(time.time()), 0, 1, 2, 0, 3, 1000, 20, 50, 100,
                                                            1, 50, 0, 0, 0, 0, 2.6, 1]]}


class FakeSock:
    def __init__(self, packets):
        self.packets = list(packets)

    def recvfrom(self, n):
        if not self.packets:
            raise weather.socket.timeout()
        return self.packets.pop(0)


r = weather.Reader({"kind": "tempest", "serial": "ST-1"})
r._sock = FakeSock([(json.dumps(obs).encode(), ("8.8.8.8", 1)),          # from the internet: ignored
                    (json.dumps(obs).encode(), ("192.168.1.20", 1))])
assert r.read(wait=1) and r._hub == "192.168.1.20"
fake = dict(obs, obs=[[int(time.time()), 0, 1, 99, 0, 3, 1000, 20, 50, 100, 1, 50, 0, 0, 0, 0, 2.6, 1]])
r._sock = FakeSock([(json.dumps(fake).encode(), ("192.168.1.66", 1))])  # another computer: ignored
assert r.read(wait=0.2) is None

# 7. Windows: an ffmpeg a normal user could replace isn't picked when an admin-only one exists
env = {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)", "SystemRoot": r"C:\Windows",
       "PROGRAMDATA": r"C:\ProgramData"}
with mock.patch.object(prereqs, "WINDOWS", True), mock.patch.dict(os.environ, env), \
        mock.patch("os.path.normcase", side_effect=lambda p: p.lower().replace("/", "\\")), \
        mock.patch("os.path.abspath", side_effect=lambda p: p), mock.patch("os.sep", "\\"):
    assert prereqs.admin_only_location(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe")
    assert not prereqs.admin_only_location(r"C:\Users\mike\AppData\Local\Microsoft\WinGet\ffmpeg.exe")
    assert not prereqs.admin_only_location(r"C:\ffmpeg\bin\ffmpeg.exe")
    assert prereqs.admin_only_location(r"C:\ProgramData\camagent\venv\Scripts\python.exe")
    assert not prereqs.admin_only_location(r"C:\ProgramData\other\ffmpeg.exe")

# 8. The Linux service is sandboxed: read-only system, no new privileges, no capabilities
unit = service._linux_unit("/etc/camagent/camagent.toml")
for line in ("NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=yes", "PrivateTmp=yes", "CapabilityBoundingSet=\n"):
    assert line in unit, line

# 9. Commands need the website's time stamp, aren't from the future, and aren't huge
from camagent import agent as A
a = A.CameraAgent.__new__(A.CameraAgent)
a.cfg = {"agent": {"stale_command_s": 5}}
a.topics = {"config": "cam/x/config", "ack": "cam/x/ack"}
a.log = mock.Mock()
a._handle = mock.Mock(return_value="done")
sent = []
client = mock.Mock(publish=lambda t, p, qos=0: sent.append(json.loads(p)))
msg = lambda body: mock.Mock(topic="cam/x/cmd", payload=body if isinstance(body, bytes) else json.dumps(body).encode())
a._on_message(client, None, msg({"op": "home", "req": "1"}))
a._on_message(client, None, msg({"op": "home", "req": "2", "ts": time.time() + 3600}))
a._on_message(client, None, msg(b"x" * (A.MAX_COMMAND_BYTES + 1)))
a._on_message(client, None, msg({"op": "home", "req": "3", "ts": time.time()}))
assert [s["ok"] for s in sent] == [False, False, False, True] and a._handle.call_count == 1

print("OK")
