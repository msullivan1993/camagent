"""Following a camera whose local address changes (0.17.8).

A camera should have a fixed (reserved) address on its network, but a router reset or a new router can hand it
another one. CamAgent learns the camera's hardware (MAC) address while it can reach it: from the camera over ONVIF,
or from this computer's ARP table. If the camera then stops answering at its address for a few minutes, CamAgent
asks the local network who's there (the same ONVIF discovery setup uses), finds the device with that MAC, checks the
camera's login works there, and moves to it. Never to a device whose MAC doesn't match, nor one where the login
fails. The new address is kept in CamAgent's state folder (the camera's settings file isn't changed); the camera's
Health tab says what happened, so the host can give it a fixed address.

0.17.12 (security review): a device on the network can copy a MAC address, so the MAC only finds candidates. CamAgent
moves only to an address on the same local network whose device reports the camera's own serial number (learned
while the camera answered), never tries an address again for a day after it refused the login or didn't match, and
the host can switch following off ([camera] follow = false).
"""
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import time
from urllib.parse import urlparse, urlunparse

from . import camera
from .config import state_dir

DOWN_CHECKS = 3                 # the camera unreachable this many checks in a row...
CHECK_S = 60                    # ...one a minute: then look for it
LEARN_EVERY_S = 600             # until its MAC and serial number are known, try to learn them this often
REFUSED_S = 24 * 3600           # an address that refused the login or wasn't the camera: left alone this long
_MAC = re.compile(r"([0-9a-f]{2})[:-]([0-9a-f]{2})[:-]([0-9a-f]{2})[:-]([0-9a-f]{2})[:-]([0-9a-f]{2})[:-]([0-9a-f]{2})",
                  re.I)


def norm_mac(text):
    m = _MAC.search(str(text or ""))
    return ":".join(g.lower() for g in m.groups()) if m else ""


def arp_mac(ip):
    """The MAC this computer has seen for ip (its ARP table), or ""."""
    try:
        if os.name == "nt":
            out = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if line.split()[:1] == [ip]:
                    return norm_mac(line)
            return ""
        with open("/proc/net/arp", encoding="ascii", errors="replace") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts and parts[0] == ip and len(parts) > 3:
                    mac = norm_mac(parts[3])
                    return "" if mac == "00:00:00:00:00:00" else mac
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def onvif_mac(cam):
    """The camera's own MAC over ONVIF (GetNetworkInterfaces), or ""."""
    try:
        for nic in cam.devicemgmt.GetNetworkInterfaces() or []:
            mac = norm_mac(getattr(getattr(nic, "Info", None), "HwAddress", ""))
            if mac:
                return mac
    except Exception:  # noqa: BLE001
        pass
    return ""


def device_serial(cam):
    """The camera's serial number over ONVIF (its login), or ""."""
    try:
        return str(getattr(cam.devicemgmt.GetDeviceInformation(), "SerialNumber", "") or "").strip()[:80]
    except Exception:  # noqa: BLE001
        return ""


def same_network(a, b):
    """Both addresses on the same local network (the /24 for IPv4, the /64 for IPv6)."""
    try:
        x, y = ipaddress.ip_address(a), ipaddress.ip_address(b)
        bits = 24 if x.version == 4 else 64
        return x.version == y.version and ipaddress.ip_network(f"{x}/{bits}", strict=False) == \
            ipaddress.ip_network(f"{y}/{bits}", strict=False)
    except ValueError:
        return False


def reachable(host, port, timeout=3):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def with_host(url, host):
    """url with its host replaced (the port and path kept)."""
    u = urlparse(url)
    if not u.hostname:
        return url
    netloc = host + (f":{u.port}" if u.port else "")
    if u.username:
        netloc = u.netloc.rsplit("@", 1)[0] + "@" + netloc
    return urlunparse(u._replace(netloc=netloc))


class Follower:
    def __init__(self, agent):
        self.agent = agent
        self.cfg = agent.cfg
        self.cid = agent.cam_id
        self.log = logging.getLogger(f"{self.cid}.follow")
        self.down = 0
        self.notice = ""
        self.refused = {}                # address -> when it refused the login or didn't match
        self.state = self._load()
        self._apply_saved()

    # ---------- what's remembered ----------
    def _path(self):
        return state_dir() / f"follow-{self.cid}.json"

    def _load(self):
        try:
            with open(self._path(), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            path = self._path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.state), encoding="utf-8")
        except OSError as e:
            self.log.debug("couldn't save the camera's address: %s", e)

    def _apply_saved(self):
        """A move found before a restart: use it, as long as the settings still name the old address (if the host
        changed the address by hand since, theirs wins)."""
        c = self.cfg["camera"]
        moved = self.state.get("moved") or {}
        if moved.get("from") and moved.get("to") and c.get("host") == moved["from"]:
            self._use(moved["to"])
            self.notice = self._notice(moved["from"], moved["to"])

    # ---------- the watch ----------
    def run(self, stop):
        stop.wait(30)                                    # let everything connect first
        last_learn = 0.0
        while not stop.is_set():
            c = self.cfg["camera"]
            host = c.get("host", "")
            port = int(c.get("onvif_port") or 80)
            if host and reachable(host, port):
                self.down = 0
                if not (self.state.get("mac") and self.state.get("serial")) \
                        and time.monotonic() - last_learn > LEARN_EVERY_S:
                    last_learn = time.monotonic()
                    self._learn(host)
            elif host:
                self.down += 1
                if (self.down >= DOWN_CHECKS and self.state.get("mac") and self.state.get("serial")
                        and self.cfg["camera"].get("follow", True)):
                    self._look_for_it(host, port)
            stop.wait(CHECK_S)

    def _learn(self, host):
        ptz = getattr(self.agent, "ptz", None)
        cam = getattr(ptz, "onvif", None) if ptz is not None else None
        if not self.state.get("mac"):
            mac = (onvif_mac(cam) if cam is not None else "") or arp_mac(host)
            if mac:
                self.state["mac"] = mac
                self._save()
                self.log.info("camera's hardware address: %s (CamAgent finds it by this if its address changes)", mac)
        if not self.state.get("serial"):
            c = self.cfg["camera"]
            try:
                cam = cam or camera.onvif_connect(host, int(c.get("onvif_port") or 80), c.get("username", ""),
                                                  c.get("password", ""))
                serial = device_serial(cam)
            except Exception:  # noqa: BLE001 - no ONVIF: no serial, so no following
                serial = ""
            if serial:
                self.state["serial"] = serial
                self._save()

    def _look_for_it(self, old, port):
        mac = self.state["mac"]
        self.log.warning("the camera isn't answering at %s; looking for it on the network (MAC %s)", old, mac)
        try:
            found = camera.discover(timeout=4)
        except Exception as e:  # noqa: BLE001
            self.log.warning("couldn't search the network: %s", e)
            return
        now = time.monotonic()
        for item in found:
            ip = item.get("host", "")
            if not ip or ip == old or not same_network(ip, old) or now - self.refused.get(ip, -REFUSED_S) < REFUSED_S:
                continue
            if not reachable(ip, port) or arp_mac(ip) != mac:   # reaching it also fills the ARP table
                continue
            c = self.cfg["camera"]
            try:
                info = camera.onvif_connect(ip, port, c.get("username", ""), c.get("password", "")) \
                    .devicemgmt.GetDeviceInformation()
                serial = str(getattr(info, "SerialNumber", "") or "").strip()[:80]
            except Exception as e:  # noqa: BLE001 - same MAC, but the login fails there: don't move
                self.refused[ip] = now
                self.log.warning("found the camera's MAC at %s, but its login didn't work there (%s); not moving "
                                 "(not trying it again today)", ip, camera.friendly_error(e))
                continue
            if serial != self.state["serial"]:
                self.refused[ip] = now
                self.log.warning("the device at %s has the camera's MAC but isn't the camera (another serial "
                                 "number); not moving", ip)
                continue
            self._moved(old, ip)
            return
        self.log.info("the camera wasn't found on the network; still trying at %s", old)

    def _moved(self, old, new):
        orig = (self.state.get("moved") or {}).get("from") or old
        self.state["moved"] = {"from": orig, "to": new, "at": int(time.time())}
        self._save()
        self._use(new)
        self.down = 0
        self.notice = self._notice(old, new)
        self.log.warning("the camera moved from %s to %s on the local network; CamAgent followed it", old, new)
        if getattr(self.agent, "uplink", None):
            self.agent.uplink.restart_now("the camera's address changed")
        ptz = getattr(self.agent, "ptz", None)
        if ptz is not None and ptz.connected:
            ptz._lost(RuntimeError("the camera's address changed"))

    def _use(self, host):
        c = self.cfg["camera"]
        if urlparse(c.get("rtsp_url", "")).hostname == c.get("host"):     # not a video address on another device
            c["rtsp_url"] = with_host(c.get("rtsp_url", ""), host)
        c["host"] = host

    @staticmethod
    def _notice(old, new):
        return (f"the camera's local address changed from {old} to {new} (CamAgent followed it by its hardware "
                "address). Give it a fixed (reserved) address in your router so it doesn't move again")
