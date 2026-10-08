# camagent installer for Windows 10/11 and Windows Server.
# Run in PowerShell as Administrator:
#   irm https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.ps1 | iex
# Re-running it upgrades camagent and keeps your config.
#
# Private repository? Set a read-only token first:
#   $env:CAMAGENT_REPO = "https://TOKEN@github.com/msullivan1993/camagent.git"

$ErrorActionPreference = "Stop"
$Repo = if ($env:CAMAGENT_REPO) { $env:CAMAGENT_REPO } else { "https://github.com/msullivan1993/camagent.git" }
$Ref  = if ($env:CAMAGENT_REF)  { $env:CAMAGENT_REF }  else { "main" }
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
    winget install -e --id $id --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
}

function Find-Python {
    foreach ($cmd in @(@("py", "-3.12"), @("py", "-3"), @("python"))) {
        if (Get-Command $cmd[0] -ErrorAction SilentlyContinue) {
            $args_ = @($cmd | Select-Object -Skip 1) + @("-c", "import sys;print(sys.executable if sys.version_info>=(3,11) else '')")
            $exe = & $cmd[0] @args_ 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        }
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
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Winget-Install "Gyan.FFmpeg" }

Write-Host "==> Installing camagent from $Repo ($Ref)"
New-Item -ItemType Directory -Force -Path $Base, $Bin | Out-Null
$venvPy = Join-Path $Base "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $Base "venv") }
& $venvPy -m pip install -q --upgrade pip
& $venvPy -m pip install -q --upgrade "camagent @ git+$Repo@$Ref"
& $venvPy -m pip install -q --upgrade --force-reinstall --no-deps "camagent @ git+$Repo@$Ref"

# a 'camagent' command on the PATH, without exposing the venv's python.exe
Set-Content -Path (Join-Path $Bin "camagent.cmd") -Value "@`"$venvPy`" -m camagent %*" -Encoding ASCII
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($machinePath -notlike "*$Bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$machinePath;$Bin", "Machine")
}
Refresh-Path

# Start menu shortcut: opens the camagent menu (it asks for administrator rights itself)
try {
    $lnk = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\camagent.lnk"
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sc.TargetPath = $venvPy
    $sc.Arguments = "-m camagent"
    $sc.Description = "camagent: YonderView camera agent"
    $sc.Save()
} catch { Write-Host "    (couldn't create the Start menu shortcut: $_)" }

# the config holds passwords: only Administrators and SYSTEM may read this folder
icacls $Base /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" | Out-Null

Write-Host ""
& $venvPy -m camagent version
Write-Host ""
if (Test-Path (Join-Path $Base "camagent.toml")) {
    Write-Host "Existing config kept. Restart to apply the update:  camagent restart"
} else {
    Write-Host "Next step (in this Administrator window):  camagent configure"
}
