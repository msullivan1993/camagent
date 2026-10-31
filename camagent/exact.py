"""Exact pan, tilt and zoom from Dahua-family cameras (0.17.10: Dahua, Amcrest, Lorex, IMOU).

ONVIF reports a camera's position as numbers from -1 to 1, which YonderView ties to the compass and the horizon by
calibrating. These cameras also report their real degrees and zoom through their own web interface
(/cgi-bin/ptz.cgi?action=getStatus), the same numbers they show on screen. CamAgent sends them with the position, so
YonderView can set tilt and zoom up by itself.

Asked only of cameras that say they're one of these brands (other brands could count the request as a failed
login). The camera's own login is used; on Amcrest and Dahua it must also be one of the camera's normal users
(Setup > System > Account), not only an ONVIF user. If the camera refuses it, CamAgent says so once and stops asking
until it reconnects, so the camera's lockout counter isn't run down.
"""
import math
import urllib.error
import urllib.request

BRANDS = ("dahua", "amcrest", "lorex", "imou")
PATH = "/cgi-bin/ptz.cgi?action=getStatus&channel=1"
REFUSED_NOTE = ("the camera refused its login for reading exact degrees (on Amcrest and Dahua the login must also be "
                "one of the camera's normal users, Setup > System > Account, not only an ONVIF user); YonderView uses "
                "ONVIF positions instead")


def is_dahua(manufacturer):
    m = str(manufacturer or "").lower()
    return any(b in m for b in BRANDS)


def parse(text):
    """{"pan": 0..360, "tilt": degrees (+ down), "zoom": x magnification} from getStatus's answer, or None.
    Dahua spells it "Postion"; AbsPosition[2] is the zoom x100 (its focal length over the widest one)."""
    vals = {}
    for line in str(text or "").splitlines():
        key, sep, v = line.strip().partition("=")
        if sep:
            vals[key] = v.strip()
    try:
        pan = float(vals.get("status.Postion[0]", vals.get("status.Position[0]")))
        tilt = float(vals.get("status.Postion[1]", vals.get("status.Position[1]")))
    except (TypeError, ValueError):
        return None
    out = {"pan": round(pan % 360, 2), "tilt": round(tilt, 2)}
    try:
        zoom = float(vals["status.AbsPosition[2]"]) / 100
        if math.isfinite(zoom) and 1 <= zoom <= 200:
            out["zoom"] = round(zoom, 2)
    except (KeyError, ValueError):
        pass
    if not all(math.isfinite(v) for v in out.values()) or not -90 <= out["tilt"] <= 90:
        return None
    return out


class _OneTry(urllib.request.HTTPDigestAuthHandler):
    """Python's digest login tries a refused password up to 6 times; a camera counts each toward its lockout."""

    def http_error_auth_reqed(self, auth_header, host, req, headers):
        if self.retried >= 1:
            raise urllib.error.HTTPError(req.full_url, 401, "login refused", headers, None)
        return super().http_error_auth_reqed(auth_header, host, req, headers)


class Reader:
    def __init__(self, host, user, password, port=80, log=None):
        self.url = f"http://{host}:{int(port)}{PATH}" if int(port) != 80 else f"http://{host}{PATH}"
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, self.url, user, password)
        self.opener = urllib.request.build_opener(_OneTry(mgr))
        self.log = log
        self.off = ""            # why it stopped asking ("" while it works)

    def read(self):
        if self.off:
            return None
        try:
            with self.opener.open(self.url, timeout=3) as r:
                got = parse(r.read(8192).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self.off = REFUSED_NOTE
                if self.log:
                    self.log.warning(REFUSED_NOTE)
            elif e.code == 404:
                self.off = "this camera doesn't offer exact degrees"
            return None
        except (OSError, ValueError):
            return None              # a slow answer now and then: try again with the next position
        if got is None:
            self.off = "this camera's exact degrees weren't readable"
        return got
