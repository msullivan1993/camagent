"""Plain-language guidance shown during setup. Menus differ between camera models, so wording stays general
where it has to, and names the usual places for the common brands."""

INTRO = """
Welcome! This connects a camera to YonderView. It takes about five minutes.

You'll need:
  1. The camera, on the same network as this computer, and its IP address.
     (It's in the camera's phone app or your router's list of devices, or
     CamAgent can search for it.)
  2. The camera's username and password.
  3. A YonderView account with this camera approved (or its settings block:
     on yonderview.net, your camera's page > Connection > Generate credentials).

Four steps: your camera, its video, your YonderView account, then a test.
Nothing is saved until the end. Press Enter to keep a value shown in
[brackets]; press Ctrl+C to cancel.
"""

CAMERA = """CamAgent talks to the camera with ONVIF, a standard almost every IP camera
supports, to find its video and (for PTZ cameras) to steer it.
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

STREAM = """Send the camera's main (full-quality) video: H.264, 1080p or less, with a
keyframe at least every 4 seconds. CamAgent checks this for you below.
"""

STREAM_DETAILS = """Camera video settings (under Video, Encode or Camera > Video in the camera's web page):
  Required:     H.264 (not H.265); this camera's resolution limit on YonderView
                (1080p unless raised); a keyframe at least every 4 seconds.
                Turn off "smart codec" / "H.264+" options.
  Recommended:  1080p at 30 fps, 4-6 Mbps (VBR capped near 8), keyframe every
                2 seconds (I-frame interval 60 at 30 fps).
  Everything else is up to you.
"""

RTSP_PATHS = """The video address (RTSP) for common brands looks like:
  * Amcrest / Dahua:  rtsp://IP:554/cam/realmonitor?channel=1&subtype=0
  * Hikvision:        rtsp://IP:554/Streaming/Channels/101
  * Reolink:          rtsp://IP:554/h264Preview_01_main
  * Axis:             rtsp://IP:554/axis-media/media.amp
Leave the username and password out of the address; CamAgent adds them.
"""

SERVER = """Easiest: sign in. CamAgent shows a code; enter it at yonderview.net/link on
your phone or another computer, then pick this camera.
Or paste the settings block from your camera's page on yonderview.net
(Settings > Connection > Generate credentials). It's shown only once; making a
new one turns the old one off.
"""

DONE = """
You're done! Your camera should show Live on yonderview.net within a minute.
  * Keep this computer on, and don't let it sleep (Windows: Settings > System >
    Power > Screen and sleep > "Never" while plugged in).
  * To check on it later, open CamAgent and choose "See my cameras", or
    "Check for problems" if something looks wrong.
  * Stuck? Use "Report a problem" on yonderview.net.
"""


def show(text):
    print(text.rstrip("\n"))
