"""Console UI: history on top, progress lines, and ONE input line at the bottom.

The terminal has no separate boxes, so we simulate them. When a message arrives
while the user is typing we
    1. erase the input line         (\\r + ESC[K / ESC[J)
    2. print the incoming message and a newline
    3. redraw the prompt "> " together with the text typed so far
All screen output goes through one lock so two threads never print at once.

Progress lines (file transfer) live in a small "footer" between the history and
the prompt and are updated in place.
"""

import atexit
import codecs
import os
import shutil
import sys
import threading
import unicodedata

PROMPT = "> "
IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import msvcrt
else:
    import select
    import termios
    import tty


def char_width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text):
    return sum(char_width(ch) for ch in text)


def enable_ansi_on_windows():
    """Turn on ANSI escape processing in the Windows 10+ console."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)   # VT processing
    except Exception:
        os.system("")   # old trick that also enables ANSI on many consoles


class ConsoleUI:
    def __init__(self, interactive=None, out=None):
        self.lock = threading.RLock()
        self.out = out or sys.stdout
        if interactive is None:
            interactive = sys.stdin.isatty() and sys.stdout.isatty()
        self.interactive = interactive
        self._buf = []                 # characters typed so far
        self._footers = {}             # key -> progress text
        self._drawn_footers = 0        # footer lines currently on screen
        self._drawn_width = text_width(PROMPT)
        self._started = False
        self._old_term = None
        self._fd = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    # ------------------------------------------------------------ lifecycle
    def start(self):
        with self.lock:
            if not self.interactive:
                return
            enable_ansi_on_windows()
            if not IS_WINDOWS:
                self._fd = sys.stdin.fileno()
                self._old_term = termios.tcgetattr(self._fd)
                tty.setcbreak(self._fd)          # no echo, no line buffering, Ctrl-C still works
                atexit.register(self.stop)
            self._started = True
            self._write(PROMPT)

    def stop(self):
        with self.lock:
            if self._old_term is not None:
                try:
                    termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_term)
                except Exception:
                    pass
                self._old_term = None
            if self._started:
                self._started = False
                self._write("\r\033[J")

    # --------------------------------------------------------------- output
    def _write(self, text):
        try:
            self.out.write(text)
            self.out.flush()
        except (OSError, ValueError):
            pass

    @staticmethod
    def _cols():
        return max(20, shutil.get_terminal_size((80, 24)).columns)

    @staticmethod
    def _fit(text, cols):
        """Cut a footer line so it never wraps (wrapping would break cursor maths)."""
        out, width = [], 0
        for ch in text:
            w = char_width(ch)
            if width + w > cols - 1:
                break
            out.append(ch)
            width += w
        return "".join(out)

    def _redraw(self, text=None):
        """Erase input line + footers, optionally print text, redraw footers + prompt."""
        cols = self._cols()
        # footers sit above the input line; the input may itself span several rows
        up = self._drawn_footers + max(0, (self._drawn_width - 1) // cols)
        parts = []
        if up:
            parts.append(f"\033[{up}A")
        parts.append("\r\033[J")                       # to column 0, clear to end of screen
        if text is not None:
            parts.append(text + "\n")
        lines = [self._fit(t, cols) for t in self._footers.values()]
        for line in lines:
            parts.append(line + "\n")
        typed = "".join(self._buf)
        parts.append(PROMPT + typed)
        self._drawn_footers = len(lines)
        self._drawn_width = text_width(PROMPT + typed)
        self._write("".join(parts))

    def print_line(self, text=""):
        with self.lock:
            if self.interactive and self._started:
                self._redraw(text)
            else:
                self._write(text + "\n")

    def set_footer(self, key, text):
        """Show/update an in-place progress line (interactive terminals only)."""
        with self.lock:
            if not (self.interactive and self._started):
                return
            if self._footers.get(key) == text:
                return
            self._footers[key] = text
            self._redraw()

    def clear_footer(self, key):
        with self.lock:
            if key in self._footers:
                del self._footers[key]
                if self.interactive and self._started:
                    self._redraw()

    # ---------------------------------------------------------------- input
    def read_line(self):
        """Block until the user presses Enter. Returns the line, or None on EOF."""
        if not self.interactive:
            line = sys.stdin.readline()
            return None if line == "" else line.rstrip("\r\n")
        while True:
            ch = self._getkey()
            if ch is None:
                return None
            if ch in ("\r", "\n"):
                with self.lock:
                    line = "".join(self._buf)
                    self._buf.clear()
                    self._redraw()                 # erase the typed line, fresh prompt
                return line
            if ch in ("\x7f", "\x08"):             # backspace
                with self.lock:
                    if self._buf:
                        self._buf.pop()
                        self._redraw()
            elif ch == "\x03":                     # Ctrl-C (Windows delivers it as a key)
                raise KeyboardInterrupt
            elif ch == "\x04":                     # Ctrl-D
                with self.lock:
                    if not self._buf:
                        return None
            elif ch == "\x15":                     # Ctrl-U: clear the line
                with self.lock:
                    self._buf.clear()
                    self._redraw()
            elif ch and ch.isprintable():
                with self.lock:
                    self._buf.append(ch)
                    self._drawn_width += char_width(ch)
                    self._write(ch)

    if IS_WINDOWS:
        def _getkey(self):
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):             # arrow / function key: swallow 2nd code
                msvcrt.getwch()
                return ""
            return ch
    else:
        def _getkey(self):
            while True:
                b = os.read(self._fd, 1)
                if not b:
                    return None
                if b == b"\x1b":                   # escape sequence (arrows...): ignore it
                    self._skip_escape()
                    return ""
                text = self._decoder.decode(b)
                if text:
                    return text

        def _skip_escape(self):
            if not select.select([self._fd], [], [], 0.03)[0]:
                return
            nxt = os.read(self._fd, 1)
            if nxt in (b"[", b"O"):
                while select.select([self._fd], [], [], 0.03)[0]:
                    c = os.read(self._fd, 1)
                    if c and 0x40 <= c[0] <= 0x7E:
                        return
