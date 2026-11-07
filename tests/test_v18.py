"""0.18 (beta): camagent remote, the website setup helper: checks in, runs only known jobs, sends results."""
import sys
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
