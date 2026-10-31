"""0.17.5: a wrong computer clock doesn't break camera logins and is pointed out; the mark draws on Windows."""
import email.utils
import os
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import camera, link, prereqs, versions  # noqa: E402

# the clock: from YonderView's Date header; behind/ahead in plain words; time zones don't enter into it
when = 1_800_000_000


class Answer:
    def __init__(self, date): self.headers = {"Date": date}
    def __enter__(self): return self
    def __exit__(self, *a): return False


date = email.utils.formatdate(when, usegmt=True)
with mock.patch.object(link._opener, "open", return_value=Answer(date)), \
        mock.patch.object(link.time, "time", return_value=when - 3 * 3600 - 60):
    off = link.clock_offset("https://yonderview.net")
assert off == 3 * 3600 + 60, off
assert "3 h 1 min behind" in link.clock_problem(off)
assert "ahead" in link.clock_problem(-300)
assert link.clock_problem(20) == "" and link.clock_problem(None) == ""
with mock.patch.object(link._opener, "open", side_effect=OSError("offline")):
    assert link.clock_offset("https://yonderview.net") is None

# shown on the Health tab, checked at most hourly
prereqs._clock.update(at=0.0, problem="")
with mock.patch.object(link, "clock_offset", return_value=4000) as asked:
    assert "behind" in prereqs.clock_notice()
    prereqs.clock_notice()
    assert asked.call_count == 1
prereqs._clock.update(at=0.0, problem="")

# ONVIF logins stamped with the camera's time; a camera that won't tell it: connect as before
made = []


def fake_cam(*a, **k):
    made.append(k.get("adjust_time", False))
    if k.get("adjust_time") and len(made) == 3:
        raise RuntimeError("needs a login to tell its time")
    return NS()


with mock.patch("camagent._onvif.ONVIFCamera", side_effect=fake_cam):
    camera.onvif_connect("h", 80, "u", "p")
    assert made == [True]
    made.append("x")
    camera.onvif_connect("h", 80, "u", "p")
    assert made[-2:] == [True, False], made

# the mark on Windows names a font file (no fontconfig there), with ffmpeg's escaping
with mock.patch.object(versions.os, "name", "nt"), \
        mock.patch.dict(versions.os.environ, {"WINDIR": r"C:\Windows"}), \
        mock.patch.object(versions.os.path, "isfile", return_value=True), \
        mock.patch.object(versions.os.path, "join", side_effect=lambda *p: "\\".join(p)):
    assert versions._font() == r"fontfile='C\:/Windows/Fonts/arial.ttf'", versions._font()
with mock.patch.object(versions.os, "name", "posix"):
    assert versions._font() == "font='DejaVu Sans'"
print("v0.17.5 OK")

# AMD: Windows' own video decoding named (not "auto", which failed on a Ryzen mini PC), and a processor-decode
# fallback after repeated failures (0.17.6)
pre, enc, _ = versions.encoder("amf")
assert pre == ["-hwaccel", "d3d11va"] and "h264_amf" in enc
pre, enc, _ = versions.encoder("amf-lite")
assert pre == [] and "h264_amf" in enc
assert "amf-lite" in versions.GPU_METHODS
# a 1440p camera sent as is: the PC makes 1080p, 720p and 480p; converted to 1080p first: 720p and 480p
ladder = [{"suffix": s, "height": h} for s, h in (("1080", 1080), ("720", 720), ("480", 480))]
assert [v["suffix"] for v in versions.pick(ladder, 1440)] == ["1080", "720", "480"]
assert [v["suffix"] for v in versions.pick(ladder, 1080)] == ["720", "480"]
print("v0.17.6 OK")
