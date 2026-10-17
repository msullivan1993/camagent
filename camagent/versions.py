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
from .config import redact, rtsp_with_auth, scrub, srt_url

GPU_METHODS = ("qsv", "qsv-lite", "vaapi", "vaapi-lite", "nvenc", "nvenc-lite", "amf")
MAX_FAST_FAILS = 3          # ffmpeg dying this many times within FAST_S of starting: give up (the server takes over)
FAST_S = 20


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
    cmd += ["-rtsp_transport", "tcp", *pre, "-i", src, "-filter_complex", ";".join(graph)]
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
        self.checked = False
        self.proc = None
        self.wanted = None             # (versions, mark) as last asked
        self.keep_until = 0.0
        self.fast_fails = 0
        self.gave_up = False
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._find_method, daemon=True, name=f"versions-check-{cid}").start()
        threading.Thread(target=self._watch, daemon=True, name=f"versions-{cid}").start()

    # ---------- can this PC do it? ----------
    def _find_method(self):
        """A GPU method: the one setup chose for converting, else a quick hardware check (in the background)."""
        s = self.cfg["stream"]
        try:
            mode = s.get("versions", "auto")
            if mode == "off":
                return
            if s.get("encoder") in GPU_METHODS:
                self.method = s["encoder"]
                return
            if mode == "cpu":                      # a strong CPU, no GPU: allowed on purpose
                self.method = "x264"
                return
            ffmpeg = find_ffmpeg(s.get("ffmpeg", ""))
            if not ffmpeg:
                return
            results = convert.detect(ffmpeg, methods=[m for m in convert.candidates() if m in GPU_METHODS])
            self.method = next((m for m, speed in results if speed), None)
            if self.method:
                self.log.info("this PC can make lower-quality versions (%s)", convert.LABELS.get(self.method))
        except Exception as e:  # noqa: BLE001  -- no GPU method: the server makes them
            self.log.debug("versions check failed: %s", e)
        finally:
            self.checked = True

    def capability(self):
        """For telemetry: may the website ask this PC for versions?"""
        if self.gave_up and time.time() - self.gave_up > 3600:
            self.gave_up, self.fast_fails = False, 0
        return {"ok": bool(self.method) and not self.gave_up and self.cfg["stream"].get("versions", "auto") != "off",
                "method": self.method, "running": self.running(), "error": self.error or None}

    def running(self):
        return bool(self.proc and self.proc.poll() is None)

    # ---------- what the website asks ----------
    def want(self, versions, mark=None, keep_s=180):
        """Start (or keep making) these versions. Returns a short status for the website."""
        if self.gave_up and time.time() - self.gave_up > 3600:     # try again an hour later
            self.gave_up, self.fast_fails = False, 0
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
        self.started = time.time()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, text=True, cwd=tempfile.gettempdir())
        threading.Thread(target=self._read_errors, args=(self.proc,), daemon=True).start()

    def _read_errors(self, proc):
        for line in proc.stderr:
            line = scrub(line.rstrip())
            if line:
                self.error = line[-200:]
                self.log.warning("versions ffmpeg: %s", line)

    def _kill(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
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
                if self.proc and self.proc.poll() is not None:
                    quick = time.time() - getattr(self, "started", 0) < FAST_S
                    self.fast_fails = self.fast_fails + 1 if quick else 0
                    if self.fast_fails >= MAX_FAST_FAILS:
                        self.gave_up, self.wanted = time.time(), None
                        self.log.warning("versions keep failing (%s); the server will make them", self.error)
                        continue
                    versions, mark = json.loads(self.wanted)
                    self._start(versions, mark)
