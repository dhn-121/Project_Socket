#!/usr/bin/env python3
"""Automated integration tests for LAN Chat.

Starts three real instances (Bich, Ha, Chau) on this machine and drives them through
stdin/stdout, exactly like three users would.  Run:

    python tests/run_tests.py

Takes about 30 seconds (discovery broadcasts every 3 s, timeout is 10 s).
Exit code 0 = everything passed.
"""

import hashlib
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import protocol  # noqa: E402

LANCHAT = os.path.join(ROOT, "lanchat.py")
RESULTS = []


# ------------------------------------------------------------------ helpers
class Instance:
    """One running lanchat.py process with its output captured line by line."""

    def __init__(self, name, port, workdir, extra=()):
        self.name = name
        self.downloads = os.path.join(workdir, f"downloads_{name}")
        self.lines = []
        self.proc = subprocess.Popen(
            [sys.executable, "-u", LANCHAT, "--name", name, "--port", str(port),
             "--plain", "--downloads", self.downloads, *extra],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", bufsize=1, cwd=workdir)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def send(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def mark(self):
        return len(self.lines)

    def expect(self, pattern, timeout=15, since=0):
        """Index of the first line (>= since) matching the regex, or -1 after timeout."""
        rx = re.compile(pattern)
        end = time.time() + timeout
        while time.time() < end:
            for i in range(since, len(self.lines)):
                if rx.search(self.lines[i]):
                    return i
            time.sleep(0.05)
        return -1

    def alive(self):
        return self.proc.poll() is None

    def kill(self):
        self.proc.kill()
        self.proc.wait()
        self._close_pipes()

    def _close_pipes(self):
        for pipe in (self.proc.stdin, self.proc.stdout):
            try:
                pipe.close()
            except Exception:
                pass

    def quit(self):
        if self.alive():
            try:
                self.send("/quit")
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        self._close_pipes()

    def dump(self):
        return "\n".join(f"    | {l}" for l in self.lines[-25:])


def check(description, condition, detail=""):
    RESULTS.append((description, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {description}")
    if not condition and detail:
        print(detail)
    return bool(condition)


def section(title):
    print(f"\n== {title}")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------- protocol / framing tests
def test_framing():
    section("Protocol: framing over a TCP byte stream")
    a, b = socket.socketpair()
    reader = protocol.FrameReader(b)

    # 1) one frame delivered one byte at a time (partial reads)
    frame = protocol.pack_msg("xin chào, hello") + protocol.BYE
    sender = threading.Thread(target=lambda: [(a.sendall(frame[i:i + 1]), time.sleep(0.001))
                                              for i in range(len(frame))])
    sender.start()
    ftype, fields, length = protocol.read_header(reader)
    text = reader.read_exact(length).decode("utf-8")
    check("MSG split into 1-byte pieces is reassembled", ftype == "MSG" and text == "xin chào, hello")
    ftype, _, _ = protocol.read_header(reader)
    check("BYE after it is parsed", ftype == "BYE")
    sender.join()

    # 2) several frames arriving in ONE recv() (message boundaries do not exist in TCP)
    a.sendall(protocol.pack_msg("one") + protocol.pack_msg("two") + protocol.pack_msg("three"))
    got = []
    for _ in range(3):
        _, _, n = protocol.read_header(reader)
        got.append(reader.read_exact(n).decode())
    check("3 frames glued together are separated correctly", got == ["one", "two", "three"], str(got))

    # 3) FILE frame with binary payload that contains '|' bytes
    payload = bytes(range(256)) * 50 + b"|FILE|MSG|BYE|"
    # send from a thread: on macOS a socketpair buffer is only ~8 KB, so a 13 KB sendall()
    # would block forever if nobody is reading yet (deadlock in a single-threaded test)
    big_frame = protocol.pack_file_header("a|b.bin", len(payload)) + payload
    file_sender = threading.Thread(target=a.sendall, args=(big_frame,))
    file_sender.start()
    ftype, fields, size = protocol.read_header(reader)
    data = b"".join(reader.read_chunks(size))
    file_sender.join()
    check("FILE payload with '|' bytes survives intact", ftype == "FILE" and data == payload)
    check("'|' in a file name is replaced", fields[0] == "a_b.bin", fields[0])

    # 4) garbage is rejected, not crashed on
    a.sendall(b"NOPE|1|2|")
    try:
        protocol.read_header(reader)
        rejected = False
    except protocol.ProtocolError:
        rejected = True
    check("unknown frame type raises ProtocolError", rejected)
    a.close()
    try:
        protocol.read_header(reader)
        closed = False
    except (protocol.ConnectionClosed, protocol.ProtocolError):
        closed = True
    check("closed connection raises ConnectionClosed", closed)
    b.close()

    # 5) UDP HELLO
    check("HELLO round-trip",
          protocol.parse_hello(protocol.build_hello("Quan", 5000, "online")) == ("Quan", 5000, "online"))
    bad = [b"HELLO|x|99999|online", b"HELLO||5000|online", b"HELLO|x|5000|busy",
           b"HELLO|x|5000", b"\xff\xfe", b"HI|x|5000|online"]
    check("malformed HELLO datagrams are ignored", all(protocol.parse_hello(d) is None for d in bad))
    check("unsafe file names are cleaned",
          protocol.safe_filename("../../etc/passwd") == "passwd"
          and protocol.safe_filename("C:\\x\\y.txt") == "y.txt")


# --------------------------------------------------------- integration tests
def test_system(work):
    bich = Instance("Bich", 5101, work, extra=("--throttle", "1024"))
    ha = Instance("Ha", 5102, work)
    chau = Instance("Chau", 5103, work)
    everyone = [bich, ha, chau]
    try:
        section("UDP discovery + online list")
        ok = True
        for inst in everyone:
            for other in everyone:
                if other is not inst:
                    ok &= inst.expect(rf"\[\+\] {other.name} is online", timeout=12) >= 0
        check("every peer notices the two others coming online", ok,
              "\n".join(i.dump() for i in everyone))
        m = bich.mark()
        bich.send("/list")
        check("/list shows both users with name, IP and status",
              bich.expect(r"\d\. Ha \(\S+\) online", since=m, timeout=3) >= 0
              and bich.expect(r"\d\. Chau \(\S+\) online", since=m, timeout=3) >= 0, bich.dump())

        section("Status (UDP)")
        m = bich.mark()
        ha.send("/status away")
        check("away status reaches the others (notification)",
              bich.expect(r"Ha is now away", since=m, timeout=6) >= 0, bich.dump())
        m = bich.mark()
        bich.send("/list")
        check("/list displays 'away'", bich.expect(r"Ha \(\S+\) away", since=m, timeout=3) >= 0, bich.dump())
        ha.send("/status online")
        check("back to online", bich.expect(r"Ha is now online", since=m, timeout=6) >= 0)
        m = ha.mark()
        ha.send("/status busy")
        check("invalid status is rejected", ha.expect(r"Usage: /status", since=m, timeout=3) >= 0)

        section("TCP chat, both directions, unread counters")
        m_b, m_h = bich.mark(), ha.mark()
        bich.send("/chat Ha")
        check("/chat opens a TCP connection", bich.expect(r"Connected to Ha", since=m_b) >= 0, bich.dump())
        bich.send("hello Ha, ready for the demo?")
        bich.send("second line")
        check("sender sees own message as [Bich] ...",
              bich.expect(r"\[Bich\] hello Ha, ready for the demo\?", since=m_b, timeout=3) >= 0)
        check("receiver (other partner) gets unread notification",
              ha.expect(r"New message from Bich \(1 unread\)", since=m_h) >= 0
              and ha.expect(r"New message from Bich \(2 unread\)", since=m_h) >= 0, ha.dump())
        m_h = ha.mark()
        ha.send("/chat Bich")
        check("switching shows the unread messages",
              ha.expect(r"Switched to Bich - 2 unread", since=m_h) >= 0
              and ha.expect(r"\[Bich\] hello Ha, ready for the demo\?", since=m_h) >= 0
              and ha.expect(r"\[Bich\] second line", since=m_h) >= 0, ha.dump())
        m_b = bich.mark()
        ha.send("Yes! Send me the slides")
        check("reverse direction works", bich.expect(r"\[Ha\] Yes! Send me the slides", since=m_b) >= 0, bich.dump())
        m_h = ha.mark()
        bich.send("unicode: Tiếng Việt có dấu ✓")
        check("current partner's message is shown immediately (UTF-8 intact)",
              ha.expect(r"\[Bich\] unicode: Tiếng Việt có dấu ✓", since=m_h) >= 0, ha.dump())

        section("Several peers at once")
        m_h = ha.mark()
        chau.send("/chat Ha")
        chau.send("from Chau 1")
        chau.send("from Chau 2")
        check("message from a non-current peer -> notification with count",
              ha.expect(r"New message from Chau \(2 unread\)", since=m_h) >= 0, ha.dump())
        check("...and its text is NOT printed while chatting with someone else",
              ha.expect(r"\[Chau\] from Chau", since=m_h, timeout=1) < 0)
        m_h = ha.mark()
        ha.send("/list")
        check("/list shows unread count and current partner",
              ha.expect(r"Chau .*\(2 unread\)", since=m_h, timeout=3) >= 0
              and ha.expect(r"Bich .*<- chatting", since=m_h, timeout=3) >= 0, ha.dump())
        m_h = ha.mark()
        ha.send("/chat Chau")
        check("switching to Chau lists its 2 unread messages",
              ha.expect(r"Switched to Chau - 2 unread", since=m_h) >= 0
              and ha.expect(r"\[Chau\] from Chau 2", since=m_h) >= 0, ha.dump())

        section("File transfer while chatting")
        src = os.path.join(work, "slides.bin")
        with open(src, "wb") as f:
            f.write(os.urandom(3 * 1024 * 1024 + 123))       # ~3 MB, odd size
        m_h = ha.mark()
        ha.send("/chat Bich")
        bich.send("/chat Ha")
        time.sleep(0.3)
        m_b, m_h = bich.mark(), ha.mark()
        bich.send(f"/send {src}")          # throttled to 1 MB/s -> takes ~3 s
        time.sleep(0.8)
        for i in range(1, 4):
            bich.send(f"chat during transfer {i}")
            time.sleep(0.3)
        done_b = bich.expect(r"Sent slides\.bin .* 100% Done", since=m_b, timeout=20)
        done_h = ha.expect(r"Received slides\.bin .* 100% Done", since=m_h, timeout=20)
        check("sender reports 100% Done", done_b >= 0, bich.dump())
        check("receiver reports 100% Done", done_h >= 0, ha.dump())
        chat_idx = [ha.expect(rf"\[Bich\] chat during transfer {i}", since=m_h, timeout=2) for i in (1, 2, 3)]
        check("chat messages arrived WHILE the file was still transferring",
              all(i >= 0 for i in chat_idx) and done_h >= 0 and max(chat_idx) < done_h,
              f"chat lines at {chat_idx}, done at {done_h}\n{ha.dump()}")
        dest = os.path.join(ha.downloads, "slides.bin")
        check("file saved in downloads/ with identical content (SHA-256)",
              os.path.isfile(dest) and sha256(dest) == sha256(src))
        check("no leftover .part file", not any(n.endswith(".part") for n in os.listdir(ha.downloads)))

        m_h = ha.mark()
        bich.send(f"/send {src}")
        ha.expect(r"Received slides \(1\)\.bin .* 100% Done", since=m_h, timeout=20)
        check("same file name is not overwritten (slides (1).bin)",
              os.path.isfile(os.path.join(ha.downloads, "slides (1).bin")))

        empty = os.path.join(work, "empty file.txt")
        open(empty, "wb").close()
        m_h = ha.mark()
        bich.send(f'/send "{empty}"')
        ok = ha.expect(r"Received empty file\.txt .* 100% Done", since=m_h, timeout=10) >= 0
        check("empty file with a space in its name works",
              ok and os.path.getsize(os.path.join(ha.downloads, "empty file.txt")) == 0, ha.dump())

        section("Error handling (no crashes)")
        m = bich.mark()
        bich.send("/send /no/such/file.pdf")
        check("/send missing file -> error", bich.expect(r"File not found", since=m, timeout=3) >= 0, bich.dump())
        m = bich.mark()
        bich.send("/chat Nobody")
        check("/chat unknown user -> error", bich.expect(r"No online user named 'Nobody'", since=m, timeout=3) >= 0)
        m = bich.mark()
        bich.send("/chat 99")
        check("/chat bad number -> error", bich.expect(r"No user with number 99", since=m, timeout=3) >= 0)
        m = bich.mark()
        bich.send("/frobnicate")
        check("unknown command -> short help",
              bich.expect(r"Unknown command: /frobnicate", since=m, timeout=3) >= 0
              and bich.expect(r"/status <online\|away>", since=m, timeout=3) >= 0)
        m_b, m_h = bich.mark(), ha.mark()
        bich.send("/leave")
        check("/leave closes the chat on both sides",
              bich.expect(r"Left the chat with Ha", since=m_b, timeout=3) >= 0
              and ha.expect(r"Bich closed the chat", since=m_h, timeout=5) >= 0, ha.dump())
        m = bich.mark()
        bich.send("typing with no chat open")
        check("message without an open chat -> error", bich.expect(r"No chat is open", since=m, timeout=3) >= 0)

        section("Peer crash and timeout")
        ha.send("/chat Chau")                          # Chau becomes Ha's current partner
        ha.expect(r"Switched to Chau", since=ha.mark() - 1, timeout=5)
        time.sleep(0.3)
        m_h, m_b = ha.mark(), bich.mark()
        t_kill = time.time()
        chau.kill()                                    # kill -9: no BYE, no goodbye
        check("open TCP chat: peer crash is reported", ha.expect(r"Connection with Chau was lost",
                                                                  since=m_h, timeout=8) >= 0, ha.dump())
        check("crashed peer is back at the main prompt (no partner)",
              ha.expect(r"Back to the main prompt", since=m_h, timeout=1) >= 0)
        m = bich.mark()
        bich.send("/chat Chau")
        check("connecting to a user who just died -> error, no crash",
              bich.expect(r"Cannot connect to Chau|Connected to Chau", since=m, timeout=8) >= 0 and bich.alive())
        i = bich.expect(r"\[-\] Chau went offline", since=m_b, timeout=15)
        elapsed = time.time() - t_kill
        # The 10 s count starts at the LAST HELLO heard (up to 3 s before the kill), and the
        # listener checks once per second -> removal 7..11 s after the kill.
        check("UDP timeout removes the dead peer (10 s since last HELLO)",
              i >= 0 and 6.5 <= elapsed <= 12, f"took {elapsed:.1f}s\n{bich.dump()}")
        print(f"        (peer removed {elapsed:.1f} s after it was killed)")
        m = bich.mark()
        bich.send("/list")
        check("online list no longer contains Chau",
              bich.expect(r"Chau", since=m, timeout=1.5) < 0 and bich.expect(r"Ha", since=m, timeout=3) >= 0)

        section("Interrupted file transfer")
        big = os.path.join(work, "big.bin")
        with open(big, "wb") as f:
            f.write(os.urandom(20 * 1024 * 1024))
        bich.send("/chat Ha")
        bich.expect(r"Connected to Ha|Switched to Ha", since=bich.mark() - 1, timeout=5)
        m_h = ha.mark()
        bich.send(f"/send {big}")                      # throttled: would take ~20 s
        started = ha.expect(r"Receiving big\.bin", since=m_h, timeout=8) >= 0
        time.sleep(1.5)
        part_seen = any(n.startswith("big.bin") and n.endswith(".part") for n in os.listdir(ha.downloads))
        bich.kill()                                    # sender dies in the middle
        check("receiver started and had a .part file", started and part_seen, ha.dump())
        check("receiver reports the interruption",
              ha.expect(r"interrupted; partial file deleted", since=m_h, timeout=10) >= 0, ha.dump())
        left = [n for n in os.listdir(ha.downloads) if n.startswith("big.bin")]
        check("partial file was deleted", left == [], str(left))
    finally:
        for inst in everyone:
            inst.quit()


def main():
    work = tempfile.mkdtemp(prefix="lanchat_test_")
    try:
        test_framing()
        test_system(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    for desc, ok in RESULTS:
        if not ok:
            print(f"  FAILED: {desc}")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
