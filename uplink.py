"""Runs ffmpeg (camera RTSP -> server SRT), restarts it when it dies or stalls, and keeps stats."""
import collections
import logging
import subprocess
import threading
import time

from .camera import find_ffmpeg
from .config import redact, rtsp_with_auth, scrub, srt_url

log = logging.getLogger("uplink")

# ffmpeg messages that are expected with many cameras and don't affect the stream
HARMLESS = (
    "Timestamps are unset in a packet",
)


class Uplink(threading.Thread):
    def __init__(self, cfg: dict):
        super().__init__(name="uplink", daemon=True)
        self.cfg = cfg
        self.proc = None
        self._stop = threading.Event()
        self._last_frame_at = time.time()
        self._stderr = collections.deque(maxlen=20)
        self.stats = {
            "running": False, "fps": 0.0, "bitrate_kbps": 0.0, "speed": 0.0,
            "frames": 0, "restarts": 0, "started_at": None, "last_error": "",
        }

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
        cmd += ["-rtsp_transport", "tcp",
                "-i", rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]),
                "-map", "0:v:0", "-c:v", "copy"]
        if s.get("audio"):
            cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "64k", "-ar", "48000",
                    "-af", "aresample=async=1"]
        else:
            cmd += ["-an"]
        cmd += ["-f", "mpegts", srt_url(self.cfg)]
        return cmd

    # ---------- output readers ----------
    def _read_progress(self, proc):
        block = {}
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            block[key] = value
            if key != "progress":
                continue
            try:
                frames = int(block.get("frame", "0") or 0)
                if frames > self.stats["frames"]:
                    self._last_frame_at = time.time()
                self.stats["frames"] = frames
                self.stats["fps"] = float(block.get("fps", "0") or 0)
                br = block.get("bitrate", "0").replace("kbits/s", "").strip()
                self.stats["bitrate_kbps"] = float(br) if br not in ("", "N/A") else 0.0
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
                log.debug("ffmpeg: %s", line)
                continue
            self._stderr.append(line)
            log.warning("ffmpeg: %s", line)

    # ---------- main loop ----------
    def run(self):
        backoff = 2
        stall = int(self.cfg["stream"].get("stall_seconds", 15))
        while not self._stop.is_set():
            try:
                cmd = self.command()
            except RuntimeError as e:
                self.stats["last_error"] = str(e)
                log.error("%s", e)
                self._stop.wait(30)
                continue

            log.info("starting ffmpeg -> %s", redact(srt_url(self.cfg)))
            started = time.time()
            self._last_frame_at = started
            self.stats.update(running=True, frames=0, started_at=started)
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             stdin=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError as e:
                self.stats.update(running=False, last_error=str(e))
                log.error("could not start ffmpeg: %s", e)
                self._stop.wait(30)
                continue

            threading.Thread(target=self._read_progress, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._read_stderr, args=(self.proc,), daemon=True).start()

            # watchdog: ffmpeg can hang forever when a camera disappears
            while self.proc.poll() is None and not self._stop.is_set():
                if time.time() - self._last_frame_at > stall:
                    log.warning("no new frames for %ss; restarting ffmpeg", stall)
                    self._kill()
                    break
                self._stop.wait(1)

            self._kill()
            code = self.proc.returncode
            self.stats["running"] = False
            if self._stop.is_set():
                break
            self.stats["restarts"] += 1
            self.stats["last_error"] = self._stderr[-1] if self._stderr else f"exit code {code}"
            log.warning("ffmpeg exited (code %s); %s", code, self.stats["last_error"])

            if time.time() - started > 60:      # it ran a while, so start the backoff over
                backoff = 2
            delay, backoff = backoff, min(backoff * 2, 60)
            log.info("restarting in %ss", delay)
            self._stop.wait(delay)

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
        self._stop.set()
        self._kill()
