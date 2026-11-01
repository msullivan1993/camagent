"""0.17.8: following a camera whose local address changes (by its MAC); PTZ health on the Health tab; setup says
whether this PC makes the 720p/480p."""
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from camagent import follow, ptz as P  # noqa: E402

assert follow.norm_mac("HwAddress: 9C-8E-CD-1A-2B-3C") == "9c:8e:cd:1a:2b:3c"
assert follow.norm_mac("nothing") == ""
assert follow.with_host("rtsp://172.16.20.113:554/cam/realmonitor?channel=1", "172.16.20.150") == \
    "rtsp://172.16.20.150:554/cam/realmonitor?channel=1"
assert follow.with_host("rtsp://u:p@10.0.0.5/live", "10.0.0.9") == "rtsp://u:p@10.0.0.9/live"


def agent(host="172.16.20.113"):
    cfg = {"camera": {"host": host, "onvif_port": 80, "username": "op", "password": "pw",
                      "rtsp_url": f"rtsp://{host}:554/live"}}
    return NS(cfg=cfg, cam_id="cam_1", ptz=None, uplink=NS(restart_now=mock.Mock()))


with tempfile.TemporaryDirectory() as d, mock.patch.object(follow, "state_dir", return_value=Path(d)):
    a = agent()
    f = follow.Follower(a)
    f.state.update(mac="aa:bb:cc:dd:ee:ff", serial="SN1")
    others = [{"host": "172.16.20.40"}, {"host": "172.16.20.150"}]
    macs = {"172.16.20.40": "11:22:33:44:55:66", "172.16.20.150": "aa:bb:cc:dd:ee:ff"}
    login = mock.Mock(return_value=NS(SerialNumber="SN1"))
    with mock.patch.object(follow.camera, "discover", return_value=others), \
            mock.patch.object(follow, "reachable", return_value=True), \
            mock.patch.object(follow, "arp_mac", side_effect=lambda ip: macs.get(ip, "")), \
            mock.patch.object(follow.camera, "onvif_connect", return_value=NS(devicemgmt=NS(GetDeviceInformation=login))):
        f._look_for_it("172.16.20.113", 80)
    # followed to the device with the same MAC (not the other one), checked the login, restarted the stream
    assert a.cfg["camera"]["host"] == "172.16.20.150"
    assert a.cfg["camera"]["rtsp_url"] == "rtsp://172.16.20.150:554/live"
    login.assert_called_once()
    a.uplink.restart_now.assert_called_once()
    assert "172.16.20.113 to 172.16.20.150" in f.notice
    # after a restart: the move is used again while the settings still name the old address...
    a2 = agent()
    f2 = follow.Follower(a2)
    assert a2.cfg["camera"]["host"] == "172.16.20.150" and f2.notice
    # ...but an address the host set by hand since wins
    a3 = agent("172.16.20.200")
    follow.Follower(a3)
    assert a3.cfg["camera"]["host"] == "172.16.20.200"

with tempfile.TemporaryDirectory() as d, mock.patch.object(follow, "state_dir", return_value=Path(d)):
    a = agent()
    f = follow.Follower(a)
    f.state.update(mac="aa:bb:cc:dd:ee:ff", serial="SN1")
    bad = mock.Mock(side_effect=RuntimeError("401 not authorized"))
    with mock.patch.object(follow.camera, "discover", return_value=[{"host": "172.16.20.150"}]), \
            mock.patch.object(follow, "reachable", return_value=True), \
            mock.patch.object(follow, "arp_mac", return_value="aa:bb:cc:dd:ee:ff"), \
            mock.patch.object(follow.camera, "onvif_connect", return_value=NS(devicemgmt=NS(GetDeviceInformation=bad))):
        f._look_for_it("172.16.20.113", 80)
    assert a.cfg["camera"]["host"] == "172.16.20.113"          # same MAC but the login fails there: stays put
    with mock.patch.object(follow.camera, "discover", return_value=[{"host": "172.16.20.150"}]), \
            mock.patch.object(follow, "reachable", return_value=True), \
            mock.patch.object(follow, "arp_mac", return_value="aa:bb:cc:dd:ee:ff"), \
            mock.patch.object(follow.camera, "onvif_connect") as again:
        f._look_for_it("172.16.20.113", 80)
    again.assert_not_called()                                   # refused once: not tried again today

# 0.17.12 (security review): the MAC only finds candidates; the device must be the camera (its serial number), on the
# same network, and the host can switch following off
with tempfile.TemporaryDirectory() as d, mock.patch.object(follow, "state_dir", return_value=Path(d)):
    a = agent()
    f = follow.Follower(a)
    f.state.update(mac="aa:bb:cc:dd:ee:ff", serial="SN1")
    impostor = NS(devicemgmt=NS(GetDeviceInformation=lambda: NS(SerialNumber="FAKE")))
    with mock.patch.object(follow.camera, "discover", return_value=[{"host": "172.16.20.150"}, {"host": "10.9.9.9"}]), \
            mock.patch.object(follow, "reachable", return_value=True), \
            mock.patch.object(follow, "arp_mac", return_value="aa:bb:cc:dd:ee:ff"), \
            mock.patch.object(follow.camera, "onvif_connect", return_value=impostor) as conn:
        f._look_for_it("172.16.20.113", 80)
    assert a.cfg["camera"]["host"] == "172.16.20.113" and a.uplink.restart_now.call_count == 0
    assert [c.args[0] for c in conn.call_args_list] == ["172.16.20.150"]      # another network: never asked
    assert follow.same_network("10.0.0.5", "10.0.0.200") and not follow.same_network("10.0.0.5", "10.0.1.5")
    # a video address on another device (an NVR) isn't rewritten when the camera moves
    a = agent()
    a.cfg["camera"]["rtsp_url"] = "rtsp://172.16.20.2:554/ch1"
    follow.Follower(a)._use("172.16.20.150")
    assert a.cfg["camera"]["rtsp_url"] == "rtsp://172.16.20.2:554/ch1" and a.cfg["camera"]["host"] == "172.16.20.150"
    # switched off by the host
    a = agent()
    a.cfg["camera"]["follow"] = False
    f = follow.Follower(a)
    f.state.update(mac="aa:bb:cc:dd:ee:ff", serial="SN1")
    f.down = 5
    with mock.patch.object(f, "_look_for_it") as look, mock.patch.object(follow, "reachable", return_value=False):
        stop = mock.Mock(); stop.is_set.side_effect = [False, True]; stop.wait = lambda s: None
        f.run(stop)
    look.assert_not_called()

# PTZ health: a note when it hasn't been reachable for minutes, or stops answering position requests
cfg = {"platform": {"camera_id": "p1"}, "agent": {}, "camera": {}}
p = P.PTZ(cfg)
assert p.problem() == ""
p.lost_since = time.monotonic() - 300
assert "haven't been reachable for 5 min" in p.problem()
p.connected, p.lost_since, p.reports_position = True, None, None
p.status_fails = 6
assert "isn't answering position requests" in p.problem()
p.reports_position = False                                   # a camera that says it doesn't report: no note
assert p.problem() == ""
p.status_fails, p.status_ms = 0, 140
assert p.health() == {"connected": True, "status_ms": 140, "status_fails": 0}
print("v0.17.8 OK")
