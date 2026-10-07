# camagent

The YonderView site agent. One program that:

* pulls the camera's stream over RTSP and sends it to the server over SRT (ffmpeg, supervised and auto-restarted)
* connects to the MQTT broker and handles PTZ commands over ONVIF, with a local safety stop
* publishes online/offline status and telemetry (fps, bitrate, restarts, PTZ state)
* runs as a service on Linux (systemd) and Windows (WinSW)

Everything is configured in one file, `camagent.toml`, using an interactive setup.

## Install

**Linux / Raspberry Pi** (Debian, Ubuntu, Raspberry Pi OS):

```bash
curl -fsSL https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.sh | sudo bash
sudo camagent configure
```

**Windows** (PowerShell as Administrator):

```powershell
irm https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.ps1 | iex
camagent configure
```

The installers set up Python, ffmpeg, and Git if needed, install camagent into its own
environment, and never overwrite an existing config. Run them again to upgrade.

While the repository is private, give the installer a read-only token:

```bash
sudo CAMAGENT\_REPO=https://TOKEN@github.com/msullivan1993/camagent.git bash install.sh
```

```powershell
$env:CAMAGENT\_REPO = "https://TOKEN@github.com/msullivan1993/camagent.git"
```

## Setup

`camagent configure` walks through:

1. **Finding the camera**: searches the network with ONVIF discovery, or enter its address.
2. **Picking the stream**: asks the camera for its streams over ONVIF and lists them with
resolution and codec. If ONVIF isn't available, it tries common RTSP addresses for popular brands.
3. **Checking the stream** with ffprobe, and offering to include audio if the camera sends it.
4. **Server details**: paste the block from yvcam's "Show stream settings", or type values in.
5. **Testing** the MQTT login, saving the config, and installing the service.

Run it again at any time to change settings; Enter keeps the current value.

## Commands

|Command|What it does|
|-|-|
|`camagent configure`|Interactive setup|
|`camagent run`|Run in the foreground (useful for testing)|
|`camagent discover`|List ONVIF cameras on the network|
|`camagent install-service`|Install and start the service|
|`camagent uninstall-service`|Remove the service|
|`camagent restart`|Restart the service|
|`camagent update`|Update from Git and restart|
|`camagent version`|Show the version|

## Files

||Linux|Windows|
|-|-|-|
|Config|`/etc/camagent/camagent.toml`|`C:\\ProgramData\\camagent\\camagent.toml`|
|Program|`/opt/camagent/venv`|`C:\\ProgramData\\camagent\\venv`|
|Logs|`journalctl -u camagent -f`|`C:\\ProgramData\\camagent\\logs`|

The config contains passwords and is readable only by administrators and the service.

## MQTT topics

|Topic|Direction|Content|
|-|-|-|
|`cam/<id>/cmd`|server → agent|`{"op":"move","pan":0.5,"tilt":0,"zoom":0,"ms":800,"req":"1"}`, `stop`, `preset` (with `id`), `presets`, `info`|
|`cam/<id>/ack`|agent → server|`{"req":"1","ok":true,"result":...}`|
|`cam/<id>/status`|agent → server|`online` / `offline` (retained)|
|`cam/<id>/config`|server → agent|`{"invert\_pan":false,"invert\_tilt":false}` (retained)|
|`cam/<id>/telemetry`|agent → server|version, uptime, uplink stats, PTZ state|

Commands may include `"ts"` (Unix time); the agent ignores commands older than
`\[agent] stale\_command\_s`.

