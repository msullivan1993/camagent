"""What this computer needs, checked at the start of setup (with offers to fix) and by `camagent doctor`."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"


def which(name):
    """Like shutil.which, but never the current folder: on Windows shutil.which looks there first, and the
    service's folder must not be a place where anyone can drop an "ffmpeg.exe" that then runs as SYSTEM."""
    exts = [""] + (os.environ.get("PATHEXT", ".EXE").lower().split(";") if WINDOWS else [])
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or folder in (".", os.curdir) or not os.path.isabs(folder):
            continue
        for ext in exts:
            p = os.path.join(folder, name + ext)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def admin_only_location(path) -> bool:
    """Windows: is this file somewhere only administrators can change (Program Files, Windows, camagent's own
    locked folder)? The service runs ffmpeg as SYSTEM, so an ffmpeg a normal user could replace (a per-user winget
    install under C:\\Users, or C:\\ffmpeg) would let them run anything as SYSTEM. Always True elsewhere."""
    if not WINDOWS or not path:
        return True
    p = os.path.normcase(os.path.abspath(path))
    roots = [os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
             os.environ.get("ProgramW6432", ""),
             # not all of C:\\Windows: users can write in some of its folders (Temp, Tasks, tracing...)
             os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "camagent")]
    return any(r and p.startswith(os.path.normcase(os.path.abspath(r)) + os.sep) for r in roots)


_warned_unsafe = set()


def _windows_ffmpeg_candidates():
    """ffmpeg installed by winget isn't on PATH until a new window is opened (and never for the service),
    so look where winget and common manual installs put it. Not in folders any user can create (like C:\\ffmpeg):
    whatever is found there would run as SYSTEM."""
    roots = [os.environ.get("ProgramFiles", "")]                 # machine-wide installs only (see admin_only_location)
    pats = ["WinGet/Packages/Gyan.FFmpeg*/**/bin/ffmpeg.exe", "Microsoft/WinGet/Packages/Gyan.FFmpeg*/**/bin/ffmpeg.exe",
            "ffmpeg/bin/ffmpeg.exe"]
    for root in filter(None, roots):
        for pat in pats:
            for hit in sorted(Path(root).glob(pat), reverse=True):     # newest version folder first
                yield str(hit)


def find_ffmpeg(configured=""):
    """The ffmpeg to run. On Windows, one in an administrators-only folder is preferred over the configured or
    PATH one when those sit where a normal user could replace them."""
    picks = []
    if configured and Path(configured).exists():
        picks.append(configured)
    found = which("ffmpeg")
    if found:
        picks.append(found)
    if WINDOWS:
        picks += list(_windows_ffmpeg_candidates())
    for p in picks:
        if admin_only_location(p):
            return p
    if picks:                                   # only unsafe ones: keep the camera working, but say so
        if picks[0] not in _warned_unsafe:
            _warned_unsafe.add(picks[0])
            print(f"WARNING: ffmpeg at {picks[0]} is in a folder any user of this computer can change, and CamAgent "
                  f"runs it with full system rights (a security risk). To fix it, install ffmpeg for all users: in an "
                  f"administrator window type winget install -e --id Gyan.FFmpeg --scope machine, then change the "
                  f"camera's settings in CamAgent again.", flush=True)
        return picks[0]
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
            print("  winget (Windows' app installer) isn't available. Download ffmpeg from "
                  "https://www.gyan.dev/ffmpeg/builds/ (the 'essentials' build), unzip it to "
                  "C:\\Program Files\\ffmpeg, and run setup again.")
            return False
        # for all users: under Program Files, where only administrators can change it (the service runs it)
        cmd = ["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--scope", "machine",
               "--accept-source-agreements", "--accept-package-agreements"]
    elif shutil.which("apt-get"):
        cmd = ["apt-get", "install", "-y", "ffmpeg"]
    else:
        print("  CamAgent can't install ffmpeg on this system by itself. Install it with your system's package "
              "manager, then run setup again.")
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
    print(f"  Python {v.major}.{v.minor}: OK" if v >= (3, 11) else
          f"  Python {v.major}.{v.minor}: too old (3.11 or newer is needed; run the CamAgent installer again)")

    from . import camera                     # (through camera, so tests and callers can substitute it)
    ffmpeg = camera.find_ffmpeg(configured_ffmpeg)
    if not ffmpeg:
        print("  ffmpeg: not found. CamAgent needs this free video tool to send video.")
        if yes("Install ffmpeg now?", default=True) and install_ffmpeg():
            ffmpeg = camera.find_ffmpeg()
        if not ffmpeg:
            print("  ffmpeg still isn't available. Install it, then run setup again.")
            print("  (Windows: in an administrator window type winget install -e --id Gyan.FFmpeg --scope machine;")
            print("  Linux: sudo apt install ffmpeg)")
            if not yes("Continue setup anyway? (It won't stream until ffmpeg is installed.)", default=False):
                raise SystemExit("Setup stopped. Install ffmpeg, then run camagent again.")
    if ffmpeg:
        print(f"  ffmpeg: {ffmpeg_version(ffmpeg) or ffmpeg}")
        srt = srt_supported(ffmpeg)
        if srt is False:
            print("  This copy of ffmpeg can't send video the way YonderView receives it (it was built without SRT).")
            print("  Install the full version (Windows: winget install -e --id Gyan.FFmpeg --scope machine;")
            print("  Linux: sudo apt install ffmpeg).")
        elif srt:
            print("  Sending video to YonderView (SRT): OK")
    ffprobe = camera.find_ffprobe(ffmpeg)
    if ffmpeg and not ffprobe:
        print("  ffprobe (part of ffmpeg): not found, so setup can't check the camera's video. Reinstalling ffmpeg")
        print("  usually fixes this.")

    mins = sleep_minutes()
    if mins:
        print(f"  This computer goes to sleep after {mins} minutes, which would take the camera offline.")
        if yes("Turn off sleep while plugged in?", default=True):
            print("  Sleep turned off." if disable_sleep() else
                  "  Couldn't change it. Set it yourself: Settings > System > Power > Screen and sleep > Never.")
    elif mins == 0:
        print("  Sleep: off. Good.")
    print()
    return ffmpeg, ffprobe
