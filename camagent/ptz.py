"""ONVIF PTZ control: connects in the background, enforces a local stop timer, applies invert settings."""
import logging
import threading

from .camera import friendly_error, onvif_connect

log = logging.getLogger("ptz")


def _clamp(v):
    return max(-1.0, min(1.0, float(v)))


class PTZ:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.max_move_ms = int(cfg["agent"].get("max_move_ms", 2000))
        self.settings = {"invert_pan": False, "invert_tilt": False}
        self.connected = False
        self.available = True          # becomes False if the camera has no PTZ profile
        self.ptz = None
        self.token = None
        self._lock = threading.RLock()
        self._timer = None
        self._current = None
        self._gen = 0
        self._stop = threading.Event()
        self._connecting = threading.Lock()

    # ---------- connection ----------
    def start(self):
        threading.Thread(target=self._connect_loop, name="ptz-connect", daemon=True).start()

    def _connect_loop(self):
        if not self._connecting.acquire(blocking=False):
            return                      # a connect loop is already running
        try:
            while not self._stop.is_set() and not self.connected and self.available:
                try:
                    self._connect()
                except Exception as e:
                    log.warning("camera not reachable over ONVIF: %s; retrying in 15s", friendly_error(e))
                    self._stop.wait(15)
        finally:
            self._connecting.release()

    def _connect(self):
        c = self.cfg["camera"]
        cam = onvif_connect(c["host"], c["onvif_port"], c["username"], c["password"])
        media = cam.create_media_service()
        ptz = cam.create_ptz_service()
        profiles = media.GetProfiles()
        wanted = c.get("ptz_profile", "")
        chosen = None
        for p in profiles:
            if wanted and p.token == wanted:
                chosen = p
                break
            if not wanted and getattr(p, "PTZConfiguration", None):
                chosen = p
                break
        if not chosen:
            self.available = False
            log.warning("no ONVIF profile with PTZ found; PTZ disabled for this camera")
            return
        with self._lock:
            self.ptz, self.token, self.connected = ptz, chosen.token, True
        log.info("PTZ ready on profile %s (%s)", getattr(chosen, "Name", ""), chosen.token)

    def _lost(self, err):
        try:
            from zeep.exceptions import Fault
            if isinstance(err, Fault):      # the camera answered with an error: still connected
                return
        except ImportError:
            pass
        log.warning("PTZ call failed (%s); reconnecting", err)
        with self._lock:
            self.connected = False
        self.start()

    def _require(self):
        if not self.available:
            raise RuntimeError("this camera has no PTZ")
        if not self.connected:
            raise RuntimeError("camera not connected")

    # ---------- commands ----------
    def stop(self):
        with self._lock:
            self._gen += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._current = None
            if not self.connected:
                return
            try:
                self.ptz.Stop({"ProfileToken": self.token, "PanTilt": True, "Zoom": True})
            except Exception as e:
                self._lost(e)
                raise

    def _expire(self, gen):
        with self._lock:
            if gen == self._gen:
                try:
                    self.stop()
                except Exception:
                    pass

    def move(self, pan, tilt, zoom, ms):
        self._require()
        pan, tilt = float(pan), float(tilt)
        if self.settings.get("invert_pan"):
            pan = -pan
        if self.settings.get("invert_tilt"):
            tilt = -tilt
        v = (_clamp(pan), _clamp(tilt), _clamp(zoom))
        if v == (0.0, 0.0, 0.0):
            return self.stop()
        with self._lock:
            self._gen += 1
            if self._timer:
                self._timer.cancel()
            if v != self._current:          # only talk to the camera when the motion changes
                velocity = {}
                if v[0] or v[1]:
                    velocity["PanTilt"] = {"x": v[0], "y": v[1]}
                if v[2]:
                    velocity["Zoom"] = {"x": v[2]}
                try:
                    self.ptz.ContinuousMove({"ProfileToken": self.token, "Velocity": velocity})
                except Exception as e:
                    self._current = None
                    self._lost(e)
                    raise
                self._current = v
            # local safety stop, no matter what happens upstream
            self._timer = threading.Timer(min(int(ms), self.max_move_ms) / 1000, self._expire, args=(self._gen,))
            self._timer.daemon = True
            self._timer.start()

    def goto_preset(self, preset):
        self._require()
        try:
            self.ptz.GotoPreset({"ProfileToken": self.token, "PresetToken": str(preset)})
        except Exception as e:
            self._lost(e)
            raise

    def presets(self):
        self._require()
        try:
            return [{"token": p.token, "name": getattr(p, "Name", "") or p.token}
                    for p in self.ptz.GetPresets({"ProfileToken": self.token}) or []]
        except Exception as e:
            self._lost(e)
            raise

    def shutdown(self):
        self._stop.set()
        try:
            self.stop()
        except Exception:
            pass
