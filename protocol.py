"""Application-layer protocol for LAN Chat.

UDP (discovery / status) - one text datagram per message:

    HELLO|<name>|<tcp_port>|<status>          status = online | away

TCP (a byte stream with NO message boundaries, so every frame starts with a
header that tells the receiver the type and, if needed, how many bytes follow):

    HI|<name>|<tcp_port>|<kind>|              first frame of a connection, kind = CHAT | FILE
    MSG|<length>|<utf-8 text>                 <length> = number of bytes of the text
    FILE|<filename>|<size>|<raw file bytes>   <size>   = number of bytes that follow
    ACK|<size>|                               receiver confirms the file was stored
    ERR|<reason>|                             receiver failed to store the file
    BYE|                                      graceful close of a chat

Header text is terminated by '|'.  '|' never appears inside a multi-byte UTF-8
sequence, so splitting the raw bytes on b'|' is safe.
"""

import os
import re

UDP_PORT = 9999
BROADCAST_INTERVAL = 3.0   # seconds between presence broadcasts
PEER_TIMEOUT = 10.0        # seconds without a HELLO before a peer is removed
STATUSES = ("online", "away")
ENCODING = "utf-8"

MAX_HEADER_FIELD = 512     # longest header field we accept (bytes)
MAX_MSG = 1024 * 1024      # longest chat message we accept (bytes)
CHUNK = 64 * 1024          # read/write size for file transfer

# frame type -> (number of header fields after the type, index of the field
#                that holds the payload length, or None when there is no payload)
FRAMES = {
    "HI": (3, None),
    "MSG": (1, 0),
    "FILE": (2, 1),
    "ACK": (1, None),
    "ERR": (1, None),
    "BYE": (0, None),
}


class ProtocolError(Exception):
    """The peer sent something that is not valid protocol."""


class ConnectionClosed(Exception):
    """The peer closed the TCP connection."""


# ---------------------------------------------------------------- validation

def valid_name(name):
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 32
        and name == name.strip()
        and "|" not in name
        and all(ch.isprintable() for ch in name)
    )


def sanitize_text(text):
    """Replace control characters (e.g. ESC) so a peer cannot inject terminal codes."""
    return "".join(ch if ch.isprintable() else "?" for ch in text)


def safe_filename(raw):
    """Keep only the base name and drop characters that are unsafe on disk."""
    name = raw.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    name = name.strip().strip(".")
    return name[:200] or "file"


def human_size(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


# ----------------------------------------------------------------------- UDP

def build_hello(name, tcp_port, status):
    return f"HELLO|{name}|{tcp_port}|{status}".encode(ENCODING)


def parse_hello(data):
    """Return (name, tcp_port, status) or None when the datagram is malformed."""
    try:
        text = data.decode(ENCODING)
    except UnicodeDecodeError:
        return None
    parts = text.split("|")
    if len(parts) != 4 or parts[0] != "HELLO":
        return None
    _, name, port, status = parts
    if not valid_name(name) or status not in STATUSES:
        return None
    if not re.fullmatch(r"\d{1,5}", port) or not 1 <= int(port) <= 65535:
        return None
    return name, int(port), status


# ----------------------------------------------------------------------- TCP

def pack_hi(name, tcp_port, kind):
    return f"HI|{name}|{tcp_port}|{kind}|".encode(ENCODING)


def pack_msg(text):
    data = text.encode(ENCODING)
    return b"MSG|" + str(len(data)).encode() + b"|" + data


def pack_file_header(filename, size):
    safe = filename.replace("|", "_")
    return f"FILE|{safe}|{size}|".encode(ENCODING)


def pack_ack(size):
    return f"ACK|{size}|".encode(ENCODING)


def pack_err(reason):
    return f"ERR|{reason.replace('|', '/')}|".encode(ENCODING)


BYE = b"BYE|"


class FrameReader:
    """Buffered reader on top of a socket: reads exact numbers of bytes.

    recv() may return fewer bytes than asked (or bytes of two frames at once),
    so every read loops until enough data has arrived.
    """

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def _fill(self):
        data = self.sock.recv(CHUNK)
        if not data:
            raise ConnectionClosed()
        self.buf += data

    def read_until(self, limit=MAX_HEADER_FIELD):
        """Read bytes up to the next '|' (the '|' is consumed, not returned)."""
        while True:
            i = self.buf.find(b"|")
            if i >= 0:
                out = bytes(self.buf[:i])
                del self.buf[:i + 1]
                return out
            if len(self.buf) > limit:
                raise ProtocolError("header field too long")
            self._fill()

    def read_exact(self, n):
        while len(self.buf) < n:
            self._fill()
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_chunks(self, n):
        """Yield exactly n bytes in pieces (used for files, never held in memory at once)."""
        while n > 0:
            if not self.buf:
                self._fill()
            take = min(n, len(self.buf))
            chunk = bytes(self.buf[:take])
            del self.buf[:take]
            n -= take
            yield chunk


def read_header(reader):
    """Read one frame header. Returns (type, fields, payload_length).

    The payload itself (MSG text / FILE bytes) is left unread for the caller.
    """
    ftype = reader.read_until(limit=16).decode("ascii", "replace")
    spec = FRAMES.get(ftype)
    if spec is None:
        raise ProtocolError(f"unknown frame type {ftype!r}")
    nfields, len_idx = spec
    fields = [reader.read_until().decode(ENCODING, "replace") for _ in range(nfields)]
    payload_len = 0
    if len_idx is not None:
        try:
            payload_len = int(fields[len_idx])
        except ValueError:
            raise ProtocolError("bad length field")
        if payload_len < 0:
            raise ProtocolError("negative length")
    return ftype, fields, payload_len


def unique_path(directory, filename):
    """directory/filename, or 'name (1).ext', 'name (2).ext', ... if it exists."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    n = 1
    while os.path.exists(candidate) or os.path.exists(candidate + ".part"):
        candidate = os.path.join(directory, f"{base} ({n}){ext}")
        n += 1
    return candidate
