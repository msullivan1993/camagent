# camagent

The YonderView site agent. One program that:

- pulls the camera's stream over RTSP and sends it to the server over SRT (ffmpeg, supervised and auto-restarted)
- connects to the MQTT broker and handles PTZ commands over ONVIF, with a local safety stop
- publishes online/offline status and telemetry (fps, bitrate, restarts, PTZ state)
- runs as a service on Linux (systemd) and Windows (WinSW)

Everything is configured in one file, `camagent.toml`, using an interactive setup.

## Requirements

The install scripts below set these up automatically. If you install another way, you need:

| | Linux / Raspberry Pi | Windows |
|---|---|---|
| Python 3.11 or newer | `sudo apt install python3 python3-venv` | `winget install -e --id Python.Python.3.12` |
| ffmpeg | `sudo apt install ffmpeg` | `winget install -e --id Gyan.FFmpeg` |
| Git | `sudo apt install git` | `winget install -e --id Git.Git` |

camagent won't stream video without ffmpeg. `camagent doctor` checks for all of it.

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
sudo CAMAGENT_REPO=https://TOKEN@github.com/msullivan1993/camagent.git bash install.sh
```
```powershell
$env:CAMAGENT_REPO = "https://TOKEN@github.com/msullivan1993/camagent.git"
```

## The menu

Run `camagent` on its own (or the **camagent** Start menu shortcut on Windows) for a menu with
everything: show cameras and status, add / change / remove a camera, check everything, show the
recent log, restart or install the service, update, and find cameras on the network. On Windows it
asks for administrator rights itself. The commands below still work for scripts and remote use.

## Setup

`camagent configure` walks through:

1. **Finding the camera**: searches the network with ONVIF discovery, or enter its address.
2. **Picking the stream**: asks the camera for its streams over ONVIF and lists them with
   resolution and codec. If ONVIF isn't available, it tries common RTSP addresses for popular brands.
3. **Checking the stream** with ffprobe, and offering to include audio if the camera sends it.
4. **Server details**: paste the settings block from the camera's **Connection** page on yonderview.net.
5. **Testing** the server login, saving the config, and installing the service.

Run it again at any time to change settings; Enter keeps the current value.

If a step fails (ONVIF login rejected, stream unreadable, server login refused), setup stops and
offers to go back and fix it instead of saving settings that won't work.

**Setup checks this computer first:** Python, ffmpeg (and offers to install it with winget or apt if
it's missing), SRT support in that ffmpeg build, ffprobe, and on Windows whether the computer goes to
sleep (offering to turn that off, since a sleeping computer takes the camera offline).

**Automatic updates** are on unless you turn them off during setup (or later from the menu): each night
around 3 AM camagent installs a newer release if there is one, checks the cameras come back, and goes back
to the previous version if they don't. Results are in `logs/update.log` and in `camagent doctor`.

**Setup measures the stream** for a few seconds and shows what the camera sends (resolution, frame
rate, bitrate, keyframe interval). It only raises things that cause problems: a stream over the camera's
resolution limit on YonderView, keyframes more than 4 seconds apart, or frames being dropped.
Recommended (not required): 1080p at 30 fps, 4-6 Mbps, keyframe every 2 seconds. `camagent doctor` runs
the same checks any time.

**Video must be H.264.** Browsers can't reliably play H.265 (HEVC). Setup lists H.264 streams
first, flags others, and stops if the stream it reads isn't H.264: change the stream to H.264 in
the camera's web page (and turn off "smart codec" / H.264+), or pick another stream such as the
sub-stream. ONVIF sometimes reports the codec wrongly, so the check reads the actual stream.

## Several cameras

One camagent can send any number of cameras. Each camera has its own stream, server login and
PTZ connection, and runs independently: if one camera goes offline or has a bad setting, the
others keep streaming.

```
camagent add                 # set up another camera (paste that camera's settings block)
camagent list                # every camera here, with live status
camagent configure <camera>  # change one camera
camagent remove <camera>     # stop sending a camera from this machine
camagent doctor <camera>     # check one camera (or all of them, with no name)
```

Upload bandwidth is the real limit: each camera needs roughly its own bitrate. Video is copied,
not re-encoded, so CPU use stays low even on a Raspberry Pi.

Upgrading from 0.1.x: the existing camera keeps working as is. The first time it's changed with
`camagent configure`, it moves to the new layout (`cameras/<camera>.toml`) automatically.

## Commands

| Command | What it does |
|---|---|
| `camagent add` | Set up another camera |
| `camagent configure [camera]` | Change a camera's settings (or set up the first one) |
| `camagent list` | List cameras with live status (stream, server connection, PTZ) |
| `camagent remove [camera]` | Stop sending a camera from this machine |
| `camagent run` | Run in the foreground (useful for testing) |
| `camagent discover` | List ONVIF cameras on the network |
| `camagent doctor [camera]` | Check Python, ffmpeg, each camera, the server, and the service, with fixes for anything wrong |
| `camagent install-service` | Install and start the service |
| `camagent uninstall-service` | Remove the service |
| `camagent restart` | Restart the service |
| `camagent update` | Update from Git and restart |
| `camagent version` | Show the version |

## Troubleshooting

Run `camagent doctor` first (as administrator, or with `sudo` on Linux). It checks every
piece the agent depends on and says what to do about anything that fails.

## Files

| | Linux | Windows |
|---|---|---|
| Shared settings | `/etc/camagent/camagent.toml` | `C:\ProgramData\camagent\camagent.toml` |
| Cameras | `/etc/camagent/cameras/<camera>.toml` | `C:\ProgramData\camagent\cameras\<camera>.toml` |
| Live status | `/var/lib/camagent/status.json` | `C:\ProgramData\camagent\status.json` |
| Program | `/opt/camagent/venv` | `C:\ProgramData\camagent\venv` |
| Logs | `journalctl -u camagent -f` | `C:\ProgramData\camagent\logs` |

The config contains passwords and is readable only by administrators and the service.
On Linux, after upgrading from 0.1.x, run `sudo camagent install-service` once so `camagent list` can show live status.

## MQTT topics

| Topic | Direction | Content |
|---|---|---|
| `cam/<id>/cmd` | server → agent | `{"op":"move","pan":0.5,"tilt":0,"zoom":0,"ms":800,"req":"1"}`, `stop`, `preset` (with `id`), `presets`, `preset_record` (`name`, optional `id` to overwrite), `preset_delete` (`id`), `info` |
| `cam/<id>/ack` | agent → server | `{"req":"1","ok":true,"result":...}` |
| `cam/<id>/status` | agent → server | `online` / `offline` (retained) |
| `cam/<id>/config` | server → agent | `{"invert_pan":false,"invert_tilt":false}` (retained) |
| `cam/<id>/telemetry` | agent → server | version, uptime, uplink stats, PTZ state |

Commands may include `"ts"` (Unix time); the agent ignores commands older than
`[agent] stale_command_s`.
