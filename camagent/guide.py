"""Plain-language guidance shown during setup. The words are in text.py (edit them there). Menus differ between
camera models, so wording stays general where it has to, and names the usual places for the common brands."""
from . import text

INTRO = text.GUIDE_INTRO
CAMERA = text.GUIDE_CAMERA
ONVIF_USERS = text.GUIDE_ONVIF_USERS
STREAM = text.GUIDE_STREAM
STREAM_DETAILS = text.GUIDE_STREAM_DETAILS
RTSP_PATHS = text.GUIDE_RTSP_PATHS
SERVER = text.GUIDE_SERVER
DONE = text.GUIDE_DONE


def show(text):
    print(text.rstrip("\n"))
