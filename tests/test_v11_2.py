"""Run with: python tests/test_v11_2.py  (from the repo folder).
A camera that lists audio but sends none: ffmpeg waits for every stream, so nothing goes out. The uplink
notices (no frames at all), sends video only, and tries sound again later."""
import os, stat, sys, tempfile, time
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config
from camagent.uplink import Uplink

d = Path(tempfile.mkdtemp())
calls = d / "calls.txt"
fake = d / "ffmpeg"
# like the real ffmpeg waiting for audio packets that never come: runs, but never reports a frame
# Like the real one on test1234: video goes out (frames and bytes rise), the audio stream never gets anything,
# the server never accepts the stream, the counters stop; when stopped, ffmpeg names the empty audio output.
fake.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\n"
                "trap 'echo \"[af#0:1 @ 0x1] No filtered frames for output stream, trying to initialize anyway.\" >&2; "
                "exit 255' TERM\n"
                "for n in 15 30 45 60; do printf 'frame=%s\\ntotal_size=%s000000\\nspeed=1.0x\\nprogress=continue\\n' "
                "$n $n; sleep 0.2; done\nwhile :; do sleep 0.1; done\n")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

c = {s: dict(config.DEFAULTS[s]) for s in config.CAMERA_SECTIONS + config.MAIN_SECTIONS}
c["camera"].update(rtsp_url="rtsp://10.0.0.5/main")
c["platform"].update(camera_id="au_0001", ingest_host="127.0.0.1", srt_password="PW")
c["stream"].update(ffmpeg=str(fake), audio=True, stall_seconds=2)

with mock.patch("camagent.uplink.find_ffmpeg", lambda p: str(fake)):
    up = Uplink(c)
    assert "0:a:0?" in up.command() and up.stats["audio"] == "on"
    up.start()
    deadline = time.time() + 20
    while time.time() < deadline and len(calls.read_text().splitlines() if calls.exists() else []) < 2:
        time.sleep(0.2)
    up.stop()
    lines = calls.read_text().splitlines()
    assert len(lines) >= 2, lines
    assert "0:a:0?" in lines[0] and "-c:a" in lines[0]            # first try: with sound
    assert "0:a:0?" not in lines[1] and "-an" in lines[1]         # nothing went out: video only next
    assert up.stats["audio"] == "missing"
    assert up.stats["restarts"] == 0                              # a planned switch, not counted as a failure
    # while it's missing, a quiet check (beside the running video) looks for sound every AUDIO_CHECK_S
    def checked(result):
        up._restart.clear(); up._audio_checked_at = 0.0; up._audio_off_until = time.time() + 5
        with mock.patch("camagent.camera.sound_check", lambda cfg: result):
            up._check_audio_back()
            t = time.time() + 5
            while up._audio_checking and time.time() < t:
                time.sleep(0.05)
    checked(False)                                                # still silent: no restart, blind retry pushed back
    assert not up._restart.is_set() and up._audio_off_until > time.time() + Uplink.AUDIO_RETRY_S - 5
    assert "-an" in up.command()
    with mock.patch("camagent.camera.sound_check", lambda cfg: 1 / 0):
        up._check_audio_back()                                    # checked moments ago: not again yet
    assert not up._audio_checking
    checked(True)                                                 # sound is back: restart with audio
    assert up._restart.is_set() and "0:a:0?" in up.command() and up.stats["audio"] == "on"
    up._restart.clear()
    # without ffprobe, sound is just tried again after AUDIO_RETRY_S
    up._audio_off_until = time.time() - 1
    assert "0:a:0?" in up.command() and up.stats["audio"] == "on"
    # audio off in settings: nothing reported
    c["stream"]["audio"] = False
    assert "audio" not in (Uplink(c).command() and Uplink(c).stats)

# Changing the camera's stream over ONVIF: if sound stops afterwards (some firmware switches it off), say so
import builtins
from camagent import camera, configure
out = []
sounds = iter([True, False])                                    # before the change: sound; after: none
fake_controls = mock.MagicMock()
fake_controls.return_value.stream_fixes.return_value = ["resolution 3840x2160 -> 1920x1080"]
with mock.patch.object(camera, "sound_check", lambda cfg: next(sounds)), \
     mock.patch("camagent.controls.OnvifSession"), mock.patch("camagent.controls.Controls", fake_controls), \
     mock.patch.object(configure.time, "sleep", lambda s: None), \
     mock.patch.object(builtins, "print", lambda *a, **k: out.append(" ".join(map(str, a)))):
    c["stream"]["audio"] = True
    configure._fix_stream(c)
assert any("stopped sending sound" in line for line in out), out
print("v0.11.2 audio OK")
