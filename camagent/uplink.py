"""Runs ffmpeg (camera RTSP -> server SRT), restarts it when it dies or stalls, and keeps stats."""
import collections
import logging
import subprocess
import threading
import time

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
        self._halt = threading.Event()
        self._last_frame_at = time.time()
        self._stderr = collections.deque(maxlen=20)
        self.stats = {
            "running": False, "fps": 0.0, "fps_camera": 0.0, "bitrate_kbps": 0.0, "speed": 0.0,
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
                now = time.time()
                frames = int(block.get("frame", "0") or 0)
                if frames > self.stats["frames"]:
                    self._last_frame_at = now
                self.stats["frames"] = frames
                size = int(block.get("total_size", "0") or 0) if block.get("total_size", "N/A") != "N/A" else 0
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
            self.log.warning("ffmpeg: %s", line)

    # ---------- main loop ----------
    def run(self):
        backoff = 2
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
            self._last_frame_at = started
            self.stats.update(running=True, frames=0, started_at=started)
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                             stdin=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError as e:
                self.stats.update(running=False, last_error=str(e))
                self.log.error("could not start ffmpeg: %s", e)
                self._halt.wait(30)
                continue

            threading.Thread(target=self._read_progress, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._read_stderr, args=(self.proc,), daemon=True).start()

            # watchdog: ffmpeg can hang forever when a camera disappears
            while self.proc.poll() is None and not self._halt.is_set():
                if time.time() - self._last_frame_at > stall:
                    self.log.warning("no new frames for %ss; restarting ffmpeg", stall)
                    self._kill()
                    break
                self._halt.wait(1)

            self._kill()
            code = self.proc.returncode
            self.stats["running"] = False
            if self._halt.is_set():
                break
            self.stats["restarts"] += 1
            self.stats["last_error"] = self._stderr[-1] if self._stderr else f"exit code {code}"
            self.log.warning("ffmpeg exited (code %s); %s", code, self.stats["last_error"])

            if time.time() - started > 60:      # it ran a while, so start the backoff over
                backoff = 2
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
