"""Local weather stations read on the site's network, reported to YonderView with the camera's telemetry.

BETA: written from the vendors' published local interfaces and tested against sample data, not yet against
every real device. Readings use one set of metric fields (the website converts for viewers):
temp_c humidity dewpoint_c pressure_hpa wind_ms gust_ms wind_dir rain_rate_mmh rain_day_mm uv solar_wm2
lightning_km lightning_count, plus t (Unix time of the reading)."""
import json
import math
import re
import socket
import time
import urllib.request

TEMPEST_PORT = 50222


def dewpoint(temp_c, rh):
    if temp_c is None or not rh:
        return None
    a, b = 17.62, 243.12
    g = math.log(rh / 100) + a * temp_c / (b + temp_c)
    return round(b * g / (a - g), 1)


def sea_level(station_hpa, elevation_m, temp_c=15.0):
    if station_hpa is None:
        return None
    if not elevation_m:
        return None                       # without elevation, station pressure would mislead: leave it out
    t = (temp_c if temp_c is not None else 15.0) + 273.15
    return round(station_hpa * (1 - 0.0065 * elevation_m / (t + 0.0065 * elevation_m)) ** -5.257, 1)


def _get_json(url, timeout=5):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "camagent"}), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------- Tempest: the hub broadcasts JSON on UDP 50222 ----------

def tempest_socket(timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    s.bind(("", TEMPEST_PORT))
    s.settimeout(timeout)
    return s


def tempest_discover(seconds=15):
    """Serial numbers of Tempest stations heard on this network: {serial: hub_serial}."""
    found, end = {}, time.time() + seconds
    try:
        s = tempest_socket()
    except OSError as e:
        raise RuntimeError(f"can't listen for Tempest broadcasts (UDP {TEMPEST_PORT}): {e}")
    with s:
        while time.time() < end:
            try:
                msg = json.loads(s.recv(4096))
            except (socket.timeout, ValueError):
                continue
            if msg.get("type") in ("obs_st", "rapid_wind", "device_status") and msg.get("serial_number"):
                found[msg["serial_number"]] = msg.get("hub_sn", "")
    return found


class TempestDay:
    """Tempest reports rain per minute; this adds it up into today's total (reset at local midnight)."""

    def __init__(self):
        self.day, self.total = time.localtime().tm_yday, 0.0

    def add(self, mm, at):
        d = time.localtime(at).tm_yday
        if d != self.day:
            self.day, self.total = d, 0.0
        self.total += mm or 0.0
        return round(self.total, 2)


def parse_tempest(msg, day, elevation_m=0):
    """An obs_st message to a reading (None for other message types)."""
    if msg.get("type") != "obs_st" or not msg.get("obs"):
        return None
    o = list(msg["obs"][0]) + [None] * 18
    # fields per the Tempest UDP API: time, wind lull/avg/gust (m/s), direction, wind sample interval (s),
    # station pressure (hPa), temp (C), humidity, lux, UV, solar (W/m2), rain over the report interval (mm),
    # precip type, lightning distance (km), lightning count, battery (V), report interval (minutes)
    t, _lull, avg, gust, wdir, _wind_sample_s, pres, temp, rh, _lux, uv, solar, rain_mm, _ptype, ldist, lcount = o[:16]
    minutes = o[17] or 1
    return {"t": t, "temp_c": temp, "humidity": rh, "dewpoint_c": dewpoint(temp, rh),
            "pressure_hpa": sea_level(pres, elevation_m, temp), "wind_ms": avg, "gust_ms": gust, "wind_dir": wdir,
            "rain_rate_mmh": None if rain_mm is None else round(rain_mm / minutes * 60, 2),
            "rain_day_mm": day.add(rain_mm, t) if rain_mm is not None else None, "uv": uv, "solar_wm2": solar,
            "lightning_km": ldist if lcount else None, "lightning_count": lcount}


# ---------- Davis WeatherLink Live: GET /v1/current_conditions ----------

RAIN_SIZE_MM = {1: 0.254, 2: 0.2, 3: 0.1, 4: 0.0254}       # what one rain "count" is


def _f_to_c(f):
    return None if f is None else round((f - 32) * 5 / 9, 2)


def parse_weatherlink(doc):
    data = doc.get("data") or {}
    out = {"t": data.get("ts") or time.time()}
    for c in data.get("conditions") or []:
        kind = c.get("data_structure_type")
        if kind == 1 and "temp" in c:                          # the outdoor sensor suite
            mm = RAIN_SIZE_MM.get(c.get("rain_size"), 0.254)
            mph = lambda v: None if v is None else round(v * 0.44704, 2)  # noqa: E731
            out.update(temp_c=_f_to_c(c.get("temp")), humidity=c.get("hum"), dewpoint_c=_f_to_c(c.get("dew_point")),
                       wind_ms=mph(c.get("wind_speed_avg_last_1_min")), gust_ms=mph(c.get("wind_speed_hi_last_2_min")),
                       wind_dir=c.get("wind_dir_scalar_avg_last_1_min"),
                       rain_rate_mmh=None if c.get("rain_rate_last") is None else round(c["rain_rate_last"] * mm, 2),
                       rain_day_mm=None if c.get("rainfall_daily") is None else round(c["rainfall_daily"] * mm, 2),
                       uv=c.get("uv_index"), solar_wm2=c.get("solar_rad"))
        elif kind == 3 and c.get("bar_sea_level") is not None:  # barometer, inHg
            out["pressure_hpa"] = round(c["bar_sea_level"] * 33.8639, 1)
    return out if len(out) > 1 else None


# ---------- Ecowitt gateway (GW1100/GW2000...): GET /get_livedata_info ----------

def _num_unit(text, unit=""):
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*([^\d\s].*)?$", str(text or ""))
    if not m:
        return None, ""
    return float(m.group(1)), (m.group(2) or unit or "").strip().lower()


def _temp(text, unit=""):
    v, u = _num_unit(text, unit)
    return None if v is None else (round((v - 32) * 5 / 9, 2) if u.startswith("f") or "°f" in u else v)


def _speed(text):
    v, u = _num_unit(text)
    if v is None:
        return None
    if "mph" in u:
        return round(v * 0.44704, 2)
    if "km" in u:
        return round(v / 3.6, 2)
    if "knot" in u or u == "kn":
        return round(v * 0.514444, 2)
    return v                                                     # m/s


def _pressure(text):
    v, u = _num_unit(text)
    if v is None:
        return None
    return round(v * 33.8639, 1) if "inhg" in u else round(v * 1.33322, 1) if "mmhg" in u else v


def _rain(text):
    v, u = _num_unit(text)
    return None if v is None else round(v * 25.4, 2) if u.startswith("in") else v


def parse_ecowitt(doc):
    out = {"t": time.time()}
    for item in doc.get("common_list") or []:
        iid, val = str(item.get("id", "")).lower(), item.get("val")
        if iid == "0x02":
            out["temp_c"] = _temp(val, item.get("unit", ""))
        elif iid == "0x03":
            out["dewpoint_c"] = _temp(val, item.get("unit", ""))
        elif iid == "0x07":
            out["humidity"] = _num_unit(val)[0]
        elif iid == "0x0a":
            out["wind_dir"] = _num_unit(val)[0]
        elif iid == "0x0b":
            out["wind_ms"] = _speed(val)
        elif iid == "0x0c":
            out["gust_ms"] = _speed(val)
        elif iid == "0x15":
            out["solar_wm2"] = _num_unit(val)[0]
        elif iid == "0x17":
            out["uv"] = _num_unit(val)[0]
    rain = (doc.get("piezoRain") or []) + (doc.get("rain") or [])        # piezo (WS90) first if present
    for item in rain:
        iid = str(item.get("id", "")).lower()
        if iid == "0x0e" and "rain_rate_mmh" not in out:
            out["rain_rate_mmh"] = _rain(item.get("val"))
        elif iid == "0x10" and "rain_day_mm" not in out:
            out["rain_day_mm"] = _rain(item.get("val"))
    for wh in doc.get("wh25") or []:
        if wh.get("rel"):
            out["pressure_hpa"] = _pressure(wh["rel"])
    for lt in doc.get("lightning") or []:
        d = _num_unit(lt.get("distance"))
        if d[0] is not None:
            out["lightning_km"] = round(d[0] * 1.60934, 1) if d[1].startswith("mi") else d[0]
        c = _num_unit(lt.get("count"))[0]
        if c is not None:
            out["lightning_count"] = int(c)
    if out.get("dewpoint_c") is None:
        out["dewpoint_c"] = dewpoint(out.get("temp_c"), out.get("humidity"))
    return out if len(out) > 2 else None


def ecowitt_id(host):
    try:
        mac = (_get_json(f"http://{host}/get_network_info", 4) or {}).get("mac")
        if mac:
            return str(mac).replace(":", "").upper()
    except Exception:  # noqa: BLE001
        pass
    return f"ecowitt-{host}"


# ---------- one reader per configured station ----------

class Reader:
    def __init__(self, wcfg):
        self.kind = wcfg.get("kind", "")
        self.host = wcfg.get("host", "")
        self.serial = wcfg.get("serial", "")
        self.elevation = float(wcfg.get("elevation_m") or 0)
        self.interval = max(10, int(wcfg.get("interval_s") or 30))
        self.uid = self.serial if self.kind == "tempest" else ""
        self._sock = None
        self._day = TempestDay()

    def station_id(self):
        if not self.uid:
            if self.kind == "weatherlink":
                try:
                    self.uid = str((_get_json(f"http://{self.host}/v1/current_conditions") or {}).get("data", {}).get("did") or "")
                except Exception:  # noqa: BLE001
                    pass
                self.uid = self.uid or f"wll-{self.host}"
            elif self.kind == "ecowitt":
                self.uid = ecowitt_id(self.host)
        return self.uid

    def read(self, wait=None):
        """The next reading, or None if nothing arrived in time."""
        if self.kind == "weatherlink":
            return parse_weatherlink(_get_json(f"http://{self.host}/v1/current_conditions"))
        if self.kind == "ecowitt":
            return parse_ecowitt(_get_json(f"http://{self.host}/get_livedata_info"))
        if self.kind == "tempest":
            if self._sock is None:
                self._sock = tempest_socket()
            end = time.time() + (wait or 70)                     # obs_st arrives about once a minute
            while time.time() < end:
                try:
                    msg = json.loads(self._sock.recv(4096))
                except socket.timeout:
                    continue
                except ValueError:
                    continue
                if self.serial and msg.get("serial_number") != self.serial:
                    continue
                if not self.uid and msg.get("serial_number"):
                    self.uid = msg["serial_number"]
                r = parse_tempest(msg, self._day, self.elevation)
                if r:
                    return r
            return None
        raise ValueError(f"unknown weather station type: {self.kind!r}")

    def close(self):
        if self._sock:
            self._sock.close()
            self._sock = None
