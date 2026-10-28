#!/usr/bin/env python3
"""Sign a camagent release (0.16+). Run on Mike's computer (WSL), never on GitHub: agents install only releases
signed with this key, so someone who took over the GitHub account still couldn't push an update to hosts.

  .venv/bin/python scripts/sign_release.py key-new          once: make the key (asks for a passphrase)
  .venv/bin/python scripts/sign_release.py pubkey           show the public key (goes in camagent/release.py)
  .venv/bin/python scripts/sign_release.py sign v0.16.0     after pushing the tag and the Release workflow passing
  .venv/bin/python scripts/sign_release.py sign v0.17.0 --test
                            the same, published as a pre-release: installers and `camagent update --ref v0.17.0`
                            can install it by name, but agents' nightly check (the latest release) skips it
  .venv/bin/python scripts/sign_release.py promote v0.17.0  tested: make it the latest release (agents take it nightly)

`sign` builds the wheel from the tag itself (a clean copy of exactly what the tag holds), takes the tag's
requirements.lock (the exact dependencies, with their checksums; scripts/lock_requirements.py) as
camagent-<version>-requirements.lock, writes SHA256SUMS for both, signs it, uploads all four to the tag's draft
release (gh CLI) and publishes it. Agents and the installers install the dependencies only from that signed lock
(0.17). The passphrase is asked each time. Needs `pip install build cryptography` in the venv.

The key lives in ~/.config/camagent-release/release-key.pem (encrypted with the passphrase). Keep a backup of that
file and the passphrase somewhere safe (a password manager): without it, no new update can reach hosts.
"""
import base64
import getpass
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

KEY = Path.home() / ".config" / "camagent-release" / "release-key.pem"
REPO = Path(__file__).resolve().parent.parent


def _crypto():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return serialization, Ed25519PrivateKey


def _git(*args, cwd=REPO):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _public_b64(private_key):
    serialization, _ = _crypto()
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _load():
    serialization, _ = _crypto()
    if not KEY.exists():
        sys.exit(f"No release key at {KEY}. Make one first: scripts/sign_release.py key-new")
    pw = getpass.getpass("Release key passphrase: ").encode()
    try:
        return serialization.load_pem_private_key(KEY.read_bytes(), password=pw)
    except ValueError:
        sys.exit("Wrong passphrase.")


def key_new():
    serialization, Ed25519PrivateKey = _crypto()
    if KEY.exists():
        sys.exit(f"{KEY} already exists; not replacing it.")
    pw = getpass.getpass("New passphrase for the release key (long; keep it in your password manager): ")
    if len(pw) < 12:
        sys.exit("Use at least 12 characters.")
    if getpass.getpass("Again: ") != pw:
        sys.exit("They don't match.")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.BestAvailableEncryption(pw.encode()))
    KEY.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(KEY.parent, 0o700)
    fd = os.open(KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    print(f"Saved {KEY} (only you can read it). Back it up, with the passphrase, somewhere safe.")
    print(f"Public key (for camagent/release.py PUBLIC_KEYS):\n  {_public_b64(key)}")


def sign(tag, test=False):
    if not tag.startswith("v"):
        sys.exit("Give the tag, e.g. v0.16.0")
    version = tag[1:]
    commit = _git("rev-list", "-n", "1", tag)
    remote = _git("ls-remote", "origin", f"refs/tags/{tag}^{{}}") or _git("ls-remote", "origin", f"refs/tags/{tag}")
    if not remote or remote.split()[0] != commit:
        sys.exit(f"{tag} on GitHub isn't the {tag} here ({commit[:12]}). Push it (or check who moved it) first.")
    key = _load()
    pub = _public_b64(key)
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "src"
        src.mkdir()
        archive = subprocess.run(["git", "archive", tag], cwd=REPO, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
        code = (src / "camagent" / "__init__.py").read_text()
        if f'__version__ = "{version}"' not in code:
            sys.exit(f"{tag} doesn't hold camagent {version} (camagent/__init__.py).")
        if pub not in (src / "camagent" / "release.py").read_text():
            sys.exit(f"{tag}'s camagent/release.py doesn't list this key, so agents running it couldn't update "
                     f"later. Add it to PUBLIC_KEYS first:\n  {pub}")
        # the commit's time (not the tag's: an annotated tag's "git show" prints its message too)
        env = dict(os.environ, SOURCE_DATE_EPOCH=_git("log", "-1", "--format=%ct", f"{tag}^{{commit}}"))
        built = subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(Path(d) / "dist"), str(src)],
                               env=env, capture_output=True, text=True)
        if built.returncode:
            sys.exit("Building the package failed:\n" + (built.stdout + built.stderr)[-3000:])
        wheel = Path(d) / "dist" / f"camagent-{version}-py3-none-any.whl"
        if not wheel.exists():
            sys.exit(f"The build didn't make {wheel.name}.")
        # the dependencies: the tag's lock, checked against the tag's pyproject.toml
        if not (src / "requirements.lock").exists():
            sys.exit(f"{tag} has no requirements.lock. Make it: .venv/bin/python scripts/lock_requirements.py")
        sys.path.insert(0, str(src / "scripts"))
        import lock_requirements                          # the tag's own copy
        problems = lock_requirements.check((src / "requirements.lock").read_text(encoding="utf-8"),
                                           lock_requirements.project_requirements(src / "pyproject.toml"))
        if problems:
            sys.exit(f"{tag}'s requirements.lock isn't right (run scripts/lock_requirements.py, commit, tag again):\n  "
                     + "\n  ".join(problems))
        lock = Path(d) / "dist" / f"camagent-{version}-requirements.lock"
        lock.write_bytes((src / "requirements.lock").read_bytes())
        sums = "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in (wheel, lock)).encode()
        sig = base64.b64encode(key.sign(sums)) + b"\n"
        sys.path.insert(0, str(src))
        from camagent import release                      # the tag's own checker must accept it
        release._check(version, sums, sig, wheel.read_bytes(), lock_bytes=lock.read_bytes())
        (Path(d) / "SHA256SUMS").write_bytes(sums)
        (Path(d) / "SHA256SUMS.sig").write_bytes(sig)
        subprocess.run(["gh", "release", "view", tag], cwd=REPO, check=True, capture_output=True)
        subprocess.run(["gh", "release", "upload", tag, str(wheel), str(lock), str(Path(d) / "SHA256SUMS"),
                        str(Path(d) / "SHA256SUMS.sig"), "--clobber"], cwd=REPO, check=True)
        # the Release workflow leaves it a draft (invisible to agents) until it's signed: publish it now
        if test:
            subprocess.run(["gh", "release", "edit", tag, "--draft=false", "--prerelease", "--latest=false"],
                           cwd=REPO, check=True, capture_output=True)
            print(f"Signed and published {tag} as a pre-release (for testing). Agents won't take it nightly. Install it "
                  f"by name (CAMAGENT_REF={tag}, or camagent update --ref {tag}); when it's good: "
                  f"sign_release.py promote {tag}")
            return
        subprocess.run(["gh", "release", "edit", tag, "--draft=false", "--latest"], cwd=REPO, check=True,
                       capture_output=True)
    print(f"Signed, uploaded and published {tag}. Agents will install it on their next nightly check.")


def promote(tag):
    """A tested pre-release becomes the latest release (already signed: nothing is rebuilt)."""
    subprocess.run(["gh", "release", "edit", tag, "--prerelease=false", "--latest"], cwd=REPO, check=True)
    print(f"{tag} is now the latest release. Agents will install it on their next nightly check.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("key-new", "pubkey", "sign", "promote"):
        sys.exit(__doc__)
    if sys.argv[1] == "key-new":
        key_new()
    elif sys.argv[1] == "pubkey":
        print(_public_b64(_load()))
    elif sys.argv[1] == "promote":
        if len(sys.argv) != 3:
            sys.exit("Usage: sign_release.py promote v0.17.0")
        promote(sys.argv[2])
    else:
        if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "--test"):
            sys.exit("Usage: sign_release.py sign v0.16.0 [--test]")
        sign(sys.argv[2], test=len(sys.argv) == 4)


if __name__ == "__main__":
    main()
