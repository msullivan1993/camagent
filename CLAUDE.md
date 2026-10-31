# camagent

The small agent hosts run next to their IP camera (Windows PC or Raspberry Pi / Linux). It pulls the camera's
RTSP stream and pushes it to YonderView over SRT, and keeps an MQTT connection for commands (PTZ, ONVIF
controls) and telemetry (health, weather stations, camera alarms). Current version: **0.17.8**. Public repo:
github.com/msullivan1993/camagent. Releases are git tags (`v0.9.1`); agents update themselves nightly (~3 AM,
with rollback). **Since 0.16 agents install only signed releases** (camagent/release.py, Ed25519 key in Mike's WSL
`~/.config/camagent-release/`, passphrase-protected). Releasing: bump `__version__`, push, tag `vX.Y.Z` and push the
tag (CI tests it and makes a *draft* release), then Mike runs `.venv/bin/python scripts/sign_release.py sign vX.Y.Z`
(builds the wheel from the tag, signs SHA256SUMS, uploads, publishes). Never attach release files any other way.
**Since 0.17 the signature covers the dependencies:** `requirements.lock` (exact versions + every file's SHA-256 from
PyPI and piwheels) ships in each release as `camagent-<v>-requirements.lock`, listed in SHA256SUMS; agents and the
installers install it with `--require-hashes --only-binary :all:`. After changing dependencies (pyproject ranges,
upper bounds) or to take updates: `.venv/bin/pip install -U -e .`, run the tests, `.venv/bin/python
scripts/lock_requirements.py`, commit the lock. The installers carry a copy of `scripts/verify_release.py`
(stdlib Ed25519 check; test_v17 fails if the copies differ). onvif-zeep (sdist only on PyPI) is kept inside as
`camagent/_onvif`.

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
