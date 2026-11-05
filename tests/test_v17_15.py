"""0.17.15: a box's eMMC wear on the Health tab and in the checks; box-setup's plan."""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import storage  # noqa: E402


def fake_block(root, name, life, eol):
    d = Path(root, name, "device")
    d.mkdir(parents=True)
    (d / "life_time").write_text(life)
    (d / "pre_eol_info").write_text(eol)


with tempfile.TemporaryDirectory() as root:
    fake_block(root, "mmcblk0", "0x02 0x03\n", "0x01\n")
    Path(root, "mmcblk0boot0").mkdir()                       # boot partitions are skipped
    Path(root, "mmcblk1").mkdir()                            # a microSD: no wear report
    assert storage.emmc_wear(root) == [{"device": "mmcblk0", "used_pct": 30, "eol": 1}]
    assert storage.wear_notice(root) == ""
with tempfile.TemporaryDirectory() as root:
    fake_block(root, "mmcblk0", "0x08 0x05\n", "0x01\n")
    assert "about 80% worn" in storage.wear_notice(root) and "soon" in storage.wear_notice(root)
with tempfile.TemporaryDirectory() as root:
    fake_block(root, "mmcblk0", "0x0B 0x0B\n", "0x03\n")
    note = storage.wear_notice(root)
    assert "100% worn" in note and "now" in note

with mock.patch.object(storage, "swap_files", return_value=["/var/swap"]), \
        mock.patch.object(storage, "_tmp_in_memory", return_value=False), \
        mock.patch.object(storage.Path, "exists", return_value=False):
    texts = [t for t, _ in storage.plan()]
assert any("memory" in t and "log" in t for t in texts) and any("swap" in t for t in texts) and any("/tmp" in t for t in texts)
print("v0.17.15 OK")
