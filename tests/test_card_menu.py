import os
import unittest
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from src.models import Podcast
from src.ui.helpers import show_card_menu


@unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
                     "precisa de um display (use xvfb-run)")
class CardMenuTests(unittest.TestCase):
    def test_show_card_menu_activates_action(self):
        app = Adw.Application(application_id="io.github.test.cardmenu")
        calls = []

        def on_activate(app):
            win = Adw.ApplicationWindow(application=app)

            def add(name, cb, param=None):
                act = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
                act.connect("activate", cb)
                win.add_action(act)

            add("mark-podcast-seen", lambda _a, v: calls.append(("seen", v.get_string())), "s")

            btn = Gtk.Button(label="Card")
            win.set_content(btn)
            win.present()

            p = Podcast(id="test:1", itunes_id=1, feed_url="", title="Test")
            show_card_menu(p, btn, 10, 10)

            popover = None
            c = btn.get_first_child()
            while c:
                if isinstance(c, Gtk.Popover):
                    popover = c
                    break
                c = c.get_next_sibling()

            self.assertIsNotNone(popover)

            def find_btns(w):
                res = []
                child = w.get_first_child()
                while child:
                    if "Button" in type(child).__name__:
                        res.append(child)
                    res.extend(find_btns(child))
                    child = child.get_next_sibling()
                return res

            btns = find_btns(popover)
            self.assertGreaterEqual(len(btns), 2)
            # Button 1 is "Marcar como visto"
            btns[1].activate()

            GLib.timeout_add(50, lambda: app.quit())

        app.connect("activate", on_activate)
        app.run([])
        self.assertEqual(calls, [("seen", "test:1")])

    def test_episode_row_context_menu_activates_action(self):
        from src.models import Episode
        from src.ui.components.episode_row import EpisodeRow

        app = Adw.Application(application_id="io.github.test.epmenu")
        app.downloads = type("Mock", (), {"is_active": lambda *_: False, "connect": lambda *_: 0})()
        app.db = type("Mock", (), {"in_queue": lambda *_: False, "connect": lambda *_: 0})()
        app.playback = type("Mock", (), {"current": None, "state": None, "position": 0.0, "rate": 1.0, "connect": lambda *_: 0})()
        calls = []

        def on_activate(app):
            win = Adw.ApplicationWindow(application=app)

            def add(name, cb, param=None):
                act = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
                act.connect("activate", cb)
                app.add_action(act)

            add("mark-seen", lambda _a, v: calls.append(("seen", v.get_string())), "s")

            ep = Episode(id="ep:1", podcast_id="pod:1", title="Ep Title", is_new=True)
            row = EpisodeRow(ep)
            box = Gtk.ListBox()
            box.append(row)
            win.set_content(box)
            win.present()

            row._show_context_menu(10, 10)

            popover = None
            c = row.get_first_child()
            while c:
                if isinstance(c, Gtk.Popover):
                    popover = c
                    break
                c = c.get_next_sibling()

            self.assertIsNotNone(popover)

            def find_btns(w):
                res = []
                child = w.get_first_child()
                while child:
                    if "Button" in type(child).__name__:
                        res.append(child)
                    res.extend(find_btns(child))
                    child = child.get_next_sibling()
                return res

            btns = find_btns(popover)
            # Find the button for "Marcar como visto"
            seen_btn = None
            for b in btns:
                # We can activate each or find by label
                label_widget = b.get_first_child()
                # GtkModelButton structure
                seen_btn = b
                break

            # Activate and verify
            for b in btns:
                b.activate()
                if calls:
                    break

            GLib.timeout_add(50, lambda: app.quit())

        app.connect("activate", on_activate)
        app.run([])
        self.assertEqual(calls, [("seen", "ep:1")])

    def test_window_close_saves_now_playing_and_progress(self):
        from src.models import Episode
        from src.window import PodFlowWindow

        settings = {}
        saved = []

        class MockDB:
            def get_setting(self, k, default=None): return settings.get(k, default)
            def set_setting(self, k, v): settings[k] = v

        class MockPlayback:
            def __init__(self):
                self.current = Episode(id="ep1", podcast_id="p1", title="E1")
            def _save_progress(self, final=False):
                saved.append(final)

        class MockSheet:
            def get_open(self): return True

        class DummyWin:
            def __init__(self):
                self.app = type("App", (), {"db": MockDB(), "playback": MockPlayback()})()
                self.now_playing_sheet = MockSheet()
            def is_maximized(self): return False
            def get_default_size(self): return (1200, 800)

        win = DummyWin()
        PodFlowWindow._on_close_request(win)

        self.assertTrue(settings["now_playing_open"])
        self.assertEqual(settings["window_size"], [1200, 800])
        self.assertEqual(saved, [True])


if __name__ == "__main__":
    unittest.main()
