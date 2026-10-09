"""Plain-language guidance shown during setup. Menus differ between camera models, so wording stays general
where it has to, and names the usual places for the common brands."""

INTRO = """
Welcome! This connects a camera to YonderView. It takes about five minutes.

You'll need:
  1. The camera, on the same network as this computer, with its IP address.
     (Find it in the camera's phone app, in your router's list of connected
     devices, or let the search below look for it.)
  2. A login for the camera. Many cameras need a separate "ONVIF" user for
     this; tips are shown in the next step if the login doesn't work.
  3. The camera's settings block from yonderview.net:
     open your camera's page > Connection > Generate credentials > copy it.

Steps: camera > stream > server > test. Nothing is saved until the end.
Press Enter to keep a value shown in [brackets]. Press Ctrl+C to cancel.
"""

CAMERA = """Step 1 of 4: the camera
camagent talks to the camera over ONVIF (a standard most IP cameras support)
to find its video streams and, for PTZ cameras, to steer it.
  * ONVIF port: usually 80. Reolink uses 8000; some others use 8080 or 8899.
  * Login: use the camera's admin login, or an ONVIF user if the camera has
    a separate one (see the tips below if it's rejected).
"""

ONVIF_USERS = """Where to turn on ONVIF or add an ONVIF user (menus vary by model):
  * Amcrest / Dahua:  Settings > Network > ONVIF (or System > Account > ONVIF User)
  * Hikvision:        Configuration > Network > Advanced Settings >
                      Integration Protocol > enable ONVIF, then Add a user
  * Reolink:          Network > Advanced > Port Settings > enable ONVIF (port 8000)
  * Axis:             System > ONVIF > add an ONVIF user
  * Others: look for ONVIF under Network, Security or Integration settings.
Also check the camera's clock is set automatically (NTP): ONVIF logins fail
when the camera's time is far off.
"""

STREAM = """Step 2 of 4: the stream
Cameras usually offer a "main" stream (full quality) and a smaller "sub" stream.
Send the main stream.
  Required:     H.264 (not H.265); within this camera's resolution limit on
                YonderView (1080p unless raised); a keyframe at least every
                4 seconds. Turn off "smart codec" / "H.264+" options.
  Recommended:  1080p at 30 fps, 4-6 Mbps (VBR capped near 8), keyframe
                every 2 seconds (I-frame interval 60 at 30 fps).
  Everything else is up to you. Higher quality is welcome if your upload can
  carry it. (Settings are under Video, Encode or Camera > Video in the
  camera's own web page.)
"""

RTSP_PATHS = """If you know your camera's brand, its RTSP address usually looks like:
  * Amcrest / Dahua:  rtsp://IP:554/cam/realmonitor?channel=1&subtype=0
  * Hikvision:        rtsp://IP:554/Streaming/Channels/101
  * Reolink:          rtsp://IP:554/h264Preview_01_main
  * Axis:             rtsp://IP:554/axis-media/media.amp
Leave the login out of the address; camagent adds it.
"""

SERVER = """Step 3 of 4: YonderView
On yonderview.net, open your camera's page and click Connection, then
Generate credentials. Copy the whole settings block and paste it here.
The block is shown only once. If you lose it, generate a new one (the old one
stops working, which is fine).
"""

TEST = "Step 4 of 4: testing the connection to YonderView"

DONE = """
All set. What happens now:
  * camagent runs in the background and starts with the computer. Keep this
    computer on and stop it from sleeping (Windows: Settings > System >
    Power > Screen and sleep > "Never" while plugged in).
  * Within a minute, your camera's page on yonderview.net should show Live.
  * To check on it here: run camagent and choose "Show cameras and their
    status", or "Check everything" if something looks wrong.
  * Something not working? Use "Report a problem" on yonderview.net.
"""


def show(text):
    print(text.rstrip("\n"))
