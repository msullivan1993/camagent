"""The agent. Each camera gets a CameraAgent (its own uplink, broker login, and PTZ); the Supervisor runs
them side by side, so one camera's problems never stop the others."""
import json
import os
import logging
import signal
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from . import __version__
from .ptz import PTZ
from .uplink import Uplink

log = logging.getLogger("camagent")

REQUIRED = [
    ("platform", "camera_id"), ("platform", "mqtt_host"), ("platform", "mqtt_password"),
]
REQUIRED_STREAM = [
    ("camera", "rtsp_url"), ("platform", "ingest_host"), ("platform", "srt_password"),
]


def missing_settings(cfg):
    missing = [f"[{s}] {k}" for s, k in REQUIRED if not cfg[s].get(k)]
    if cfg["stream"].get("enabled", True):
        missing += [f"[{s}] {k}" for s, k in REQUIRED_STREAM if not cfg[s].get(k)]
    if cfg["camera"].get("ptz", True) and not cfg["camera"].get("host"):
        missing.append("[camera] host")
    return missing


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
        self.ptz = PTZ(cfg) if cfg["camera"].get("ptz", True) else None

        p = cfg["platform"]
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.cam_id)
        self.mq.username_pw_set(self.cam_id, p["mqtt_password"])
        if p.get("mqtt_tls"):
            self.mq.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.mq.will_set(self.topics["status"], "offline", qos=1, retain=True)
        self.mq.reconnect_delay_set(min_delay=1, max_delay=30)
        self.mq.on_connect = self._on_connect
        self.mq.on_disconnect = self._on_disconnect
        self.mq.on_message = self._on_message

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
        if msg.topic == self.topics["config"]:
            self._apply_config(msg.payload)
            return
        req = None
        try:
            cmd = json.loads(msg.payload)
            req = cmd.get("req")
            ts = cmd.get("ts")
            if ts and time.time() - float(ts) > float(self.cfg["agent"].get("stale_command_s", 5)):
                raise RuntimeError("command too old; ignored")
            result = self._handle(cmd)
            if cmd.get("op") != "move":
                self.log.info("command: %s", cmd.get("op"))
            c.publish(self.topics["ack"], json.dumps({"req": req, "ok": True, "result": result}), qos=1)
        except Exception as e:
            self.log.warning("command failed: %s", e)
            c.publish(self.topics["ack"], json.dumps({"req": req, "ok": False, "error": str(e)}), qos=1)

    def _apply_config(self, payload):
        try:
            new = json.loads(payload or b"{}")
        except ValueError:
            self.log.warning("ignored bad config message")
            return
        if self.ptz:
            self.ptz.settings.update({k: bool(new.get(k, False)) for k in ("invert_pan", "invert_tilt")})
            self.log.info("camera settings: %s", self.ptz.settings)
        self.server = {"max_height": new.get("max_height"), "declined": new.get("declined") or ""}
        if self.server["declined"]:
            self.log.error("YonderView declined this camera's video: %s", self.server["declined"])

    def _handle(self, cmd):
        op = cmd.get("op")
        if op == "info":
            return self._info()
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
        if op == "preset_record":
            return {"token": self.ptz.record_preset(cmd.get("name", "Preset"), cmd.get("id") or None)}
        if op == "preset_delete":
            return self.ptz.delete_preset(cmd["id"])
        raise ValueError(f"unknown op: {op}")

    # ---------- status ----------
    def _info(self):
        return {
            "version": __version__,
            "uptime_s": int(time.time() - self.started),
            "ptz": {"enabled": bool(self.ptz),
                    "available": bool(self.ptz and self.ptz.available),
                    "connected": bool(self.ptz and self.ptz.connected)},
            "uplink": dict(self.uplink.stats, fps_configured=self.cfg["camera"].get("fps_configured") or None)
                      if self.uplink else None,
        }

    def _publish_telemetry(self):
        try:
            self.mq.publish(self.topics["telemetry"], json.dumps(self._info()), qos=0)
        except Exception as e:
            self.log.debug("telemetry publish failed: %s", e)

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
        }

    # ---------- lifecycle ----------
    def start(self):
        self.log.info("starting")
        if self.uplink:
            self.uplink.start()
        if self.ptz:
            self.ptz.start()
        p = self.cfg["platform"]
        self.mq.connect_async(p["mqtt_host"], int(p["mqtt_port"]), keepalive=30)
        self.mq.loop_start()

    def shutdown(self):
        self.stopping.set()
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
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
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
        for t in threads:
            t.join(10)
