"""The agent. Each camera gets a CameraAgent (its own uplink, broker login, and PTZ); the Supervisor runs
them side by side, so one camera's problems never stop the others."""
import collections
import json
import os
import logging
import signal
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from . import __version__
from .config import scrub
from .ptz import PTZ
from .uplink import Uplink

log = logging.getLogger("camagent")

REQUIRED = [
    ("platform", "camera_id"), ("platform", "mqtt_host"), ("platform", "mqtt_password"),
]
REQUIRED_STREAM = [
    ("camera", "rtsp_url"), ("platform", "ingest_host"), ("platform", "srt_password"),
    ("platform", "srt_passphrase"),     # 0.17: never sent unencrypted (YonderView always gives one)
]


def missing_settings(cfg):
    missing = [f"[{s}] {k}" for s, k in REQUIRED if not cfg[s].get(k)]
    if cfg["stream"].get("enabled", True):
        missing += [f"[{s}] {k}" for s, k in REQUIRED_STREAM if not cfg[s].get(k)]
    if cfg["camera"].get("ptz", True) and not cfg["camera"].get("host"):
        missing.append("[camera] host")
    return missing


MAX_COMMAND_BYTES = 64 * 1024        # the website's commands are a few hundred bytes
MAX_CONFIG_BYTES = 16 * 1024         # ...and its camera settings message less still
FUTURE_COMMAND_S = 60                # a command dated further ahead than this is refused (0.17)
SEEN_REQ_S = 300                     # a command id seen in the last 5 minutes isn't run again (a replay)
SEEN_REQ_MAX = 500
CONTROL_OPS = {"focus", "autofocus", "daynight", "aux", "home", "reboot", "sync_clock", "point", "probe"}


class CameraAgent:
    def __init__(self, cfg: dict):
        missing = missing_settings(cfg)
        if missing:
            raise ValueError("missing " + ", ".join(missing))
        self.cfg = cfg
        self.cam_id = cfg["platform"]["camera_id"]
        self.log = logging.getLogger(f"{self.cam_id}.agent")
        self.topics = {t: f"cam/{self.cam_id}/{t}" for t in ("cmd", "ack", "status", "config", "telemetry")}
        self.started = time.time()
        self.stopping = threading.Event()
        self.mqtt_connected = False
        self.server = {"max_height": None, "declined": ""}

        self.uplink = Uplink(cfg) if cfg["stream"].get("enabled", True) else None
        self.versions = None
        if self.uplink and cfg["stream"].get("versions", "auto") != "off":
            from .versions import Versions
            self.versions = Versions(cfg, self.uplink)
        self.ptz = PTZ(cfg) if cfg["camera"].get("ptz", True) else None
        self.ptz_position = None       # last reported PTZ position (pan/tilt/zoom), for the map direction
        self.position_kind = "unknown" # "real" once the reported position changes with movement; "fake" if it never does
        self.controls = self.session = None
        self.events_pending = collections.deque(maxlen=50)     # camera alarms not yet delivered
        self._sent_cmd = None
        cam = cfg["camera"]
        if self.ptz or (cam.get("host") and cam.get("username")):
            from .controls import Controls, OnvifSession
            if not self.ptz:                                    # fixed camera: ONVIF just for its controls
                self.session = OnvifSession(cfg)
            a = cfg.get("agent") or {}
            self.controls = Controls(self.ptz or self.session, light_minutes=int(a.get("light_minutes", 15)),
                                     daynight_minutes=int(a.get("daynight_minutes", 30)))
        wcfg = cfg.get("weather") or {}
        self.weather = None
        if wcfg.get("kind"):
            from .weather import Reader
            self.weather = Reader(wcfg)
            self.weather_name = wcfg.get("name") or f"{cfg['platform'].get('camera_id', '')} weather"
        self.weather_pending = collections.deque(maxlen=120)
        self.weather_last = 0.0   # readings not yet delivered (kept through short outages)

        p = cfg["platform"]
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.cam_id)
        self.mq.username_pw_set(self.cam_id, p["mqtt_password"])
        if p.get("mqtt_tls"):
            self.mq.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.mqtt_refused = ""
        if not p.get("mqtt_tls") and p.get("mqtt_host", "") not in ("localhost", "127.0.0.1", "::1"):
            # the camera's MQTT password would cross the internet unencrypted: don't connect at all
            self.mqtt_refused = (f"not connecting to {p.get('mqtt_host')} without TLS (it would send the password "
                                 "unencrypted); set mqtt_tls = true and port 8883 (camagent configure)")
        self.mq.will_set(self.topics["status"], "offline", qos=1, retain=True)
        self.mq.reconnect_delay_set(min_delay=1, max_delay=30)
        self.mq.on_connect = self._on_connect
        self.mq.on_disconnect = self._on_disconnect
        self.mq.on_message = self._on_message
        self.mq.suppress_exceptions = True    # a callback failing must never stop the MQTT connection (0.17)
        self._seen_req = collections.OrderedDict()      # command id -> when (replays are ignored)

    # ---------- MQTT ----------
    def _on_connect(self, c, userdata, flags, reason_code, properties=None):
        self.log.info("MQTT connected: %s", reason_code)
        self.mqtt_connected = not reason_code.is_failure
        if reason_code.is_failure:
            return
        c.subscribe(self.topics["cmd"], qos=1)
        c.subscribe(self.topics["config"], qos=1)
        c.publish(self.topics["status"], "online", qos=1, retain=True)
        self._publish_telemetry()

    def _on_disconnect(self, c, userdata, flags, reason_code, properties=None):
        self.mqtt_connected = False
        if not self.stopping.is_set():
            self.log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, c, userdata, msg):
        try:
            if msg.topic == self.topics["config"]:
                self._apply_config(msg.payload)
            else:
                self._on_command(c, msg)
        except Exception as e:  # noqa: BLE001 - one bad message must never stop the others (or the connection)
            self.log.warning("ignored a message that couldn't be handled: %s", scrub(str(e))[:200])

    def _replayed(self, req):
        """True if this command id was already run in the last few minutes (remembered: a bounded list)."""
        if req is None:
            return False
        seen = self.__dict__.setdefault("_seen_req", collections.OrderedDict())
        now, key = time.monotonic(), str(req)[:100]
        while seen:
            _, at = next(iter(seen.items()))
            if now - at < SEEN_REQ_S and len(seen) < SEEN_REQ_MAX:
                break
            seen.popitem(last=False)
        if key in seen:
            return True
        seen[key] = now
        return False

    def _on_command(self, c, msg):
        req = None
        try:
            if len(msg.payload) > MAX_COMMAND_BYTES:
                raise RuntimeError("command too big; ignored")
            cmd = json.loads(msg.payload)
            if not isinstance(cmd, dict):
                raise RuntimeError("not a command; ignored")
            req = cmd.get("req")
            if req is not None and not isinstance(req, (str, int)):
                req = None
                raise RuntimeError("bad command id; ignored")
            ts = cmd.get("ts")
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                raise RuntimeError("command without a time; ignored")      # the website always sends one
            age = time.time() - float(ts)
            if age > float(self.cfg["agent"].get("stale_command_s", 5)):
                raise RuntimeError("command too old; ignored")
            # a little "from the future" is fine (this PC's clock may be behind); a lot isn't (0.17: a command
            # saved and replayed later would otherwise pass the age check)
            if age < -FUTURE_COMMAND_S:
                raise RuntimeError("command dated in the future (check this computer's clock); ignored")
            if self._replayed(req):
                self.log.info("command %s already done; ignored a repeat", str(req)[:40])
                return
            result = self._handle(cmd)
            if cmd.get("op") != "move":
                self.log.info("command: %s", cmd.get("op"))
            c.publish(self.topics["ack"], json.dumps({"req": req, "ok": True, "result": result}), qos=1)
        except Exception as e:
            self.log.warning("command failed: %s", e)
            c.publish(self.topics["ack"], json.dumps({"req": req, "ok": False, "error": scrub(str(e))}), qos=1)

    def _apply_config(self, payload):
        try:
            if len(payload or b"") > MAX_CONFIG_BYTES:
                raise ValueError("too big")
            new = json.loads(payload or b"{}")
            if not isinstance(new, dict):
                raise ValueError("not a settings message")
        except ValueError as e:
            self.log.warning("ignored bad config message (%s)", e)
            return
        try:
            self._use_config(new)
        except Exception as e:  # noqa: BLE001
            self.log.warning("ignored bad config message: %s", scrub(str(e))[:200])

    def _use_config(self, new):
        if self.ptz:
            self.ptz.settings.update({k: bool(new.get(k, False)) for k in ("invert_pan", "invert_tilt")})
            self.log.info("camera settings: %s", self.ptz.settings)
        max_height = new.get("max_height")
        self.server = {"max_height": max_height if isinstance(max_height, int) and not isinstance(max_height, bool)
                       else None, "declined": str(new.get("declined") or "")[:500]}
        try:
            latency = int(new.get("latency_ms") or 0)
        except (TypeError, ValueError):
            latency = 0
        if latency and self.uplink:
            latency = max(120, min(8000, latency))
            if latency != int(self.cfg["stream"].get("srt_latency_ms", 400)):
                self.cfg["stream"]["srt_latency_ms"] = latency       # set on the camera's page on yonderview.net
                self.uplink.restart_now(f"latency now {latency} ms")
        if self.server["declined"]:
            self.log.error("YonderView declined this camera's video: %s", self.server["declined"])

    def _handle(self, cmd):
        op = cmd.get("op")
        if op == "info":
            return self._info()
        if op in CONTROL_OPS:
            return self._control(op, cmd)
        if op == "versions":                    # the website wants lower-quality versions made here (or not)
            if not self.versions:
                return {"ok": False, "error": "versions are off on this PC"}
            if cmd.get("on"):
                return self.versions.want(cmd.get("versions") or [], cmd.get("mark"), cmd.get("keep_s", 180))
            return self.versions.stop()
        if not self.ptz:
            raise RuntimeError("PTZ is disabled for this camera")
        if op == "move":
            return self.ptz.move(cmd.get("pan", 0), cmd.get("tilt", 0), cmd.get("zoom", 0), cmd.get("ms", 400))
        if op == "stop":
            return self.ptz.stop()
        if op == "preset":
            return self.ptz.goto_preset(cmd["id"])
        if op == "presets":
            return self.ptz.presets()
        if op == "position":
            return self.position_now()
        if op == "preset_record":
            return {"token": self.ptz.record_preset(cmd.get("name", "Preset"), cmd.get("id") or None)}
        if op == "preset_delete":
            return self.ptz.delete_preset(cmd["id"])
        raise ValueError(f"unknown op: {op}")

    def _control(self, op, cmd):
        c = self.controls
        if not c:
            raise RuntimeError("this camera has no ONVIF controls set up")
        if op == "focus":
            return c.focus(cmd.get("dir", "stop"), cmd.get("ms", 600))
        if op == "autofocus":
            return c.autofocus()
        if op == "daynight":
            return c.daynight(cmd.get("mode", "auto"), keep=bool(cmd.get("keep")))
        if op == "aux":
            return c.aux(str(cmd.get("command", "")), keep=bool(cmd.get("keep")))
        if op == "home":
            return c.home()
        if op == "reboot":
            return c.reboot()
        if op == "sync_clock":
            r = c.sync_clock()
            c.probe()
            self._publish_telemetry()
            return r
        if op == "point":
            return c.point(cmd.get("pan", 0), cmd.get("tilt"), cmd.get("zoom"))
        if op == "probe":
            caps = c.probe()
            self._publish_telemetry()
            return {"ok": True, "caps": caps}
        raise ValueError(f"unknown op: {op}")

    def watch_camera(self, stop):
        """Once the camera answers over ONVIF: find out what it supports (again every 6 hours), and listen for
        its own alarms (covered, moved, out of focus) for as long as the agent runs."""
        target = self.ptz or self.session
        while not stop.is_set() and not target.connected:
            stop.wait(5)
        if stop.is_set():
            return
        threading.Thread(target=self.controls.watch_events, args=(stop, self._on_camera_event), daemon=True,
                         name=f"events-{self.cam_id}").start()
        while not stop.is_set():
            try:
                self.controls.probe()
                self._publish_telemetry()
            except Exception as e:  # noqa: BLE001
                self.log.info("camera probe failed: %s", e)
            stop.wait(6 * 3600)

    def _on_camera_event(self, event):
        self.log.warning("camera alarm: %s", event.get("topic"))
        self.events_pending.append(event)
        self._publish_telemetry()

    # ---------- status ----------
    def _info(self):
        return {
            "version": __version__,
            "uptime_s": int(time.time() - self.started),
            "ptz": {"enabled": bool(self.ptz),
                    "available": bool(self.ptz and self.ptz.available),
                    "connected": bool(self.ptz and self.ptz.connected),
                    "position": self.ptz_position},
            "caps": dict(self.controls.caps, position=self.position_kind) if self.controls and self.controls.caps
                    else None,
            **({"events": list(self.events_pending)} if self.events_pending else {}),
            "uplink": dict(self.uplink.stats, fps_configured=self.cfg["camera"].get("fps_configured") or None)
                      if self.uplink else None,
            "versions": self.versions.capability() if self.versions else {"ok": False},
            **({"weather": {"station": {"kind": self.weather.kind, "uid": self.weather.uid, "name": self.weather_name},
                            "readings": list(self.weather_pending)}}
               if self.weather and self.weather_pending and self.weather.uid else {}),
        }

    POSITION_BUSY_S = 2      # how often to ask the camera where it points while it's steered from YonderView
    POSITION_IDLE_S = 5      # ...and otherwise (it may be moved from its own app or web page, which we don't see)

    def track_position(self, stop):
        """Report where the PTZ camera points: every 2 s while it's being moved (and for 20 s after), every
        5 s otherwise. Telemetry goes out only when the position actually changes, so the map cone follows."""
        baseline, moves_then = None, 0
        while not stop.is_set():
            busy = self.ptz and time.time() - self.ptz.last_activity < 20
            pos = self.ptz.position() if self.ptz else None
            commanded = getattr(self.ptz, "commanded", None) if self.ptz else None
            commanded = commanded if isinstance(commanded, dict) else None
            if self.position_kind == "fake" and commanded:
                if commanded is not self._sent_cmd:
                    self._sent_cmd = commanded
                    self.ptz_position = dict(commanded, commanded=True)
                    self._publish_telemetry()
                stop.wait(self.POSITION_BUSY_S if busy else self.POSITION_IDLE_S)
                continue
            if (self.position_kind == "unknown" and self.ptz and getattr(self.ptz, "reports_position", None) is False):
                self.position_kind = "fake"           # the camera says so itself: no need to wait for moves
                self.log.info("this camera says it doesn't report its position: map direction will need preset "
                              "directions")
                self._publish_telemetry()
                continue
            if pos is not None:
                # Real or placeholder? Some cameras answer the position request with a fixed value.
                if baseline is None:
                    baseline, moves_then = pos, self.ptz.moves
                elif self.position_kind != "real":
                    changed = any(abs((pos.get(k) or 0) - (baseline.get(k) or 0)) > 0.01 for k in ("pan", "tilt", "zoom"))
                    if changed:
                        self.position_kind = "real"           # sent with the position update just below
                    elif (self.ptz.moves - moves_then >= 3 and time.time() - self.ptz.last_activity > 3
                          and self.position_kind != "fake"):
                        self.position_kind = "fake"
                        self.log.info("this camera reports a fixed position while moving: map direction will need "
                                      "preset directions")
                        self._publish_telemetry()
                self._note_position(pos)
            stop.wait(self.POSITION_BUSY_S if busy else self.POSITION_IDLE_S)

    def _note_position(self, pos):
        """Keep a fresh reading; send it to the website only if the camera moved."""
        old = self.ptz_position or {}
        moved = any(abs((pos.get(k) or 0) - (old.get(k) or 0)) > 0.002 for k in ("pan", "tilt", "zoom"))
        if moved or not old:
            self.ptz_position = dict(pos, t=int(time.time()))
            self._publish_telemetry()

    def position_now(self):
        """Where the camera points right now, asked of the camera this moment (the website calls this before it
        saves a map direction, so a reading from before the last move is never used)."""
        if self.position_kind == "fake":
            commanded = getattr(self.ptz, "commanded", None)
            if not isinstance(commanded, dict):
                raise RuntimeError("this camera doesn't report where it points")
            return dict(commanded, commanded=True)
        pos = self.ptz.position()
        if pos is None:
            raise RuntimeError("the camera didn't say where it points")
        self._note_position(pos)
        return dict(pos, fresh=True)

    def _publish_telemetry(self):
        """True if it went out (pending weather readings are then cleared)."""
        try:
            sent, sent_events = len(self.weather_pending), len(self.events_pending)
            info = self.mq.publish(self.topics["telemetry"], json.dumps(self._info()), qos=1)
            if self.mqtt_connected and info.rc == 0:
                for _ in range(sent):                    # only those included; newer ones wait for next time
                    if self.weather_pending:
                        self.weather_pending.popleft()
                for _ in range(sent_events):
                    if self.events_pending:
                        self.events_pending.popleft()
                return True
        except Exception as e:
            self.log.debug("telemetry publish failed: %s", e)
        return False

    def track_weather(self, stop):
        """Read the local weather station and send each reading with the telemetry."""
        w = self.weather
        while not stop.is_set():
            try:
                w.station_id()
                r = w.read()
                if r:
                    self.weather_last = time.time()
                    self.weather_pending.append({k: v for k, v in r.items() if v is not None})
                    self._publish_telemetry()
                    if w.kind != "tempest":          # Tempest paces itself (one observation a minute)
                        stop.wait(w.interval)
            except Exception as e:  # noqa: BLE001
                self.log.warning("weather station not readable: %s; retrying in 60s", e)
                stop.wait(60)
        w.close()

    def status(self):
        """For `camagent list`: a short snapshot of this camera."""
        up = self.uplink.stats if self.uplink else None
        return {
            "mqtt": self.mqtt_connected,
            "streaming": bool(up and up["running"] and up["fps"] > 0),
            "fps": up["fps"] if up else None,
            "bitrate_kbps": up["bitrate_kbps"] if up else None,
            "restarts": up["restarts"] if up else None,
            "last_error": (up or {}).get("last_error", ""),
            "ptz": None if not self.ptz else ("connected" if self.ptz.connected else
                                              "unavailable" if not self.ptz.available else "connecting"),
            "declined": self.server.get("declined", ""),
            "position": self.position_kind if self.ptz else None,
            "weather": None if not self.weather else {
                "kind": self.weather.kind, "station": self.weather.uid,
                "age_s": int(time.time() - self.weather_last) if self.weather_last else None,
                "waiting": len(self.weather_pending)},
        }

    # ---------- lifecycle ----------
    def start(self):
        self.log.info("starting")
        if self.uplink:
            self.uplink.start()
        if self.weather:
            threading.Thread(target=self.track_weather, args=(self.stopping,), daemon=True,
                             name=f"weather-{self.cam_id}").start()
        if self.session:
            self.session.start()
        if self.controls:
            threading.Thread(target=self.watch_camera, args=(self.stopping,), daemon=True,
                             name=f"camera-{self.cam_id}").start()
        if self.ptz:
            self.ptz.start()
            threading.Thread(target=self.track_position, args=(self.stopping,), daemon=True,
                             name=f"ptzpos-{self.cam_id}").start()
        p = self.cfg["platform"]
        if self.mqtt_refused:
            self.log.error("MQTT: %s. Video still goes out; camera controls and status don't.", self.mqtt_refused)
            return
        self.mq.connect_async(p["mqtt_host"], int(p["mqtt_port"]), keepalive=20)   # notice a dead link fast
        self.mq.loop_start()

    def shutdown(self):
        self.stopping.set()
        if self.versions:
            self.versions.shutdown()
        if self.controls:
            self.controls.shutdown()
        if self.session:
            self.session.shutdown()
        if self.ptz:
            self.ptz.shutdown()
        if self.uplink:
            self.uplink.stop()
        try:
            self.mq.publish(self.topics["status"], "offline", qos=1, retain=True).wait_for_publish(3)
        except Exception:
            pass
        self.mq.disconnect()
        self.mq.loop_stop()


class Supervisor:
    """Runs every configured camera. A camera with a broken config is reported and skipped."""

    def __init__(self, main_path=None):
        from . import config
        self.main_path = main_path
        self.main, cams, problems = config.load_all(main_path)
        for p in problems:
            log.error("skipping %s", p)
        self.agents = []
        for cfg in cams:
            cid = cfg["platform"].get("camera_id", "?")
            try:
                self.agents.append(CameraAgent(cfg))
            except ValueError as e:
                log.error("camera '%s' not started: %s (fix with: camagent configure %s)", cid, e, cid)
        if not self.agents:
            raise SystemExit("No cameras are configured. Add one with: camagent add")
        self.stopping = threading.Event()
        self.started = time.time()

    def _write_status(self):
        from . import config
        path = config.status_path()
        data = {"pid": os.getpid(), "version": __version__, "updated": time.time(),
                "started": self.started, "cameras": {a.cam_id: a.status() for a in self.agents}}
        text = json.dumps(data)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                # Windows (0.17): the service's own account may change status.json but not make files beside it
                with open(path, "r+", encoding="utf-8") as f:
                    f.write(text)
                    f.truncate()
            except OSError as e:
                log.debug("couldn't write %s: %s", path, e)

    def run(self):
        ids = ", ".join(a.cam_id for a in self.agents)
        log.info("camagent %s starting %d camera(s): %s", __version__, len(self.agents), ids)
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
            if sig is not None:
                try:
                    signal.signal(sig, lambda *_: self.stopping.set())
                except (ValueError, OSError):
                    pass
        for a in self.agents:
            a.start()

        interval = int(self.main["agent"].get("telemetry_interval_s", 30))
        status_every = min(interval, 10)
        last_telemetry = time.time()
        self._write_status()
        try:
            while not self.stopping.wait(status_every):
                self._write_status()
                if time.time() - last_telemetry >= interval:
                    for a in self.agents:
                        a._publish_telemetry()
                    last_telemetry = time.time()
        except KeyboardInterrupt:
            pass
        log.info("shutting down")
        threads = [threading.Thread(target=a.shutdown, daemon=True) for a in self.agents]
        for t in threads:
            t.start()
        deadline = time.time() + 15                # everyone together, not 10 s each
        for t in threads:
            t.join(max(0.1, deadline - time.time()))
        if any(t.is_alive() for t in threads) or any(t.is_alive() and not t.daemon for t in threading.enumerate()
                                                    if t is not threading.current_thread()):
            # something is still waiting (e.g. ffmpeg stuck in a Raspberry Pi's video driver can't be stopped):
            # leave anyway, so a restart never hangs on us; systemd cleans up what's left
            log.warning("some parts didn't stop in time; exiting anyway")
            logging.shutdown()
            os._exit(0)
