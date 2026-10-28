# camagent release verifier (scripts/verify_release.py; install.sh and install.ps1 carry identical copies)
# Downloads a CamAgent release from GitHub into an empty folder and checks it BEFORE anything is installed: SHA256SUMS
# must carry a valid Ed25519 signature by YonderView's release key (the same key camagent/release.py pins), and the
# package and its requirements.lock must match the checksums in it. Standard library only (it runs before anything
# is installed), with its own small Ed25519 check (RFC 8032). Usage: python -I verify_release.py <folder> [vX.Y.Z]
# Prints the version on success; on failure, says why and exits 1.
import base64
import hashlib
import json
import os
import re
import sys
import urllib.request

KEYS = [
    "ooa4QK2G7l08JCUW2CriBJSLECBHUNusIShwpJEOZHM=",     # Mike's release key, made 2026-10-21
]
REPO = "msullivan1993/camagent"
MAX_DOWNLOAD = 50 * 1024 * 1024

# ----- Ed25519 signature check (RFC 8032, section 6) -----
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a, b):
    x = (a[1] - a[0]) * (b[1] - b[0]) % _P
    y = (a[1] + a[0]) * (b[1] + b[0]) % _P
    c = 2 * a[3] * b[3] * _D % _P
    d = 2 * a[2] * b[2] % _P
    e, f, g, h = y - x, d - c, d + c, y + x
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def ed25519_verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    a, r = _decompress(public), _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


# ----- the release -----
class Refused(Exception):
    pass


class _HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise Refused(f"refusing a redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(url, accept="application/octet-stream"):
    if not url.lower().startswith("https://"):
        raise Refused(f"refusing a download over plain http: {url}")
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "camagent-installer"})
    with urllib.request.build_opener(_HTTPSOnly).open(req, timeout=60) as r:
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise Refused(f"download too big: {url}")
    return data


def signed(sums, sig, keys=None):
    try:
        raw = base64.b64decode(sig.strip(), validate=True)
    except ValueError:
        return False
    return any(ed25519_verify(base64.b64decode(k), sums, raw) for k in (KEYS if keys is None else keys))


def parse_sums(sums):
    out = {}
    for line in sums.decode("utf-8", "strict").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *]([A-Za-z0-9._+-]+)", line.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


def fetch(folder, tag="", keys=None):
    """Download and check the release (the latest if no tag) into folder. Returns its version."""
    if tag and not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise Refused(f"{tag!r} isn't a release (like v0.17.0). To test a branch, install a release first, then "
                      "use: camagent update --ref <branch> --unsigned")
    api = f"https://api.github.com/repos/{REPO}/releases/" + (f"tags/{tag}" if tag else "latest")
    info = json.loads(get(api, "application/vnd.github+json"))
    tag = str(info.get("tag_name") or "")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise Refused(f"the latest release has an unexpected name: {tag!r}")
    version = tag[1:]
    assets = {a.get("name"): a.get("browser_download_url", "") for a in info.get("assets") or []}
    wheel, lock = f"camagent-{version}-py3-none-any.whl", f"camagent-{version}-requirements.lock"
    missing = [n for n in ("SHA256SUMS", "SHA256SUMS.sig", wheel, lock) if not assets.get(n)]
    if missing:
        raise Refused(f"CamAgent {tag} isn't a signed release this installer can use (missing {', '.join(missing)})")
    sums, sig = get(assets["SHA256SUMS"]), get(assets["SHA256SUMS.sig"])
    if not signed(sums, sig, keys):
        raise Refused(f"CamAgent {tag}: the signature doesn't match YonderView's release key")
    listed = parse_sums(sums)
    files = {"SHA256SUMS": sums, "SHA256SUMS.sig": sig}
    for n in (wheel, lock):
        if n not in listed:
            raise Refused(f"CamAgent {tag}: the signed checksums don't list {n}")
        data = get(assets[n])
        if hashlib.sha256(data).hexdigest() != listed[n]:
            raise Refused(f"CamAgent {tag}: {n} doesn't match its signed checksum")
        files[n] = data
    for n, data in files.items():
        with open(os.path.join(folder, n), "wb") as f:
            f.write(data)
    return version


def main(argv):
    if len(argv) < 2 or not os.path.isdir(argv[1]) or os.listdir(argv[1]):
        sys.stderr.write("usage: verify_release.py <empty folder> [vX.Y.Z]\n")
        return 2
    try:
        version = fetch(argv[1], argv[2] if len(argv) > 2 else "")
    except Exception as e:  # noqa: BLE001 - whatever went wrong, nothing gets installed
        sys.stderr.write(f"\nNOT INSTALLED: {e}.\nFor your safety, CamAgent is installed only when the download is an "
                         "official release, signed with YonderView's release key; nothing on this computer was "
                         "changed. Try again in a few minutes; if it keeps happening, contact support (Report a "
                         "problem on yonderview.net).\n")
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
# end of camagent release verifier
