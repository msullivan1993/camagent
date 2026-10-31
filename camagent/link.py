"""Signing camagent in to a YonderView account, and setting a camera up from it.

Like signing in a TV app: camagent shows a short code, the host enters it at yonderview.net/link in their own
browser (signed in there, with two-factor) and approves this computer. camagent never sees their password; it
gets a token that can only list the cameras they own or co-own and fetch a camera's connection settings (the same
settings block as the camera's Connection page). The token is kept in account.toml, which only administrators can
read. Signing out revokes it on the website too; unlinking the computer at yonderview.net/link does the same.
Website 1.118+: a computer may set up only the cameras the person allowed it (the others are listed but marked;
connecting one answers with the address where they can allow it), and a token unused for 90 days stops working."""
import json
import os
import socket
import time
import urllib.error
import urllib.request

from . import __version__

DEFAULT_SITE = "https://yonderview.net"


class LinkError(Exception):
    """Something the person can act on, in plain words."""


class NotSignedIn(LinkError):
    pass


SIGNED_OUT = ("This computer is signed out of YonderView (it was unlinked on the website, or unused for 90 days). "
              "Sign in again.")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """The API never redirects; following one would carry the Authorization header to another address."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def _check_site(site):
    from urllib.parse import urlparse
    u = urlparse(site)
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise LinkError(f"The YonderView address must start with https:// (it's {site!r}).")


def _call(site, method, path, data=None, token=None, timeout=20):
    """(HTTP status, JSON body). Network trouble becomes a LinkError."""
    _check_site(site)
    url = site.rstrip("/") + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", f"camagent/{__version__}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with _opener.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise LinkError(f"Couldn't reach {site} ({reason}). Check this computer's internet connection.") from None
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def clock_offset(site=DEFAULT_SITE, timeout=8):
    """How far this computer's clock is from YonderView's, in seconds (positive: this computer is behind), from
    the Date header every answer carries (to the second). None if YonderView can't be reached. Absolute time
    (UTC): the time zones of this computer, the camera and the host don't matter."""
    import email.utils
    try:
        _check_site(site)
        req = urllib.request.Request(site.rstrip("/") + "/", method="HEAD",
                                     headers={"User-Agent": f"camagent/{__version__}"})
        t0 = time.time()
        try:
            with _opener.open(req, timeout=timeout) as r:
                date = r.headers.get("Date")
        except urllib.error.HTTPError as e:                  # any answer carries the time (even a redirect)
            date = e.headers.get("Date")
        t1 = time.time()
        server = email.utils.parsedate_to_datetime(date).timestamp() if date else None
    except (LinkError, OSError, ValueError, TypeError):
        return None
    return None if server is None else round(server - (t0 + t1) / 2)


def clock_problem(offset):
    """A plain-words warning when the clock is more than a minute off, else ""."""
    if offset is None or abs(offset) < 60:
        return ""
    mins = abs(offset) // 60
    how = f"{mins // 60} h {mins % 60} min" if mins >= 60 else f"{mins} min"
    fix = ("Settings > Time & language > Date & time > Sync now (or, in an administrator window: w32tm /resync)"
           if os.name == "nt" else "sudo timedatectl set-ntp true")
    return (f"this computer's clock is {how} {'behind' if offset > 0 else 'ahead'}, which breaks camera logins "
            f"and recording times. Fix: {fix}")


def computer_name():
    try:
        return socket.gethostname()[:100] or "camagent"
    except OSError:
        return "camagent"


def sign_in(site=DEFAULT_SITE, name=None, show=print, sleep=time.sleep):
    """Run the sign-in: show the code and address, wait for approval. Returns (token, account name).
    Ctrl+C stops waiting (KeyboardInterrupt goes to the caller)."""
    status, d = _call(site, "POST", "/api/agent/link/start", {"name": name or computer_name()})
    if status == 429:
        raise LinkError(d.get("error") or "Too many sign-in attempts. Try again in a few minutes.")
    if status != 200 or not d.get("device_code"):
        raise LinkError(f"The sign-in couldn't start (YonderView answered {status}). Try again in a minute.")
    show("")
    show("  On your phone or another computer, open:")
    show(f"      {d['verification_uri']}")
    show("  sign in to YonderView, and type this code:")
    show("")
    show(f"      {d['user_code']}")
    show("")
    show(f"  (Or open this link, which fills in the code for you: {d.get('verification_uri_complete', d['verification_uri'])} )")
    show(f"  Waiting for you to enter the code (it works for {int(d.get('expires_in') or 600) // 60} minutes; Ctrl+C cancels)...")
    interval = int(d.get("interval") or 5)
    deadline = time.monotonic() + int(d.get("expires_in") or 600)
    while time.monotonic() < deadline:
        sleep(interval)
        status, p = _call(site, "POST", "/api/agent/link/poll", {"device_code": d["device_code"]})
        state = p.get("status")
        if state == "approved" and p.get("token"):
            return p["token"], p.get("account", "")
        if state == "pending":
            continue
        if state == "slow_down":
            interval += 2
            continue
        if state == "denied":
            raise LinkError("The sign-in was declined on the website, so this computer wasn't linked. Nothing changed.")
        if state == "expired":
            break
        raise LinkError(f"The sign-in stopped unexpectedly (YonderView answered {status}). Try again in a minute.")
    raise LinkError("The code expired before it was approved. Start the sign-in again for a new code.")


def cameras(site, token):
    """The cameras this account owns or co-owns: [{slug, name, place, has_credentials, enabled, max_height,
    allowed}]. "allowed": False = this computer may not set that camera up until the person allows it on the
    website (servers before 1.118 don't say: allowed)."""
    status, d = _call(site, "GET", "/api/agent/cameras", token=token)
    if status == 401:
        raise NotSignedIn(SIGNED_OUT)
    if status != 200:
        raise LinkError(f"Couldn't get your cameras from YonderView (it answered {status}). Try again in a minute.")
    cams = [c for c in d.get("cameras") or [] if isinstance(c, dict) and c.get("slug")]
    for c in cams:
        c["allowed"] = c.get("allowed") is not False
    return cams


def connect(site, token, slug, replace=False):
    """A camera's settings block. Returns ("ok", block, camera info), ("confirm", message, None) when the camera
    already has connection settings (new ones disconnect whatever uses the old ones), or ("approve", address,
    message) when this computer isn't allowed to set that camera up yet (the person allows it at that address)."""
    status, d = _call(site, "POST", f"/api/agent/cameras/{slug}/connect", {"replace": bool(replace)}, token=token)
    if status == 200 and d.get("settings_block"):
        return "ok", d["settings_block"], d.get("camera") or {}
    if status == 409 and d.get("needs_confirm"):
        return "confirm", d.get("error", ""), None
    if status == 403 and d.get("needs_approval"):
        url = str(d.get("approve_url") or "")
        if not url.startswith(site.rstrip("/") + "/"):
            url = site.rstrip("/") + "/link"         # only ever show an address on the YonderView site itself
        return "approve", url, str(d.get("error") or "")
    if status == 401:
        raise NotSignedIn(SIGNED_OUT)
    raise LinkError(d.get("error") or f"Couldn't get the camera's connection settings (YonderView answered {status}). "
                                     "Try again in a minute.")


def sign_out(site, token):
    """Revoke this computer's token on the website. True if it's revoked (or already wasn't valid); False if the
    website couldn't be reached or said something else (the caller still forgets the token here)."""
    try:
        status, _ = _call(site, "POST", "/api/agent/signout", {}, token=token)
    except LinkError:
        return False
    return status in (200, 401)
