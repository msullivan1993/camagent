"""Run with: python tests/test_v17.py  (from the repo folder).
Security (0.17): signed dependencies (requirements.lock), signed installers, the Windows service's own account,
unsafe ffmpeg refused, sign-out revokes, sturdier MQTT, SRT passphrase required, and the website's per-camera
permission for a computer."""
import base64, builtins, hashlib, importlib.util, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from camagent import agent as A, config, configure, link, menu, prereqs, release, service, update  # noqa: E402
from camagent import versions as V  # noqa: E402

quiet = mock.patch.object(builtins, "print", lambda *a, **k: None)
key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
pub = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                     serialization.PublicFormat.Raw)).decode()


def signed_release(version, files):
    sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items()).encode()
    return sums, base64.b64encode(key.sign(sums))


# 1. From 0.17 a release must carry its signed lock, and the lock must match
wheel, lock = b"PK wheel 0.17", b"zeep==4.3.3 --hash=sha256:" + b"a" * 64 + b"\n"
sums, sig = signed_release("0.17.0", {release.wheel_name("0.17.0"): wheel, release.lock_name("0.17.0"): lock})
release._check("0.17.0", sums, sig, wheel, keys=[pub], lock_bytes=lock)
for bad_lock in (None, lock + b"evil==1.0\n"):
    try:
        release._check("0.17.0", sums, sig, wheel, keys=[pub], lock_bytes=bad_lock)
        raise AssertionError("accepted a missing or changed lock")
    except release.NotSigned:
        pass
only_wheel, sig2 = signed_release("0.17.0", {release.wheel_name("0.17.0"): wheel})
try:
    release._check("0.17.0", only_wheel, sig2, wheel, keys=[pub])
    raise AssertionError("accepted a 0.17 release without its lock")
except release.NotSigned as e:
    assert "dependencies" in str(e)
old_sums, old_sig = signed_release("0.16.5", {release.wheel_name("0.16.5"): wheel})
release._check("0.16.5", old_sums, old_sig, wheel, keys=[pub])      # older releases (rolling back) still install

with tempfile.TemporaryDirectory() as d, mock.patch.object(release, "cache_dir", return_value=Path(d)):
    files = {"SHA256SUMS": sums, "SHA256SUMS.sig": sig, release.wheel_name("0.17.0"): wheel,
             release.lock_name("0.17.0"): lock}
    info = {"assets": [{"name": n, "browser_download_url": f"https://github.com/dl/{n}"} for n in files]}

    def get(url, accept="application/octet-stream"):
        return json.dumps(info).encode() if "api.github.com" in url else files[url.rsplit("/", 1)[1]]
    with mock.patch.object(release, "_get", side_effect=get) as g:
        w = release.fetch("https://github.com/msullivan1993/camagent.git", "v0.17.0", keys=[pub])
        assert release.lock_for(w).read_bytes() == lock
        n = g.call_count
        release.fetch("https://github.com/msullivan1993/camagent.git", "v0.17.0", keys=[pub])
        assert g.call_count == n                                    # kept copy, lock checked too
        release.lock_for(w).write_bytes(lock + b"x")                # a kept lock changed: downloaded again
        release.fetch("https://github.com/msullivan1993/camagent.git", "v0.17.0", keys=[pub])
        assert g.call_count > n and release.lock_for(w).read_bytes() == lock

# 2. The updater installs the dependencies only from the lock (checksums, wheels only), the package with --no-deps
runs = []
with mock.patch.object(update.subprocess, "run", side_effect=lambda cmd, **k: runs.append(cmd) or
                       NS(stdout="camagent 0.17.0", returncode=0)):
    update._pip_install(Path("/r/camagent-0.17.0-py3-none-any.whl"), Path("/r/camagent-0.17.0-requirements.lock"))
    assert "--require-hashes" in runs[0] and ":all:" in runs[0] and runs[0][-2:] == ["-r", "/r/camagent-0.17.0-requirements.lock"]
    assert "--no-deps" in runs[1] and "--upgrade" not in runs[1] and runs[1][-1].endswith(".whl")
    assert all(c[:3] == [sys.executable, "-I", "-m"] for c in runs)
    runs.clear()
    update._pip_install(Path("/r/camagent-0.16.5-py3-none-any.whl"), None)       # before 0.17: no deps fetched
    assert len(runs) == 2 and "--no-deps" in runs[0] and "-r" not in runs[0]

# 3. The lock in the repo pins every dependency, in pyproject's range, with checksums (scripts/lock_requirements.py)
spec = importlib.util.spec_from_file_location("lock_requirements", ROOT / "scripts" / "lock_requirements.py")
LR = importlib.util.module_from_spec(spec); spec.loader.exec_module(LR)
reqs = LR.project_requirements()
assert not any("onvif" in r for r in reqs)                           # kept inside camagent now (camagent/_onvif)
lock_text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
assert LR.check(lock_text, reqs) == [], LR.check(lock_text, reqs)
pins = LR.parse_lock(lock_text)
assert {"zeep", "paho-mqtt", "cryptography", "lxml", "cffi"} <= set(pins) and all(len(h) >= 1 for _, h in pins.values())
assert all(re.search(r"<\d", r) or "==" in r for r in reqs), reqs          # pyproject ranges have upper bounds
assert LR.check(lock_text.replace("zeep==", "zeepx=="), reqs)                  # missing: reported
assert LR.check("zeep==9.0 \\\n    --hash=sha256:" + "b" * 64 + "\n", ["zeep>=4.2,<5"])   # out of range
assert LR.check("zeep==4.3.3\n", ["zeep>=4.2,<5"])                             # no checksums
from camagent._onvif import ONVIFCamera  # noqa: E402,F401 - the kept copy imports

# 4. The installers' verifier: same code in both installers, the same key as release.py, a correct Ed25519 check,
#    and nothing written unless the signature and checksums are right
canon = (ROOT / "scripts" / "verify_release.py").read_text(encoding="utf-8").replace("\r\n", "\n")


def embedded(path):
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index("# camagent release verifier (")
    end = text.index("# end of camagent release verifier", start) + len("# end of camagent release verifier\n")
    return text[start:end]


for f in ("install.sh", "install.ps1"):
    assert embedded(ROOT / "scripts" / f) == canon, (f"scripts/{f}'s copy of the verifier differs from "
                                                    "scripts/verify_release.py: copy it in again")
spec = importlib.util.spec_from_file_location("verify_release", ROOT / "scripts" / "verify_release.py")
VR = importlib.util.module_from_spec(spec); spec.loader.exec_module(VR)
assert VR.KEYS == release.PUBLIC_KEYS
for i in range(5):
    k = Ed25519PrivateKey.generate()
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    m = os.urandom(i * 31)
    s = k.sign(m)
    assert VR.ed25519_verify(raw, m, s) and not VR.ed25519_verify(raw, m + b"!", s)
    assert not VR.ed25519_verify(raw, m, s[:10] + bytes([s[10] ^ 1]) + s[11:])
    assert not VR.ed25519_verify(raw, m, s[:40] + bytes([s[40] ^ 1]) + s[41:])
# RFC 8032 test vector 1
assert VR.ed25519_verify(bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"), b"",
                         bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33b"
                                       "acc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"))

rel = {"SHA256SUMS": sums, "SHA256SUMS.sig": sig, release.wheel_name("0.17.0"): wheel,
       release.lock_name("0.17.0"): lock}


def gh(files, tag="v0.17.0"):
    def get(url, accept="application/octet-stream"):
        if "api.github.com" in url:
            return json.dumps({"tag_name": tag, "assets": [{"name": n, "browser_download_url": f"https://github.com/dl/{n}"}
                                                           for n in files]}).encode()
        return files[url.rsplit("/", 1)[1]]
    return get


with tempfile.TemporaryDirectory() as d, mock.patch.object(VR, "get", side_effect=gh(rel)):
    assert VR.fetch(d, "", keys=[pub]) == "0.17.0"
    assert (Path(d) / release.lock_name("0.17.0")).read_bytes() == lock
for bad in ({**rel, release.wheel_name("0.17.0"): wheel + b"x"},                         # package changed
            {**rel, "SHA256SUMS.sig": base64.b64encode(other.sign(sums))},                  # someone else's key
            {k: v for k, v in rel.items() if k != release.lock_name("0.17.0")}):           # no lock
    with tempfile.TemporaryDirectory() as d, mock.patch.object(VR, "get", side_effect=gh(bad)):
        try:
            VR.fetch(d, "", keys=[pub])
            raise AssertionError("verifier accepted a bad release")
        except VR.Refused:
            assert not os.listdir(d)                                                     # nothing written
# a release from before 0.17.0 (no lock): its signed package alone is enough, and still must match
old_wheel = b"old wheel"
old_sums = f"{hashlib.sha256(old_wheel).hexdigest()}  {release.wheel_name('0.16.5')}\n".encode()
old = {"SHA256SUMS": old_sums, "SHA256SUMS.sig": base64.b64encode(key.sign(old_sums)),
       release.wheel_name("0.16.5"): old_wheel}
with tempfile.TemporaryDirectory() as d, mock.patch.object(VR, "get", side_effect=gh(old, "v0.16.5")):
    assert VR.fetch(d, "", keys=[pub]) == "0.16.5"
    assert sorted(os.listdir(d)) == sorted(old)
with tempfile.TemporaryDirectory() as d, \
        mock.patch.object(VR, "get", side_effect=gh({**old, release.wheel_name("0.16.5"): b"evil"}, "v0.16.5")):
    try:
        VR.fetch(d, "", keys=[pub])
        raise AssertionError("verifier accepted a changed old package")
    except VR.Refused:
        assert not os.listdir(d)
for tag in ("main", "v1.0;rm -rf /", "../v1.0.0"):
    try:
        VR.fetch(tempfile.gettempdir(), tag, keys=[pub])
        raise AssertionError(tag)
    except VR.Refused:
        pass
# a failure is a plain message and exit code 1 (the installers stop there)
with tempfile.TemporaryDirectory() as d, mock.patch.object(VR, "get", side_effect=gh(rel)), \
        mock.patch.object(sys, "stderr") as err:
    assert VR.main(["x", d]) == 1 and "NOT INSTALLED" in "".join(c.args[0] for c in err.write.call_args_list)
# install.sh's block parses and runs: with a folder that isn't empty, the verifier refuses and the script stops
sh = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
block = sh[sh.index('VERSION=$(python3 -I -'):sh.index(") || exit 1") + len(") || exit 1")]
block = block.replace("python3", sys.executable)
with tempfile.TemporaryDirectory() as d:
    (Path(d) / "x").write_text("x")
    r = subprocess.run(["bash", "-c", f'set -euo pipefail; DL="{d}"; REF=""\n{block}\necho REACHED'],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "REACHED" not in r.stdout and "usage" in r.stderr, (r.returncode, r.stdout, r.stderr)
assert "git+" not in sh and "--require-hashes" in sh and "--no-deps" in sh and "pip install -q --upgrade pip" not in sh
ps = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
assert "git+" not in ps and "--require-hashes" in ps and "--no-deps" in ps and "Git.Git" not in ps
sign_src = (ROOT / "scripts" / "sign_release.py").read_text(encoding="utf-8")
assert "requirements.lock" in sign_src and "lock_bytes=" in sign_src

# 5. Windows: the service runs as its own account (NT SERVICE\camagent), with its SID known up front
assert service.service_sid("TrustedInstaller") == "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
assert service.service_sid().startswith("S-1-5-80-") and service.service_sid("CamAgent") == service.service_sid()
with tempfile.TemporaryDirectory() as d:
    main = Path(d) / "camagent.toml"
    assert service.windows_account(main)[0] == "LocalSystem"                 # default (0.17.3: as before 0.17)
    m = config.load_or_defaults(main); m["agent"]["windows_account"] = "localservice"; config.save_main(m, main)
    assert service.windows_account(main) == ("NT AUTHORITY\\LocalService", "S-1-5-19", "localservice")
    m["agent"]["windows_account"] = "system"; config.save_main(m, main)
    assert service.windows_account(main) == ("LocalSystem", None, "system")
    m["agent"]["windows_account"] = "nonsense"; config.save_main(m, main)
    assert service.windows_account(main)[2] == "system"
    calls = []
    with mock.patch.object(service.subprocess, "run", side_effect=lambda cmd, **k: calls.append(cmd) or
                           NS(returncode=0, stdout="", stderr="")), \
            mock.patch.object(service, "_win_grant_access") as grant, quiet, \
            mock.patch.object(prereqs, "unsafe_python", return_value=""):
        m["agent"]["windows_account"] = "virtual"; config.save_main(m, main)       # 0.17.0-0.17.2's saved default
        assert service.windows_account(main)[2] == "system"
        m["agent"]["windows_account"] = "limited"; config.save_main(m, main)       # chosen (0.17.4)
        assert service._win_apply_account(main)
        # 0.17.1: no "password=" (an empty one made Windows refuse the account: error 1057)
        assert calls[-1] == ["sc", "config", "camagent", "obj=", "NT SERVICE\\camagent"]
        assert grant.call_args[0][0] == service.service_sid()
        m["agent"]["windows_account"] = "system"; config.save_main(m, main)
        service._win_apply_account(main)
        assert calls[-1] == ["sc", "config", "camagent", "obj=", "LocalSystem"]
    # what the account may touch: read everything, write logs, state and status.json only; account.toml locked
    icacls = []
    with mock.patch.object(service, "_icacls", side_effect=lambda *a: icacls.append(a) or True), \
            mock.patch.object(service, "base_dir", return_value=Path(d)), \
            mock.patch.object(config, "base_dir", return_value=Path(d)), \
            mock.patch.object(config, "os", NS(name="nt", environ=os.environ, path=os.path)), \
            mock.patch.object(config, "default_config_path", return_value=main), \
            mock.patch.object(config, "lock_windows_file") as locked:
        (Path(d) / "account.toml").write_text("[account]\n")
        service._win_grant_access("S-1-5-80-1")
        grants = {(str(a[0]), a[2]) for a in icacls}
        assert (d, "*S-1-5-80-1:(OI)(CI)(RX)") in grants and (str(Path(d) / "logs"), "*S-1-5-80-1:(OI)(CI)(M)") in grants
        assert (str(Path(d) / "state"), "*S-1-5-80-1:(OI)(CI)(M)") in grants
        assert (str(Path(d) / "status.json"), "*S-1-5-80-1:(M)") in grants
        assert not any("account" in str(a[0]) for a in icacls) and locked.call_args[0][0] == Path(d) / "account.toml"
assert "-I -u -m camagent run" in service._win_xml("C:/x/camagent.toml")
# WinSW's checksum is checked on every install, not only when it's first downloaded
with tempfile.TemporaryDirectory() as d:
    exe = Path(d) / "camagent-service.exe"
    exe.write_bytes(b"tampered")
    assert not service._winsw_ok(exe)
    with mock.patch.object(service, "os", NS(name="nt", environ=os.environ)), mock.patch.object(service, "_require_admin"), \
            mock.patch.object(service, "_win_paths", return_value=(exe, Path(d) / "x.xml")), \
            mock.patch.object(service, "is_installed", return_value=True), \
            mock.patch.object(service, "_download_checked") as dl, mock.patch.object(service, "_run"), \
            mock.patch.object(service, "_win_apply_account"), mock.patch.object(service, "_win_xml", return_value=""), \
            mock.patch.object(service.subprocess, "run"), mock.patch.object(service.time, "sleep"), \
            mock.patch.object(service, "update_schedule_installed", return_value=True), quiet:
        (Path(d) / "camagent.toml").write_text("")
        service.install(Path(d) / "camagent.toml")
        assert dl.called                                            # replaced from the checked download

# 6. Windows: an ffmpeg a normal user could replace (e.g. C:\ffmpeg) is used anyway, with a warning for the host
#    (0.17.3, Mike: securing the PC is the host's job; refusing it kept cameras off the air)
with mock.patch.object(prereqs, "WINDOWS", True), mock.patch.object(prereqs, "admin_only_location", return_value=False), \
        mock.patch.object(prereqs, "is_file", return_value=True), \
        mock.patch.object(prereqs, "which", return_value=r"C:\ffmpeg\bin\ffmpeg.exe"), \
        mock.patch.object(prereqs, "_windows_ffmpeg_candidates", return_value=[]), quiet:
    assert prereqs.find_ffmpeg("") == r"C:\ffmpeg\bin\ffmpeg.exe"
    assert "any user of this computer can change" in prereqs.unsafe_ffmpeg()
    assert prereqs.unsafe_ffmpeg() in prereqs.notices()                 # on the camera's Health tab
with mock.patch.object(prereqs, "WINDOWS", True), mock.patch.object(prereqs, "admin_only_location", return_value=True), \
        mock.patch.object(prereqs, "which", return_value=r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"):
    assert prereqs.find_ffmpeg("").endswith("ffmpeg.exe") and prereqs.unsafe_ffmpeg() == ""

# 7. Sign-out revokes the token on the website (401 = already invalid: fine too); offline, it says what to do
for status, revoked in ((200, True), (401, True), (500, False)):
    with tempfile.TemporaryDirectory() as d:
        main = Path(d) / "camagent.toml"
        m = config.load_or_defaults(main); m["account"].update(token="yva_x", name="Mike"); config.save_main(m, main)
        out = []
        with mock.patch.object(link, "_call", return_value=(status, {})) as call, \
                mock.patch.object(builtins, "print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
            configure.sign_out(main)
        assert call.call_args[0][1:3] == ("POST", "/api/agent/signout") and call.call_args[1]["token"] == "yva_x"
        assert call.call_args[0][3] == {}
        assert config.load(main)["account"]["token"] == ""
        assert ("yonderview.net/link" in " ".join(out)) is not revoked
with tempfile.TemporaryDirectory() as d:
    main = Path(d) / "camagent.toml"
    m = config.load_or_defaults(main); m["account"].update(token="yva_x"); config.save_main(m, main)
    out = []
    with mock.patch.object(link, "_call", side_effect=link.LinkError("Couldn't reach")), \
            mock.patch.object(builtins, "print", side_effect=lambda *a, **k: out.append(" ".join(map(str, a)))):
        configure.sign_out(main)
    assert config.load(main)["account"]["token"] == "" and "Your computers" in " ".join(out)

# 8. The website's per-camera permission: listed but marked; connecting asks for the OK, then tries again
with mock.patch.object(link, "_call", return_value=(200, {"cameras": [{"slug": "a"}, {"slug": "b", "allowed": False}]})):
    cams = link.cameras("https://yonderview.net", "t")
assert [c["allowed"] for c in cams] == [True, False]
with mock.patch.object(link, "_call", return_value=(401, {})):
    try:
        link.cameras("https://yonderview.net", "t")
        raise AssertionError
    except link.NotSignedIn as e:
        assert "90 days" in str(e)
approve = {"needs_approval": True, "approve_url": "https://yonderview.net/link/computers/3/allow/b/", "error": "..."}
with mock.patch.object(link, "_call", return_value=(403, approve)):
    assert link.connect("https://yonderview.net", "t", "b") == ("approve", approve["approve_url"], "...")
with mock.patch.object(link, "_call", return_value=(403, dict(approve, approve_url="https://evil.example/x"))):
    assert link.connect("https://yonderview.net", "t", "b")[1] == "https://yonderview.net/link"
BLOCK = ("CAMERA=b\nSRT_URL=srt://ingest.yonderview.net:8890?streamid=publish:b:b:PW&latency=400000&passphrase=PH"
         "&pbkeylen=16\nMQTT_HOST=mqtt.yonderview.net\nMQTT_PASS=MQ")
cfg = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS}
seq = iter([("approve", approve["approve_url"], "..."), ("ok", BLOCK, {})])
shown, picks = [], iter(["2", ""])                                    # camera 2, then Enter once it's allowed
with mock.patch.object(configure, "account_token", return_value=("https://yonderview.net", "t", "")), \
        mock.patch.object(link, "cameras", return_value=[{"slug": "a", "name": "A", "allowed": True},
                                                         {"slug": "b", "name": "B", "allowed": False}]), \
        mock.patch.object(link, "connect", side_effect=lambda *a, **k: next(seq)) as conn, \
        mock.patch.object(builtins, "input", lambda *a: next(picks)), \
        mock.patch.object(builtins, "print", side_effect=lambda *a, **k: shown.append(" ".join(map(str, a)))):
    assert configure._from_account(cfg)
assert conn.call_count == 2 and cfg["platform"]["camera_id"] == "b" and cfg["platform"]["srt_passphrase"] == "PH"
assert any("needs your OK on the website first" in s for s in shown) and any(approve["approve_url"] in s for s in shown)
seq = iter([("approve", approve["approve_url"], "...")])
picks = iter(["2", "q"])
with mock.patch.object(configure, "account_token", return_value=("https://yonderview.net", "t", "")), \
        mock.patch.object(link, "cameras", return_value=[{"slug": "a", "name": "A"}, {"slug": "b", "name": "B"}]), \
        mock.patch.object(link, "connect", side_effect=lambda *a, **k: next(seq)), \
        mock.patch.object(builtins, "input", lambda *a: next(picks)), quiet:
    assert not configure._from_account(cfg)                         # q: back to pasting a block

# 9. MQTT: bad messages never stop the connection; commands far in the future or repeated aren't run
c = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
c["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=False)
c["platform"].update(camera_id="mq_1", ingest_host="i", srt_password="s", srt_passphrase="phrase0123", mqtt_host="m",
                     mqtt_password="p")
a = A.CameraAgent(c)
assert a.mq.suppress_exceptions is True
sent = []
client = mock.Mock(publish=lambda t, p, qos=0: sent.append(json.loads(p)))
a._handle = mock.Mock(return_value="done")
msg = lambda body, topic="cam/mq_1/cmd": NS(topic=topic, payload=body if isinstance(body, bytes) else json.dumps(body).encode())
with quiet:
    for body in (b"[1, 2]", b"\"x\"", b"null", b"{bad", b"{}" + b" " * A.MAX_CONFIG_BYTES, json.dumps({"max_height": "x"}).encode()):
        a._on_message(client, None, msg(body, "cam/mq_1/config"))                 # ignored, nothing raised
    assert a.server["max_height"] is None
    with mock.patch.object(a, "_use_config", side_effect=TypeError("boom")):
        a._on_message(client, None, msg({}, "cam/mq_1/config"))
    with mock.patch.object(a, "_on_command", side_effect=KeyError("boom")):
        a._on_message(client, None, msg({"op": "home"}))                          # caught too
    a._on_message(client, None, msg({"op": "home", "req": "f1", "ts": time.time() + 3600}))     # a day-old replay?
    a._on_message(client, None, msg({"op": "home", "req": "r1", "ts": time.time()}))
    a._on_message(client, None, msg({"op": "home", "req": "r1", "ts": time.time()}))           # the same again
    a._on_message(client, None, msg({"op": "home", "req": "r2", "ts": time.time() + 20}))      # clock a bit behind
assert [x["ok"] for x in sent] == [False, True, True] and a._handle.call_count == 2, sent
a._seen_req.clear()
for i in range(A.SEEN_REQ_MAX + 50):
    a._replayed(f"x{i}")
assert len(a._seen_req) <= A.SEEN_REQ_MAX

# 10. SRT: a settings block without a passphrase is refused; values are URL-encoded
try:
    config.parse_settings_block("CAMERA=x\nSRT_URL=srt://i:8890?streamid=publish:x:x:PW&latency=400000")
    raise AssertionError("accepted a block without a passphrase")
except ValueError as e:
    assert "unencrypted" in str(e)
c2 = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
c2["platform"].update(camera_id="cam_1", ingest_host="i", srt_password="p&w=1", srt_passphrase="a b&c")
url = config.srt_url(c2)
assert "streamid=publish:cam_1:cam_1:p%26w%3D1&" in url and "passphrase=a%20b%26c&" in url, url
assert "p%26w" not in config.redact(url) and "a%20b" not in config.redact(url) and "a%20b" not in config.scrub(url)
c2["platform"]["srt_passphrase"] = ""
try:
    config.srt_url(c2)
    raise AssertionError
except ValueError:
    pass
c2["camera"].update(rtsp_url="rtsp://x", ptz=False); c2["platform"].update(mqtt_host="m", mqtt_password="p")
assert "[platform] srt_passphrase" in A.missing_settings(c2)

# 11. keep_s is capped at 10 minutes; bad values fall back
assert V.keep_seconds(10 ** 9) == 600 and V.keep_seconds(5) == 30 and V.keep_seconds("x") == 180
assert V.keep_seconds(None) == 180 and V.keep_seconds(float("inf")) == 180

# 12. The menu reopens with -I
with mock.patch("os.execv") as ex, quiet:
    menu.relaunch("cfg.toml")
assert ex.call_args[0][1][:4] == [sys.executable, "-I", "-m", "camagent"]

# 13. A camera file with a bad camera ID is skipped, with a clear reason
with tempfile.TemporaryDirectory() as d:
    main = Path(d) / "camagent.toml"
    (Path(d) / "cameras").mkdir()
    (Path(d) / "cameras" / "bad.toml").write_text('[platform]\ncamera_id = "../../etc/x"\n')
    (Path(d) / "cameras" / "good_1.toml").write_text('[platform]\ncamera_id = "good_1"\n')
    _, cams, problems = config.load_all(main)
    assert [x["platform"]["camera_id"] for x in cams] == ["good_1"] and "isn't valid" in problems[0]

# 14. status.json is rewritten in place when no file can be made beside it (the Windows service's account)
with tempfile.TemporaryDirectory() as d:
    st = Path(d) / "status.json"
    st.write_text("{\"old\": " + "1" * 500 + "}")
    sup = A.Supervisor.__new__(A.Supervisor)
    sup.agents, sup.started = [], time.time()
    with mock.patch.object(config, "status_path", return_value=st), \
            mock.patch.object(A.os, "replace", side_effect=PermissionError("no")):
        sup._write_status()
    assert json.loads(st.read_text())["version"] == A.__version__

print("v0.17 OK")
