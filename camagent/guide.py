"""Plain-language guidance shown during setup. Menus differ between camera models, so wording stays general
where it has to, and names the usual places for the common brands."""

INTRO = """
Welcome! This connects a camera to YonderView. It takes about five minutes.

You'll need:
  1. Your camera, plugged in and on the same network (or Wi-Fi) as this
     computer. CamAgent can search for it. If it can't, the camera's address
     (four numbers like 192.168.1.64) is in its phone app or your router's
     list of devices.
  2. The username and password you use for the camera's app or web page.
  3. A YonderView account with this camera added and approved
     (on yonderview.net: Host a camera).

Tip: "the camera's web page" means typing its address into a web browser on
this network (for example http://192.168.1.64) and signing in. That's where
its video and ONVIF settings are.

Four steps: your camera, its video, your YonderView account, then a test.
Nothing is saved until the end. Press Enter to keep a value shown in
[brackets]; press Ctrl+C to cancel.
"""

CAMERA = """CamAgent finds your camera on this network and asks it for its video. Most cameras
allow this through a standard called ONVIF; if yours doesn't, the next steps say how to turn it on.
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
  Recommended:  1080p at 30 fps, 4-6 Mbps (megabits per second) is plenty,
                a keyframe (a full picture) every 2 seconds: set "I-frame
                interval" or "keyframe interval" to twice the frame rate,
                e.g. 60 at 30 fps.
  Everything else is up to you.
"""

RTSP_PATHS = """The video address (RTSP) for common brands, with your camera's address filled in:
  * Amcrest / Dahua:  rtsp://{ip}:554/cam/realmonitor?channel=1&subtype=0
  * Hikvision:        rtsp://{ip}:554/Streaming/Channels/101
  * Reolink:          rtsp://{ip}:554/h264Preview_01_main
  * Axis:             rtsp://{ip}:554/axis-media/media.amp
Not listed? Search the web for "<your camera model> RTSP address", or check its manual.
Leave the username and password out of the address; CamAgent adds them.
"""

SERVER = """Before this step, your camera must be added on yonderview.net (Host a camera) and approved.
Easiest: sign in. CamAgent shows a code; enter it at yonderview.net/link on
your phone or another computer, then pick this camera.
Or paste the settings block from your camera's page on yonderview.net
(Settings > Connection > Generate credentials). It's shown only once; making a
new one turns the old one off.
"""

DONE = """
You're done! Within a minute, open yonderview.net, find your camera and check it says Live.
  * Keep this computer on, and don't let it sleep (Windows: Settings > System >
    Power > Screen and sleep > "Never" while plugged in).
  * To check on it later, open CamAgent and choose "See my cameras", or
    "Check for problems" if something looks wrong.
  * Stuck? Use "Report a problem" on yonderview.net.
"""


def show(text):
    print(text.rstrip("\n"))
