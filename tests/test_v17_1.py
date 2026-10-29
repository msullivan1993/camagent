"""0.17.1: the Windows service moves to its own limited account only when it can run there.

0.17.0 on a PC set up long ago on a Python installed for one user (C:\\Users\\Admin\\...) switched anyway; the
limited account can't open that folder, so the service failed every few seconds ("No Python at ...")."""
import builtins
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import agent as A, config, prereqs, service  # noqa: E402

quiet = mock.patch.object(builtins, "print", lambda *a, **k: None)

# the Python the venv was made from, read from pyvenv.cfg
with tempfile.TemporaryDirectory() as d:
    Path(d, "pyvenv.cfg").write_text("home = C:\\Users\\Admin\\AppData\\Local\\Programs\\Python\\Python312\n"
                                     "version = 3.12.7\n", encoding="utf-8")
    with mock.patch.object(prereqs.sys, "prefix", d), mock.patch.object(prereqs, "WINDOWS", True):
        assert prereqs.base_python().endswith("Python312" + os.sep + "python.exe") or \
            prereqs.base_python().endswith("Python312/python.exe")

# one-user Python: a notice, and the service stays LocalSystem (no permissions granted, no virtual account)
with mock.patch.object(prereqs, "WINDOWS", True), \
        mock.patch.object(prereqs, "base_python", return_value=r"C:\Users\Admin\AppData\Local\Programs\Python\Python312\python.exe"), \
        mock.patch.object(prereqs, "admin_only_location", return_value=False):
    why = prereqs.unsafe_python()
    assert "installed for one user" in why and "installer again" in why
    assert prereqs.notices() == [why]
    calls = []
    with tempfile.TemporaryDirectory() as d:
        main = Path(d) / "camagent.toml"
        with mock.patch.object(service.subprocess, "run", side_effect=lambda cmd, **k: calls.append(cmd) or
                               NS(returncode=0, stdout="", stderr="")), \
                mock.patch.object(service, "_win_grant_access") as grant, quiet:
            service._win_apply_account(main)
        assert calls[-1] == ["sc", "config", "camagent", "obj=", "LocalSystem"], calls
        grant.assert_not_called()

# all-users Python: no notice; the limited account
with mock.patch.object(prereqs, "WINDOWS", True), mock.patch.object(prereqs, "admin_only_location", return_value=True):
    assert prereqs.unsafe_python() == "" and prereqs.notices() == []

# Linux, macOS: never a notice (the service has always run as its own user there)
with mock.patch.object(prereqs, "WINDOWS", False):
    assert prereqs.unsafe_python() == ""

# the notice reaches the website in the telemetry (the camera's Health tab)
fake = NS(started=0, ptz=None, ptz_position=None, controls=None, events_pending=[], uplink=None, versions=None,
          weather=None, weather_pending=[], weather_name="", position_kind=None)
with mock.patch.object(prereqs, "notices", return_value=["fix me"]):
    assert A.CameraAgent._info(fake)["notices"] == ["fix me"]
with mock.patch.object(prereqs, "notices", return_value=[]):
    assert "notices" not in A.CameraAgent._info(fake)
print("v0.17.1 OK")
