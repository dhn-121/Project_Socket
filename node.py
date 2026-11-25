"""TCP side of LAN Chat: chat connections and file transfer.

Threads (besides main / UDP threads that live in discovery.py):
  * acceptor        - accept() new TCP connections, start a thread for each
  * receiver (peer) - one per chat connection: reads frames, shows messages
  * file receiver   - one per incoming file (its own TCP connection)
  * file sender     - one per outgoing file (its own TCP connection)

A file always travels on its OWN connection, so a long transfer never blocks chat.
All shared state (connections, current partner, unread messages) is guarded by self.lock.
"""

import os
import shutil
import socket
import threading
import time
from pathlib import Path

import protocol
from protocol import ConnectionClosed, FrameReader, ProtocolError


class ChatError(Exception):
    """A problem the user should be told about (never a crash)."""


class Conn:
    """One chat connection to one peer."""

    def __init__(self, sock, name, remote_ip, peer_port, initiator):
        self.sock = sock
        self.name = name              # peer's name
        self.remote_ip = remote_ip
        self.peer_port = peer_port    # peer's listening TCP port
        self.initiator = initiator    # name of the side that opened the connection
        self.reader = FrameReader(sock)
        self.send_lock = threading.Lock()
        self.closed = False

    def send(self, data):
        with self.send_lock:          # two threads must not interleave bytes of two frames
            self.sock.sendall(data)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for op in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                op()
            except OSError:
                pass


def tune_socket(sock):
    """Low latency + detect a crashed peer within ~25 s (best effort, OS dependent)."""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        elif hasattr(sock, "ioctl") and hasattr(socket, "SIO_KEEPALIVE_VALS"):   # Windows
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 5000))
    except OSError:
        pass


class Node:
    def __init__(self, name, ui, download_dir="downloads", throttle_kbps=0):
        self.name = name
        self.ui = ui
        self.download_dir = download_dir
        self.throttle_kbps = throttle_kbps     # demo helper: slow file sending down
        self.discovery = None
        self.tcp_port = None
        self.server = None
        self.stopping = False

        self.lock = threading.RLock()
        self.conns = {}        # peer name -> Conn
        self.current = None    # name of the current chat partner
        self.unread = {}       # peer name -> [texts not yet shown]
        self.history = {}      # peer name -> [(sender, text)]
        self._transfer_id = 0

    # ------------------------------------------------------------ lifecycle
    def listen(self, port=None):
        """Open the TCP listening socket. port=None -> first free port from 5000."""
        candidates = [port] if port else range(5000, 5100)
        last_error = None
        for p in candidates:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name != "nt":
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                srv.bind(("", p))
            except OSError as e:
                srv.close()
                last_error = e
                continue
            srv.listen(16)
            self.server = srv
            self.tcp_port = p
            return p
        raise OSError(f"cannot listen on TCP port {port or '5000-5099'}: {last_error}")

    def attach(self, discovery):
        self.discovery = discovery

    def start(self):
        threading.Thread(target=self._accept_loop, daemon=True, name="tcp-acceptor").start()

    def shutdown(self):
        self.stopping = True
        with self.lock:
            conns = list(self.conns.values())
            self.conns.clear()
            self.current = None
        for c in conns:
            try:
                c.send(protocol.BYE)
            except OSError:
                pass
            c.close()
        try:
            self.server.close()
        except OSError:
            pass

    def _say(self, text):
        self.ui.print_line(text)

    # ------------------------------------------------------------- accepting
    def _accept_loop(self):
        while not self.stopping:
            try:
                sock, addr = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock, addr[0]), daemon=True,
                             name="tcp-receiver").start()

    def _serve(self, sock, remote_ip):
        """First frame must be HI; it says whether this is a chat or a file connection."""
        tune_socket(sock)
        reader = FrameReader(sock)
        try:
            sock.settimeout(5)
            ftype, fields, _ = protocol.read_header(reader)
            if ftype != "HI":
                raise ProtocolError("expected HI")
            name, port_text, kind = fields
            if not protocol.valid_name(name) or name == self.name or kind not in ("CHAT", "FILE"):
                raise ProtocolError("bad HI")
            peer_port = int(port_text)
            sock.settimeout(None)
        except (ProtocolError, ConnectionClosed, OSError, ValueError):
            try:
                sock.close()
            except OSError:
                pass
            return
        if kind == "CHAT":
            conn = Conn(sock, name, remote_ip, peer_port, initiator=name)
            conn.reader = reader                       # keep bytes already buffered
            if self._register(conn) is conn:
                self._run_chat(conn)
            else:
                conn.close()
        else:
            self._receive_file(sock, reader, name)

    # ------------------------------------------------------- chat connections
    def _register(self, conn):
        """Store conn as THE connection to that peer. If both sides connected at the same
        moment there are two; both sides keep the one opened by the smaller name."""
        with self.lock:
            old = self.conns.get(conn.name)
            if old is None or old.initiator == conn.initiator or conn.initiator < old.initiator:
                self.conns[conn.name] = conn
                winner, loser = conn, old
            else:
                winner, loser = old, conn
        if loser is not None:
            loser.close()
        return winner

    def _run_chat(self, conn):
        """Receiver thread of one peer: show messages until the connection ends."""
        reason = "lost"
        try:
            while True:
                ftype, _fields, length = protocol.read_header(conn.reader)
                if ftype == "MSG":
                    if length > protocol.MAX_MSG:
                        raise ProtocolError("message too long")
                    text = conn.reader.read_exact(length).decode(protocol.ENCODING, "replace")
                    self._on_message(conn.name, protocol.sanitize_text(text))
                elif ftype == "BYE":
                    reason = "left"
                    break
                else:
                    raise ProtocolError(f"unexpected frame {ftype}")
        except (ConnectionClosed, ProtocolError, OSError):
            reason = "lost"
        finally:
            self._drop(conn, reason)

    def _drop(self, conn, reason):
        with self.lock:
            registered = self.conns.get(conn.name) is conn
            if registered:
                del self.conns[conn.name]
            was_current = registered and self.current == conn.name
            if was_current:
                self.current = None
        conn.close()
        if not registered or self.stopping:
            return                                    # replaced / closed by us: stay quiet
        if reason == "left":
            self._say(f"[*] {conn.name} closed the chat." +
                      (" Back to the main prompt." if was_current else ""))
        else:
            self._say(f"[!] Connection with {conn.name} was lost." +
                      (" Back to the main prompt." if was_current else ""))

    def _on_message(self, name, text):
        with self.lock:
            self.history.setdefault(name, []).append((name, text))
            if self.current == name:
                self._say(f"[{name}] {text}")
            else:
                box = self.unread.setdefault(name, [])
                box.append(text)
                self._say(f"[*] New message from {name} ({len(box)} unread)")

    # ---------------------------------------------------------- user actions
    def open_chat(self, query):
        """/chat <name|number>: connect (if needed) and make the peer the current partner."""
        if self.discovery is None:
            raise ChatError("discovery is not running")
        try:
            peer = self.discovery.find(query)
        except LookupError as e:
            raise ChatError(str(e))
        name = peer.name
        with self.lock:
            conn = self.conns.get(name)
        created = False
        if conn is None:
            sock, ip, error = None, None, None
            for ip in peer.ips:                       # best address first, then the others
                try:
                    sock = socket.create_connection((ip, peer.tcp_port), timeout=3)
                    break
                except OSError as e:
                    error = e
            if sock is None:
                raise ChatError(f"Cannot connect to {name} ({peer.ip}:{peer.tcp_port}): "
                                f"{error.strerror or error}. The user may have gone offline.")
            try:
                tune_socket(sock)
                sock.settimeout(None)
                sock.sendall(protocol.pack_hi(self.name, self.tcp_port, "CHAT"))
            except OSError as e:
                sock.close()
                raise ChatError(f"Cannot talk to {name}: {e.strerror or e}")
            new = Conn(sock, name, ip, peer.tcp_port, initiator=self.name)
            conn = self._register(new)
            if conn is new:
                created = True
                threading.Thread(target=self._run_chat, args=(conn,), daemon=True,
                                 name=f"tcp-receiver-{name}").start()
        with self.lock:
            self.current = name
            pending = self.unread.pop(name, [])
            if created:
                self._say(f"[*] Connected to {name} ({conn.remote_ip}:{conn.peer_port})")
            if pending:
                self._say(f"[*] Switched to {name} - {len(pending)} unread:")
                for text in pending:
                    self._say(f"[{name}] {text}")
            elif not created:
                self._say(f"[*] Switched to {name}")

    def send_message(self, text):
        with self.lock:
            name = self.current
            conn = self.conns.get(name) if name else None
        if conn is None:
            raise ChatError("No chat is open. Use /list, then /chat <name|number>.")
        try:
            conn.send(protocol.pack_msg(text))
        except OSError:
            self._drop(conn, "lost")
            raise ChatError(f"Could not send to {name}: connection lost.")
        with self.lock:
            self.history.setdefault(name, []).append((self.name, text))

    def leave(self):
        with self.lock:
            name = self.current
            conn = self.conns.pop(name, None) if name else None
            self.current = None
        if conn is None:
            raise ChatError("No chat is open.")
        try:
            conn.send(protocol.BYE)
        except OSError:
            pass
        conn.close()
        return name

    # ---------------------------------------------------------- file sending
    def send_file(self, path_text):
        """/send <file>: validate, then transfer in a background thread."""
        path = Path(path_text.strip().strip('"').strip("'")).expanduser()
        if not path.is_file():
            raise ChatError(f"File not found: {path_text}")
        with self.lock:
            name = self.current
            conn = self.conns.get(name) if name else None
        if conn is None:
            raise ChatError("No chat is open. Use /chat <name|number> first.")
        try:
            size = path.stat().st_size
        except OSError as e:
            raise ChatError(f"Cannot read file: {e.strerror or e}")
        with self.lock:
            self._transfer_id += 1
            tid = self._transfer_id
        threading.Thread(target=self._send_file_worker,
                         args=(tid, name, (conn.remote_ip, conn.peer_port), path, size),
                         daemon=True, name=f"file-sender-{tid}").start()

    def _send_file_worker(self, tid, name, addr, path, size):
        fname = path.name
        key = f"send{tid}"
        label = f"{fname} ({protocol.human_size(size)})"
        sock = None
        self._say(f"[*] Sending {label} to {name} ...")
        try:
            sock = socket.create_connection(addr, timeout=5)
            tune_socket(sock)
            sock.settimeout(30)
            sock.sendall(protocol.pack_hi(self.name, self.tcp_port, "FILE"))
            sock.sendall(protocol.pack_file_header(fname, size))
            sent, last_pct = 0, -1
            with open(path, "rb") as f:
                while sent < size:
                    chunk = f.read(min(protocol.CHUNK, size - sent))
                    if not chunk:
                        raise OSError("file changed while sending")
                    sock.sendall(chunk)
                    sent += len(chunk)
                    if self.throttle_kbps:
                        time.sleep(len(chunk) / (self.throttle_kbps * 1024.0))
                    pct = 100 * sent // size
                    if pct != last_pct:
                        last_pct = pct
                        self.ui.set_footer(key, f"[*] Sending {fname} to {name} ... {pct}%")
            reader = FrameReader(sock)
            ftype, fields, _ = protocol.read_header(reader)
            if ftype == "ERR":
                raise OSError(f"{name} rejected the file: {fields[0]}")
            if ftype != "ACK" or fields[0] != str(size):
                raise OSError("no valid confirmation from the receiver")
            self.ui.clear_footer(key)
            self._say(f"[*] Sent {label} to {name} ... 100% Done.")
        except (OSError, ConnectionClosed, ProtocolError) as e:
            self.ui.clear_footer(key)
            detail = e.strerror if isinstance(e, OSError) and e.strerror else (str(e) or type(e).__name__)
            self._say(f"[!] Sending {fname} to {name} failed: {detail}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    # -------------------------------------------------------- file receiving
    def _reserve_file(self, filename):
        """Pick a free file name in downloads/ and create its '.part' file."""
        os.makedirs(self.download_dir, exist_ok=True)
        with self.lock:
            dest = protocol.unique_path(self.download_dir, filename)
            part = dest + ".part"
            return dest, part, open(part, "xb")

    def _receive_file(self, sock, reader, sender):
        part, handle, key = None, None, None
        try:
            sock.settimeout(30)                        # a stalled sender = interrupted transfer
            ftype, fields, size = protocol.read_header(reader)
            if ftype != "FILE":
                raise ProtocolError("expected FILE")
            fname = protocol.safe_filename(fields[0])
            os.makedirs(self.download_dir, exist_ok=True)
            if shutil.disk_usage(self.download_dir).free < size:
                sock.sendall(protocol.pack_err("not enough disk space"))
                self._say(f"[!] Rejected {fname} from {sender}: not enough disk space.")
                return
            dest, part, handle = self._reserve_file(fname)
            with self.lock:
                self._transfer_id += 1
                key = f"recv{self._transfer_id}"
            label = f"{os.path.basename(dest)} ({protocol.human_size(size)})"
            self._say(f"[*] Receiving {label} from {sender} ...")
            got, last_pct = 0, -1
            for chunk in reader.read_chunks(size):
                handle.write(chunk)
                got += len(chunk)
                pct = 100 * got // size if size else 100
                if pct != last_pct:
                    last_pct = pct
                    self.ui.set_footer(key, f"[*] Receiving {fname} from {sender} ... {pct}%")
            handle.close()
            handle = None
            os.replace(part, dest)
            part = None
            sock.sendall(protocol.pack_ack(size))
            self.ui.clear_footer(key)
            self._say(f"[*] Received {label} from {sender} ... 100% Done. "
                      f"Saved to {os.path.join(self.download_dir, os.path.basename(dest))}")
        except (ConnectionClosed, ProtocolError, OSError) as e:
            if key:
                self.ui.clear_footer(key)
            if handle is not None:
                handle.close()
            if part is not None:                       # interrupted: delete the partial file
                try:
                    os.remove(part)
                except OSError:
                    pass
                self._say(f"[!] Transfer from {sender} was interrupted; partial file deleted.")
            else:
                self._say(f"[!] File transfer from {sender} failed: {e}")
            try:
                sock.sendall(protocol.pack_err("transfer failed"))
            except OSError:
                pass
        finally:
            try:
                sock.close()
            except OSError:
                pass
