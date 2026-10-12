"""Camera controls beyond pan/tilt/zoom, over ONVIF: focus, day/night, auxiliary commands (wiper, washer, lights,
IR, heater), home position and reboot. Plus the probe that finds out which of these a camera really has.

Safety rails live here, so they hold even if the website connection drops: lights switch themselves off,
forced day/night returns to automatic, and the wiper and washer have cooldowns."""
import datetime
import logging
import threading
import time

LIGHT_WORDS = ("light", "ir", "illuminat", "lamp", "led")
COOLDOWN_S = {"wiper": 5, "washer": 300}


def aux_kind(command):
    c = command.lower()
    if "wiper" in c:
        return "wiper"
    if "wash" in c:
        return "washer"
    if any(w in c for w in ("heat", "defog", "defrost")):
        return "heater"
    if any(w in c.split("|")[0].replace("tt:", "") for w in LIGHT_WORDS):
        return "light"
    return "other"


def is_on(command):
    return command.lower().endswith(("|on", ":on")) or command.lower().endswith(" on")


def off_for(command, available):
    """The matching "off" command for an "on" one (e.g. tt:IRLamp|On -> tt:IRLamp|Off), if the camera has it."""
    for sep in ("|", ":"):
        if sep in command:
            base = command.rsplit(sep, 1)[0]
            for cand in available:
                if cand.lower() == f"{base}{sep}off".lower():
                    return cand
    return None


class Controls:
    def __init__(self, ptz, light_minutes=15, daynight_minutes=30, log=None):
        self.p = ptz                                   # the connected PTZ helper (its ONVIF session and tokens)
        self.light_s = light_minutes * 60
        self.daynight_s = daynight_minutes * 60
        self.log = log or logging.getLogger("camagent.controls")
        self.caps = {}
        self._last = {}
        self._timers = {}
        self._focus_timer = None

    # ---------- helpers ----------
    def _imaging(self):
        if not (self.p.onvif and self.p.source):
            raise RuntimeError("this camera has no imaging controls")
        return self.p.onvif.create_imaging_service()

    def _timer(self, key, seconds, fn):
        old = self._timers.pop(key, None)
        if old:
            old.cancel()
        if seconds:
            t = threading.Timer(seconds, fn)
            t.daemon = True
            self._timers[key] = t
            t.start()

    # ---------- probe ----------
    def probe(self):
        """What the camera says it supports. Nothing here moves the camera or switches anything on."""
        cam, caps = self.p.onvif, {"probed": int(time.time())}
        if not cam:
            return caps
        try:
            dev = cam.create_devicemgmt_service()
            info = dev.GetDeviceInformation()
            caps["device"] = {"manufacturer": getattr(info, "Manufacturer", ""), "model": getattr(info, "Model", ""),
                              "firmware": getattr(info, "FirmwareVersion", "")}
            caps["reboot"] = True
            try:
                st = dev.GetSystemDateAndTime()
                u = st.UTCDateTime
                cam_t = datetime.datetime(u.Date.Year, u.Date.Month, u.Date.Day, u.Time.Hour, u.Time.Minute,
                                          u.Time.Second, tzinfo=datetime.timezone.utc)
                drift = int((cam_t - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
                caps["clock_drift_s"] = drift
                # Some firmware works out "UTC" as local time minus the standard offset, forgetting daylight
                # saving: the camera's clock is right, only its report is an hour off.
                tz = str(getattr(getattr(st, "TimeZone", None), "TZ", "") or "")
                dst = bool(getattr(st, "DaylightSavings", False)) or "DST" in tz.upper() or "," in tz
                if abs(abs(drift) - 3600) < 120 and (dst or time.localtime().tm_isdst):
                    caps["clock_note"] = "dst"
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            self.log.info("device information unavailable: %s", e)
        if self.p.ptz:
            try:
                node = self.p.ptz.GetNodes()[0]
                spaces = getattr(node, "SupportedPTZSpaces", None)
                caps["ptz"] = {"home": bool(getattr(node, "HomeSupported", False)),
                               "presets": int(getattr(node, "MaximumNumberOfPresets", 0) or 0),
                               "absolute": bool(spaces and getattr(spaces, "AbsolutePanTiltPositionSpace", None))}
                caps["aux"] = [str(a) for a in (getattr(node, "AuxiliaryCommands", None) or [])]
            except Exception as e:  # noqa: BLE001
                self.log.info("PTZ node details unavailable: %s", e)
        if self.p.source:
            try:
                img = cam.create_imaging_service()
                opts = img.GetOptions({"VideoSourceToken": self.p.source})
                focus = getattr(opts, "Focus", None)
                modes = [str(m).upper() for m in (getattr(focus, "AutoFocusModes", None) or [])]
                caps["focus"] = {"auto": "AUTO" in modes}
                try:
                    mo = img.GetMoveOptions({"VideoSourceToken": self.p.source})
                    caps["focus"]["move"] = bool(getattr(mo, "Continuous", None))
                except Exception:  # noqa: BLE001
                    caps["focus"]["move"] = False
                ir = [str(m).upper() for m in (getattr(opts, "IrCutFilterModes", None) or [])]
                if ir:
                    caps["daynight"] = sorted(set(ir) & {"ON", "OFF", "AUTO"})
            except Exception as e:  # noqa: BLE001
                self.log.info("imaging options unavailable: %s", e)
        self.caps = caps
        return caps

    # ---------- commands ----------
    def focus(self, direction, ms=600):
        img, src = self._imaging(), self.p.source
        if direction == "stop":
            img.Stop({"VideoSourceToken": src})
            return {"ok": True}
        speed = {"near": -0.5, "far": 0.5}[direction]
        img.Move({"VideoSourceToken": src, "Focus": {"Continuous": {"Speed": speed}}})
        self._timer("focus", max(100, min(int(ms), 2000)) / 1000,
                    lambda: self._safe(lambda: img.Stop({"VideoSourceToken": src})))
        return {"ok": True}

    def autofocus(self):
        self._imaging().SetImagingSettings({"VideoSourceToken": self.p.source,
                                            "ImagingSettings": {"Focus": {"AutoFocusMode": "AUTO"}},
                                            "ForcePersistence": False})
        return {"ok": True}

    def daynight(self, mode, keep=False):
        mode = {"day": "ON", "night": "OFF", "auto": "AUTO"}[mode]
        self._imaging().SetImagingSettings({"VideoSourceToken": self.p.source,
                                            "ImagingSettings": {"IrCutFilter": mode}, "ForcePersistence": False})
        # forced day or night goes back to automatic after a while, unless the host chose to keep it
        self._timer("daynight", 0 if (mode == "AUTO" or keep) else self.daynight_s,
                    lambda: self._safe(lambda: self.daynight("auto")))
        return {"ok": True, "reverts_in_s": 0 if (mode == "AUTO" or keep) else self.daynight_s}

    def aux(self, command, keep=False):
        available = self.caps.get("aux") or []
        if command not in available:
            raise ValueError("this camera doesn't offer that command")
        kind = aux_kind(command)
        wait = COOLDOWN_S.get(kind, 0) - (time.time() - self._last.get(kind, 0))
        if is_on(command) and wait > 0:
            raise RuntimeError(f"please wait {int(wait) + 1} s before using the {kind} again")
        self.p.ptz.SendAuxiliaryCommand({"ProfileToken": self.p.token, "AuxiliaryData": command})
        self._last[kind] = time.time()
        reply = {"ok": True}
        if kind == "light" and is_on(command) and not keep:
            off = off_for(command, available)
            if off:                                           # lights switch themselves off again
                self._timer(f"aux:{off}", self.light_s, lambda: self._safe(
                    lambda: self.p.ptz.SendAuxiliaryCommand({"ProfileToken": self.p.token, "AuxiliaryData": off})))
                reply["off_in_s"] = self.light_s
        elif kind == "light" and not is_on(command):
            for key in [k for k in self._timers if k.startswith("aux:")]:
                self._timer(key, 0, None)
        return reply

    def home(self):
        self.p.last_activity = time.time()
        self.p.moves += 1
        self.p.ptz.GotoHomePosition({"ProfileToken": self.p.token})
        return {"ok": True}

    def reboot(self):
        self.p.onvif.create_devicemgmt_service().SystemReboot()
        return {"ok": True}

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self.log.warning("automatic camera control step failed: %s", e)

    def shutdown(self):
        for t in self._timers.values():
            t.cancel()


# ---------- camagent probe ----------

def describe(caps):
    """Readable lines for `camagent probe`."""
    out = []
    d = caps.get("device") or {}
    if d:
        out.append(f"Camera:      {d.get('manufacturer', '?')} {d.get('model', '?')} (firmware {d.get('firmware', '?')})")
    if "clock_drift_s" in caps:
        drift = caps["clock_drift_s"]
        if caps.get("clock_note") == "dst":
            out.append("Clock:       OK (the camera reports UTC without daylight saving; its own time is right)")
        elif abs(drift) < 60:
            out.append("Clock:       OK")
        else:
            out.append(f"Clock:       off by {drift} s (check the camera's time zone, daylight saving or NTP)")
    p = caps.get("ptz")
    if p:
        out.append(f"PTZ:         presets up to {p.get('presets') or '?'}, home position "
                   f"{'yes' if p.get('home') else 'no'}, absolute moves {'yes' if p.get('absolute') else 'no'}")
    f = caps.get("focus")
    if f:
        out.append(f"Focus:       near/far {'yes' if f.get('move') else 'no'}, auto {'yes' if f.get('auto') else 'no'}")
    if caps.get("daynight"):
        out.append(f"Day/night:   {', '.join({'ON': 'day', 'OFF': 'night', 'AUTO': 'auto'}.get(m, m) for m in caps['daynight'])}")
    aux = caps.get("aux") or []
    out.append(f"Extras:      {', '.join(aux) if aux else 'none offered (no wiper, lights or similar over ONVIF)'}")
    out.append(f"Reboot:      {'yes' if caps.get('reboot') else 'unknown'}")
    return out


def test_moves(p):
    """Ask the camera to point to two absolute positions and let the person watching say whether it did.
    Some cameras claim absolute moves but ignore them (or fake them, like their position reports)."""
    from .configure import yes
    if not yes("This will turn the camera left, then right, then back home. Watch the video. Continue?", default=False):
        return None
    results = []
    for x, where in ((-0.5, "left"), (0.5, "right")):
        p.ptz.AbsoluteMove({"ProfileToken": p.token, "Position": {"PanTilt": {"x": x, "y": 0.0}}})
        time.sleep(6)
        results.append(yes(f"Did the camera turn to the {where}?", default=False))
    try:
        p.ptz.GotoHomePosition({"ProfileToken": p.token})
    except Exception:  # noqa: BLE001
        pass
    works = all(results)
    print("\n  Absolute moves: " + ("WORK on this camera. YonderView can point it at a compass direction."
                                   if works else "don't work on this camera (it ignores them)."))
    return works


def cli_probe(config_path=None, camera_id=None, moves=False):
    from . import config
    from .configure import _pick
    from .ptz import PTZ
    _, cams, _ = config.load_all(config_path)
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    p = PTZ(cam)
    print(f"Asking {cam['camera'].get('host')} what it supports...")
    try:
        p._connect()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Couldn't reach the camera over ONVIF: {e}")
    caps = Controls(p).probe()
    print()
    for line in describe(caps):
        print("  " + line)
    pos = p.position()
    if pos:
        print(f"  Position:    reports pan {pos['pan']}, tilt {pos['tilt']}, zoom {pos['zoom']} "
              f"(whether it's real shows once the camera has been moved a few times)")
    if moves and (caps.get("ptz") or {}).get("absolute"):
        print()
        test_moves(p)
    elif moves:
        print("\n  This camera doesn't offer absolute moves, so there's nothing to test.")
    else:
        print("\nNothing was moved or switched on. Wiper and lights can be tried from the camera's Control page.")
        if (caps.get("ptz") or {}).get("absolute"):
            print("To check whether its absolute moves really work: camagent probe --test-moves")
