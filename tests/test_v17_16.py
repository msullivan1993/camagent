"""0.17.16: passwords in the settings files encrypted on Linux with the box's key (kept by systemd-creds)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cryptography.fernet import Fernet  # noqa: E402

from camagent import secret, service  # noqa: E402

key = Fernet.generate_key()
with mock.patch.object(secret, "on_windows", return_value=False):
    secret._KEY.clear(); secret._KEY["k"] = key
    sealed = secret.protect("Camera$Pass")
    assert sealed.startswith("sealed:") and "Camera$Pass" not in sealed
    assert secret.unprotect(sealed) == "Camera$Pass"
    assert secret.protect(sealed) == sealed                        # not twice
    secret._KEY["k"] = Fernet.generate_key()                       # another box
    assert secret.unprotect(sealed) == ""
    secret._KEY["k"] = None                                        # no key here: plain, as before
    assert secret.protect("plain") == "plain"
    assert secret.unprotect("plain") == "plain"

    # the service reads the key from its credentials folder
    secret._KEY.clear()
    with tempfile.TemporaryDirectory() as d:
        Path(d, secret.KEY_NAME).write_bytes(key + b"\n")
        with mock.patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": d}):
            assert secret.linux_key() == key
    secret._KEY.clear()

    # made once, with the host key, as root
    with tempfile.TemporaryDirectory() as d:
        kf = os.path.join(d, "secret.cred")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            Path(kf).write_bytes(b"encrypted")
            return mock.Mock(returncode=0)
        with mock.patch.object(secret, "KEY_FILE", kf), mock.patch.object(secret, "creds_supported", return_value=True), \
                mock.patch.object(secret.os, "geteuid", return_value=0, create=True), \
                mock.patch("subprocess.run", side_effect=fake_run):
            assert secret.ensure_linux_key()
            assert "--with-key=host" in calls[0] and oct(os.stat(kf).st_mode)[-3:] == "600"
            assert secret.ensure_linux_key() and len(calls) == 1      # only once
            with mock.patch.object(service, "default_config_path", return_value=Path("/etc/camagent/camagent.toml")):
                unit = service._linux_unit("/etc/camagent/camagent.toml")
            assert f"LoadCredentialEncrypted=camagent-key:{kf}" in unit
    secret._KEY.clear()
print("v0.17.16 OK")
