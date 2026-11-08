"""Plain-language guidance shown during setup. Menus differ between camera models, so wording stays general
where it has to, and names the usual places for the common brands."""

INTRO = """
Welcome. This setup connects your camera to YonderView. It takes about five minutes.

Before you start, have these ready:
  1. Your camera, plugged in and on the same network (or Wi-Fi) as this
     computer. CamAgent tries to find your camera for you. If it can't,
     you type the camera's IP address. That's four numbers like
     192.168.1.64. You can find it in the camera's phone app or in your
     router's list of devices.
  2. The camera's username and password. These are the ones you use in
     the camera's phone app or web page (not your YonderView login).
  3. Your camera added on yonderview.net (choose Host a camera) and approved.

Tip: The camera's web page is the page you open by typing the camera's IP
address into a web browser on this network (for example http://192.168.1.64).
Sign in there to change the camera's video settings. If setup asks you to,
turn on ONVIF there too.

Setup has four steps: find your camera, check its video, connect your
YonderView account, and run a test. CamAgent saves nothing until the end.
Press Enter to keep a value shown in [brackets]. Press Ctrl+C to cancel.
"""

CAMERA = """CamAgent looks for your camera on this network and asks the camera how to get its video.
Most cameras answer through ONVIF, the camera's control and setup standard.
If your camera doesn't answer, the next steps show you how to turn ONVIF on.
"""

ONVIF_USERS = """To turn on ONVIF or add an ONVIF user, open the camera's web page. Menus vary by model:
  * Amcrest / Dahua:  Settings > Network > ONVIF (or System > Account > ONVIF User)
  * Hikvision:        Configuration > Network > Advanced Settings >
                      Integration Protocol > enable ONVIF, then Add a user
  * Reolink:          Network > Advanced > Port Settings > enable ONVIF (port 8000)
  * Axis:             System > ONVIF > add an ONVIF user
  * Others: look for ONVIF under Network, Security or Integration settings.
Also set the camera to keep its clock automatically (often called NTP, or
"sync with internet time"). ONVIF logins fail when the camera's clock is far off.
"""

STREAM = """YonderView needs your camera's main (full-quality) video in H.264 format, 1080p or smaller.
The camera must also send a complete picture (a "keyframe") at least every 4 seconds. Most cameras do
this already. In the camera's web page it's often called "I-frame interval" or "GOP".
Not sure what your camera sends? That's fine. CamAgent checks it below and tells you
what to change, if anything.
"""

STREAM_DETAILS = """Camera video settings (in the camera's web page, under Video, Encode or Camera > Video):
  Required:     H.264 (not H.265). The size limit for this camera on YonderView
                (1080p unless YonderView raised it). A keyframe at least
                every 4 seconds. Turn off "smart codec" and "H.264+" options.
  Recommended:  1080p at 30 fps (frames per second). 4-6 Mbps (megabits per
                second) is plenty. A keyframe (a full picture) every
                2 seconds: set "I-frame interval" or "keyframe interval" to
                twice the frame rate, for example 60 at 30 fps.
  Everything else is up to you.
"""

RTSP_PATHS = """Every camera has a video address that starts with rtsp:// (RTSP is how cameras send live video).
Here are the usual addresses for common brands, with your camera's IP address filled in:
  * Amcrest / Dahua:  rtsp://{ip}:554/cam/realmonitor?channel=1&subtype=0
  * Hikvision:        rtsp://{ip}:554/Streaming/Channels/101
  * Reolink:          rtsp://{ip}:554/h264Preview_01_main
  * Axis:             rtsp://{ip}:554/axis-media/media.amp
Not listed? Search the web for "<your camera model> RTSP address", or check the camera's manual.
Leave the username and password out of the address. CamAgent adds them.
"""

SERVER = """Next, CamAgent gets this camera's connection settings from YonderView. You must already have
added the camera on yonderview.net (Host a camera) and had it approved.
Easiest: sign in. CamAgent shows you a short code. On your phone or another computer, go to
yonderview.net/link and enter the code. Then pick this camera here.
Or: copy the connection settings from your camera's page on yonderview.net (Settings > Connection >
Create connection settings) and paste them here. YonderView shows them only once. Making new
ones turns the old ones off.
"""

DONE = """
You're done. Within a minute, open yonderview.net, find your camera and check that it says Live.
  * Keep this computer on, and don't let it sleep (Windows: Settings > System >
    Power > Screen and sleep > "Never" while plugged in).
  * To check on your camera later, open CamAgent and choose "See if your cameras are working".
    If something looks wrong, choose "Check for problems".
  * Stuck? Choose "Report a problem" on yonderview.net.
"""


def show(text):
    print(text.rstrip("\n"))
