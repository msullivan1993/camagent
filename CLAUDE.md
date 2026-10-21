# camagent

The small agent hosts run next to their IP camera (Windows PC or Raspberry Pi / Linux). It pulls the camera's
RTSP stream and pushes it to YonderView over SRT, and keeps an MQTT connection for commands (PTZ, ONVIF
controls) and telemetry (health, weather stations, camera alarms). Current version: **0.14.11** (test branch pi-encoders; main 0.13.1). Public repo:
github.com/msullivan1993/camagent. Releases are git tags (`v0.9.1`); agents update themselves nightly (~3 AM,
with rollback).

## Layout
`camagent/` package: agent.py (per-camera agent: stream, MQTT, telemetry, watchers) · config.py (TOML config,
settings block parsing, SRT URL) · configure.py (interactive add/edit camera) · menu.py · camera.py (ONVIF
discovery/connect, ffprobe) · ptz.py · controls.py (ONVIF probe, focus/day-night/aux/reboot/clock/point,
stream fixer, alarm events, OnvifSession for fixed cameras) · link.py (sign in to YonderView: device sign-in,
list cameras, fetch a camera's settings block) · convert.py (4K/H.265 -> 1080p H.264 before sending: GPU
detection by test-encoding, CPU fallback) · weather.py (Tempest, Davis, Ecowitt) · doctor.py ·
update.py · service.py · uplink.py · prereqs.py. Installers in `scripts/` (install.ps1, install.sh).
Work happens in Claude Code in WSL (`~/camagent`, venv `.venv`, installed with `pip install -e .`). `C:\yvprojects\camagent` is a
one-way backup mirror (refreshed every 15 min): never edit there. `D:\Documents\camagent` and
`C:\yvprojects\camagent-old-2026-10-13` are pre-WSL copies.

## Tests
`for t in tests/test_*.py; do .venv/bin/python "$t"; done` from the repo folder: one script each (simulated
cameras with mocks); all must print OK. CI (`.github/workflows/tests.yml`) runs them on every push with
Python 3.12 and 3.14 (test_v05 can't fake Windows paths on Linux with 3.11); `release.yml` runs them again before publishing a tag.

## Conventions
- Menu (0.10): 8 top-level choices; related actions are grouped in short submenus (Change a camera..., Service
  and log..., Updates..., YonderView account..., More tools...). Keep the top level short; add new actions to a
  group. tests/test_menu.py walks it.
- Safety rails for camera controls (lights timer, day/night revert, wiper/washer cooldowns) are enforced here.
- Permission refusals from cameras become plain explanations (NotAllowed), remembered in caps["denied"].
- Audio is optional per camera (AAC 64k when enabled).
- Resolution: YonderView prefers 1080p (above is approved/paid). `[stream] convert = "1080p"` re-encodes the one main
  stream here (still no substreams, ever): `encoder` is picked by setup by test-encoding a 4K clip with each
  method (qsv/vaapi/nvenc/amf, "-lite" = GPU encode only, x264 last); H.264, no B-frames, 2 s keyframes. A GPU
  method failing with encoder errors 3x in a row falls back to x264 (telemetry uplink.convert.fallback).
- Server addresses come from the settings block (ingest.yonderview.net SRT :8890, mqtt.yonderview.net :8883 TLS),
  either pasted or fetched after signing in (website 1.44+: /link, /api/agent/...). The sign-in token lives in
  camagent.toml [account] (protected like the camera passwords; never in a camera file or a log).

## Next
Accept ingest address changes pushed from the server (multi-server assignment + backup address for failover);
remote agent tools (restart, logs, doctor) from the web panel; Windows installer / Pi image later.
