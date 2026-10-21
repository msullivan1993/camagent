"""Lower-quality versions made on this PC, when the website asks (camagent 0.13+).

YonderView normally makes a camera's 720p/480p (and 1080p for bigger cameras) on its servers. When this PC has a
usable GPU, the website can ask camagent to make them here instead, but only while someone is watching them, so
the extra upload happens only then. The site sends the same ladder (sizes, frame rates, bitrates) and burned-in
mark it uses itself, so a version looks the same wherever it's made.

It's a second ffmpeg next to the main upload, so starting or stopping versions never interrupts the camera's own
stream. It reads the camera once, decodes it on the GPU, and for each version resizes, adds the mark if asked and
encodes on the GPU, then sends each one over SRT to that version's path (same login and passphrase as the
camera). It stops when told, or by itself if the website hasn't said "still wanted" for keep_s seconds, and it
gives up (so the server takes over) if ffmpeg keeps failing. Config: [stream] versions = "auto" (with a
GPU), "cpu" (also without one: x264, for strong CPUs) or "off"."""
import json
import logging
import os
import subprocess
import tempfile
import threading
import time

from . import convert
from .camera import find_ffmpeg
from .config import redact, rtsp_with_auth, scrub, srt_url, status_path

# ffmpeg lines that aren't problems (it corrects the timestamps itself); kept out of the error shown on the website
HARMLESS = ("Non-monotonic DTS", "changing to", "Past duration", "Queue input is backward in time")

GPU_METHODS = ("qsv", "qsv-lite", "vaapi", "vaapi-lite", "nvenc", "nvenc-lite", "amf", "v4l2m2m", "v4l2m2m-lite",
               "rkmpp")
MAX_FAST_FAILS = 3          # ffmpeg dying this many times within FAST_S of starting: give up (the server takes over)
FAST_S = 20
SLOW_SPEED = 0.9            # slower than this (x real time)...
SLOW_FOR_S = 30             # ...for this long, after WARMUP_S: this PC can't keep up; give up, the server takes over
WARMUP_S = 15
RETRY_AFTER_S = 3600        # after giving up, offer again this much later


def encoder(method):
    """(options before the input, video encoder options, pixel format filter) for frames in system memory.
    Decoding uses the GPU too where it can (-hwaccel without an output format hands frames back to the CPU,
    where they're resized and marked; small versions make that cheap)."""
    base = method.replace("-lite", "")
    if base == "qsv":
        return ["-hwaccel", "auto"], ["-c:v", "h264_qsv", "-preset", "veryfast"], "format=nv12"
    if base == "vaapi":
        dev = convert.VAAPI_DEVICE
        return (["-vaapi_device", dev, "-hwaccel", "vaapi", "-hwaccel_device", dev],
                ["-c:v", "h264_vaapi"], "format=nv12,hwupload")
    if base == "nvenc":
        return ["-hwaccel", "cuda"], ["-c:v", "h264_nvenc", "-preset", "p3", "-tune", "ll"], "format=nv12"
    if base == "amf":
        return ["-hwaccel", "auto"], ["-c:v", "h264_amf", "-quality", "speed"], "format=nv12"
    if method == "v4l2m2m":                        # Raspberry Pi: decode on its video engine too
        return ["-c:v", "h264_v4l2m2m"], ["-c:v", "h264_v4l2m2m"], "format=yuv420p"
    if method == "v4l2m2m-lite":
        return [], ["-c:v", "h264_v4l2m2m"], "format=yuv420p"
    if base == "rkmpp":
        return [], ["-c:v", "h264_rkmpp", "-rc_mode", "CBR"], "format=nv12"
    return [], ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency"], "format=yuv420p"


def _mark_filters(f1, f2, every, chars):
    """The site's burned-in mark: two lines that jump to a new random-looking spot every `every` seconds
    (the same scheme as the server's, cameras/transcode.py)."""
    n = max(1, int(every))

    def rnd(seed):
        x = f"sin(floor(t/{n})*12.9898+{seed})*43758.5453"
        return f"({x}-floor({x}))"
    m = "H/40"
    text_w = max(chars[0] * 0.56 / 26 if f1 else 0, chars[1] * 0.56 / 40 if f2 else 0)
    block_w = f"min(W-2*{m}\\,{text_w:.4f}*H)"
    block_h = "H*0.0756" if (f1 and f2) else "H*0.0423"
    cx = f"({m}+{block_w}/2+(W-2*{m}-{block_w})*{rnd(1.7)})"
    y0 = f"({m}+(H-2*{m}-{block_h})*{rnd(4.3)})"
    font = "Arial" if os.name == "nt" else "DejaVu Sans"
    look = f"fontcolor=white@0.7:shadowcolor=black@0.6:shadowx=1:shadowy=1:font='{font}'"
    out = []
    if f1:
        out.append(f"drawtext=textfile='{f1}':fontsize=h/26:{look}:x='{cx}-tw/2':y='{y0}'")
    if f2:
        below = "+h/26*1.25" if f1 else ""
        out.append(f"drawtext=textfile='{f2}':fontsize=h/40:{look}:x='{cx}-tw/2':y='{y0}{below}'")
    return out


def pick(versions, source_height):
    """720p and 480p always; 1080p/1440p only when what we send is bigger than that."""
    return [v for v in versions if int(v["height"]) <= 720 or (source_height and source_height > int(v["height"]))]


def args(ffmpeg, method, src, outputs, mark_files=None, mark=None, audio=False, wallclock=True):
    """One ffmpeg: read the camera once, one output per version. outputs: [{suffix, height, fps, kbps, url}]."""
    pre, enc, fmt = encoder(method)
    graph = [f"[0:v]split={len(outputs)}" + "".join(f"[s{i}]" for i in range(len(outputs)))]
    for i, o in enumerate(outputs):
        chain = [f"scale=-2:'min({int(o['height'])},ih)'"]
        if int(o.get("fps") or 0):
            chain.append(f"fps={int(o['fps'])}")
        if mark and mark_files and o["suffix"] in (mark.get("on") or []):
            chain += _mark_filters(mark_files[0], mark_files[1], mark.get("every", 15),
                                   (len(mark.get("line1") or ""), len(mark.get("line2") or "")))
        chain.append(fmt)
        graph.append(f"[s{i}]" + ",".join(chain) + f"[o{i}]")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning"]
    if wallclock:
        cmd += ["-use_wallclock_as_timestamps", "1"]
    # The camera's stream is known (the uplink reads it all the time): probe it for 2 s, not ffmpeg's default 5,
    # so the versions reach the server before it gives up waiting (a Pi 3B took ~11 s to start with the default).
    cmd += ["-rtsp_transport", "tcp", "-analyzeduration", "2000000", "-probesize", "2000000", *pre,
            "-i", src, "-filter_complex", ";".join(graph)]
    for i, o in enumerate(outputs):
        k = int(o["kbps"])
        cmd += ["-map", f"[o{i}]"]
        cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "64k", "-ar", "48000"] if audio else []
        cmd += [*enc, "-bf", "0", "-b:v", f"{k}k", "-maxrate", f"{int(k * 1.25)}k", "-bufsize", f"{k * 2}k",
                "-force_key_frames", "expr:gte(t,n_forced*2)"]
        cmd += [] if audio else ["-an"]
        cmd += ["-f", "mpegts", o["url"]]
    return cmd


class Versions:
    """Makes the versions the website asks for, while it keeps asking."""

    def __init__(self, cfg, uplink=None, log=None):
        self.cfg, self.uplink = cfg, uplink
        cid = cfg["platform"].get("camera_id", "")
        self.log = log or logging.getLogger(f"{cid}.versions")
        self.method = None
        self.checked = False           # the GPU check has run (only once the website first asks: see want())
        self._checking = False
        self.speed = None              # ffmpeg's latest speed (x real time)
        self.slow_since = None
        self.proc = None
        self.wanted = None             # (versions, mark) as last asked
        self.keep_until = 0.0
        self.fast_fails = 0
        self.gave_up = False
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cid = cid
        self._known_method()                       # from the config only: no hardware check at startup
        if not self.checked:
            self._load_saved_check()               # ...or the check's result from before a restart or update
        threading.Thread(target=self._watch, daemon=True, name=f"versions-{cid}").start()

    # ---------- can this PC do it? ----------
    def _known_method(self):
        """What the config already says: the GPU method setup chose for converting, or "cpu" on purpose."""
        s = self.cfg["stream"]
        mode = s.get("versions", "auto")
        if mode == "off":
            self.checked = True
        elif s.get("encoder") in GPU_METHODS:
            self.method, self.checked = s["encoder"], True
        elif mode == "cpu":                        # a strong CPU, no GPU: allowed on purpose
            self.method, self.checked = "x264", True

    # ---------- the check's result, kept across restarts (so the first viewer after one isn't left waiting) ----------
    CHECK_DAYS = 30

    def _check_file(self):
        return status_path().with_name(f"versions-check-{self._cid}.json")

    def _ffmpeg_id(self):
        ffmpeg = find_ffmpeg(self.cfg["stream"].get("ffmpeg", ""))
        try:
            return f"{ffmpeg}:{int(os.path.getmtime(ffmpeg))}" if ffmpeg else ""
        except OSError:
            return str(ffmpeg or "")

    def _load_saved_check(self):
        try:
            with open(self._check_file(), encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        if (saved.get("ffmpeg") == self._ffmpeg_id() and time.time() - saved.get("at", 0) < self.CHECK_DAYS * 86400
                and (saved.get("method") in GPU_METHODS or saved.get("method") is None)):
            self.method, self.checked = saved.get("method"), True

    def _save_check(self):
        try:
            path = self._check_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"method": self.method, "ffmpeg": self._ffmpeg_id(), "at": time.time()}),
                            encoding="utf-8")
        except OSError as e:
            self.log.debug("couldn't save the versions check: %s", e)

    def _find_method(self):
        """The GPU check (a short test encode), run once, in the background, the first time the website asks.
        Hosts whose cameras never use host-made versions never run it."""
        s = self.cfg["stream"]
        try:
            ffmpeg = find_ffmpeg(s.get("ffmpeg", ""))
            if not ffmpeg:
                return
            results = convert.detect(ffmpeg, methods=[m for m in convert.candidates() if m in GPU_METHODS],
                                     size="1920x1080",         # what cameras send (YonderView takes 1080p)
                                     timeout=20)               # a 2 s clip: a working engine needs well under 10 s
            self.method = next((m for m, speed in results if speed), None)
            if self.method:
                self.log.info("this PC can make lower-quality versions (%s)", convert.LABELS.get(self.method))
            else:
                why = "; ".join(f"{convert.LABELS.get(m, m)}: {convert.LAST_ERRORS.get(m, 'no answer')}"
                                for m, _ in results) or "no video engine to try on this computer"
                self.log.warning("this PC can't make lower-quality versions, so the server makes them (%s)", why)
            self._save_check()
        except Exception as e:  # noqa: BLE001  -- no GPU method: the server makes them
            self.log.warning("the check for making lower-quality versions failed: %s", e)
        finally:
            self.checked, self._checking = True, False

    def capability(self):
        """For telemetry: may the website ask this PC for versions?"""
        self._maybe_retry()
        may = bool(self.method) or not self.checked          # not checked yet: the website may ask (that checks)
        return {"ok": may and not self.gave_up and self.cfg["stream"].get("versions", "auto") != "off",
                "method": self.method or ("unchecked" if not self.checked else None), "running": self.running(),
                "speed": self.speed, "error": self.error or None}

    def _maybe_retry(self):
        if self.gave_up and time.time() - self.gave_up > RETRY_AFTER_S:
            self.gave_up, self.fast_fails, self.error = False, 0, ""

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    # ---------- what the website asks ----------
    def want(self, versions, mark=None, keep_s=180):
        """Start (or keep making) these versions. Returns a short status for the website."""
        self._maybe_retry()
        if not self.checked and self.cfg["stream"].get("versions", "auto") != "off":
            if not self._checking:                 # first request: check the GPU now; the server covers this one
                self._checking = True
                threading.Thread(target=self._find_method, daemon=True, name=f"versions-check-{self._cid}").start()
            return {"ok": False, "checking": True, "error": "checking this PC's GPU; try again shortly"}
        if not self.method or self.gave_up:
            return {"ok": False, "error": self.error or "this PC can't make versions"}
        with self._lock:
            self.keep_until = time.time() + max(30, int(keep_s or 180))
            spec = json.dumps([versions, mark], sort_keys=True)
            if self.wanted != spec or not self.running():
                self.wanted = spec
                self._start(versions, mark)
        return {"ok": True, "running": self.running(), "method": self.method}

    def stop(self, reason="the website asked"):
        with self._lock:
            self.wanted = None
            self.keep_until = 0.0
            self._kill()
        self.log.info("versions stopped (%s)", reason)
        return {"ok": True}

    def shutdown(self):
        self._stop.set()
        self.stop("shutting down")

    # ---------- running ffmpeg ----------
    def _outputs(self, versions):
        s, p = self.cfg["stream"], self.cfg["platform"]
        sent_height = 1080 if s.get("convert", "off") != "off" else int(s.get("source_height") or 0)
        out = []
        for v in pick(versions, sent_height):
            o = dict(v)
            o["url"] = srt_url(self.cfg, path=f"{p['camera_id']}_{v['suffix']}")
            out.append(o)
        return out

    def _start(self, versions, mark):
        self._kill()
        cam, s = self.cfg["camera"], self.cfg["stream"]
        ffmpeg = find_ffmpeg(s.get("ffmpeg", ""))
        outputs = self._outputs(versions)
        if not ffmpeg or not outputs:
            self.error = "no ffmpeg" if not ffmpeg else "nothing to make"
            return
        files = None
        if mark and (mark.get("line1") or mark.get("line2")):
            files = []
            for text in (mark.get("line1"), mark.get("line2")):
                if not text:
                    files.append("")
                    continue
                fd, path = tempfile.mkstemp(prefix="camagent-mark-", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(text)
                files.append(os.path.basename(path))       # ffmpeg runs in the temp folder: no drive letters
        audio = bool(self.uplink and self.uplink.stats.get("audio") == "on")
        cmd = args(ffmpeg, self.method, rtsp_with_auth(cam["rtsp_url"], cam["username"], cam["password"]),
                   outputs, files, mark, audio, s.get("wallclock_timestamps", True))
        self.log.info("making %s with %s -> %s", ", ".join(o["suffix"] + "p" for o in outputs),
                      convert.LABELS.get(self.method, self.method), redact(outputs[0]["url"]))
        cmd[1:1] = ["-progress", "pipe:1", "-nostats"]       # its speed, to notice a PC that can't keep up
        self.started, self.speed, self.slow_since = time.time(), None, None
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, cwd=tempfile.gettempdir())
        threading.Thread(target=self._read_errors, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._read_progress, args=(self.proc,), daemon=True).start()

    def _read_progress(self, proc):
        for line in proc.stdout:
            if line.startswith("speed="):
                try:
                    self.speed = float(line.split("=", 1)[1].strip().rstrip("x"))
                except ValueError:
                    pass

    def _read_errors(self, proc):
        for line in proc.stderr:
            line = scrub(line.rstrip())
            if not line:
                continue
            if any(w in line for w in HARMLESS):         # timestamp notes ffmpeg fixes itself: not a problem
                self.log.debug("versions ffmpeg: %s", line)
                continue
            self.error = line[-200:]
            self.log.warning("versions ffmpeg: %s", line)

    def _kill(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)                         # let ffmpeg close the video engine itself: a Raspberry Pi's
                                                   # engine can hang until a reboot if killed mid-drain
            except subprocess.TimeoutExpired:
                p.kill()

    def _watch(self):
        while not self._stop.wait(2):
            with self._lock:
                if self.wanted is None:
                    continue
                if time.time() > self.keep_until:
                    self.wanted = None
                    self._kill()
                    self.log.info("versions stopped (the website stopped asking)")
                    continue
                if self.running() and time.time() - self.started > WARMUP_S and self.speed is not None:
                    if self.speed >= SLOW_SPEED:
                        self.slow_since = None
                    elif self.slow_since is None:
                        self.slow_since = time.time()
                    elif time.time() - self.slow_since > SLOW_FOR_S:
                        self.error = f"this PC is too slow for these versions ({self.speed:.2f}x real time)"
                        self.log.warning("%s; the server will make them (trying again in an hour)", self.error)
                        self.gave_up, self.wanted = time.time(), None
                        self._kill()
                        continue
                if self.proc and self.proc.poll() is not None:
                    quick = time.time() - getattr(self, "started", 0) < FAST_S
                    self.fast_fails = self.fast_fails + 1 if quick else 0
                    lite = f"{self.method}-lite"
                    if self.fast_fails >= MAX_FAST_FAILS and lite in GPU_METHODS:
                        # decoding on the GPU fails on this camera (e.g. 4K on a Raspberry Pi): decode on the CPU
                        self.log.warning("versions keep failing with %s; decoding on the CPU instead",
                                         convert.LABELS.get(self.method))
                        self.method, self.fast_fails = lite, 0
                    if self.fast_fails >= MAX_FAST_FAILS:
                        self.gave_up, self.wanted = time.time(), None
                        self.log.warning("versions keep failing (%s); the server will make them", self.error)
                        continue
                    versions, mark = json.loads(self.wanted)
                    self._start(versions, mark)
