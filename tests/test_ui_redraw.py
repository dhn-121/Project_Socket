#!/usr/bin/env python3
"""Tests the console UI in a REAL pseudo-terminal (80x24) and checks what the screen looks like.

Requirement 4.3: when a message arrives while the user is typing, the input line is erased,
the message is printed, and the prompt is redrawn with the text typed so far.

Needs the dev-only package `pyte` (a terminal emulator):  pip install pyte
Linux / macOS only (uses the pty module).  Run:  python tests/test_ui_redraw.py
"""

import fcntl
import os
import re
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time

try:
    import pyte
except ImportError:
    print("SKIP: pip install pyte  (dev-only dependency for this test)")
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from run_tests import Instance, check, RESULTS  # noqa: E402

LANCHAT = os.path.join(ROOT, "lanchat.py")
ROWS, COLS = 24, 80


class TerminalInstance:
    """lanchat.py running inside a pty; its output is rendered by pyte."""

    def __init__(self, name, port, workdir, extra=()):
        self.master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        env = dict(os.environ, TERM="xterm", PYTHONIOENCODING="utf-8")
        def make_controlling_tty():
            # like a real terminal window: new session whose controlling tty is the pty,
            # so that Ctrl-C (byte 0x03) turns into SIGINT for the program
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen(
            [sys.executable, "-u", LANCHAT, "--name", name, "--port", str(port),
             "--downloads", os.path.join(workdir, f"dl_{name}"), *extra],
            stdin=slave, stdout=slave, stderr=slave, env=env, cwd=workdir,
            preexec_fn=make_controlling_tty)
        os.close(slave)
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while True:
            try:
                data = os.read(self.master, 4096)
            except OSError:
                return
            if not data:
                return
            with self.lock:
                self.stream.feed(data)

    def type(self, text, delay=0.01):
        for ch in text.encode("utf-8"):
            os.write(self.master, bytes([ch]))
            time.sleep(delay)

    def key(self, raw):
        os.write(self.master, raw)

    def rows(self):
        with self.lock:
            return [line.rstrip() for line in self.screen.display]

    def cursor(self):
        with self.lock:
            return self.screen.cursor.y, self.screen.cursor.x

    def wait(self, pattern, timeout=10):
        rx = re.compile(pattern)
        end = time.time() + timeout
        while time.time() < end:
            if any(rx.search(r) for r in self.rows()):
                return True
            time.sleep(0.05)
        return False

    def show(self, title):
        print(f"\n   +--- screen: {title} " + "-" * max(0, 52 - len(title)))
        rows = self.rows()
        last = max((i for i, r in enumerate(rows) if r), default=0)
        for r in rows[:last + 1]:
            print(f"   | {r}")
        print("   +" + "-" * 70)

    def quit(self):
        try:
            self.key(b"/quit\r")
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        try:
            os.close(self.master)
        except OSError:
            pass


def prompt_row(term):
    y, _x = term.cursor()
    return y


def main():
    work = tempfile.mkdtemp(prefix="lanchat_ui_")
    bich = TerminalInstance("Bich", 5111, work, extra=("--throttle", "512"))
    ha = Instance("Ha", 5112, work)
    try:
        print("== Console UI in a real terminal")
        check("both instances see each other", bich.wait(r"Ha is online", 12) and ha.expect(r"Bich is online", 12) >= 0)
        bich.type("/chat Ha\r")
        check("/chat works in the terminal UI", bich.wait(r"Connected to Ha"))
        ha.send("/chat Bich")
        bich.type("Hi Ha, are you ready for the demo?\r")
        time.sleep(0.3)
        ha.send("Yes! Send me the slides")
        bich.wait(r"\[Ha\] Yes! Send me the slides")

        # ---- the scenario from section 4.3 of the assignment
        bich.type("you can che")                           # user is typing, NO Enter yet
        time.sleep(0.2)
        bich.show("typing 'you can che' (before the message arrives)")
        ha.send("ok, which part?")
        check("incoming message is displayed", bich.wait(r"\[Ha\] ok, which part\?"))
        time.sleep(0.2)
        bich.show("after '[Ha] ok, which part?' arrived while typing")
        rows = bich.rows()
        py, px = bich.cursor()
        msg_row = next(i for i, r in enumerate(rows) if "[Ha] ok, which part?" in r)
        check("prompt is redrawn with the typed text preserved", rows[py] == "> you can che", repr(rows[py]))
        check("message was inserted ABOVE the input line", msg_row < py)
        check("cursor is right after the typed text", px == len("> you can che"), f"x={px}")
        check("typed text appears exactly once on screen (no leftovers)",
              sum(r.count("you can che") for r in rows) == 1)

        bich.type("ck it again\r")                         # finish the sentence
        check("text typed before AND after the interruption is sent as one message",
              ha.expect(r"\[Bich\] you can check it again", timeout=5) >= 0, "\n".join(ha.lines[-8:]))

        # ---- backspace, several interruptions in a row
        bich.type("abcd")
        bich.key(b"\x7f")
        ha.send("one")
        ha.send("two")
        bich.wait(r"\[Ha\] two")
        time.sleep(0.2)
        rows = bich.rows()
        py, _ = bich.cursor()
        check("backspace + two interruptions: prompt shows 'abc'", rows[py] == "> abc", repr(rows[py]))
        bich.key(b"\x15")                                  # Ctrl-U clears the line
        time.sleep(0.1)
        check("Ctrl-U clears the input line",           # rows() strips trailing spaces: "> " -> ">"
              bich.rows()[bich.cursor()[0]] == ">" and bich.cursor()[1] == len(">") + 1)

        # ---- UTF-8 typing
        bich.type("tiếng Việt")
        ha.send("xin chào")
        bich.wait(r"\[Ha\] xin chào")
        time.sleep(0.2)
        check("Vietnamese text typed by the user survives a redraw",
              bich.rows()[bich.cursor()[0]] == "> tiếng Việt", repr(bich.rows()[bich.cursor()[0]]))
        bich.key(b"\x15")

        # ---- input line longer than the terminal width (wraps onto 2 rows)
        long_text = "LONG-" + "x" * 90 + "-END"
        bich.type(long_text, delay=0.002)
        time.sleep(0.2)
        ha.send("interrupting a wrapped line")
        bich.wait(r"interrupting a wrapped line")
        time.sleep(0.2)
        bich.show("a typed line longer than 80 columns, then a message arrives")
        rows = bich.rows()
        check("wrapped input line is redrawn cleanly (one copy only)",
              sum(r.count("LONG-") for r in rows) == 1 and "-END" in "".join(rows), "\n".join(rows))
        bich.key(b"\x15")
        time.sleep(0.1)

        # ---- progress line while typing and chatting
        src = os.path.join(work, "big.bin")
        with open(src, "wb") as f:
            f.write(os.urandom(2 * 1024 * 1024))           # 2 MB at 512 KB/s = ~4 s
        bich.type(f"/send {src}\r")
        bich.wait(r"Sending big\.bin to Ha \.\.\. \d+%", 5)
        bich.type("typing during transfer")
        time.sleep(0.5)
        ha.send("message during transfer")
        bich.wait(r"\[Ha\] message during transfer")
        time.sleep(0.4)
        bich.show("file transfer in progress, user typing, message arrives")
        rows = bich.rows()
        py, _ = bich.cursor()
        check("progress line (percentage) sits right above the prompt",
              re.search(r"Sending big\.bin to Ha \.\.\. \d+%", rows[py - 1] or "") is not None, repr(rows[py - 1]))
        check("typed text is still intact during the transfer", rows[py] == "> typing during transfer", repr(rows[py]))
        check("chat message appears above the progress line",
              any("[Ha] message during transfer" in r for r in rows[:py - 1]))
        check("transfer finishes with '100% Done'", bich.wait(r"Sent big\.bin .* 100% Done", 15))
        time.sleep(0.3)
        bich.show("after the transfer finished")
        rows = bich.rows()
        py, _ = bich.cursor()
        check("progress line is gone, prompt + typed text remain",
              not any(re.search(r"Sending big\.bin to Ha \.\.\. \d+%", r) for r in rows)
              and rows[py] == "> typing during transfer", repr(rows[py]))
        check("receiver stored the file", ha.expect(r"Received big\.bin .* 100% Done", timeout=5) >= 0)
        bich.key(b"\x15")

        # ---- Ctrl-C leaves cleanly and restores the terminal
        bich.key(b"\x03")
        try:
            code = bich.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        check("Ctrl-C exits cleanly (exit code 0) and says Bye", code == 0 and bich.wait(r"Bye\.", 2), f"code={code}")
        check("peer is told the chat ended", ha.expect(r"Bich closed the chat", timeout=5) >= 0)
    finally:
        bich.quit()
        ha.quit()

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} UI checks passed")
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
