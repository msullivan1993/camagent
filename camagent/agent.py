"""The agent: connects to the broker, runs the uplink, handles PTZ commands, publishes status and telemetry."""
import json
import logging
import signal
import ssl
import threading
import time

import paho.mqtt.client as mqtt

from . import __version__
from .ptz import PTZ
from .uplink import Uplink

log = logging.getLogger("agent")

REQUIRED = [
    ("platform", "camera_id"), ("platform", "mqtt_host"), ("platform", "mqtt_password"),
]
REQUIRED_STREAM = [
    ("camera", "rtsp_url"), ("platform", "ingest_host"), ("platform", "srt_password"),
]


def check_config(cfg):
    missing = [f"[{s}] {k}" for s, k in REQUIRED if not cfg[s].get(k)]
    if cfg["stream"].get("enabled", True):
        missing += [f"[{s}] {k}" for s, k in REQUIRED_STREAM if not cfg[s].get(k)]
    if cfg["camera"].get("ptz", True) and not cfg["camera"].get("host"):
        missing.append("[camera] host")
    if missing:
        raise SystemExit("Config is missing: " + ", ".join(missing) + "\nRun: camagent configure")


class Agent:
    def __init__(self, cfg: dict):
        check_config(cfg)
        self.cfg = cfg
        self.cam_id = cfg["platform"]["camera_id"]
        self.topics = {t: f"cam/{self.cam_id}/{t}" for t in ("cmd", "ack", "status", "config", "telemetry")}
        self.started = time.time()
        self.stopping = threading.Event()

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
        log.info("MQTT connected: %s", reason_code)
        if reason_code.is_failure:
            return
        c.subscribe(self.topics["cmd"], qos=1)
        c.subscribe(self.topics["config"], qos=1)
        c.publish(self.topics["status"], "online", qos=1, retain=True)
        self._publish_telemetry()

    def _on_disconnect(self, c, userdata, flags, reason_code, properties=None):
        if not self.stopping.is_set():
            log.warning("MQTT disconnected: %s", reason_code)

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
                log.info("command: %s", cmd.get("op"))
            c.publish(self.topics["ack"], json.dumps({"req": req, "ok": True, "result": result}), qos=1)
        except Exception as e:
            log.warning("command failed: %s", e)
            c.publish(self.topics["ack"], json.dumps({"req": req, "ok": False, "error": str(e)}), qos=1)

    def _apply_config(self, payload):
        try:
            new = json.loads(payload or b"{}")
        except ValueError:
            log.warning("ignored bad config message")
            return
        if self.ptz:
            self.ptz.settings.update({k: bool(new.get(k, False)) for k in ("invert_pan", "invert_tilt")})
            log.info("camera settings: %s", self.ptz.settings)

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
        raise ValueError(f"unknown op: {op}")

    # ---------- status ----------
    def _info(self):
        return {
            "version": __version__,
            "uptime_s": int(time.time() - self.started),
            "ptz": {"enabled": bool(self.ptz),
                    "available": bool(self.ptz and self.ptz.available),
                    "connected": bool(self.ptz and self.ptz.connected)},
            "uplink": dict(self.uplink.stats) if self.uplink else None,
        }

    def _publish_telemetry(self):
        try:
            self.mq.publish(self.topics["telemetry"], json.dumps(self._info()), qos=0)
        except Exception as e:
            log.debug("telemetry publish failed: %s", e)

    # ---------- lifecycle ----------
    def run(self):
        log.info("camagent %s starting for camera '%s'", __version__, self.cam_id)
        for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
            if sig is not None:
                try:
                    signal.signal(sig, lambda *_: self.stopping.set())
                except (ValueError, OSError):
                    pass

        if self.uplink:
            self.uplink.start()
        if self.ptz:
            self.ptz.start()

        p = self.cfg["platform"]
        self.mq.connect_async(p["mqtt_host"], int(p["mqtt_port"]), keepalive=30)
        self.mq.loop_start()

        interval = int(self.cfg["agent"].get("telemetry_interval_s", 30))
        try:
            while not self.stopping.wait(interval):
                self._publish_telemetry()
        except KeyboardInterrupt:
            pass
        self.shutdown()

    def shutdown(self):
        log.info("shutting down")
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
