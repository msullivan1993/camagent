"""CamAgent's wording: the menu, the first-run question, the setup headings and prompts, and the guide texts.

HOW TO EDIT THIS FILE (plain words)
  * Each message is a name on the left, an equals sign, and the words in quotes. Change only the words in quotes.
  * Keep every {placeholder} in curly braces exactly as it is, for example {version} or {name}.
    CamAgent fills those in when it shows the message. You can move a placeholder inside the sentence.
  * Keep each short message on one line. Long messages in triple quotes (three quote marks) can span lines.
  * Don't use a double quote mark inside a message unless you write it as \\" (a backslash first).
  * Menu labels are shown as numbered choices. Setup compares choices by these exact words, so change a label
    here only, never in another file.
  * After you edit, save the file and run CamAgent again. A typo in the quotes stops CamAgent from starting.
    Then undo your last change.
Not everything is here yet. Error messages and some small prompts are still inside the other files.
"""

# ---------------------------------------------------------------------------------------------------------
# First run: which way to set up
# ---------------------------------------------------------------------------------------------------------
MODE_WELCOME = "Welcome to CamAgent."
MODE_QUESTION = "How do you want to set up your cameras?"
MODE_WEBSITE = "Set up on the website (recommended)"
MODE_WEBSITE_ABOUT = "CamAgent links to your YonderView account. You then set up your cameras on yonderview.net."
MODE_LOCAL = "Set up here on this computer (advanced)"
MODE_LOCAL_ABOUT = "You answer CamAgent's questions in this window to add each camera."
MODE_SWITCH_NOTE = "You can switch any time from the menu."
MODE_PROMPT = "Type 1 or 2 and press Enter (or 0 to quit): "
MODE_RETRY = "Type 1 or 2, or 0 to quit."

# ---------------------------------------------------------------------------------------------------------
# The menu (both modes)
# ---------------------------------------------------------------------------------------------------------
MENU_TITLE = "=== CamAgent {version} ==="
MENU_PROMPT = "Type a number and press Enter: "
MENU_QUIT = "Quit"
MENU_BACK = "Go back"
MENU_PICK_AGAIN = "Type a number from 1 to {n}, or 0 to {back}."
MENU_PRESS_ENTER = "\nPress Enter to go back to the menu."
MENU_CANCELED = "\nCanceled. Nothing was changed."
MENU_WENT_WRONG = "Something went wrong: {error}"
MENU_WENT_WRONG_2 = 'To look for the cause, choose "Check for problems" (4) in the menu.'
MENU_WENT_WRONG_3 = 'Still stuck? Choose "Report a problem" on yonderview.net and include the message above.'

# Headings inside the menu
HEAD_CAMERAS = "Cameras"
HEAD_WRONG = "If something's wrong"
HEAD_SETTINGS = "Settings"

# Local mode menu labels
M_LIST = "See if your cameras are working"
M_ADD = "Add a camera"
M_CHANGE_OR_REMOVE = "Change or remove a camera..."
M_CHECK = "Check for problems"
M_RESTART = "Restart CamAgent"
M_START = "Start CamAgent (runs in the background, starts with this computer)"
M_LOG = "Show the recent log (useful for support)"
M_SIGN_OUT = "Sign out of YonderView"
M_SIGN_IN = "Sign in to YonderView"
M_UPDATES = "Updates..."
M_ADVANCED = "Advanced tools..."
M_SWITCH_TO_WEBSITE = "Switch to setting up on the website"

# Submenus
SUB_CHANGE_OR_REMOVE = "Change or remove a camera"
SUB_UPDATES = "Updates"
SUB_ADVANCED = "Advanced tools"
SUB_CHANGE_CAMERA = "Change a camera's settings"
SUB_WEATHER = "Add or change a weather station (beta)"
SUB_REMOVE = "Remove a camera from this computer"
SUB_UPDATE_NOW = "Update CamAgent now"
SUB_AUTO_ON = "Automatic nightly updates are on. Turn them off"
SUB_AUTO_OFF = "Automatic nightly updates are off. Turn them on"
SUB_SEARCH = "Search the network for cameras"
SUB_PROBE = "See what a camera can do (focus, day/night, wiper...)"

# Website mode menu labels
W_OPEN_LINK = "Open {address} to set up cameras"
W_CAMERAS = "See the cameras this computer sends"
W_CHECK = "Check for problems"
W_LOG = "Show the recent log"
W_SIGN_IN = "Sign in to YonderView"
W_SIGN_OUT = "Sign out of YonderView"
W_UPDATES = "Updates..."
W_SWITCH_TO_LOCAL = "Switch to setting up here on this computer (advanced)"
W_HEAD_SET_UP = "Set up cameras"
W_HEAD_HELP = "Help"
W_HEAD_SETTINGS = "Settings"

# Website mode messages
W_WORKING = "CamAgent is getting this computer ready for setup on the website..."
W_READY = "This computer is ready. Open {address} and pick this computer to add your cameras."
W_NEED_SIGN_IN = "You haven't signed in yet. Choose {number} to sign in. Then you can set up cameras on {address}."
W_OPEN_PRINT = "To set up your cameras, open this address in a web browser (on any computer or phone):"
W_OPEN_SIGNED_OUT = "Sign in first, so the website can find this computer."
W_OPENING = "CamAgent is opening it in your browser..."
W_NO_CAMERAS = "No cameras are set up on this computer yet. Set them up on {address}."
W_STATE_LINE = "YonderView: {account}  |  {running}  |  {cams}"
W_PROBLEMS = ("! CamAgent found {problems} with your saved settings."
              " Choose {number} (Check for problems) to see what's wrong.")
W_SETUP_FAILED = "CamAgent couldn't finish getting this computer ready: {error}"
W_SETUP_RETRY = "Choose \"Check for problems\" to see what's wrong. CamAgent tries again next time you open it."

# Status line pieces (both modes)
S_CAMERA_ONE = "1 camera"
S_CAMERAS_N = "{n} cameras"
S_RUNNING = "CamAgent is running"
S_STOPPED = "CamAgent is stopped"
S_NOT_INSTALLED = "CamAgent isn't running yet"
S_OTHER_STATE = "CamAgent: {state}"
S_ACCOUNT_IN = "signed in as {name}"
S_ACCOUNT_OUT = "not signed in"
S_ACCOUNT_FALLBACK = "your account"
S_LINE = "{cams}  |  {running}  |  YonderView: {account}"
S_PROBLEMS = ("! CamAgent found {problems} with your saved settings."
              " Choose 4 (Check for problems) to see what's wrong and how to fix it.")
S_PROBLEM_ONE = "1 problem"
S_PROBLEMS_N = "{n} problems"
S_NO_CAMERAS = "You haven't added a camera yet. Choose 2 to add one."
S_NEXT_START = "Next, choose 5 to start CamAgent. CamAgent then keeps sending your cameras in the background."
S_NEXT_START_2 = "It starts by itself whenever this computer turns on."
S_STOPPED_NOTE = "CamAgent is stopped, so it isn't sending your cameras. Choose 5 to start it again."

# Mode switching
SWITCHED_TO_WEBSITE = "OK. CamAgent now sets up cameras on the website. You didn't lose any cameras."
SWITCHED_TO_LOCAL = "OK. CamAgent now sets up cameras here on this computer. You didn't lose any cameras."

# ---------------------------------------------------------------------------------------------------------
# Setup (configure.py): headings and the prompts people see most
# ---------------------------------------------------------------------------------------------------------
H_STEP1 = "Step 1 of 4: Your camera"
H_STEP2 = "Step 2 of 4: The camera's video"
H_STEP3 = "Step 3 of 4: Your YonderView account"
H_STEP4 = "Step 4 of 4: Testing the connection"
H_CONVERT = "Optional: make the video 1080p"
H_VERSIONS = "The lower qualities (720p and 480p)"
H_WEATHER = "Weather station (beta)"

P_SEARCH = "Do you want CamAgent to search this network for your camera? (Easiest; takes 3 seconds)"
P_SEARCHING = "CamAgent is searching this network (3 seconds)..."
P_FOUND = "CamAgent found these cameras (IP address:port, then the camera's name). Which one is yours?"
P_NONE_OF_THESE = "None of these. I'll type the address myself"
P_IP = "Your camera's IP address (four numbers with dots, like 192.168.1.64)"
P_ONVIF_PORT = ("The camera's ONVIF port, the port for its control and setup standard "
                "(not sure? Press Enter for the usual one. Reolink cameras use 8000)")
P_USERNAME = "Camera's username"
P_PASSWORD = "Camera's password"
P_LOGIN_INTRO_1 = ("  Next, enter your camera's login (not your YonderView login). The safest choice is a login just for")
P_LOGIN_INTRO_2 = ("  YonderView, with Operator rights. Operator rights let CamAgent turn and zoom the camera and use "
                   "presets.")
P_LOGIN_INTRO_3 = "  They don't let CamAgent change the camera's settings."
P_KEEP_LOGIN = "Keep the camera's current login ({username})"
P_ASKING_CAMERA = "CamAgent is asking your camera what video it can send (this can take up to a minute)..."
P_TYPE_NUMBER = "Type a number and press Enter: "

MAKE_LOGIN = ("Let CamAgent make up a login (\"{login_name}\" and a strong password) "
              "for you to add on the camera (recommended)")
OWN_LOGIN = "Use a login your camera already has"
TRY_FIX = "Let CamAgent try to change it for you (with CamAgent's own camera login)"
SIGN_IN = "Sign in to YonderView and pick your camera (easiest)"
PASTE = "Paste the connection settings from your camera's Connection page"
TYPED = "Type the values one by one (only if YonderView support asks you to)"

# The "make the video 1080p" step
C_YOUR_CAMERA = "Your camera sends {what}{codec}. YonderView takes 1080p in H.264."
C_H265 = " in H.265"
C_INFO_1 = "CamAgent can convert the video to 1080p H.264 on this computer before it sends the video."
C_INFO_2 = "This doesn't change your camera. A mini PC with an Intel graphics chip does this easily."
C_INFO_3 = "Without one, this computer needs a strong processor."
C_HERE = "Convert the video to 1080p H.264 on this computer (recommended)"
C_SOURCE = "Send your camera's own {what}"
C_CAMERA_SIDE = "I'll change the camera's setting to 1080p H.264 myself"
C_BANDWIDTH = ("Bigger video needs more upload and data: about 8 Mbps for 1440p (about 2.6 TB a month) and about "
               "16 Mbps for 4K (about 5 TB a month). 1080p uses about 4.5 Mbps (about 1.5 TB a month). "
               "Check your internet's upload speed and data cap.")

# Testing the connection (step 4)
F_REPLACE = "Camera '{cid}' is already set up on this computer. Do you want to replace its settings?"
F_YV = "  YonderView: {msg}"
F_OVER_LIMIT_1 = "\n  Your video is {height}p, but YonderView's limit for this camera is {limit}p."
F_OVER_LIMIT_2 = "  YonderView would decline (refuse) the video as soon as it connects."
F_OVER_LIMIT_3 = ("  Change it yourself in the camera's web page: find the Video (or Encode) settings, set the "
                  "Resolution to {limit}p or lower,")
F_OVER_LIMIT_4 = "  and save. Then choose \"I've changed the camera. Check again\"."
F_OVER_LIMIT_5 = ("  If you want to send more than {limit}p, ask YonderView to raise this camera's limit. "
                  "Until then, YonderView declines bigger video.")
F_OTHER = "Pick a different video from the camera"
F_RECHECK = "I've changed the camera. Check again"
F_KEEP = "Save anyway (not recommended: YonderView declines the video until you fix this)"
F_CHECK_NET = "  Check this computer's internet connection first. If that's fine, get the YonderView settings again."
F_AGAIN = "Enter the YonderView details again"
F_ANYWAY = "Save anyway (not recommended: CamAgent can't connect until you fix this)"
F_CANCEL = "Cancel without saving"
F_CANCELED = "Canceled. CamAgent saved nothing."
F_SAVED = "\nCamAgent saved camera '{cid}'.  ({saved})"

# Adding another camera
ADD_N_1 = "Adding camera #{n} on this computer. To keep a value shown in [brackets], press Enter."
ADD_N_2 = "You need this camera's IP address and login."
ADD_N_3 = "You also need to sign in to YonderView (or have its connection settings)."

# Signing in
SIGNIN_INTRO_1 = "\nWhen you sign in, this computer links to your YonderView account. Then CamAgent can get your"
SIGNIN_INTRO_2 = "cameras' connection settings. You sign in on the website. CamAgent never sees your password."
SIGNIN_DONE_1 = "  You're signed in as {name}. (You can unlink this computer any time from your profile"
SIGNIN_DONE_2 = "  on the website.)"
SIGNOUT_DONE = "You're signed out. This computer's sign-in no longer works, here or anywhere."
NOT_SIGNED_IN = "You haven't signed in to YonderView on this computer."

ADD_LOGIN_STEPS = """Open your camera's web page, sign in as its admin, and add this login. Menus vary by model:
  * Amcrest / Dahua:  Setup > System > Account. Add it under ONVIF User. Also add it under
                      Users (the normal list) so the map can show exact degrees.
  * Hikvision:        Configuration > Network > Advanced Settings > Integration Protocol > Add
  * Reolink:          Settings > Users > Add User
  * Others: look for Users or ONVIF User under System, Security or Network.
Rights: choose Operator (or a User allowed to use PTZ, which means turn and zoom).
Don't choose Administrator. YonderView doesn't need it."""

# ---------------------------------------------------------------------------------------------------------
# Guide texts (shown during setup)
# ---------------------------------------------------------------------------------------------------------
GUIDE_INTRO = """
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

GUIDE_CAMERA = """CamAgent looks for your camera on this network and asks the camera how to get its video.
Most cameras answer through ONVIF, the camera's control and setup standard.
If your camera doesn't answer, the next steps show you how to turn ONVIF on.
"""

GUIDE_ONVIF_USERS = """To turn on ONVIF or add an ONVIF user, open the camera's web page. Menus vary by model:
  * Amcrest / Dahua:  Settings > Network > ONVIF (or System > Account > ONVIF User)
  * Hikvision:        Configuration > Network > Advanced Settings >
                      Integration Protocol > enable ONVIF, then Add a user
  * Reolink:          Network > Advanced > Port Settings > enable ONVIF (port 8000)
  * Axis:             System > ONVIF > add an ONVIF user
  * Others: look for ONVIF under Network, Security or Integration settings.
Also set the camera to keep its clock automatically (often called NTP, or
"sync with internet time"). ONVIF logins fail when the camera's clock is far off.
"""

GUIDE_STREAM = """YonderView needs your camera's main (full-quality) video in H.264 format, 1080p or smaller.
The camera must also send a complete picture (a "keyframe") at least every 4 seconds. Most cameras do
this already. In the camera's web page it's often called "I-frame interval" or "GOP".
Not sure what your camera sends? That's fine. CamAgent checks it below and tells you
what to change, if anything.
"""

GUIDE_STREAM_DETAILS = """Camera video settings (in the camera's web page, under Video, Encode or Camera > Video):
  Required:     H.264 (not H.265). The size limit for this camera on YonderView
                (1080p unless YonderView raised it). A keyframe at least
                every 4 seconds. Turn off "smart codec" and "H.264+" options.
  Recommended:  1080p at 30 fps (frames per second). 4-6 Mbps (megabits per
                second) is plenty. A keyframe (a full picture) every
                2 seconds: set "I-frame interval" or "keyframe interval" to
                twice the frame rate, for example 60 at 30 fps.
  Everything else is up to you.
"""

GUIDE_RTSP_PATHS = """Every camera has a video address that starts with rtsp:// (RTSP is how cameras send live video).
Here are the usual addresses for common brands, with your camera's IP address filled in:
  * Amcrest / Dahua:  rtsp://{ip}:554/cam/realmonitor?channel=1&subtype=0
  * Hikvision:        rtsp://{ip}:554/Streaming/Channels/101
  * Reolink:          rtsp://{ip}:554/h264Preview_01_main
  * Axis:             rtsp://{ip}:554/axis-media/media.amp
Not listed? Search the web for "<your camera model> RTSP address", or check the camera's manual.
Leave the username and password out of the address. CamAgent adds them.
"""

GUIDE_SERVER = """Next, CamAgent gets this camera's connection settings from YonderView. You must already have
added the camera on yonderview.net (Host a camera) and had it approved.
Easiest: sign in. CamAgent shows you a short code. On your phone or another computer, go to
yonderview.net/link and enter the code. Then pick this camera here.
Or: copy the connection settings from your camera's page on yonderview.net (Settings > Connection >
Create connection settings) and paste them here. YonderView shows them only once. Making new
ones turns the old ones off.
"""

GUIDE_DONE = """
You're done. Within a minute, open yonderview.net, find your camera and check that it says Live.
  * Keep this computer on, and don't let it sleep (Windows: Settings > System >
    Power > Screen and sleep > "Never" while plugged in).
  * To check on your camera later, open CamAgent and choose "See if your cameras are working".
    If something looks wrong, choose "Check for problems".
  * Stuck? Choose "Report a problem" on yonderview.net.
"""
