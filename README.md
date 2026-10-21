# CamAgent

CamAgent sends your camera to [YonderView](https://yonderview.net). Install it on a computer that's on
the same network as the camera (a Windows PC, a mini PC or a Raspberry Pi) and leave that computer on.

## First-time setup (about 10 minutes)

**Before you start:** on yonderview.net, sign in (or create an account), choose **Host a camera**, and
wait for the camera to be approved. Have the camera plugged in, and know its username and password
(the ones for its app or web page).

**1. Install CamAgent.**

On **Windows**: press the Windows key, type `PowerShell`, right-click **Windows PowerShell** and choose
**Run as administrator**. Paste this line (right-click pastes) and press Enter:

```powershell
irm https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.ps1 | iex
```

On a **Raspberry Pi or Linux** computer (Debian, Ubuntu, Raspberry Pi OS): open a terminal, paste this
line and press Enter (it asks for your password):

```bash
curl -fsSL https://raw.githubusercontent.com/msullivan1993/camagent/main/scripts/install.sh | sudo bash
```

The installer sets up everything CamAgent needs (Python, ffmpeg and Git). Running it again upgrades
CamAgent and never touches your settings.

**2. Open CamAgent.** On Windows, use the **CamAgent** shortcut in the Start menu, or type `camagent` in
an administrator PowerShell. On Linux, type `sudo camagent`.

**3. Choose "2. Add a camera"** and answer the questions. CamAgent finds the camera, checks its video,
signs in to YonderView (you type a code at yonderview.net/link), tests the connection, and starts
sending. Within a minute your camera shows **Live** on yonderview.net.

Stuck? Open CamAgent and choose **Check for problems**: it says what's wrong and what to do. Or use
**Report a problem** on yonderview.net.

## How it works (technical)

- pulls the camera's stream over RTSP and sends it to the server over SRT (ffmpeg, supervised and auto-restarted)
- connects to the MQTT broker and handles PTZ commands over ONVIF, with a local safety stop
- publishes online/offline status and telemetry (fps, bitrate, restarts, PTZ state)
- runs as a service on Linux (systemd) and Windows (WinSW)

Everything is configured in one file, `camagent.toml`, using the interactive setup.

## Requirements

The install scripts set these up automatically. If you install another way, you need:

| | Linux / Raspberry Pi | Windows |
|---|---|---|
| Python 3.11 or newer | `sudo apt install python3 python3-venv` | `winget install -e --id Python.Python.3.12` |
| ffmpeg | `sudo apt install ffmpeg` | `winget install -e --id Gyan.FFmpeg` |
| Git | `sudo apt install git` | `winget install -e --id Git.Git` |

CamAgent won't stream video without ffmpeg. **Check for problems** (`camagent doctor`) checks for all of it.

## The menu

Run `camagent` on its own (or the **CamAgent** Start menu shortcut on Windows) for a menu with
everything, grouped by what you're doing:

```
=== CamAgent 0.14.1 ===
1 camera  |  CamAgent is running  |  YonderView: signed in as Mike

 Cameras
  1. See my cameras and how they're doing
  2. Add a camera
  3. Change or remove a camera...

 If something's wrong
  4. Check for problems
  5. Restart CamAgent
  6. Show the recent log

 Settings
  7. Sign out of YonderView
  8. Updates...
  9. Advanced tools...

  0. Quit
```

The line under the title says what to do next when something's missing (no cameras yet, CamAgent not
started). On Windows it asks for administrator rights itself. The commands below still work for scripts
and remote use.

## Setup

`camagent configure` walks through:

1. **Finding the camera**: searches the network with ONVIF discovery, or enter its address.
2. **Picking the stream**: asks the camera for its streams over ONVIF and lists them with
   resolution and codec. If ONVIF isn't available, it tries common RTSP addresses for popular brands.
3. **Checking the stream** with ffprobe, and offering to include audio if the camera sends it.
4. **Resolution**: YonderView takes 1080p H.264. If the camera sends more (4K, 1440p...) or H.265, camagent
   offers to convert it to 1080p on this computer, so the camera keeps its full resolution for your own uses.
   It tests this computer's video hardware first (Intel Quick Sync, VA-API, NVIDIA, AMD; the CPU last) and
   uses the first that works. A small PC with an Intel GPU does it easily. Sending above 1080p is also
   possible where YonderView has approved it for the camera.
5. **Server details**: easiest, **sign in to YonderView**: camagent shows a short code, you enter it at
   yonderview.net/link on your phone or another computer (signed in there, so your password never goes
   through camagent), approve this computer, and pick the camera from your account. camagent fetches its
   connection settings itself. Or paste the settings block from the camera's **Connection** page.
   Linked computers are listed on your profile on yonderview.net, where you can unlink them.
6. **Testing** the server login, saving the config, and installing the service.

Run it again at any time to change settings; Enter keeps the current value.

If a step fails (ONVIF login rejected, stream unreadable, server login refused), setup stops and
offers to go back and fix it instead of saving settings that won't work.

**Setup checks this computer first:** Python, ffmpeg (and offers to install it with winget or apt if
it's missing), SRT support in that ffmpeg build, ffprobe, and on Windows whether the computer goes to
sleep (offering to turn that off, since a sleeping computer takes the camera offline).

**Camera controls:** camagent asks each camera what it supports (focus, day/night, wiper, lights, home, reboot)
and the camera's Control page shows just those. `camagent probe <camera>` prints the same list, without moving
or switching anything. Lights switch themselves off and forced day/night returns to automatic after a while.

**Fixing the camera's stream:** `camagent fix-stream <camera>` shows what would bring the camera to H.264,
1080p or less, and 2-second keyframes, and changes it after asking (setup offers the same). Most cameras need an
administrator login for this. camagent also listens for the camera's own alarms (covered, moved, out of focus)
and reports them to the host.

**Weather station (beta):** camagent can read a Tempest, Davis WeatherLink Live or Ecowitt gateway on the
same network and show its conditions on the camera's page. Menu: *Weather station (beta)*, or
`camagent weather <camera>`.

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
camagent add                 # set up another camera (sign in and pick it, or paste its settings block)
camagent signin              # sign this computer in to your YonderView account
camagent signout             # forget this computer's sign-in (unlink it on your profile to revoke it)
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

Open CamAgent and choose **Check for problems** (or run `camagent doctor` as administrator, or with `sudo`
on Linux). It checks every piece CamAgent depends on and says what to do about anything that fails.

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
| `cam/<id>/cmd` | server → agent | `{"op":"move","pan":0.5,"tilt":0,"zoom":0,"ms":800,"req":"1"}`, `stop`, `preset` (with `id`), `presets`, `preset_record` (`name`, optional `id` to overwrite), `preset_delete` (`id`), `info`, `versions` (`on`, the ladder and mark, `keep_s`: see below) |
| `cam/<id>/ack` | agent → server | `{"req":"1","ok":true,"result":...}` |
| `cam/<id>/status` | agent → server | `online` / `offline` (retained) |
| `cam/<id>/config` | server → agent | `{"invert_pan":false,"invert_tilt":false}` (retained) |
| `cam/<id>/telemetry` | agent → server | version, uptime, uplink stats, PTZ state, `versions` (can this PC make lower qualities) |

Commands may include `"ts"` (Unix time); the agent ignores commands older than
`[agent] stale_command_s`.

## Lower qualities made on this PC (0.13)

The website can ask camagent to make a camera's 720p and 480p (plus 1080p for bigger cameras) on this PC while
someone is watching them, instead of on YonderView's servers. It's off unless YonderView turns it on for the camera,
and the extra upload happens only while those versions are watched. A second ffmpeg runs next to the main upload
(the camera's own stream is never interrupted): one decode, each version resized, marked and encoded on the GPU,
sent over SRT with the camera's own login. If this PC can't keep up (slower than real time for 30 s) or ffmpeg keeps
failing, camagent stops and tells the website, and the server makes them again; it offers once more an hour later.
The GPU check (a short test encode) runs only the first time the website asks.

```
[stream]
versions = "auto"     # use this PC when YonderView asks and the PC has a working GPU (default)
versions = "cpu"      # also without a GPU (x264): only for strong CPUs
versions = "off"      # never
```

**Raspberry Pi and Rockchip boards (0.14).** On a Raspberry Pi 3 or 4, camagent uses the Pi's own video engine
(`h264_v4l2m2m`, up to 1080p; Raspberry Pi OS's ffmpeg has it). It decodes the camera there too, so the camera itself must be 1080p (or smaller), up to about 30 fps. A Pi 4 should make a 1080p30 camera's 720p and 480p comfortably; a Pi 3 is tighter, and its CPU can't take over the decoding. If a Pi can't keep up, camagent hands the versions back to the server by itself. The Pi 5 has no video encoder, so it would need
`versions = "cpu"`. Rockchip boards (e.g. Radxa Rock) work with an ffmpeg built with Rockchip MPP (`h264_rkmpp`, from
the ffmpeg-rockchip packages); with the stock Debian ffmpeg the check finds nothing and the server keeps making the
versions. The same engines also convert H.265 cameras to H.264.

