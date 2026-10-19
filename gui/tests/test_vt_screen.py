"""python -m unittest discover -s gui/tests   (from the Foundry folder)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from vt_screen import ANSI16, Screen  # noqa: E402


class ScreenTest(unittest.TestCase):
    def test_conpty_startup_and_a_prompt(self):
        s = Screen(40, 5)
        s.feed("\x1b[?9001h\x1b[?1004h\x1b[?25l\x1b[2J\x1b[m\x1b[HPS C:\\p>\x1b[1C\x1b]0;pwsh\x07\x1b[?25h")
        self.assertEqual(s.line_text(s.lines[0]), "PS C:\\p>")
        self.assertEqual((s.x, s.y), (9, 0))
        self.assertEqual(s.title, "pwsh")
        self.assertTrue(s.cursor_visible)

    def test_colors_and_erase_chars(self):
        s = Screen(30, 3)
        s.feed("> \x1b[93mecho \x1b[97m6\x1b[38;5;196m!\x1b[48;2;1;2;3mX\x1b[m done\x1b[3X")
        row = s.lines[0]
        self.assertEqual(s.line_text(row), "> echo 6!X done")
        self.assertEqual(row[2][1][0], ANSI16[11])      # bright yellow
        self.assertEqual(row[8][1][0], "#ff0000")       # 256-color red
        self.assertEqual(row[9][1][1], "#010203")       # true-color background
        self.assertEqual(row[11][1], (None, None, False, False, False, False))

    def test_wrap_scroll_and_history(self):
        s = Screen(5, 2)
        s.feed("abcdefgh\r\nij\r\nkl")
        self.assertEqual([s.line_text(l) for l in s.history], ["abcde", "fgh"])
        self.assertEqual([s.line_text(l) for l in s.lines], ["ij", "kl"])
        self.assertEqual(s.text(), "abcde\nfgh\nij\nkl")
        self.assertEqual(s.history_added, 2)

    def test_exact_width_does_not_wrap_early(self):
        s = Screen(4, 3)
        s.feed("abcd\r\nef")
        self.assertEqual([s.line_text(l) for l in s.lines], ["abcd", "ef", ""])

    def test_cursor_moves_and_line_erase(self):
        s = Screen(10, 3)
        s.feed("hello\x1b[3D\x1b[K!\x1b[2;4Hx\x1b[1;1H\x1b[2C@")
        self.assertEqual(s.line_text(s.lines[0]), "he@")   # '@' lands on the '!' written after the erase
        self.assertEqual(s.line_text(s.lines[1]), "   x")

    def test_insert_delete_and_scroll_region(self):
        s = Screen(6, 4)
        s.feed("123456\r\x1b[2P")
        self.assertEqual(s.line_text(s.lines[0]), "3456")
        s.feed("\r\x1b[2@")
        self.assertEqual(s.line_text(s.lines[0]), "  3456")
        s.feed("\x1b[2;3r\x1b[2;1Ha\r\nb\r\nc")   # region rows 2-3: 'c' scrolls 'a' out of the region
        self.assertEqual([s.line_text(l) for l in s.lines], ["  3456", "b", "c", ""])
        self.assertEqual(len(s.history), 0)     # region scrolls never reach the scrollback

    def test_alt_screen_restores_main(self):
        s = Screen(8, 2)
        s.feed("main")
        s.feed("\x1b[?1049hfull\x1b[?1049l")
        self.assertEqual(s.line_text(s.lines[0]), "main")

    def test_cursor_position_report(self):
        replies = []
        s = Screen(10, 5, respond=replies.append)
        s.feed("\x1b[3;4H\x1b[6n")
        self.assertEqual(replies, ["\x1b[3;4R"])

    def test_resize_keeps_cursor_line(self):
        s = Screen(10, 5)
        s.feed("1\r\n2\r\n3\r\n4\r\n5")
        s.resize(6, 2)
        self.assertEqual([s.line_text(l) for l in s.lines], ["4", "5"])
        self.assertEqual([s.line_text(l) for l in s.history], ["1", "2", "3"])
        self.assertEqual((s.cols, s.y), (6, 1))

    def test_osc_with_st_and_unknown_sequences(self):
        s = Screen(10, 2)
        s.feed("\x1b]2;title\x1b\\ok\x1bPdcs stuff\x1b\\\x1b(B\x1b[>4;1m!")
        self.assertEqual(s.title, "title")
        self.assertEqual(s.line_text(s.lines[0]), "ok!")


if __name__ == "__main__":
    unittest.main()
