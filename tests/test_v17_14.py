"""0.17.14: camagent gpu-test builds one-pass commands (camera read once, 1080p/720p/480p) and reads ffmpeg's results."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import gputest  # noqa: E402

assert gputest.ways("qsv") == [] and len(gputest.ways("amf")) == 3
name, pre, chain, enc = gputest.ways("amf")[1]
cmd = gputest.command("ffmpeg", "rtsp://u:p@10.0.0.5/live", pre, chain, enc)
assert cmd.index("-hwaccel_output_format") < cmd.index("-i") and "-benchmark" in cmd
graph = cmd[cmd.index("-filter_complex") + 1]
assert graph.startswith("[0:v]split=3[s0][s1][s2]") and "vpp_amf=w=-2:h=480" in graph
assert cmd.count("-f") == 3 and cmd.count("null") == 3
text = "frame=  450 fps= 30 q=-0.0 size=N/A time=00:00:15.00 bitrate=N/A speed=1.01x\nbench: utime=3.20s stime=0.80s rtime=15.10s"
speed, cpu, wall = gputest.parse(text)
assert (speed, cpu, wall) == (1.01, 4.0, 15.1)
assert gputest.parse("nothing") == (None, None, None)
print("v0.17.14 OK")
