import os
import unittest

import tests  # noqa: F401
from gi.repository import Adw, Gtk

from src.ui.components import marquee
from src.ui.components.marquee import MarqueeLabel
from src.ui.components.player_bar import PlayerRow
from tests.helpers import run_until

LONG = "Calma Urgente #212 – Economia, desigualdade e o futuro do Brasil, com Laura Carvalho"


def _block(width: int) -> Gtk.Widget:
    widget = Gtk.Box()
    widget.set_size_request(width, 40)
    return widget


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "precisa de um display (use xvfb-run)")
class MarqueeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self._rest = marquee.REST_MS
        marquee.REST_MS = 50
        self.marquee = MarqueeLabel()
        self.window = Gtk.Window(child=self.marquee, default_width=160)
        self.window.present()
        self.assertTrue(run_until(lambda: self.marquee.get_width() > 0, 5))

    def tearDown(self):
        marquee.REST_MS = self._rest
        self.window.destroy()

    def test_short_text_stays_still(self):
        self.marquee.set_text("Curto")
        run_until(lambda: False, 0.4)
        self.assertFalse(self.marquee._overflow)
        self.assertEqual(self.marquee._offset, 0)
        self.assertIsNone(self.marquee.get_tooltip_text())

    def test_long_text_scrolls_after_resting(self):
        self.marquee.set_text(LONG)
        self.assertTrue(run_until(lambda: self.marquee._overflow, 2))
        self.assertEqual(self.marquee.get_tooltip_text(), LONG)
        self.assertTrue(run_until(lambda: self.marquee._offset > 5, 5), "o texto não rolou")

    def test_new_text_returns_to_rest(self):
        self.marquee.set_text(LONG)
        self.assertTrue(run_until(lambda: self.marquee._offset > 5, 5))
        self.marquee.set_text("Curto")
        self.assertEqual(self.marquee._offset, 0)
        self.assertTrue(run_until(lambda: not self.marquee._overflow, 2))

    def test_paused_marquee_rests(self):
        self.marquee.set_running(False)
        self.marquee.set_text(LONG)
        run_until(lambda: False, 0.5)
        self.assertTrue(self.marquee._overflow)
        self.assertEqual(self.marquee._offset, 0)


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "precisa de um display (use xvfb-run)")
class PlayerRowTests(unittest.TestCase):
    def _layout(self, width: int, compact: bool = False):
        start, center, end = _block(100), _block(200), _block(60)
        row = PlayerRow(start, center, end)
        row.set_compact(compact)
        window = Gtk.Window(child=row, default_width=width)
        window.present()
        self.addCleanup(window.destroy)
        self.assertTrue(run_until(lambda: row.get_width() == width, 5))
        return [(child.compute_bounds(row)[1].get_x(), child.get_width())
                for child in (start, center, end)]

    def test_wide_row_centres_the_transport(self):
        (start_x, start_w), (center_x, center_w), (end_x, end_w) = self._layout(1000)
        self.assertEqual(start_x, 0)
        self.assertAlmostEqual(center_x + center_w / 2, 500, delta=1)
        self.assertEqual(start_w, end_w)
        self.assertGreater(start_w, 100)  # the title column gets more than its minimum

    def test_compact_row_gives_the_rest_to_the_title(self):
        (_, start_w), (center_x, center_w), (end_x, end_w) = self._layout(400, compact=True)
        self.assertEqual(end_x + end_w, 400)
        self.assertEqual(start_w, 400 - center_w - end_w - 2 * PlayerRow.COMPACT_SPACING)


if __name__ == "__main__":
    unittest.main()
