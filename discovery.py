"""Peer discovery and online status over UDP broadcast.

Two threads:
  * broadcaster - sends HELLO|name|tcp_port|status every 3 s
  * listener    - receives HELLO from others, keeps the online list and removes
                  a peer that has not been heard from for 10 s
"""

import socket
import threading
import time

import protocol


class Peer:
    def __init__(self, name, ip, tcp_port, status, my_ip="127.0.0.1"):
        self.name = name
        self.tcp_port = tcp_port
        self.status = status
        self.last_seen = time.monotonic()
        self.ips = []              # every source address its HELLOs came from, best first
        self.ip = ip               # the best one (shown in /list, tried first when connecting)
        self.note_ip(ip, my_ip)

    def note_ip(self, ip, my_ip):
        """A machine with several network cards (VPN, WSL, VirtualBox...) can send HELLOs from
        several addresses. Keep them all, ordered: same subnet as us > other LAN > loopback."""
        if ip not in self.ips:
            self.ips.append(ip)
        prefix = my_ip.rsplit(".", 1)[0] + "."
        same = [i for i in self.ips if not i.startswith("127.") and i.startswith(prefix)]
        other = [i for i in self.ips if not i.startswith("127.") and i not in same]
        loop = [i for i in self.ips if i.startswith("127.")]
        self.ips = same + other + loop
        self.ip = self.ips[0]

    def copy(self):
        p = Peer(self.name, self.ip, self.tcp_port, self.status)
        p.ips = list(self.ips)
        p.last_seen = self.last_seen
        return p


def local_ipv4():
    """Our LAN address (no packet is sent; connect() on UDP only picks a route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def broadcast_targets(extra=()):
    """Addresses we broadcast to. Several are used because it depends on the OS/network:
    255.255.255.255 (limited), x.y.z.255 (subnet), 127.255.255.255 (same machine / offline)."""
    targets = ["255.255.255.255", "127.255.255.255"]
    ip = local_ipv4()
    if ip != "127.0.0.1":
        a, b, c, _ = ip.split(".")
        targets.append(f"{a}.{b}.{c}.255")
    for t in extra:
        if t not in targets:
            targets.append(t)
    return targets


class Discovery:
    def __init__(self, name, tcp_port, udp_port=protocol.UDP_PORT, on_event=None,
                 extra_targets=(), interval=protocol.BROADCAST_INTERVAL,
                 timeout=protocol.PEER_TIMEOUT):
        self.name = name
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.interval = interval
        self.timeout = timeout
        self.my_ip = local_ipv4()
        self.targets = broadcast_targets(extra_targets)
        self.on_event = on_event or (lambda kind, peer, info="": None)
        self.status = "online"
        self.peers = {}                       # name -> Peer (insertion order = list order)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self._rsock = None
        self._ssock = None
        self._last_error = None
        self._warned_dup = False

    # ------------------------------------------------------------ lifecycle
    def start(self):
        r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        try:
            r.bind(("", self.udp_port))
        except OSError as e:
            r.close()
            raise OSError(f"cannot bind UDP port {self.udp_port}: {e}") from e
        r.settimeout(1.0)
        self._rsock = r

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._ssock = s

        threading.Thread(target=self._broadcast_loop, daemon=True, name="udp-broadcaster").start()
        threading.Thread(target=self._listen_loop, daemon=True, name="udp-listener").start()

    def stop(self):
        self.stop_event.set()
        for s in (self._rsock, self._ssock):
            try:
                if s:
                    s.close()
            except OSError:
                pass

    # ------------------------------------------------------------- sending
    def set_status(self, status):
        self.status = status
        self._send_hello()        # tell everybody right away, not in up to 3 s

    def _send_hello(self):
        data = protocol.build_hello(self.name, self.tcp_port, self.status)
        sent, error = 0, None
        for target in self.targets:
            try:
                self._ssock.sendto(data, (target, self.udp_port))
                sent += 1
            except OSError as e:
                error = e
        if sent == 0 and error is not None:
            msg = f"cannot send UDP broadcast: {error}"
            if msg != self._last_error:           # report each distinct problem once
                self._last_error = msg
                self.on_event("error", None, msg)
        elif sent:
            self._last_error = None

    def _broadcast_loop(self):
        while not self.stop_event.is_set():
            self._send_hello()
            self.stop_event.wait(self.interval)

    # ----------------------------------------------------------- receiving
    def _listen_loop(self):
        while not self.stop_event.is_set():
            try:
                data, (ip, _port) = self._rsock.recvfrom(2048)
            except socket.timeout:
                data = None
            except OSError:
                if self.stop_event.is_set():
                    return
                time.sleep(0.2)        # e.g. Windows ICMP "connection reset" noise
                data = None
            if data is not None:
                self._handle(data, ip)
            self._expire()

    def _handle(self, data, ip):
        parsed = protocol.parse_hello(data)
        if parsed is None:
            return                                   # not ours / malformed: ignore
        name, tcp_port, status = parsed
        if name == self.name:
            if tcp_port != self.tcp_port and not self._warned_dup:
                self._warned_dup = True
                self.on_event("error", None,
                              f"another user is also called '{name}' - use unique names")
            return                                   # our own broadcast
        event = None
        with self.lock:
            peer = self.peers.get(name)
            if peer is None:
                peer = Peer(name, ip, tcp_port, status, self.my_ip)
                self.peers[name] = peer
                event = ("online", peer.copy(), "")
            else:
                old_status = peer.status
                peer.tcp_port, peer.status = tcp_port, status
                peer.note_ip(ip, self.my_ip)
                peer.last_seen = time.monotonic()
                if old_status != status:
                    event = ("status", peer.copy(), status)
        if event:
            self.on_event(*event)

    def _expire(self):
        now = time.monotonic()
        gone = []
        with self.lock:
            for name, peer in list(self.peers.items()):
                if now - peer.last_seen > self.timeout:
                    gone.append(peer.copy())
                    del self.peers[name]
        for peer in gone:
            self.on_event("offline", peer, "")

    # -------------------------------------------------------------- queries
    def snapshot(self):
        with self.lock:
            return [p.copy() for p in self.peers.values()]

    def find(self, query):
        """Look a peer up by list number or by name (case-insensitive)."""
        peers = self.snapshot()
        query = query.strip()
        if query.isdigit():
            idx = int(query)
            if 1 <= idx <= len(peers):
                return peers[idx - 1]
            raise LookupError(f"No user with number {idx}. Type /list to see who is online.")
        for p in peers:
            if p.name.lower() == query.lower():
                return p
        raise LookupError(f"No online user named '{query}'. Type /list to see who is online.")
