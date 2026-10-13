# camagent

The small agent hosts run next to their IP camera (Windows PC or Raspberry Pi / Linux). It pulls the camera's
RTSP stream and pushes it to YonderView over SRT, and keeps an MQTT connection for commands (PTZ, ONVIF
controls) and telemetry (health, weather stations, camera alarms). Current version: **0.9.1**. Public repo:
github.com/msullivan1993/camagent. Releases are git tags (`v0.9.1`); agents update themselves nightly (~3 AM,
with rollback).

## Layout
`camagent/` package: agent.py (per-camera agent: stream, MQTT, telemetry, watchers) · config.py (TOML config,
settings block parsing, SRT URL) · configure.py (interactive add/edit camera) · menu.py · camera.py (ONVIF
discovery/connect, ffprobe) · ptz.py · controls.py (ONVIF probe, focus/day-night/aux/reboot/clock/point,
stream fixer, alarm events, OnvifSession for fixed cameras) · weather.py (Tempest, Davis, Ecowitt) · doctor.py ·
update.py · service.py · uplink.py · prereqs.py. Installers in `scripts/` (install.ps1, install.sh).
Note: there are stray old top-level .py files in the repo root (pending cleanup); the real code is `camagent/`.

## Tests
`python tests/test_*.py` from the repo folder, one script each (simulated cameras with mocks). All must print OK.

## Conventions
- Never break the menu numbering existing users/tests rely on; add new options at the end.
- Safety rails for camera controls (lights timer, day/night revert, wiper/washer cooldowns) are enforced here.
- Permission refusals from cameras become plain explanations (NotAllowed), remembered in caps["denied"].
- Audio is optional per camera (AAC 64k when enabled).
- Server addresses come from the settings block (ingest.yonderview.net SRT :8890, mqtt.yonderview.net :8883 TLS).

## Next
Accept ingest address changes pushed from the server (multi-server assignment + backup address for failover);
remote agent tools (restart, logs, doctor) from the web panel; Windows installer / Pi image later.
