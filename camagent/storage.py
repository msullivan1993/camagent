"""A dedicated box's flash storage (0.17.15): how worn its eMMC is, and `camagent box-setup`, which makes a Linux box
that only runs CamAgent write as little as possible to it.

eMMC reports its own wear (JEDEC): life_time is two estimates, 0x01 = 0-10% used ... 0x0A = 90-100%, 0x0B = past
its rated life; pre_eol_info 0x01 normal, 0x02 warning (80% of its spare blocks used), 0x03 urgent. microSD cards
don't report any of this.

box-setup (Linux, root, asks first): the system journal (CamAgent's log) kept in memory and capped; no swap file on
the flash (swap in compressed memory, zram, is fine); /tmp in memory. CamAgent itself writes only small state files.
"""
import glob
import os
import subprocess
from pathlib import Path

WARN_LIFE = 0x08            # 70-80% of its rated life used: tell the host


def _hex(text):
    try:
        return int(str(text).strip(), 16)
    except ValueError:
        return None


def emmc_wear(root="/sys/block"):
    """[{"device", "used_pct" (the higher estimate's upper bound), "eol"}] for each eMMC, or []."""
    out = []
    for dev in sorted(glob.glob(os.path.join(root, "mmcblk*"))):
        name = os.path.basename(dev)
        if "boot" in name or "rpmb" in name:
            continue
        try:
            life = Path(dev, "device", "life_time").read_text().split()
            eol = _hex(Path(dev, "device", "pre_eol_info").read_text())
        except OSError:
            continue                       # a microSD card, or no wear report
        levels = [v for v in (_hex(x) for x in life) if v]
        if not levels:
            continue
        out.append({"device": name, "used_pct": min(100, max(levels) * 10) if max(levels) <= 0x0A else 110,
                    "eol": eol})
    return out


def wear_notice(root="/sys/block"):
    """A plain-words note for the camera's Health tab when the box's flash storage is wearing out, else ""."""
    for w in emmc_wear(root):
        if (w["eol"] or 1) >= 0x02 or w["used_pct"] >= WARN_LIFE * 10:
            when = "now" if (w["eol"] or 1) >= 0x03 or w["used_pct"] > 100 else "soon"
            return (f"This computer's built-in storage ({w['device']}) is about {min(w['used_pct'], 100)}% worn out. "
                    f"Plan to replace the computer {when}. Tip: keep a spare computer set up with CamAgent; "
                    "swapping it in takes a few minutes")
    return ""


# ---------- camagent box-setup ----------
JOURNALD = "/etc/systemd/journald.conf.d/50-camagent-box.conf"
JOURNALD_TEXT = """# camagent box-setup: the log kept in memory (a box that only runs CamAgent; saves its flash storage)
[Journal]
Storage=volatile
RuntimeMaxUse=64M
"""


def swap_files():
    """Swap that lives on storage (files or partitions), not in memory (zram)."""
    try:
        lines = Path("/proc/swaps").read_text().splitlines()[1:]
    except OSError:
        return []
    return [ln.split()[0] for ln in lines if ln.split() and "zram" not in ln.split()[0]]


def plan():
    """What box-setup would change, as [(description, function)]."""
    steps = []
    if not Path(JOURNALD).exists():
        steps.append(("keep the system log (CamAgent's included) in memory, capped at 64 MB", _journal))
    if swap_files():
        steps.append((f"turn off the swap file on the storage ({', '.join(swap_files())}). Swap is space the computer uses as "
                      "extra memory, and it wears the storage out. Memory swap (zram) isn't touched", _swap))
    if not _tmp_in_memory():
        steps.append(("put /tmp in memory", _tmp))
    return steps


def _run(*cmd):
    subprocess.run(list(cmd), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _journal():
    Path(JOURNALD).parent.mkdir(parents=True, exist_ok=True)
    Path(JOURNALD).write_text(JOURNALD_TEXT, encoding="utf-8")
    _run("systemctl", "restart", "systemd-journald")


def _swap():
    for name in ("dphys-swapfile",):                 # Raspberry Pi OS's swap file service
        _run("systemctl", "disable", "--now", name)
    _run("swapoff", "-a")
    fstab = Path("/etc/fstab")
    try:
        lines = fstab.read_text().splitlines()
    except OSError:
        return
    changed = [("# camagent box-setup: " + ln) if ln.split()[2:3] == ["swap"] and not ln.lstrip().startswith("#")
               else ln for ln in lines]
    if changed != lines:
        fstab.with_suffix(".camagent-backup").write_text("\n".join(lines) + "\n", encoding="utf-8")
        fstab.write_text("\n".join(changed) + "\n", encoding="utf-8")


def _tmp_in_memory():
    try:
        return any(ln.split()[1:3] == ["/tmp", "tmpfs"] for ln in Path("/proc/mounts").read_text().splitlines())
    except OSError:
        return True


def _tmp():
    _run("systemctl", "enable", "--now", "tmp.mount")


def run():
    """`camagent box-setup`: Linux only, as root, after asking."""
    if os.name == "nt":
        print("box-setup is for Linux computers like a Raspberry Pi or a Rockchip box.")
        print("Windows computers don't need it.")
        return 0
    if os.geteuid() != 0:
        print("box-setup needs administrator rights. On Linux, type sudo in front: sudo camagent box-setup")
        return 1
    for w in emmc_wear():
        print(f"Built-in storage {w['device']}: about {min(w['used_pct'], 100)}% of its rated life is used.")
    steps = plan()
    if not steps:
        print("Nothing to change. Your computer already writes as little as it can to its storage.")
        return 0
    print("box-setup is for a computer that only runs CamAgent. box-setup saves the flash storage by writing less to it.")
    print("CamAgent would make these changes:")
    for text, _ in steps:
        print(f"  - {text}")
    print("Don't use this on a computer you use for other things. The system log won't survive a restart.")
    if input("Make these changes? (y/N): ").strip().lower() not in ("y", "yes"):
        print("CamAgent changed nothing.")
        return 0
    for text, fn in steps:
        fn()
        print(f"  Done: {text}")
    print("All done. Restart this computer to make sure everything takes effect (type: sudo reboot).")
    return 0
