"""Signing camagent in to a YonderView account, and setting a camera up from it.

Like signing in a TV app: camagent shows a short code, the host enters it at yonderview.net/link in their own
browser (signed in there, with two-factor) and approves this computer. camagent never sees their password; it
gets a token that can only list the cameras they own or co-own and fetch a camera's connection settings (the same
settings block as the camera's Connection page). The token is kept in camagent.toml [account], next to the camera
passwords and protected the same way. Unlink the computer from your profile on yonderview.net to revoke it."""
import json
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
    """The cameras this account owns or co-owns: [{slug, name, place, has_credentials, enabled, max_height}]."""
    status, d = _call(site, "GET", "/api/agent/cameras", token=token)
    if status == 401:
        raise NotSignedIn(d.get("error") or "This computer isn't signed in any more. Sign in again.")
    if status != 200:
        raise LinkError(f"Couldn't get your cameras from YonderView (it answered {status}). Try again in a minute.")
    return d.get("cameras") or []


def connect(site, token, slug, replace=False):
    """A camera's settings block. Returns ("ok", block, camera info) or ("confirm", message, None) when the camera
    already has connection settings (new ones disconnect whatever uses the old ones)."""
    status, d = _call(site, "POST", f"/api/agent/cameras/{slug}/connect", {"replace": bool(replace)}, token=token)
    if status == 200 and d.get("settings_block"):
        return "ok", d["settings_block"], d.get("camera") or {}
    if status == 409 and d.get("needs_confirm"):
        return "confirm", d.get("error", ""), None
    if status == 401:
        raise NotSignedIn(d.get("error") or "This computer isn't signed in any more. Sign in again.")
    raise LinkError(d.get("error") or f"Couldn't get the camera's connection settings (YonderView answered {status}). "
                                     "Try again in a minute.")
