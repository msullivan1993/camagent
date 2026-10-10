"""Run with: python tests/test_v07.py  (from the repo folder). Samples follow each vendor's documented format."""
import sys
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camagent import config, weather  # noqa: E402
from camagent.agent import CameraAgent  # noqa: E402

near = lambda a, b, tol=0.15: a is not None and abs(a - b) <= tol  # noqa: E731

# --- Tempest obs_st (WeatherFlow UDP API) ---
obs = {"serial_number": "ST-00000512", "type": "obs_st", "hub_sn": "HB-00013030",
       "obs": [[1588948614, 0.18, 0.22, 0.27, 144, 6, 1017.57, 22.37, 50.26, 328, 0.03, 3, 0.000000, 0, 0, 0, 2.410, 1]],
       "firmware_revision": 129}
day = weather.TempestDay()
r = weather.parse_tempest(obs, day, elevation_m=0)
assert r["t"] == 1588948614 and r["temp_c"] == 22.37 and r["humidity"] == 50.26 and r["wind_dir"] == 144
assert r["wind_ms"] == 0.22 and r["gust_ms"] == 0.27 and near(r["dewpoint_c"], 11.5, 0.3)
assert r["pressure_hpa"] is None                                  # no elevation: station pressure left out
r = weather.parse_tempest(obs, day, elevation_m=180)
assert near(r["pressure_hpa"], 1039.3, 1.5), r["pressure_hpa"]    # station 1017.6 hPa at 180 m -> sea level
obs["obs"][0][12] = 0.5                                           # 0.5 mm in the last minute
r = weather.parse_tempest(obs, day)
assert r["rain_rate_mmh"] == 30.0 and r["rain_day_mm"] == 0.5
assert weather.parse_tempest({"type": "rapid_wind", "ob": [1, 2, 3]}, day) is None

# --- Davis WeatherLink Live /v1/current_conditions ---
wll = {"data": {"did": "001D0A700002", "ts": 1531754005, "conditions": [
    {"lsid": 48308, "data_structure_type": 1, "txid": 1, "temp": 62.7, "hum": 1.1, "dew_point": -0.3,
     "wind_speed_avg_last_1_min": 10.0, "wind_dir_scalar_avg_last_1_min": 270, "wind_speed_hi_last_2_min": 20.0,
     "rain_size": 1, "rain_rate_last": 2, "rainfall_daily": 63, "solar_rad": 747, "uv_index": 5.5},
    {"lsid": 3187671188, "data_structure_type": 3, "bar_sea_level": 30.008, "bar_trend": None, "bar_absolute": 30.008},
    {"lsid": 48307, "data_structure_type": 4, "temp_in": 78.0, "hum_in": 41.1}]}}
r = weather.parse_weatherlink(wll)
assert r["t"] == 1531754005 and near(r["temp_c"], 17.06) and near(r["wind_ms"], 4.47, 0.02) and near(r["gust_ms"], 8.94, 0.02)
assert r["wind_dir"] == 270 and near(r["rain_rate_mmh"], 0.51, 0.01) and near(r["rain_day_mm"], 16.0, 0.01)
assert near(r["pressure_hpa"], 1016.2, 0.2) and r["uv"] == 5.5

# --- Ecowitt gateway /get_livedata_info (imperial units configured) ---
eco = {"common_list": [{"id": "0x02", "val": "72.3", "unit": "F"}, {"id": "0x07", "val": "55%"},
                       {"id": "0x03", "val": "55.0", "unit": "F"}, {"id": "0x0B", "val": "10.0 mph"},
                       {"id": "0x0C", "val": "20.0 mph"}, {"id": "0x0A", "val": "315"},
                       {"id": "0x15", "val": "512.30 W/m2"}, {"id": "0x17", "val": "3"}],
       "rain": [{"id": "0x0E", "val": "0.04 in/Hr"}, {"id": "0x10", "val": "0.50 in"}],
       "wh25": [{"intemp": "70.0", "unit": "F", "inhumi": "40%", "abs": "29.65 inHg", "rel": "29.92 inHg"}],
       "lightning": [{"distance": "6 mi", "date": "2026-10-10T12:00:00", "timestamp": "...", "count": "4"}]}
r = weather.parse_ecowitt(eco)
assert near(r["temp_c"], 22.39) and r["humidity"] == 55 and near(r["dewpoint_c"], 12.78) and near(r["wind_ms"], 4.47, 0.02)
assert r["wind_dir"] == 315 and near(r["rain_rate_mmh"], 1.02, 0.01) and near(r["rain_day_mm"], 12.7, 0.01)
assert near(r["pressure_hpa"], 1013.2, 0.2) and near(r["lightning_km"], 9.7, 0.1) and r["lightning_count"] == 4
metric = {"common_list": [{"id": "0x02", "val": "22.4", "unit": "C"}, {"id": "0x0B", "val": "16.1 km/h"}],
          "wh25": [{"rel": "1013.2 hPa"}]}
r = weather.parse_ecowitt(metric)
assert r["temp_c"] == 22.4 and near(r["wind_ms"], 4.47, 0.02) and r["pressure_hpa"] == 1013.2

# --- agent: readings queue up and are cleared only once delivered ---
cfg = {s: dict(config.DEFAULTS[s]) for s in config.DEFAULTS}
cfg["camera"].update(host="h", rtsp_url="rtsp://h/x", ptz=False)
cfg["platform"].update(camera_id="wx_1", ingest_host="i", srt_password="s", mqtt_host="m", mqtt_password="p")
cfg["weather"].update(kind="ecowitt", host="192.0.2.10", name="Yard")
a = CameraAgent(cfg)
a.weather.uid = "AABBCCDDEEFF"
a.weather_pending.append({"t": time.time(), "temp_c": 20.0})
a.mqtt_connected = False
a.mq = mock.Mock()
a.mq.publish.return_value = mock.Mock(rc=0)
assert a._publish_telemetry() is False and len(a.weather_pending) == 1      # offline: kept
a.mqtt_connected = True
assert a._publish_telemetry() is True and len(a.weather_pending) == 0       # delivered: cleared
import json  # noqa: E402
sent = json.loads(a.mq.publish.call_args[0][1])
assert sent["weather"]["station"] == {"kind": "ecowitt", "uid": "AABBCCDDEEFF", "name": "Yard"}
assert sent["weather"]["readings"][0]["temp_c"] == 20.0
assert a.status()["weather"]["kind"] == "ecowitt"

# --- the weather section is saved with the camera ---
assert "weather" in config.CAMERA_SECTIONS
print("v0.7 OK")
