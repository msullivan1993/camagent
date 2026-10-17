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


class NotAllowed(RuntimeError):
    """The camera refused because camagent's camera account is too low-level for this."""


def _denied(e):
    t = str(e).lower()
    return any(w in t for w in ("notauthorized", "not authorized", "sender not authorized", "unauthorized",
                                "access denied", "permission"))


TAMPER_WORDS = ("tamper", "globalscenechange", "scenechange", "imagetooblurry", "imagetoodark", "imagetoobright",
                "defocus", "videoloss", "signalloss", "obstruct", "camerablock")


class OnvifSession:
    """ONVIF for a camera without PTZ: just what focus, day/night, reboot, clock and tamper alerts need."""

    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self.log = log or logging.getLogger("camagent.onvif")
        self.onvif = self.source = self.token = self.ptz = None
        self.connected = False
        self.available = True
        self.last_activity, self.moves = 0.0, 0
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="onvif-session").start()

    def _loop(self):
        from .camera import friendly_error
        while not self._stop.is_set() and not self.connected:
            try:
                self._connect()
            except Exception as e:  # noqa: BLE001
                self.log.info("camera not reachable over ONVIF (controls): %s; retrying in 60s", friendly_error(e))
                self._stop.wait(60)

    def _connect(self):
        from .camera import onvif_connect
        c = self.cfg["camera"]
        cam = onvif_connect(c["host"], c["onvif_port"], c["username"], c["password"])
        profiles = cam.create_media_service().GetProfiles()
        prof = profiles[0]
        vsc = getattr(prof, "VideoSourceConfiguration", None)
        self.onvif, self.token = cam, prof.token
        self.source = getattr(vsc, "SourceToken", None) if vsc else None
        self.connected = True

    def shutdown(self):
        self._stop.set()


class Controls:
    def __init__(self, ptz, light_minutes=15, daynight_minutes=30, log=None):
        self.p = ptz                                   # the connected PTZ helper (its ONVIF session and tokens)
        self.light_s = light_minutes * 60
        self.daynight_s = daynight_minutes * 60
        self.log = log or logging.getLogger("camagent.controls")
        self.caps = {}
        self.denied = set()                            # commands the camera refused for lack of permission
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

    def _try(self, name, fn):
        """Run a camera command; a refusal for lack of permission becomes a plain explanation (and is remembered
        so the website can say which controls need a higher-level camera account)."""
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if _denied(e):
                self.denied.add(name)
                self.caps["denied"] = sorted(self.denied)
                raise NotAllowed(f"the camera didn't allow {name.replace('_', ' ')}: camagent's camera login needs a "
                                 "higher level (Operator or Administrator) in the camera's user settings")
            raise

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
        return self._try("autofocus", self._autofocus)

    def _autofocus(self):
        self._imaging().SetImagingSettings({"VideoSourceToken": self.p.source,
                                            "ImagingSettings": {"Focus": {"AutoFocusMode": "AUTO"}},
                                            "ForcePersistence": False})
        return {"ok": True}

    def daynight(self, mode, keep=False):
        return self._try("day/night", lambda: self._daynight(mode, keep))

    def _daynight(self, mode, keep=False):
        mode = {"day": "ON", "night": "OFF", "auto": "AUTO"}[mode]
        self._imaging().SetImagingSettings({"VideoSourceToken": self.p.source,
                                            "ImagingSettings": {"IrCutFilter": mode}, "ForcePersistence": False})
        # forced day or night goes back to automatic after a while, unless the host chose to keep it
        self._timer("daynight", 0 if (mode == "AUTO" or keep) else self.daynight_s,
                    lambda: self._safe(lambda: self._daynight("auto")))
        return {"ok": True, "reverts_in_s": 0 if (mode == "AUTO" or keep) else self.daynight_s}

    def aux(self, command, keep=False):
        available = self.caps.get("aux") or []
        if command not in available:
            raise ValueError("this camera doesn't offer that command")
        kind = aux_kind(command)
        wait = COOLDOWN_S.get(kind, 0) - (time.time() - self._last.get(kind, 0))
        if is_on(command) and wait > 0:
            raise RuntimeError(f"please wait {int(wait) + 1} s before using the {kind} again")
        self._try(kind, lambda: self.p.ptz.SendAuxiliaryCommand({"ProfileToken": self.p.token, "AuxiliaryData": command}))
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
        self._try("reboot", lambda: self.p.onvif.create_devicemgmt_service().SystemReboot())
        return {"ok": True}

    def sync_clock(self, server="pool.ntp.org"):
        """Point the camera at a time server and let it keep its own clock."""
        dev = self.p.onvif.create_devicemgmt_service()

        def run():
            dev.SetNTP({"FromDHCP": False, "NTPManual": [{"Type": "DNS", "DNSname": server}]})
            dev.SetSystemDateAndTime({"DateTimeType": "NTP", "DaylightSavings": True})
        self._try("clock sync", run)
        return {"ok": True}

    def point(self, pan, tilt=None, zoom=None):
        """Move to an absolute position (cameras that honor it). camagent remembers where it sent the camera,
        which stands in for the position on cameras that don't report one."""
        if not self.p.ptz:
            raise RuntimeError("this camera can't be pointed")
        pos = {"PanTilt": {"x": max(-1.0, min(1.0, float(pan))),
                           "y": max(-1.0, min(1.0, float(tilt if tilt is not None else 0.0)))}}
        if zoom is not None:
            pos["Zoom"] = {"x": max(0.0, min(1.0, float(zoom)))}
        self._try("pointing", lambda: self.p.ptz.AbsoluteMove({"ProfileToken": self.p.token, "Position": pos}))
        self.p.last_activity = time.time()
        self.p.moves += 1
        self.p.commanded = {"pan": pos["PanTilt"]["x"], "tilt": pos["PanTilt"]["y"],
                            "zoom": pos.get("Zoom", {}).get("x"), "t": int(time.time())}
        return {"ok": True}

    # ---------- stream settings ----------
    def stream_fixes(self, apply=False, max_height=1080, bitrate=4000):
        """What would bring the camera's main stream to H.264, at most 1080p, 2-second keyframes and a sensible
        bitrate. With apply=True, change it (most cameras need an administrator login for this)."""
        media = self.p.onvif.create_media_service()
        prof = next((p for p in media.GetProfiles() if p.token == self.p.token), None) or media.GetProfiles()[0]
        cfg = getattr(prof, "VideoEncoderConfiguration", None)
        if cfg is None:
            return []
        opts = media.GetVideoEncoderConfigurationOptions({"ConfigurationToken": cfg.token})
        h264 = getattr(opts, "H264", None)
        changes = []
        if str(getattr(cfg, "Encoding", "")).upper() != "H264" and h264 is not None:
            changes.append(f"codec {cfg.Encoding} -> H.264")
            cfg.Encoding = "H264"
        res = getattr(cfg, "Resolution", None)
        if res is not None and getattr(res, "Height", 0) > max_height and h264 is not None:
            fits = [r for r in (getattr(h264, "ResolutionsAvailable", None) or []) if r.Height <= max_height]
            if fits:
                best = max(fits, key=lambda r: r.Width * r.Height)
                changes.append(f"resolution {res.Width}x{res.Height} -> {best.Width}x{best.Height}")
                cfg.Resolution = best
        rc = getattr(cfg, "RateControl", None)
        fps = int(getattr(rc, "FrameRateLimit", 0) or 0) if rc else 0
        h264cfg = getattr(cfg, "H264", None)
        if fps and h264cfg is not None:
            want = 2 * fps
            rng = getattr(h264, "GovLengthRange", None) if h264 is not None else None
            if rng is not None:
                want = max(rng.Min, min(rng.Max, want))
            if getattr(h264cfg, "GovLength", want) != want:
                changes.append(f"keyframe interval {h264cfg.GovLength} -> {want} frames (2 s)")
                h264cfg.GovLength = want
        if rc is not None and int(getattr(rc, "BitrateLimit", 0) or 0) > 6000:
            changes.append(f"bitrate {rc.BitrateLimit} -> {bitrate} kbps")
            rc.BitrateLimit = bitrate
        if apply and changes:
            self._try("stream settings", lambda: media.SetVideoEncoderConfiguration({"Configuration": cfg,
                                                                                     "ForcePersistence": True}))
        return changes

    # ---------- tamper and other camera alarms ----------
    def watch_events(self, stop, on_event):
        """Listen for the camera's own alarms (covered, moved, out of focus, video lost) through an ONVIF
        pull-point subscription. Cameras without event support are simply left alone."""
        cam = self.p.onvif
        try:
            cam.create_events_service().CreatePullPointSubscription()
            pull = cam.create_pullpoint_service()
        except Exception as e:  # noqa: BLE001
            self.caps["events"] = False
            self.log.info("camera alarms not available: %s", e)
            return
        self.caps["events"] = True
        while not stop.is_set():
            try:
                got = pull.PullMessages({"Timeout": "PT30S", "MessageLimit": 20})
            except Exception as e:  # noqa: BLE001
                self.log.info("camera alarm feed interrupted: %s; resubscribing in 60s", e)
                stop.wait(60)
                try:
                    cam.create_events_service().CreatePullPointSubscription()
                    pull = cam.create_pullpoint_service()
                except Exception:  # noqa: BLE001
                    pass
                continue
            for msg in getattr(got, "NotificationMessage", None) or []:
                topic = str(getattr(getattr(msg, "Topic", None), "_value_1", "") or getattr(msg, "Topic", ""))
                if any(w in topic.lower().replace("/", "") for w in TAMPER_WORDS) and _event_active(msg):
                    on_event({"kind": "tamper", "topic": topic.split("/")[-1] or topic, "t": int(time.time())})

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
    if "events" in caps:
        out.append(f"Alarms:      {'yes (covered, moved, out of focus... are reported)' if caps['events'] else 'not supported'}")
    if caps.get("denied"):
        out.append(f"Not allowed: {', '.join(caps['denied'])}  (the camera login camagent uses needs a higher level)")
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


def _event_active(msg):
    """True unless the message says the alarm state went false (cameras send both edges)."""
    text = str(getattr(msg, "Message", "") or msg).lower()
    return not any(f'value="{v}"' in text or f"'value': '{v}'" in text for v in ("false", "0"))


def cli_probe(config_path=None, camera_id=None, moves=False):
    from . import config
    from .configure import _pick
    from .ptz import PTZ
    _, cams, _ = config.load_all(config_path)
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    print(f"Asking {cam['camera'].get('host')} what it supports...")
    p = PTZ(cam) if cam["camera"].get("ptz", True) else None
    try:
        if p:
            p._connect()
        if not p or not p.available or not getattr(p, "onvif", None):     # no PTZ: plain ONVIF for its controls
            p = OnvifSession(cam)
            p._connect()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Couldn't reach the camera over ONVIF: {e}")
    ctl = Controls(p)
    caps = ctl.probe()
    try:                                                     # alarms: does it publish an event feed at all?
        p.onvif.create_events_service().GetEventProperties()
        caps["events"] = True
    except Exception:  # noqa: BLE001
        caps["events"] = False
    print()
    for line in describe(caps):
        print("  " + line)
    pos = p.position() if hasattr(p, "position") else None
    if pos:
        print(f"  Position:    reports pan {pos['pan']}, tilt {pos['tilt']}, zoom {pos['zoom']} "
              f"(whether it's real shows once the camera has been moved a few times)")
    if moves and (caps.get("ptz") or {}).get("absolute"):
        print()
        test_moves(p)
    elif moves:
        print("\n  This camera doesn't offer absolute moves, so there's nothing to test.")
    else:
        print("\nNothing was moved or switched on. Controls can be tried from the camera's Control page.")
        if (caps.get("ptz") or {}).get("absolute"):
            print("To check whether its absolute moves really work: camagent probe --test-moves")



def cli_fix_stream(config_path=None, camera_id=None):
    """Offer to set the camera's main stream to what YonderView wants (H.264, up to 1080p, 2 s keyframes)."""
    from . import config
    from .configure import _pick, yes
    from .ptz import PTZ
    _, cams, _ = config.load_all(config_path)
    cam = _pick(cams, camera_id)
    if cam is None:
        return
    p = OnvifSession(cam) if not cam["camera"].get("ptz", True) else PTZ(cam)
    try:
        p._connect()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Couldn't reach the camera over ONVIF: {e}")
    c = Controls(p)
    changes = c.stream_fixes(apply=False)
    if not changes:
        print("The camera's stream is already set up the way YonderView wants it.")
        return
    print("These changes would bring the camera's stream in line:")
    for ch in changes:
        print("  - " + ch)
    if not yes("Change them on the camera now?", default=True):
        return
    from . import camera
    had_sound = camera.sound_check(cam) if cam["stream"].get("audio") else None
    try:
        c.stream_fixes(apply=True)
    except NotAllowed as e:
        raise SystemExit(f"{e}. Or change these in the camera's own web page.")
    print("Done. The stream restarts with the new settings within a minute.")
    if had_sound:
        import time
        print("Checking the camera still sends sound...")
        time.sleep(10)
        if camera.sound_check(cam) is False:
            print(camera.AUDIO_LOST)
