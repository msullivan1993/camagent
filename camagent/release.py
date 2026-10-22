"""Signed releases (0.16): what `camagent update` installs must be signed with YonderView's release key.

Each GitHub release carries three files made on Mike's computer by scripts/sign_release.py, never by GitHub:
  camagent-<version>-py3-none-any.whl   the package
  SHA256SUMS                             its checksum
  SHA256SUMS.sig                         an Ed25519 signature of SHA256SUMS with the release key

The public half of the key is below. Someone who took over the GitHub account (or a GitHub Action) could publish a
release, but not sign it, so agents won't install it. Verified packages are kept (releases/) for rolling back.
"""
import base64
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from . import config

# Ed25519 public keys (raw 32 bytes, base64). More than one only while moving to a new key.
PUBLIC_KEYS = [
    "REPLACE_WITH_RELEASE_PUBLIC_KEY",
]

API = "https://api.github.com/repos/{owner}/{name}/releases/tags/{tag}"
MAX_DOWNLOAD = 50 * 1024 * 1024


class NotSigned(Exception):
    """The release has no valid signature: don't install it."""


def _repo_parts(repo):
    m = re.search(r"github\.com[/:]([^/]+)/([^/.]+)", repo or "")
    if not m:
        raise NotSigned(f"not a GitHub repository: {repo}")
    return m.group(1), m.group(2)


class _HTTPSOnly(urllib.request.HTTPRedirectHandler):
    """GitHub sends release files from another https address; never follow a redirect to plain http."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise NotSigned(f"refusing a redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url, accept="application/octet-stream"):
    if not url.lower().startswith("https://"):
        raise NotSigned(f"refusing a download over plain http: {url}")
    opener = urllib.request.build_opener(_HTTPSOnly)
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "camagent"})
    with opener.open(req, timeout=60) as r:
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise NotSigned(f"download too big: {url}")
    return data


def verify_sums(sums: bytes, sig: bytes, keys=None) -> bool:
    """True if `sig` is a valid Ed25519 signature of `sums` by one of the release keys."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:
        raise NotSigned("the 'cryptography' package is missing, so signatures can't be checked "
                        "(reinstall camagent)") from e
    try:
        raw_sig = base64.b64decode(sig.strip(), validate=True)
    except ValueError:
        return False
    for k in keys if keys is not None else PUBLIC_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(k, validate=True)).verify(raw_sig, sums)
            return True
        except (InvalidSignature, ValueError):
            continue
    return False


def parse_sums(sums: bytes):
    """{filename: sha256} from a SHA256SUMS file ("<hex>  <name>" lines)."""
    out = {}
    for line in sums.decode("utf-8", "strict").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64}) [ *]([A-Za-z0-9._+-]+)", line.strip())
        if m:
            out[m.group(2)] = m.group(1)
    return out


def wheel_name(version):
    return f"camagent-{version}-py3-none-any.whl"


def cache_dir() -> Path:
    return config.base_dir() / "releases"


def _check(version, sums, sig, wheel_bytes, keys=None):
    """Raise NotSigned unless sums is signed, names this version's wheel, and the wheel matches it."""
    if not verify_sums(sums, sig, keys):
        raise NotSigned(f"camagent {version}: the signature doesn't match the release key")
    want = parse_sums(sums).get(wheel_name(version))
    if not want:
        raise NotSigned(f"camagent {version}: the signed checksums don't list {wheel_name(version)}")
    if hashlib.sha256(wheel_bytes).hexdigest() != want:
        raise NotSigned(f"camagent {version}: the package doesn't match its signed checksum")


def fetch(repo, tag, keys=None) -> Path:
    """Download release `tag`, check its signature and checksum, keep it in releases/, and return the wheel's path.
    A copy already kept (and still verifying) is used without downloading."""
    version = tag.lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise NotSigned(f"not a release version: {tag}")
    d = cache_dir() / version
    kept = d / wheel_name(version)
    if kept.exists():
        try:
            _check(version, (d / "SHA256SUMS").read_bytes(), (d / "SHA256SUMS.sig").read_bytes(), kept.read_bytes(),
                   keys)
            return kept
        except (OSError, NotSigned):
            pass
    owner, name = _repo_parts(repo)
    info = json.loads(_get(API.format(owner=owner, name=name, tag=tag), "application/vnd.github+json"))
    assets = {a.get("name"): a.get("browser_download_url", "") for a in info.get("assets") or []}
    need = ["SHA256SUMS", "SHA256SUMS.sig", wheel_name(version)]
    missing = [n for n in need if not assets.get(n)]
    if missing:
        raise NotSigned(f"camagent {tag} isn't a signed release (missing {', '.join(missing)})")
    sums, sig, wheel = (_get(assets[n]) for n in need)
    _check(version, sums, sig, wheel, keys)
    d.mkdir(parents=True, exist_ok=True)
    for n, data in zip(need, (sums, sig, wheel)):
        (d / n).write_bytes(data)
    _prune(keep=3)
    return kept


def _prune(keep=3):
    """Keep the newest few verified releases (enough to roll back)."""
    import shutil
    from .update import version_tuple
    try:
        dirs = sorted((p for p in cache_dir().iterdir() if p.is_dir()), key=lambda p: version_tuple(p.name))
    except OSError:
        return
    for p in dirs[:-keep]:
        shutil.rmtree(p, ignore_errors=True)
