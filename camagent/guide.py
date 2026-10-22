"""Plain-language guidance shown during setup. Menus differ between camera models, so wording stays general
where it has to, and names the usual places for the common brands."""

INTRO = """
Welcome! This connects your camera to YonderView. It takes about five minutes.

Before you start, have these ready:
  1. Your camera, plugged in and on the same network (or Wi-Fi) as this
     computer. CamAgent will try to find it for you. If it can't, you'll
     type the camera's IP address: four numbers like 192.168.1.64, shown
     in the camera's phone app or in your router's list of devices.
  2. The camera's username and password: the ones you use in its phone app
     or web page (not your YonderView login).
  3. Your camera added on yonderview.net (choose Host a camera) and approved.

Tip: "the camera's web page" means typing its IP address into a web browser
on this network (for example http://192.168.1.64) and signing in. That's
where you change its video settings, or turn on ONVIF if setup asks you to.

There are four steps: find your camera, check its video, connect your
YonderView account, then a test. Nothing is saved until the end.
Press Enter to keep a value shown in [brackets]. Press Ctrl+C to cancel.
"""

CAMERA = """CamAgent looks for your camera on this network and asks it how to get its video. Most cameras
answer through ONVIF, a standard way for programs to talk to cameras. If yours doesn't, the next
steps explain how to turn it on.
"""

ONVIF_USERS = """Where to turn on ONVIF, or add an ONVIF user, in the camera's web page (menus vary by model):
  * Amcrest / Dahua:  Settings > Network > ONVIF (or System > Account > ONVIF User)
  * Hikvision:        Configuration > Network > Advanced Settings >
                      Integration Protocol > enable ONVIF, then Add a user
  * Reolink:          Network > Advanced > Port Settings > enable ONVIF (port 8000)
  * Axis:             System > ONVIF > add an ONVIF user
  * Others: look for ONVIF under Network, Security or Integration settings.
Also make sure the camera sets its clock automatically (often called NTP, or
"sync with internet time"): ONVIF logins fail when the camera's clock is far off.
"""

STREAM = """YonderView needs the camera's main (full-quality) video in H.264 format, 1080p or smaller,
with a full picture (a "keyframe") at least every 4 seconds. Not sure what yours sends?
That's fine: CamAgent checks it for you below and says what to change, if anything.
"""

STREAM_DETAILS = """Camera video settings (in the camera's web page, under Video, Encode or Camera > Video):
  Required:     H.264 (not H.265); this camera's size limit on YonderView
                (1080p unless YonderView raised it); a keyframe at least
                every 4 seconds. Turn off "smart codec" / "H.264+" options.
  Recommended:  1080p at 30 fps (frames per second), 4-6 Mbps (megabits per
                second) is plenty, and a keyframe (a full picture) every
                2 seconds: set "I-frame interval" or "keyframe interval" to
                twice the frame rate, e.g. 60 at 30 fps.
  Everything else is up to you.
"""

RTSP_PATHS = """Every camera has a video address starting with rtsp:// (RTSP is how cameras send live video).
The usual ones for common brands, with your camera's IP address filled in:
  * Amcrest / Dahua:  rtsp://{ip}:554/cam/realmonitor?channel=1&subtype=0
  * Hikvision:        rtsp://{ip}:554/Streaming/Channels/101
  * Reolink:          rtsp://{ip}:554/h264Preview_01_main
  * Axis:             rtsp://{ip}:554/axis-media/media.amp
Not listed? Search the web for "<your camera model> RTSP address", or check its manual.
Leave the username and password out of the address; CamAgent adds them.
"""

SERVER = """Now CamAgent gets this camera's connection settings from YonderView. The camera must already
be added on yonderview.net (Host a camera) and approved.
Easiest: sign in. CamAgent shows a short code. On your phone or another computer, go to
yonderview.net/link and enter it, then pick this camera here.
Or: copy the settings block from your camera's page on yonderview.net (Settings > Connection >
Generate credentials) and paste it here. It's shown only once, and making a new one turns the
old one off.
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
