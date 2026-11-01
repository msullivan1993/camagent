"""0.17.13: converting on an AMD PC decodes on the graphics chip too (it used the processor); a failing GPU falls
back to the processor decoding, then to the CPU alone, and stays there (no blips every few minutes); ffmpeg's
"Last message repeated" notes aren't logged."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import convert, versions  # noqa: E402
from camagent.uplink import Uplink  # noqa: E402


def cfg(**stream):
    return {"platform": {"camera_id": "c1", "srt_host": "h", "srt_port": 8890, "srt_password": "pw",
                         "srt_passphrase": "0123456789abcdef"},
            "camera": {"rtsp_url": "rtsp://10.0.0.5/live", "username": "u", "password": "p"},
            "stream": {"audio": False, **stream}, "agent": {}}


before, after = convert.args("amf", 4500)
assert before == ["-hwaccel", "d3d11va"] and "h264_amf" in after
before, after = convert.args("amf-lite", 4500)
assert before == [] and "h264_amf" in after
assert "-hwaccel" not in convert.args("x264", 4500)[0]

with mock.patch("camagent.uplink.find_ffmpeg", lambda p: "/usr/bin/ffmpeg"), \
        mock.patch("camagent.uplink.srt_url", lambda c, path="": "srt://h:8890"):
    up = Uplink(cfg(convert="1080p", encoder="amf", source_height=1440))
    cmd = up.command()
    assert cmd[cmd.index("-hwaccel") + 1] == "d3d11va" and cmd.index("-hwaccel") < cmd.index("-i")
    up._fast_fails = Uplink.FAST_FAILS_TO_FALL_BACK                  # GPU decoding keeps failing
    cmd = up.command()
    assert "-hwaccel" not in cmd and "h264_amf" in cmd and up.stats["convert"]["encoder"] == "amf-lite"
    up._fast_fails = 0                                               # it ran a while: still amf-lite (no going back)
    assert "-hwaccel" not in up.command() and up.stats["convert"]["fallback"] is True
    up._fast_fails = Uplink.FAST_FAILS_TO_FALL_BACK                  # the encoder fails too: the CPU
    assert "libx264" in up.command() and up.stats["convert"]["encoder"] == "x264"

assert any("Last message repeated" in h for h in versions.HARMLESS)
print("v0.17.13 OK")
