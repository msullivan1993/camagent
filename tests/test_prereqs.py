"""Run with: python tests/test_prereqs.py  (from the repo folder)."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import prereqs  # noqa: E402

PROTOCOLS = "Supported file protocols:\nInput:\n  file\n  http\n  srt\n  tcp\nOutput:\n  file\n  srt\n"
with mock.patch("camagent.prereqs.subprocess.run", return_value=mock.Mock(stdout=PROTOCOLS)):
    assert prereqs.srt_supported("ffmpeg") is True
with mock.patch("camagent.prereqs.subprocess.run", return_value=mock.Mock(stdout="Input:\n  file\n  srtp\n")):
    assert prereqs.srt_supported("ffmpeg") is False                 # srtp isn't srt

POWER = "    Current AC Power Setting Index: 0x00000708\n    Current DC Power Setting Index: 0x00000384\n"
with mock.patch.object(prereqs, "WINDOWS", True), \
     mock.patch("camagent.prereqs.subprocess.run", return_value=mock.Mock(stdout=POWER)):
    assert prereqs.sleep_minutes() == 30
with mock.patch.object(prereqs, "WINDOWS", False):
    assert prereqs.sleep_minutes() is None

# Missing ffmpeg: offered an install, then found
found = iter(["", "C:/ffmpeg/bin/ffmpeg.exe"])
answers = iter([True, True])                                         # install? yes / turn off sleep? yes
with mock.patch("camagent.camera.find_ffmpeg", side_effect=lambda *a: next(found)), \
     mock.patch("camagent.camera.find_ffprobe", return_value="C:/ffmpeg/bin/ffprobe.exe"), \
     mock.patch.object(prereqs, "install_ffmpeg", return_value=True) as inst, \
     mock.patch.object(prereqs, "ffmpeg_version", return_value="ffmpeg version 7.1"), \
     mock.patch.object(prereqs, "srt_supported", return_value=True), \
     mock.patch.object(prereqs, "sleep_minutes", return_value=30), \
     mock.patch.object(prereqs, "disable_sleep", return_value=True) as nosleep:
    ff, fp = prereqs.check_and_fix(lambda *a, **k: next(answers))
assert (ff, fp) == ("C:/ffmpeg/bin/ffmpeg.exe", "C:/ffmpeg/bin/ffprobe.exe")
inst.assert_called_once(); nosleep.assert_called_once()
print("prereqs OK")
