"""A small VT/ANSI terminal screen: feed it a program's output, read back lines of styled cells.

It covers what Windows ConPTY and common shells emit: printing with auto-wrap, CR/LF/BS/TAB, cursor moves and
positioning, erasing, inserting/deleting characters and lines, scroll regions, SGR colors (16, 256 and true color),
cursor save/restore and visibility, the alternate screen, window titles, and cursor-position reports. Lines that
scroll off the top go into the scrollback history."""
from collections import deque

DEFAULT_STYLE = (None, None, False, False, False, False)  # fg, bg, bold, italic, underline, inverse
BLANK = (" ", DEFAULT_STYLE)

# 16-color palette (xterm-like, slightly warm to sit with Foundry's theme)
ANSI16 = ["#1e1e1e", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#d0d0d0",
          "#767676", "#ff7b84", "#b5e890", "#ffd88a", "#82c4ff", "#de9cf0", "#7fd3dc", "#ffffff"]


def color_256(n):
    if n < 16:
        return ANSI16[n]
    if n < 232:
        n -= 16
        levels = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (levels[n // 36], levels[(n // 6) % 6], levels[n % 6])
    gray = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (gray, gray, gray)


class Screen:
    def __init__(self, cols=80, rows=24, scrollback=3000, respond=None):
        """respond(text) sends replies to the program (cursor-position reports)."""
        self.cols, self.rows = cols, rows
        self.respond = respond
        self.history = deque(maxlen=scrollback)
        self.history_added = 0      # lines ever pushed into history (renderers diff against it)
        self.title = ""
        self.cursor_visible = True
        self._reset_state()
        self.lines = [self._blank_line() for _ in range(rows)]
        self._alt = None            # saved main screen while the alternate screen is on
        self._state = "normal"
        self._params = ""
        self._osc = ""
        self.dirty = True

    def _reset_state(self):
        self.x = self.y = 0
        self.style = DEFAULT_STYLE
        self.top, self.bottom = 0, self.rows - 1
        self.wrap_pending = False
        self.autowrap = True
        self._saved = (0, 0, DEFAULT_STYLE)

    def _blank_line(self, style=DEFAULT_STYLE):
        cell = (" ", (None, style[1], False, False, False, False)) if style[1] else BLANK
        return [cell] * self.cols

    # ----------------------------------------------------------------- input
    def feed(self, data):
        for ch in data:
            state = self._state
            if state == "normal":
                if ch >= " " and ch != "\x7f":
                    self._print(ch)
                elif ch == "\x1b":
                    self._state = "esc"
                else:
                    self._control(ch)
            elif state == "esc":
                self._escape(ch)
            elif state == "csi":
                if "0" <= ch <= "?" or " " <= ch <= "/":   # parameters and intermediates
                    self._params += ch
                elif "@" <= ch <= "~":
                    self._state = "normal"
                    self._csi(self._params, ch)
                elif ch == "\x1b":
                    self._state = "esc"
                else:
                    self._control(ch)
            elif state == "osc":
                if ch == "\x07":
                    self._end_osc()
                elif ch == "\x1b":
                    self._state = "osc-esc"
                else:
                    self._osc += ch
            elif state == "osc-esc":   # ESC \ ends an OSC; anything else starts a new escape
                self._end_osc()
                if ch != "\\":
                    self._state = "esc"
                    self._escape(ch)
            elif state == "string":    # DCS/APC/PM/SOS: ignored up to ST
                if ch == "\x1b":
                    self._state = "string-esc"
            elif state == "string-esc":
                self._state = "normal" if ch == "\\" else "string"
            elif state == "charset":   # ESC ( B and friends: the designator is ignored
                self._state = "normal"
        self.dirty = True

    def _end_osc(self):
        self._state = "normal"
        code, _, text = self._osc.partition(";")
        if code in ("0", "2"):
            self.title = text
        self._osc = ""

    def _control(self, ch):
        if ch == "\r":
            self.x = 0
            self.wrap_pending = False
        elif ch in "\n\x0b\x0c":
            self._linefeed()
        elif ch == "\b":
            self.wrap_pending = False
            self.x = max(0, self.x - 1)
        elif ch == "\t":
            self.wrap_pending = False
            self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)

    def _print(self, ch):
        if self.wrap_pending:
            self.x = 0
            self._linefeed()
            self.wrap_pending = False
        self.lines[self.y][self.x] = (ch, self.style)
        if self.x == self.cols - 1:
            self.wrap_pending = self.autowrap  # wrap only when the next character comes, as terminals do
        else:
            self.x += 1

    def _linefeed(self):
        if self.y == self.bottom:
            self._scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1

    def _scroll_up(self, n):
        for _ in range(max(1, n)):
            line = self.lines.pop(self.top)
            if self.top == 0 and self._alt is None:
                self.history.append(line)
                self.history_added += 1
            self.lines.insert(self.bottom, self._blank_line(self.style))

    def _scroll_down(self, n):
        for _ in range(max(1, n)):
            del self.lines[self.bottom]
            self.lines.insert(self.top, self._blank_line(self.style))

    # ---------------------------------------------------------------- escape
    def _escape(self, ch):
        self._state = "normal"
        if ch == "[":
            self._state, self._params = "csi", ""
        elif ch == "]":
            self._state, self._osc = "osc", ""
        elif ch in "P_^X":
            self._state = "string"
        elif ch in "()*+":
            self._state = "charset"
        elif ch == "7":
            self._saved = (self.x, self.y, self.style)
        elif ch == "8":
            self.x, self.y, self.style = self._saved
            self.wrap_pending = False
        elif ch == "D":
            self._linefeed()
        elif ch == "E":
            self.x = 0
            self._linefeed()
        elif ch == "M":   # reverse index
            if self.y == self.top:
                self._scroll_down(1)
            elif self.y > 0:
                self.y -= 1
        elif ch == "c":
            self.__init__(self.cols, self.rows, self.history.maxlen, self.respond)

    def _csi(self, raw, final):
        private = raw[:1] if raw[:1] in "?><=" else ""
        body = raw[len(private):].rstrip(" !\"#$%&'()*+,-./")
        params = []
        for part in body.split(";") if body else []:
            part = part.split(":")[0]
            params.append(int(part) if part.isdigit() else 0)

        def p(i, default=1):
            return params[i] if len(params) > i and params[i] else default

        if private == "?":
            if final in "hl":
                self._private_mode(params, final == "h")
            return
        if private:
            return   # >c, =c and similar queries: nothing to answer here
        if final != "m":
            self.wrap_pending = False
        if final == "A":
            self.y = max(self.top if self.y >= self.top else 0, self.y - p(0))
        elif final == "B":
            self.y = min(self.bottom if self.y <= self.bottom else self.rows - 1, self.y + p(0))
        elif final in "Ca":
            self.x = min(self.cols - 1, self.x + p(0))
        elif final == "D":
            self.x = max(0, self.x - p(0))
        elif final == "E":
            self.x, self.y = 0, min(self.rows - 1, self.y + p(0))
        elif final == "F":
            self.x, self.y = 0, max(0, self.y - p(0))
        elif final in "G`":
            self.x = min(self.cols - 1, p(0) - 1)
        elif final in "Hf":
            self.y, self.x = min(self.rows - 1, p(0) - 1), min(self.cols - 1, p(1) - 1)
        elif final == "d":
            self.y = min(self.rows - 1, p(0) - 1)
        elif final == "J":
            self._erase_display(p(0, 0))
        elif final == "K":
            self._erase_line(p(0, 0))
        elif final == "X":
            row = self.lines[self.y]
            for i in range(self.x, min(self.cols, self.x + p(0))):
                row[i] = (" ", self._erase_style())
        elif final == "P":
            row = self.lines[self.y]
            n = min(p(0), self.cols - self.x)
            del row[self.x:self.x + n]
            row.extend([(" ", self._erase_style())] * n)
        elif final == "@":
            row = self.lines[self.y]
            n = min(p(0), self.cols - self.x)
            row[self.x:self.x] = [(" ", self._erase_style())] * n
            del row[self.cols:]
        elif final == "L":
            if self.top <= self.y <= self.bottom:
                for _ in range(min(p(0), self.bottom - self.y + 1)):
                    del self.lines[self.bottom]
                    self.lines.insert(self.y, self._blank_line(self.style))
        elif final == "M":
            if self.top <= self.y <= self.bottom:
                for _ in range(min(p(0), self.bottom - self.y + 1)):
                    del self.lines[self.y]
                    self.lines.insert(self.bottom, self._blank_line(self.style))
        elif final == "S":
            self._scroll_up(p(0))
        elif final == "T":
            self._scroll_down(p(0))
        elif final == "m":
            self._sgr(params or [0])
        elif final == "r":
            top, bottom = p(0) - 1, p(1, self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.top, self.bottom = top, bottom
                self.x, self.y = 0, 0
        elif final == "s":
            self._saved = (self.x, self.y, self.style)
        elif final == "u":
            self.x, self.y, self.style = self._saved
        elif final == "n" and p(0, 0) == 6 and self.respond:
            self.respond(f"\x1b[{self.y + 1};{self.x + 1}R")
        elif final == "c" and self.respond:
            self.respond("\x1b[?1;0c")

    def _private_mode(self, params, on):
        for mode in params:
            if mode == 25:
                self.cursor_visible = on
            elif mode == 7:
                self.autowrap = on
            elif mode in (1049, 1047, 47):
                if on and self._alt is None:
                    self._alt = (self.lines, self.x, self.y, self.style)
                    self.lines = [self._blank_line() for _ in range(self.rows)]
                    self.x = self.y = 0
                elif not on and self._alt is not None:
                    self.lines, self.x, self.y, self.style = self._alt
                    self._alt = None

    def _erase_style(self):
        return (None, self.style[1], False, False, False, False) if self.style[1] else DEFAULT_STYLE

    def _erase_line(self, mode):
        row = self.lines[self.y]
        cell = (" ", self._erase_style())
        start, end = {0: (self.x, self.cols), 1: (0, self.x + 1), 2: (0, self.cols)}.get(mode, (0, 0))
        for i in range(start, min(end, self.cols)):
            row[i] = cell

    def _erase_display(self, mode):
        if mode == 0:
            self._erase_line(0)
            for i in range(self.y + 1, self.rows):
                self.lines[i] = self._blank_line(self.style)
        elif mode == 1:
            self._erase_line(1)
            for i in range(0, self.y):
                self.lines[i] = self._blank_line(self.style)
        elif mode in (2, 3):
            self.lines = [self._blank_line(self.style) for _ in range(self.rows)]
            if mode == 3:
                self.history.clear()

    def _sgr(self, params):
        fg, bg, bold, italic, underline, inverse = self.style
        i = 0
        while i < len(params):
            n = params[i]
            if n == 0:
                fg, bg, bold, italic, underline, inverse = DEFAULT_STYLE
            elif n == 1:
                bold = True
            elif n == 3:
                italic = True
            elif n == 4:
                underline = True
            elif n == 7:
                inverse = True
            elif n in (2, 22):
                bold = False
            elif n == 23:
                italic = False
            elif n == 24:
                underline = False
            elif n == 27:
                inverse = False
            elif 30 <= n <= 37:
                fg = ANSI16[n - 30]
            elif 90 <= n <= 97:
                fg = ANSI16[n - 90 + 8]
            elif n == 39:
                fg = None
            elif 40 <= n <= 47:
                bg = ANSI16[n - 40]
            elif 100 <= n <= 107:
                bg = ANSI16[n - 100 + 8]
            elif n == 49:
                bg = None
            elif n in (38, 48) and i + 1 < len(params):
                if params[i + 1] == 5 and i + 2 < len(params):
                    color = color_256(params[i + 2] % 256)
                    i += 2
                elif params[i + 1] == 2 and i + 4 < len(params):
                    color = "#%02x%02x%02x" % tuple(min(255, v) for v in params[i + 2:i + 5])
                    i += 4
                else:
                    color = None
                    i += 1
                if n == 38:
                    fg = color
                else:
                    bg = color
            i += 1
        self.style = (fg, bg, bold, italic, underline, inverse)

    # ---------------------------------------------------------------- output
    def resize(self, cols, rows):
        cols, rows = max(2, cols), max(2, rows)
        if (cols, rows) == (self.cols, self.rows):
            return
        lines = self.lines
        if rows < self.rows:
            # Keep the cursor on screen: lines above it go into history first.
            overflow = max(0, self.y - (rows - 1))
            for line in lines[:overflow]:
                self.history.append(line)
                self.history_added += 1
            lines = lines[overflow:overflow + rows]
            self.y -= overflow
        else:
            lines = lines + [[BLANK] * self.cols for _ in range(rows - self.rows)]
        self.cols, self.rows = cols, rows
        self.lines = [(line + [BLANK] * cols)[:cols] for line in lines]
        self.x, self.y = min(self.x, cols - 1), min(self.y, rows - 1)
        self.top, self.bottom = 0, rows - 1
        self.wrap_pending = False
        self.dirty = True

    @staticmethod
    def line_text(line):
        return "".join(ch for ch, _ in line).rstrip()

    def text(self, with_history=True):
        """The plain text of the scrollback and the screen, trailing blank lines dropped."""
        lines = [self.line_text(line) for line in (list(self.history) if with_history else []) + self.lines]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)
