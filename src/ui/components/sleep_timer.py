"""Sleep timer button: 15/30/45/60 minutes or end of episode, with countdown."""

from __future__ import annotations

from gi.repository import Gio, Gtk

from ...i18n import _
from ...utils.time_format import format_clock
from ..helpers import get_app

OPTIONS = ((0, "Desativado"), (15, "15 minutos"), (30, "30 minutos"), (45, "45 minutos"),
           (60, "1 hora"), (-1, "Fim do episódio"))


def sleep_menu() -> Gio.Menu:
    menu = Gio.Menu()
    for minutes, text in OPTIONS:
        menu.append(_(text), f"app.sleep-timer({minutes})")
    return menu


class SleepTimerButton(Gtk.MenuButton):
    __gtype_name__ = "PodFlowSleepTimerButton"

    def __init__(self):
        super().__init__(css_classes=["flat", "sleep-timer"], tooltip_text=_("Timer de sono"),
                         valign=Gtk.Align.CENTER)
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Image(icon_name="alarm-symbolic"))
        self.countdown = Gtk.Label(css_classes=["numeric", "caption-heading"], visible=False)
        box.append(self.countdown)
        self.set_child(box)
        self.set_menu_model(sleep_menu())
        self._handler = 0

    def do_root(self) -> None:
        Gtk.MenuButton.do_root(self)
        app = get_app()
        if app is not None and not self._handler:
            self._handler = app.playback.connect("sleep-timer-changed", lambda *_: self.refresh())
            self.refresh()

    def do_unroot(self) -> None:
        app = get_app()
        if app is not None and self._handler:
            app.playback.disconnect(self._handler)
            self._handler = 0
        Gtk.MenuButton.do_unroot(self)

    def refresh(self) -> None:
        playback = get_app().playback
        mode = playback.sleep_mode
        if mode == "time":
            self.countdown.set_text(format_clock(playback.sleep_remaining()))
        elif mode == "end":
            self.countdown.set_text(_("Fim"))
        self.countdown.set_visible(mode is not None)
        if mode is not None:
            self.add_css_class("accent")
        else:
            self.remove_css_class("accent")
