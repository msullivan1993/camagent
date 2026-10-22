"""Runs ffmpeg (camera RTSP -> server SRT), restarts it when it dies or stalls, and keeps stats."""
import collections
import logging
import subprocess
import threading
import time

from . import camera, convert
from .camera import find_ffmpeg
from .config import redact, rtsp_with_auth, scrub, srt_url


# ffmpeg messages that are expected with many cameras and don't affect the stream
HARMLESS = (
    "Timestamps are unset in a packet",
)


class Uplink(threading.Thread):
    def __init__(self, cfg: dict, log=None):
        cid = cfg["platform"].get("camera_id", "")
        super().__init__(name=f"uplink-{cid}", daemon=True)
        self.cfg = cfg
        self.log = log or logging.getLogger(f"{cid}.uplink")
        self.proc = None
        self._restart = threading.Event()
        self._halt = threading.Event()
        self._last_frame_at = time.monotonic()        # monotonic: a clock change (a Pi syncing its time) isn't a stall
        self._stderr = collections.deque(maxlen=20)
        self._fast_fails = 0                      # converting: ffmpeg exits soon after starting, in a row
        self._audio_off_until = 0.0               # camera announces audio but sends none: video only until then
        self._audio_now = False
        self._audio_checked_at = 0.0              # last quiet check for the camera's sound (while it's missing)
        self._audio_checking = False
        self._run_lines = []
        self.stats = {
            "running": False, "fps": 0.0, "fps_camera": 0.0, "bitrate_kbps": 0.0, "speed": 0.0,
            "frames": 0, "restarts": 0, "started_at": None, "last_error": "",
        }

    # ---------- converting to 1080p (optional) ----------
    FAST_FAILS_TO_FALL_BACK = 3
    AUDIO_CHECK_S = 120                       # sound missing: look for it quietly this often (video keeps going)
    AUDIO_RETRY_S = 600                       # ...or, when it can't be checked (no ffprobe), just try again this late
    NOTHING_SENT_BYTES = 32 * 1024            # less than this written out in a run: nothing really went out

    def convert_method(self):
        """None to send the camera's stream as it is; else how to convert it. A GPU method that keeps failing
        right away (a driver update, the GPU switched off) falls back to the CPU instead of leaving the camera
        offline; telemetry says so."""
        s = self.cfg["stream"]
        if s.get("convert", "off") == "off":
            self.stats.pop("convert", None)
            return None
        method = s.get("encoder") or "x264"
        if method != "x264" and self._fast_fails >= self.FAST_FAILS_TO_FALL_BACK:
            if not self.stats.get("convert", {}).get("fallback"):
                self.log.warning("%s keeps failing; converting on the CPU instead", convert.LABELS.get(method, method))
            method, fallback = "x264", True
        else:
            fallback = False
        self.stats["convert"] = {"height": convert.TARGET_HEIGHT, "encoder": method, "fallback": fallback,
                                 "source_height": int(s.get("source_height") or 0) or None}
        return method

    # what ffmpeg says when a GPU or its encoder can't be used (not when the network or the camera drops)
    ENCODER_TROUBLE = ("qsv", "vaapi", "cuda", "nvenc", "amf", "hwaccel", "hw device", "device creation",
                       "no device", "cannot load", "encoder", "init", "mfx", "libva", "dxva", "d3d11")

    def _encoder_trouble(self):
        text = " ".join(self._stderr).lower()
        if any(w in text for w in ("connection refused", "connection timed out", "srt", "rtsp", "401", "404")):
            return False
        return any(w in text for w in self.ENCODER_TROUBLE)

    def _audio_never_came(self):
        """ffmpeg says the audio output never got a single frame ("[af#0:1 ...] No filtered frames for output
        stream"), or, failing that, almost nothing went out at all."""
        if any("No filtered frames for output stream" in ln and "af#" in ln for ln in self._run_lines):
            return True
        return self.stats.get("sent_bytes", 0) < self.NOTHING_SENT_BYTES

    def _check_audio_back(self):
        """While the camera's sound is missing, check now and then (a few seconds of ffprobe, beside the running
        stream) whether it sends sound again, e.g. after a reboot. Only then restart ffmpeg with audio, so the
        video never drops just to look."""
        if self._audio_now or not self.cfg["stream"].get("audio") or self._audio_checking:
            return
        if time.time() - self._audio_checked_at < self.AUDIO_CHECK_S:
            return
        self._audio_checked_at = time.time()
        self._audio_checking = True

        def check():
            try:
                back = camera.sound_check(self.cfg)
            except Exception:                     # a check that fails just means "not yet"
                back = None
            finally:
                self._audio_checking = False
            if back is True:
                self.log.info("the camera sends sound again; adding it back")
                self._audio_off_until = 0.0
                self._restart.set()
            elif back is False:                   # still silent: don't try it blind
                self._audio_off_until = max(self._audio_off_until, time.time() + self.AUDIO_RETRY_S)
        threading.Thread(target=check, name=f"{self.name}-audio", daemon=True).start()

    # ---------- command ----------
    def command(self) -> list:
        cam, s = self.cfg["camera"], self.cfg["stream"]
        ffmpeg = find_ffmpeg(s.get("ffmpeg", ""))
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found; install it or set [stream] ffmpeg in the config")
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
               "-progress", "pipe:1", "-nostats"]
        if s.get("wallclock_timestamps", True):
            cmd += ["-use_wallclock_as_timestamps", "1"]
        method = self.convert_method()
        if method:
            before, after = convert.args(method, s.get("bitrate_kbps"),
                                         scale=int(s.get("source_height") or 9999) > convert.TARGET_HEIGHT)
        else:
            before, after = [], ["-c:v", "copy"]               # the camera's stream as it is
        cmd += ["-rtsp_transport", "tcp", *before,
                "-i", rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]),
                "-map", "0:v:0", *after]
        self._audio_now = bool(s.get("audio")) and time.time() >= self._audio_off_until
        if s.get("audio"):
            self.stats["audio"] = "on" if self._audio_now else "missing"
        else:
            self.stats.pop("audio", None)
        if self._audio_now:
            cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "64k", "-ar", "48000",
                    "-af", "aresample=async=1"]
        else:
            cmd += ["-an"]
        cmd += ["-f", "mpegts", srt_url(self.cfg)]
        return cmd

    # ---------- output readers ----------
    WINDOW_S = 10

    def _read_progress(self, proc):
        """ffmpeg's own fps/bitrate are averages since it started. Count frames and bytes over the last
        10 seconds instead, so the numbers show what the camera is really sending right now."""
        from collections import deque
        window = deque()                       # (time, frames, total bytes)
        block = {}
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            block[key] = value
            if key != "progress":
                continue
            try:
                now = time.monotonic()
                frames = int(block.get("frame", "0") or 0)
                if frames > self.stats["frames"]:
                    self._last_frame_at = now
                self.stats["frames"] = frames
                size = int(block.get("total_size", "0") or 0) if block.get("total_size", "N/A") != "N/A" else 0
                self.stats["sent_bytes"] = size
                window.append((now, frames, size))
                while window and now - window[0][0] > self.WINDOW_S:
                    window.popleft()
                t0, f0, s0 = window[0]
                if now - t0 >= 2:
                    self.stats["fps"] = round((frames - f0) / (now - t0), 1)
                    self.stats["bitrate_kbps"] = round((size - s0) * 8 / 1000 / (now - t0), 1) if size else 0.0
                self.stats["fps_camera"] = self.stats["fps"]
                sp = block.get("speed", "0").replace("x", "").strip()
                self.stats["speed"] = float(sp) if sp not in ("", "N/A") else 0.0
            except ValueError:
                pass
            block = {}

    def _read_stderr(self, proc):
        for line in proc.stderr:
            line = scrub(line.rstrip())
            if not line:
                continue
            if any(h in line for h in HARMLESS):
                self.log.debug("ffmpeg: %s", line)
                continue
            self._stderr.append(line)
            self._run_lines.append(line)
            self.log.warning("ffmpeg: %s", line)

    # ---------- main loop ----------
    def restart_now(self, reason=""):
        """Restart ffmpeg right away with the current settings (e.g. a new latency). Not counted as a failure."""
        self.log.info("restarting the stream%s", f" ({reason})" if reason else "")
        self._restart.set()

    def run(self):
        backoff = 1                               # a dropped connection (e.g. a new Starlink address) retries fast
        stall = int(self.cfg["stream"].get("stall_seconds", 15))
        while not self._halt.is_set():
            try:
                cmd = self.command()
            except RuntimeError as e:
                self.stats["last_error"] = str(e)
                self.log.error("%s", e)
                self._halt.wait(30)
                continue

            self.log.info("starting ffmpeg -> %s", redact(srt_url(self.cfg)))
            started = time.time()
            started_mono = self._last_frame_at = time.monotonic()
            self.stats.update(running=True, frames=0, sent_bytes=0, started_at=started)
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             stdin=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError as e:
                self.stats.update(running=False, last_error=str(e))
                self.log.error("could not start ffmpeg: %s", e)
                self._halt.wait(30)
                continue

            self._run_lines = []                  # this run's ffmpeg messages
            threading.Thread(target=self._read_progress, args=(self.proc,), daemon=True).start()
            err_reader = threading.Thread(target=self._read_stderr, args=(self.proc,), daemon=True)
            err_reader.start()

            # watchdog: ffmpeg can hang forever when a camera disappears
            planned = False
            while self.proc.poll() is None and not self._halt.is_set():
                if self._restart.is_set():
                    self._restart.clear()
                    planned = True
                    self._kill()
                    break
                if time.monotonic() - started_mono > stall:
                    self._check_audio_back()
                if time.monotonic() - self._last_frame_at > stall:
                    self.log.warning("no new frames for %ss; restarting ffmpeg", stall)
                    self._kill()
                    err_reader.join(3)               # its last words, e.g. which stream never got anything
                    if self._audio_now and self._audio_never_came():
                        # The camera lists audio but sends none (often after its settings changed): the server
                        # waits for data on every track, so the stream never starts. Send video only; retry later.
                        self._audio_off_until = time.time() + self.AUDIO_RETRY_S
                        self._audio_checked_at = time.time()
                        self.log.warning("the camera lists audio but sends none; sending video only (checking "
                                         "for sound every %s minutes; rebooting the camera usually brings it back)",
                                         self.AUDIO_CHECK_S // 60)
                        planned = True
                    break
                self._halt.wait(1)

            self._kill()
            code = self.proc.returncode
            self.stats["running"] = False
            if self._halt.is_set():
                break
            if planned:
                continue
            self.stats["restarts"] += 1
            self.stats["last_error"] = self._stderr[-1] if self._stderr else f"exit code {code}"
            self.log.warning("ffmpeg exited (code %s); %s", code, self.stats["last_error"])

            if time.monotonic() - started_mono > 60:      # it ran a while, so start the backoff over
                backoff = 1
                self._fast_fails = 0
            elif time.monotonic() - started_mono < 20 and self._encoder_trouble():
                self._fast_fails += 1
            delay, backoff = backoff, min(backoff * 2, 60)
            self.log.info("restarting in %ss", delay)
            self._halt.wait(delay)

    def _kill(self):
        p = self.proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)

    def stop(self):
        self._halt.set()
        self._kill()
