# camagent installer for Windows 10/11 and Windows Server.
# Run in PowerShell as Administrator:
#   irm https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.ps1 | iex
# Re-running it upgrades camagent and keeps your config.
#
# It installs only an official release signed with YonderView's release key (0.17): the package, and the exact list
# of packages it needs with their checksums, are downloaded and their signature checked (by the small verifier
# below, the same check `camagent update` does) before anything is installed. $env:CAMAGENT_REF = "v0.17.0" picks
# a release.

$ErrorActionPreference = "Stop"
$Ref  = $env:CAMAGENT_REF        # default: the latest published release, never untested "main"
$Base = Join-Path $env:ProgramData "camagent"
$Bin  = Join-Path $Base "bin"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "This installer needs administrator rights. Close this window, press the Windows key, type PowerShell, right-click Windows PowerShell, choose 'Run as administrator', and paste the install line again." }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links")
}

function Winget-Install($id, $extra = "") {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget (Windows' app installer) isn't available. Update 'App Installer' from the Microsoft Store, or install '$id' yourself, then run this again."
    }
    # for all users (Program Files): the service runs these, so a normal user mustn't be able to change them
    if ($extra) { winget install -e --id $id --scope machine --accept-package-agreements --accept-source-agreements --silent $extra }
    else { winget install -e --id $id --scope machine --accept-package-agreements --accept-source-agreements --silent }
    if ($LASTEXITCODE -ne 0) { Write-Host "    (winget returned code $LASTEXITCODE for $id; it may already be installed, possibly for one user only)" }
    Refresh-Path
}

function Test-AllUsersPath($path) {
    # under Program Files: only administrators can change it (the nightly update runs it as SYSTEM)
    $pf = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    return [bool]($pf | Where-Object { $path.ToLower().StartsWith($_.ToLower() + "\") })
}

function Find-Python {
    # Every Python the launcher knows (py -0p), plus "python" on the PATH; only one installed for all users,
    # version 3.11 or newer. (A per-user Python is skipped: its user could change what the nightly update runs as SYSTEM.)
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $cands += (& py -0p 2>$null) | ForEach-Object { if ($_ -match '([A-Za-z]:\\.+?python\.exe)\s*$') { $Matches[1] } }
    }
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { $cands += $p.Source }
    # all-users installs, found directly: a Python winget just installed isn't on this window's PATH yet
    $cands += Get-ChildItem (Join-Path $env:ProgramFiles "Python3*\python.exe") -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
    foreach ($exe in $cands | Select-Object -Unique) {
        if (-not (Test-AllUsersPath $exe)) { Write-Host "    (skipping $exe : it's installed for one user only, and CamAgent needs one installed for all users)"; continue }
        $ok = & $exe -c "import sys;print(1 if sys.version_info>=(3,11) else 0)" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "1") { return $exe }
    }
    return $null
}

Write-Host "==> Checking for Python 3.11 or newer"
$py = Find-Python
if (-not $py) {
    # 3.12 first; if that fails (e.g. Windows' installer refuses, code 1603, because the same version is already
    # installed for one user), 3.13: CamAgent's signed package list covers both
    foreach ($id in @("Python.Python.3.12", "Python.Python.3.13")) {
        Winget-Install $id
        $py = Find-Python
        if ($py) { break }
    }
    if (-not $py) { throw "Python couldn't be installed for all users (see the messages above). Install Python 3.12 or 3.13 from python.org with 'Install for all users' ticked, then run the install line again in a new administrator window." }
}
Write-Host "    $py"

Write-Host "==> Checking ffmpeg"
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff -or -not (Test-AllUsersPath $ff.Source)) {
    $machineFf = Get-ChildItem (Join-Path $env:ProgramFiles "WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe") -ErrorAction SilentlyContinue
    if (-not $machineFf) {
        # one installed for one user only makes winget think it's done: --force puts the all-users copy in too
        if ($ff) { Winget-Install "Gyan.FFmpeg" "--force" } else { Winget-Install "Gyan.FFmpeg" }
    }
}

Write-Host "==> Setting up CamAgent's folder"
# The folder holds passwords and the code the nightly update runs as SYSTEM: lock it to Administrators and SYSTEM
# *before* anything goes in it (a folder a normal user made first would stay theirs to change).
if (Test-Path $Base) {
    # by SID (names differ by language): Administrators, SYSTEM or TrustedInstaller
    $owner = (Get-Acl $Base).GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @("S-1-5-32-544", "S-1-5-18", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")) {
        throw "The folder $Base already exists, but it wasn't made by an administrator (owner: $owner), so it isn't safe to use. Delete that folder (or check who made it), then run this again."
    }
} else {
    New-Item -ItemType Directory -Force -Path $Base | Out-Null
}
# lock the folder first, then make everything inside take its permissions, then make Administrators the owner
icacls $Base /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null
if (Get-ChildItem $Base -Force -ErrorAction SilentlyContinue) { icacls "$Base\*" /reset /T /C /Q | Out-Null }
icacls $Base /setowner "*S-1-5-32-544" /T /C /Q | Out-Null
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$venv = Join-Path $Base "venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
$cfgFile = Join-Path $venv "pyvenv.cfg"
$venvHome = $null
if (Test-Path $cfgFile) {
    foreach ($line in Get-Content $cfgFile) { if ($line -match '^home\s*=\s*(.+)$') { $venvHome = $Matches[1].Trim() } }
}
if ((Test-Path $cfgFile) -and -not ($venvHome -and (Test-AllUsersPath $venvHome))) {
    Write-Host "    rebuilding CamAgent's program folder (the old one used a Python installed for one user only)"
    Stop-Service -Name camagent -ErrorAction SilentlyContinue      # it runs from that folder
    Remove-Item -Recurse -Force $venv
}
if (-not (Test-Path $venvPy)) { & $py -m venv $venv }

$RefShown = if ($Ref) { $Ref } else { "(the latest release)" }
Write-Host "==> Downloading CamAgent $RefShown and checking its signature"
# in CamAgent's locked folder (Administrators and SYSTEM only), so nothing can swap a file between the check and
# the install; removed if anything below fails
$work = Join-Path $Base ("install-" + [guid]::NewGuid().ToString("N"))
$dl = Join-Path $work "files"
New-Item -ItemType Directory -Force -Path $dl | Out-Null
$verifier = Join-Path $work "verify_release.py"
$VerifyRelease = @'
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
    # releases before 0.17.0 have no lock: their signed package only (its packages come from PyPI, as they always did)
    locked = tuple(int(n) for n in version.split(".")) >= (0, 17, 0)
    needed = (wheel, lock) if locked else (wheel,)
    missing = [n for n in ("SHA256SUMS", "SHA256SUMS.sig") + needed if not assets.get(n)]
    if missing:
        raise Refused(f"CamAgent {tag} isn't a signed release this installer can use (missing {', '.join(missing)})")
    sums, sig = get(assets["SHA256SUMS"]), get(assets["SHA256SUMS.sig"])
    if not signed(sums, sig, keys):
        raise Refused(f"CamAgent {tag}: the signature doesn't match YonderView's release key")
    listed = parse_sums(sums)
    files = {"SHA256SUMS": sums, "SHA256SUMS.sig": sig}
    for n in needed:
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
'@
[IO.File]::WriteAllText($verifier, $VerifyRelease)
try {
    $Version = & $py -I $verifier $dl $Ref
    if ($LASTEXITCODE -ne 0) { throw "CamAgent wasn't installed: the download couldn't be checked (the reason is above)." }
    $Version = "$Version".Trim()
    if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "CamAgent wasn't installed: the release check gave an unexpected answer ($Version)." }

    $lockFile = Join-Path $dl "camagent-$Version-requirements.lock"
    $wheelFile = Join-Path $dl "camagent-$Version-py3-none-any.whl"
    if (Test-Path $lockFile) {
        Write-Host "==> Installing CamAgent $Version (with only the exact packages its signed list allows)"
        & $venvPy -I -m pip install -q --disable-pip-version-check --require-hashes --only-binary :all: -r $lockFile
        if ($LASTEXITCODE -ne 0) { throw "Installing the packages CamAgent needs failed (pip returned $LASTEXITCODE). Check this computer's internet connection and run the install line again." }
        & $venvPy -I -m pip install -q --disable-pip-version-check --force-reinstall --no-deps $wheelFile
    } else {    # a release from before 0.17.0: its signed package, the packages it needs from PyPI
        Write-Host "==> Installing CamAgent $Version"
        & $venvPy -I -m pip install -q --disable-pip-version-check --force-reinstall $wheelFile
    }
    if ($LASTEXITCODE -ne 0) { throw "Installing CamAgent failed (pip returned $LASTEXITCODE). Run the install line again." }
    # keep the checked files, like `camagent update` does (for rolling back)
    $releases = Join-Path $Base "releases"
    New-Item -ItemType Directory -Force -Path $releases | Out-Null
    $kept = Join-Path $releases $Version
    if (Test-Path $kept) { Remove-Item -Recurse -Force $kept }
    Move-Item $dl $kept
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

# a 'camagent' command on the PATH, without exposing the venv's python.exe
# (-I: Python ignores the current folder, so a "camagent" folder wherever you type it can't be run instead)
Set-Content -Path (Join-Path $Bin "camagent.cmd") -Value "@`"$venvPy`" -I -m camagent %*" -Encoding ASCII
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($machinePath -notlike "*$Bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$machinePath;$Bin", "Machine")
}
Refresh-Path

# Start menu shortcut: opens the camagent menu (it asks for administrator rights itself)
try {
    $lnk = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\CamAgent.lnk"
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sc.TargetPath = $venvPy
    $sc.Arguments = "-I -m camagent"
    $sc.WorkingDirectory = $Base
    $sc.Description = "CamAgent: sends your camera to YonderView"
    $sc.Save()
} catch { Write-Host "    (couldn't create the Start menu shortcut: $_. You can still type camagent in an administrator window.)" }


# an installed service picks up this version's settings and its own limited account (as after an update)
$hadService = [bool](Get-Service -Name camagent -ErrorAction SilentlyContinue)
if ($hadService) {
    & $venvPy -I -c "from camagent import service; service.refresh_unit()"
    if ($LASTEXITCODE -ne 0) { Write-Host "    (couldn't refresh the background service's settings; open CamAgent and choose Start CamAgent)" }
    # already set up here: start it again on the new version, so the cameras don't stay off after the install
    Write-Host "==> Starting CamAgent again"
    Restart-Service camagent -ErrorAction SilentlyContinue
    if (-not $?) { Start-Service camagent -ErrorAction SilentlyContinue }
}

Write-Host ""
& $venvPy -I -m camagent version
Write-Host ""
if (Test-Path (Join-Path $Base "camagent.toml")) {
    if ($hadService) { Write-Host "Your existing settings were kept, and CamAgent is running again on this version (camagent list shows it)." }
    else { Write-Host "Your existing settings were kept. To start CamAgent in the background: type camagent and choose Start CamAgent." }
} else {
    Write-Host "CamAgent is installed. Next: type camagent in this window (or open CamAgent from the Start menu)"
    Write-Host "and choose 'Add a camera'."
}
