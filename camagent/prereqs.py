"""What this computer needs, checked at the start of setup (with offers to fix) and by `camagent doctor`."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"


def _windows_ffmpeg_candidates():
    """ffmpeg installed by winget isn't on PATH until a new window is opened (and never for the service),
    so look where winget and common manual installs put it."""
    roots = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""), "C:\\"]
    pats = ["Microsoft/WinGet/Packages/Gyan.FFmpeg*/**/bin/ffmpeg.exe", "WinGet/Packages/Gyan.FFmpeg*/**/bin/ffmpeg.exe",
            "ffmpeg/bin/ffmpeg.exe", "ffmpeg*/bin/ffmpeg.exe"]
    for root in filter(None, roots):
        for pat in pats:
            for hit in sorted(Path(root).glob(pat), reverse=True):     # newest version folder first
                yield str(hit)


def find_ffmpeg(configured=""):
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    if WINDOWS:
        return next(_windows_ffmpeg_candidates(), "")
    return ""


def ffmpeg_version(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True, text=True, timeout=10).stdout
        return out.splitlines()[0] if out else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def srt_supported(ffmpeg):
    """camagent sends video with SRT; some ffmpeg builds leave it out."""
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-protocols"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return any(line.strip() == "srt" for line in out.splitlines())


def install_ffmpeg():
    """Install ffmpeg with the system's package manager. Returns True if the command succeeded."""
    if WINDOWS:
        if not shutil.which("winget"):
            print("  winget isn't available. Download ffmpeg from https://www.gyan.dev/ffmpeg/builds/ "
                  "(the 'essentials' build), unzip it to C:\\ffmpeg, and run setup again.")
            return False
        cmd = ["winget", "install", "-e", "--id", "Gyan.FFmpeg",
               "--accept-source-agreements", "--accept-package-agreements"]
    elif shutil.which("apt-get"):
        cmd = ["apt-get", "install", "-y", "ffmpeg"]
    else:
        print("  Install ffmpeg with your system's package manager, then run setup again.")
        return False
    print("  Running: " + " ".join(cmd))
    return subprocess.run(cmd).returncode == 0


def sleep_minutes():
    """Windows only: minutes before the computer sleeps on AC power (0 = never), or None if unknown."""
    if not WINDOWS:
        return None
    try:
        out = subprocess.run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"Current AC Power Setting Index:\s*0x([0-9a-fA-F]+)", out)
    return int(m.group(1), 16) // 60 if m else None


def disable_sleep():
    return subprocess.run(["powercfg", "/change", "standby-timeout-ac", "0"]).returncode == 0


def check_and_fix(yes, configured_ffmpeg=""):
    """Interactive check at the start of setup. Returns (ffmpeg path or "", ffprobe path or "")."""
    print("Checking this computer...")
    v = sys.version_info
    print(f"  Python {v.major}.{v.minor}: OK" if v >= (3, 11) else f"  Python {v.major}.{v.minor}: too old (3.11+ needed)")

    from . import camera                     # (through camera, so tests and callers can substitute it)
    ffmpeg = camera.find_ffmpeg(configured_ffmpeg)
    if not ffmpeg:
        print("  ffmpeg: not found. camagent needs it to send video.")
        if yes("Install ffmpeg now?", default=True) and install_ffmpeg():
            ffmpeg = camera.find_ffmpeg()
        if not ffmpeg:
            print("  ffmpeg still isn't available. Install it, then run setup again.")
            if not yes("Continue setup anyway? (It won't stream until ffmpeg is installed.)", default=False):
                raise SystemExit("Setup stopped. Install ffmpeg, then run camagent again.")
    if ffmpeg:
        print(f"  ffmpeg: {ffmpeg_version(ffmpeg) or ffmpeg}")
        srt = srt_supported(ffmpeg)
        if srt is False:
            print("  ffmpeg was built without SRT, which camagent uses to send video. Install a full build")
            print("  (Windows: winget install -e --id Gyan.FFmpeg; Linux: your distribution's ffmpeg package).")
        elif srt:
            print("  SRT support: OK")
    ffprobe = camera.find_ffprobe(ffmpeg)
    if ffmpeg and not ffprobe:
        print("  ffprobe: not found (it comes with ffmpeg). Stream checks will be skipped.")

    mins = sleep_minutes()
    if mins:
        print(f"  This computer goes to sleep after {mins} minutes, which would take the camera offline.")
        if yes("Turn off sleep while plugged in?", default=True):
            print("  Sleep turned off." if disable_sleep() else "  Couldn't change it; set it in Power settings.")
    elif mins == 0:
        print("  Sleep: off. Good.")
    print()
    return ffmpeg, ffprobe
