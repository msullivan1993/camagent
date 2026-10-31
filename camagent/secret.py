"""Passwords in the settings files, encrypted on Windows so a copied file (a backup, a zip sent for help) is useless
on any other computer.

Windows: the system's data protection (DPAPI) for the whole machine: any program on this PC can decrypt (CamAgent's
service under whatever account it runs, and the menu as an administrator), nothing on another PC can. That protects
copies of the files, not a PC someone already controls; the folder's permissions (administrators and the service
only) stay the real lock. Linux: unchanged (root:camagent 0640 files; there's no machine-wide store a non-root
service could use without systemd's help).

A value written by hand in plain text still works and is encrypted the next time CamAgent saves the file.
"""
import base64
import os

PREFIX = "dpapi:"
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
    if not on_windows() or not isinstance(value, str) or not value or value.startswith(PREFIX):
        return value
    try:
        return PREFIX + base64.b64encode(_call("CryptProtectData", value.encode("utf-8"))).decode("ascii")
    except (OSError, AttributeError, ValueError):
        return value                   # never lose a password over this: plain, as before 0.17.7


def unprotect(value):
    """The value as CamAgent uses it. "" if it can't be decrypted (copied from another PC): enter it again."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    try:
        return _call("CryptUnprotectData", base64.b64decode(value[len(PREFIX):])).decode("utf-8")
    except (OSError, AttributeError, ValueError):
        return ""


def apply(cfg, fn):
    """fn (protect or unprotect) over every password field of a settings dict, in place; returns it."""
    for section, key in FIELDS:
        sec = cfg.get(section)
        if isinstance(sec, dict) and key in sec:
            sec[key] = fn(sec[key])
    return cfg
