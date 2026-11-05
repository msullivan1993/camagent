"""Passwords in the settings files, encrypted on Windows so a copied file (a backup, a zip sent for help) is useless
on any other computer.

Windows: the system's data protection (DPAPI) for the whole machine: any program on this PC can decrypt (CamAgent's
service under whatever account it runs, and the menu as an administrator), nothing on another PC can. That protects
copies of the files, not a PC someone already controls; the folder's permissions (administrators and the service
only) stay the real lock.

Linux (0.17.16): a random key encrypts them ("sealed:"), and the key itself is kept with systemd's credentials
(systemd-creds, systemd 250+), tied to this box by its systemd host key (not its TPM: one that failed would stop
the service from starting). The service gets
the key at start in memory ($CREDENTIALS_DIRECTORY, LoadCredentialEncrypted in the unit); CamAgent's commands, run
as root, ask systemd-creds for it. Without systemd-creds, or before the service is installed, files stay as they
were (root:camagent 0640). A reinstalled system can't read old values: enter the passwords again.

A value written by hand in plain text still works and is encrypted the next time CamAgent saves the file.
"""
import base64
import os

PREFIX = "dpapi:"
SEALED = "sealed:"                     # Linux: encrypted with the box's key (below)
KEY_NAME = "camagent-key"
KEY_FILE = "/etc/camagent/secret.cred"
ENTROPY = b"camagent settings v1"      # a fixed extra input: other programs' DPAPI data can't be passed off as ours
FIELDS = {("camera", "password"), ("platform", "srt_password"), ("platform", "srt_passphrase"),
          ("platform", "mqtt_password"), ("account", "token")}


def on_windows():
    return os.name == "nt"


def _blob(data=b""):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    return Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf, Blob


def _call(fn_name, data):
    import ctypes
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    inp, keep1, Blob = _blob(data)
    ent, keep2, _ = _blob(ENTROPY)
    out = Blob()
    flags = 0x1 | 0x4                  # CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE
    ok = getattr(crypt32, fn_name)(ctypes.byref(inp), None, ctypes.byref(ent), None, None, flags, ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.GetLastError(), f"{fn_name} failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(value):
    """The value as it's written to the file."""
    if not isinstance(value, str) or not value or value.startswith((PREFIX, SEALED)):
        return value
    if not on_windows():
        key = linux_key()
        if not key:
            return value               # no key here (yet): plain, as before
        from cryptography.fernet import Fernet
        return SEALED + Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")
    try:
        return PREFIX + base64.b64encode(_call("CryptProtectData", value.encode("utf-8"))).decode("ascii")
    except (OSError, AttributeError, ValueError):
        return value                   # never lose a password over this: plain, as before 0.17.7


def unprotect(value):
    """The value as CamAgent uses it. "" if it can't be decrypted (copied from another PC): enter it again."""
    if not isinstance(value, str):
        return value
    if value.startswith(SEALED):
        key = linux_key()
        if not key:
            return ""
        from cryptography.fernet import Fernet, InvalidToken
        try:
            return Fernet(key).decrypt(value[len(SEALED):].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""
    if not value.startswith(PREFIX):
        return value
    try:
        return _call("CryptUnprotectData", base64.b64decode(value[len(PREFIX):])).decode("utf-8")
    except (OSError, AttributeError, ValueError):
        return ""


# ---------- Linux: the box's key, kept by systemd ----------
_KEY = {}


def linux_key():
    """The key (bytes), or None: from the service's credentials, else (CamAgent's commands, as root) systemd-creds."""
    if "k" in _KEY:
        return _KEY["k"]
    key = None
    creds = os.environ.get("CREDENTIALS_DIRECTORY")
    if creds:
        try:
            with open(os.path.join(creds, KEY_NAME), "rb") as f:
                key = f.read().strip() or None
        except OSError:
            key = None
    if key is None and os.path.exists(KEY_FILE) and hasattr(os, "geteuid") and os.geteuid() == 0:
        import subprocess
        try:
            r = subprocess.run(["systemd-creds", "decrypt", f"--name={KEY_NAME}", KEY_FILE, "-"],
                               capture_output=True, timeout=20)
            key = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
        except (OSError, subprocess.SubprocessError):
            key = None
    _KEY["k"] = key
    return key


def creds_supported():
    """systemd-creds and a systemd new enough for LoadCredentialEncrypted (250+)."""
    import shutil
    import subprocess
    if on_windows() or not shutil.which("systemd-creds"):
        return False
    try:
        out = subprocess.run(["systemctl", "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(out.split()[1]) >= 250
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return False


def ensure_linux_key():
    """As root, when the service is installed or updated: make the box's key once (random, kept by systemd-creds).
    True if a key file exists afterwards."""
    if os.path.exists(KEY_FILE):
        return True
    if not creds_supported() or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    import subprocess
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    try:
        # the host key only, not the TPM: a TPM that ever failed to unlock would stop the service from starting
        r = subprocess.run(["systemd-creds", "encrypt", "--with-key=host", f"--name={KEY_NAME}", "-", KEY_FILE], input=key,
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0 or not os.path.exists(KEY_FILE):
        return False
    os.chmod(KEY_FILE, 0o600)
    _KEY["k"] = key
    return True


def apply(cfg, fn):
    """fn (protect or unprotect) over every password field of a settings dict, in place; returns it."""
    for section, key in FIELDS:
        sec = cfg.get(section)
        if isinstance(sec, dict) and key in sec:
            sec[key] = fn(sec[key])
    return cfg
