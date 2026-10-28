#!/usr/bin/env python3
"""Write requirements.lock (0.17): the exact version of every package camagent needs, on Linux and Windows with
Python 3.11-3.14, with the SHA-256 of every file PyPI has for it (and piwheels, where Raspberry Pis get their
packages). Each signed release carries this file, covered by the signature, and hosts install with
`pip install --require-hashes --only-binary :all:`, so nothing that isn't listed here can be installed, even if
PyPI or a package's account were taken over.

  .venv/bin/pip install -U -e .                      take updates within pyproject.toml's ranges; run the tests
  .venv/bin/python scripts/lock_requirements.py      rewrite requirements.lock (versions from this venv), commit it
  .venv/bin/python scripts/lock_requirements.py --check   offline: does the lock pin every dependency, in range?

Versions come from this virtualenv when it has the package (what the tests ran with), otherwise the newest release
in range (a dependency only some Python versions or Windows need). Dependencies come from PyPI's metadata.
Only wheels are installed on hosts (nothing is built there), so it warns when a package has no wheel for one of the
computers CamAgent runs on (64-bit Windows, Linux PC, Raspberry Pi 64-bit and 32-bit).
"""
import json
import re
import sys
import tomllib
import urllib.request
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "requirements.lock"
PYTHONS = ["3.11", "3.12", "3.13", "3.14"]
# piwheels builds for Raspberry Pi OS's own Python (3.11 on Bookworm, 3.13 on Trixie), so the 32-bit Pi is checked
# with those only
PI32_PYTHONS = ["3.11", "3.13"]
PLATFORMS = {                        # name: (marker values, wheel platform tags that fit)
    "Windows": ({"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"},
                ("win_amd64",)),
    "Linux PC": ({"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
                  "platform_machine": "x86_64"}, ("manylinux*_x86_64", "linux_x86_64")),
    "Raspberry Pi (64-bit)": ({"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
                               "platform_machine": "aarch64"}, ("manylinux*_aarch64", "linux_aarch64")),
    "Raspberry Pi (32-bit)": ({"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
                               "platform_machine": "armv7l"}, ("manylinux*_armv7l", "linux_armv7l")),
}


def _packaging():
    try:
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name, parse_wheel_filename
        from packaging.version import Version
    except ImportError:                       # pip carries its own copy
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.specifiers import SpecifierSet
        from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
        from pip._vendor.packaging.version import Version
    return Requirement, SpecifierSet, canonicalize_name, parse_wheel_filename, Version


def environments():
    for py in PYTHONS:
        for name, (marks, _) in PLATFORMS.items():
            yield name, py, dict(marks, python_version=py, python_full_version=f"{py}.0",
                                 implementation_name="cpython", platform_python_implementation="CPython",
                                 implementation_version=f"{py}.0", platform_release="", platform_version="")


def project_requirements(pyproject=None):
    data = tomllib.loads(Path(pyproject or REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


# ---------- reading and checking a lock ----------

def parse_lock(text):
    """{canonical name: (version, [sha256, ...])} from a requirements.lock."""
    *_, canonicalize_name, _, _ = _packaging()
    out, cur = {}, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z0-9._-]+)==([^\s;]+)(.*)", line)
        if m:
            cur = canonicalize_name(m.group(1))
            out[cur] = (m.group(2), [])
            line = m.group(3)
        for h in re.findall(r"--hash=sha256:([0-9a-f]{64})", line):
            if cur is None:
                raise ValueError("a hash before any package")
            out[cur][1].append(h)
    return out


def check(lock_text, requirements):
    """Problems (plain words) with a lock: every requirement pinned, in range, with checksums. [] if fine."""
    Requirement, _, canonicalize_name, _, _ = _packaging()
    try:
        pins = parse_lock(lock_text)
    except ValueError as e:
        return [f"requirements.lock can't be read: {e}"]
    problems = [f"{n}=={v} has no checksums" for n, (v, hs) in pins.items() if not hs]
    for spec in requirements:
        r = Requirement(spec)
        if r.marker and not any(r.marker.evaluate(env) for _, _, env in environments()):
            continue
        pin = pins.get(canonicalize_name(r.name))
        if not pin:
            problems.append(f"{r.name} (pyproject.toml) isn't in requirements.lock")
        elif not r.specifier.contains(pin[0], prereleases=True):
            problems.append(f"requirements.lock has {r.name}=={pin[0]}, outside pyproject.toml's {r.specifier}")
    return problems


# ---------- making a lock ----------

def _json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "camagent-lock", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _pypi(name, version=None):
    return _json(f"https://pypi.org/pypi/{name}/{version}/json" if version else f"https://pypi.org/pypi/{name}/json")


def _piwheels(name, version):
    try:
        rel = (_json(f"https://www.piwheels.org/project/{name}/json/").get("releases") or {}).get(version) or {}
    except Exception:  # noqa: BLE001 - piwheels hasn't got every package (it builds only what isn't pure Python)
        return {}
    return {fn: f["filehash"] for fn, f in (rel.get("files") or {}).items()
            if re.fullmatch(r"[0-9a-f]{64}", str(f.get("filehash", "")))}


def _pick_version(name, spec, Version):
    try:
        have = metadata.version(name)
        if spec.contains(have, prereleases=True):
            return have
    except metadata.PackageNotFoundError:
        pass
    releases = _pypi(name)["releases"]
    ok = [v for v, files in releases.items() if files and not all(f.get("yanked") for f in files)
          and not Version(v).is_prerelease and spec.contains(v)]
    if not ok:
        sys.exit(f"No release of {name} on PyPI fits {spec or 'any version'}.")
    return max(ok, key=Version)


def _fits(filename, py, platform_tags, parse_wheel_filename):
    if not filename.endswith(".whl"):
        return False
    try:
        tags = parse_wheel_filename(filename)[3]
    except Exception:  # noqa: BLE001
        return False
    cp = "cp" + py.replace(".", "")
    for t in tags:
        if t.interpreter in ("py3", "py" + py.replace(".", "")) and t.abi == "none" and t.platform == "any":
            return True
        plat_ok = t.platform == "any" or any(re.fullmatch(p.replace("*", ".*"), t.platform) for p in platform_tags)
        if not plat_ok:
            continue
        if t.interpreter == cp and t.abi in (cp, "none"):
            return True
        if t.abi == "abi3" and t.interpreter.startswith("cp3") and int(t.interpreter[3:]) <= int(cp[3:]):
            return True
    return False


def make():
    Requirement, SpecifierSet, canonicalize_name, parse_wheel_filename, Version = _packaging()
    envs = list(environments())
    todo = [(Requirement(s), "pyproject.toml") for s in project_requirements()]
    pins, via, files = {}, {}, {}
    while todo:
        req, parent = todo.pop(0)
        wanted_envs = [env for _, _, env in envs if not req.marker or req.marker.evaluate(dict(env, extra=""))]
        if not wanted_envs:
            continue
        name = canonicalize_name(req.name)
        if name in pins:
            if not req.specifier.contains(pins[name], prereleases=True):
                sys.exit(f"{parent} needs {req}, but {name}=={pins[name]} was picked for another package. Update "
                         "this venv (pip install -U -e .) and run this again.")
            via[name].add(parent)
            continue
        version = _pick_version(name, req.specifier, Version)
        info = _pypi(name, version)
        pins[name], via[name] = version, {parent}
        # (file name, sha256) pairs: piwheels rebuilds some files under the same name as PyPI's, so keep both
        files[name] = {(f["filename"], f["digests"]["sha256"]) for f in info["urls"]}
        files[name] |= set(_piwheels(name, version).items())
        if not files[name]:
            sys.exit(f"PyPI lists no files for {name}=={version}")
        deps = info["info"].get("requires_dist")
        if deps is None:                      # none, or PyPI couldn't read them (a package with no wheel)
            try:
                deps = (metadata.requires(name) or []) if metadata.version(name) == version else None
            except metadata.PackageNotFoundError:
                deps = None
            if deps is None and any(fn.endswith(".whl") for fn, _ in files[name]):
                deps = []
            if deps is None:
                sys.exit(f"Can't tell what {name}=={version} needs (PyPI doesn't say; install it in this venv).")
        for d in deps:
            dep = Requirement(d)
            if dep.marker and not any(dep.marker.evaluate(dict(env, extra=x)) for env in wanted_envs
                                      for x in ([""] + sorted(req.extras))):
                continue
            todo.append((dep, name))

    gaps = []
    for name, version in sorted(pins.items()):
        for plat, (_, plat_tags) in PLATFORMS.items():
            missing = [py for py in (PI32_PYTHONS if "32-bit" in plat else PYTHONS) if not any(_fits(fn, py, plat_tags, parse_wheel_filename)
                                                        for fn, _ in files[name])]
            if missing:
                gaps.append(f"  {name}=={version}: no wheel for {plat} with Python {', '.join(missing)}")

    out = ["# The exact packages CamAgent installs, with the SHA-256 of every file PyPI (and piwheels, for Raspberry",
           "# Pis) has for them. Made by scripts/lock_requirements.py; every signed release carries it, and hosts",
           "# install with: pip install --require-hashes --only-binary :all: -r <this file>", ""]
    for name, version in sorted(pins.items()):
        hashes = sorted({h for _, h in files[name]})
        out.append(f"# via {', '.join(sorted(via[name]))}")
        out.append(f"{name}=={version} \\")
        out += [f"    --hash=sha256:{h}" + (" \\" if i < len(hashes) - 1 else "") for i, h in enumerate(hashes)]
    text = "\n".join(out) + "\n"
    problems = check(text, project_requirements())
    if problems:
        sys.exit("The lock isn't right:\n  " + "\n  ".join(problems))
    LOCK.write_text(text, encoding="utf-8", newline="\n")
    print(f"requirements.lock: {len(pins)} packages")
    if gaps:
        print("Warning: hosts on these computers couldn't install (only wheels are installed, nothing is built):")
        print("\n".join(gaps))


def main():
    if sys.argv[1:] == ["--check"]:
        problems = check(LOCK.read_text(encoding="utf-8"), project_requirements())
        if problems:
            sys.exit("requirements.lock:\n  " + "\n  ".join(problems))
        print("requirements.lock: OK")
    elif sys.argv[1:]:
        sys.exit(__doc__)
    else:
        make()


if __name__ == "__main__":
    main()
