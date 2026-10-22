# camagent installer for Windows 10/11 and Windows Server.
# Run in PowerShell as Administrator:
#   irm https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.ps1 | iex
# Re-running it upgrades camagent and keeps your config.
#
# Private repository? Set a read-only token first:
#   $env:CAMAGENT_REPO = "https://TOKEN@github.com/msullivan1993/camagent.git"

$ErrorActionPreference = "Stop"
$Repo = if ($env:CAMAGENT_REPO) { $env:CAMAGENT_REPO } else { "https://github.com/msullivan1993/camagent.git" }
$Ref  = $env:CAMAGENT_REF        # default: the latest published release (below), never untested "main"
$Base = Join-Path $env:ProgramData "camagent"
$Bin  = Join-Path $Base "bin"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Please run PowerShell as Administrator." }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links")
}

function Winget-Install($id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget isn't available. Install '$id' manually, then run this again."
    }
    # for all users (Program Files): the service runs these as SYSTEM, so a normal user mustn't be able to change them
    winget install -e --id $id --scope machine --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { Write-Host "    (winget said $LASTEXITCODE for $id; it may already be installed for one user only)" }
    Refresh-Path
}

function Test-AllUsersPath($path) {
    # under Program Files: only administrators can change it (the service runs it as SYSTEM)
    $pf = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    return [bool]($pf | Where-Object { $path.ToLower().StartsWith($_.ToLower() + "\") })
}

function Find-Python {
    # Every Python the launcher knows (py -0p), plus "python" on the PATH; only one installed for all users,
    # version 3.11 or newer. (A per-user Python is skipped: its user could change what runs as SYSTEM.)
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $cands += (& py -0p 2>$null) | ForEach-Object { if ($_ -match '([A-Za-z]:\\.+?python\.exe)\s*$') { $Matches[1] } }
    }
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { $cands += $p.Source }
    foreach ($exe in $cands | Select-Object -Unique) {
        if (-not (Test-AllUsersPath $exe)) { Write-Host "    (skipping $exe : not installed for all users)"; continue }
        $ok = & $exe -c "import sys;print(1 if sys.version_info>=(3,11) else 0)" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$ok".Trim() -eq "1") { return $exe }
    }
    return $null
}

Write-Host "==> Checking Python 3.11+"
$py = Find-Python
if (-not $py) {
    Winget-Install "Python.Python.3.12"
    $py = Find-Python
    if (-not $py) { throw "Python installed, but this window can't see it yet. Open a new Administrator PowerShell and run the installer again." }
}
Write-Host "    $py"

Write-Host "==> Checking Git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget-Install "Git.Git" }

Write-Host "==> Checking ffmpeg"
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff -or -not (Test-AllUsersPath $ff.Source)) { Winget-Install "Gyan.FFmpeg" }

if (-not $Ref) {
    try {
        $Ref = (Invoke-RestMethod -UseBasicParsing -Headers @{ "User-Agent" = "camagent-installer" } `
                "https://api.github.com/repos/msullivan1993/camagent/releases/latest").tag_name
    } catch { }
    if (-not $Ref) { throw "Couldn't find the latest camagent release (GitHub didn't answer). Try again in a few minutes." }
}

Write-Host "==> Installing camagent from $Repo ($Ref)"
# The folder holds passwords and the code the service runs as SYSTEM: lock it to Administrators and SYSTEM
# *before* anything goes in it (a folder a normal user made first would stay theirs to change).
if (Test-Path $Base) {
    # by SID (names differ by language): Administrators, SYSTEM or TrustedInstaller
    $owner = (Get-Acl $Base).GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @("S-1-5-32-544", "S-1-5-18", "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")) {
        throw "$Base already exists and isn't owned by Administrators ($owner). Delete it (or check who made it), then run this again."
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
    Write-Host "    the old program folder was made from a Python in a user's profile: making it again"
    Stop-Service -Name camagent -ErrorAction SilentlyContinue      # it runs from that folder
    Remove-Item -Recurse -Force $venv
}
if (-not (Test-Path $venvPy)) { & $py -m venv $venv }
& $venvPy -m pip install -q --upgrade pip
& $venvPy -m pip install -q --upgrade "camagent @ git+$Repo@$Ref"
& $venvPy -m pip install -q --upgrade --force-reinstall --no-deps "camagent @ git+$Repo@$Ref"

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
    $sc.Description = "camagent: YonderView camera agent"
    $sc.Save()
} catch { Write-Host "    (couldn't create the Start menu shortcut: $_)" }


Write-Host ""
& $venvPy -m camagent version
Write-Host ""
if (Test-Path (Join-Path $Base "camagent.toml")) {
    Write-Host "Existing config kept. Restart to apply the update:  camagent restart"
} else {
    Write-Host "Next step (in this Administrator window):  camagent   (then choose Add a camera)"
}
