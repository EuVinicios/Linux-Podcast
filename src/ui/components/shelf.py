"""Horizontally scrolling shelf, laid out like Apple Podcasts' shelves.

The title doubles as "Ver tudo" ("Title ›"), and the paging arrows float over the
edges of the row, showing up only while the pointer is over the shelf.
"""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from ...i18n import _
from ..helpers import clear, section_header


class Shelf(Gtk.Box):
    __gtype_name__ = "PodFlowShelf"

    def __init__(self, title: str, subtitle: str | None = None,
                 on_see_all: Callable[[], None] | None = None, spacing: int = 18):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["shelf"])
        header = section_header(title, subtitle, on_activate=on_see_all)
        self.title_label = header.title_label
        self.append(header)

        self.row = Gtk.Box(spacing=spacing)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_height=True, child=self.row,
                                           css_classes=["shelf-scroller"])
        overlay = Gtk.Overlay(child=self.scroller)
        self.prev_button = Gtk.Button(icon_name="go-previous-symbolic",
                                      css_classes=["circular", "shelf-arrow"],
                                      tooltip_text=_("Anterior"), halign=Gtk.Align.START,
                                      valign=Gtk.Align.CENTER, margin_start=6, can_target=False)
        self.next_button = Gtk.Button(icon_name="go-next-symbolic",
                                      css_classes=["circular", "shelf-arrow"],
                                      tooltip_text=_("Próximo"), halign=Gtk.Align.END,
                                      valign=Gtk.Align.CENTER, margin_end=6, can_target=False)
        self.prev_button.connect("clicked", lambda *_: self._page(-1))
        self.next_button.connect("clicked", lambda *_: self._page(1))
        overlay.add_overlay(self.prev_button)
        overlay.add_overlay(self.next_button)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: self._set_hover(True))
        hover.connect("leave", lambda *_: self._set_hover(False))
        overlay.add_controller(hover)
        self.append(overlay)

        self._animation: Adw.TimedAnimation | None = None
        adjustment = self.scroller.get_hadjustment()
        for signal in ("value-changed", "changed"):
            adjustment.connect(signal, lambda *_: self._update_arrows())
        self._update_arrows()

    def set_title(self, title: str) -> None:
        self.title_label.set_text(title)

    def clear(self) -> None:
        clear(self.row)

    def append_item(self, widget: Gtk.Widget) -> None:
        self.row.append(widget)

    def set_items(self, widgets: list[Gtk.Widget]) -> None:
        self.clear()
        for widget in widgets:
            self.row.append(widget)
        self.scroller.get_hadjustment().set_value(0)
        self._update_arrows()

    def is_empty(self) -> bool:
        return self.row.get_first_child() is None

    def _set_hover(self, hover: bool) -> None:
        # Hidden arrows must not swallow clicks meant for the cards under them.
        if hover:
            self.add_css_class("hover")
        else:
            self.remove_css_class("hover")
        self.prev_button.set_can_target(hover)
        self.next_button.set_can_target(hover)

    def _update_arrows(self) -> None:
        adjustment = self.scroller.get_hadjustment()
        value = adjustment.get_value()
        maximum = adjustment.get_upper() - adjustment.get_page_size()
        self.prev_button.set_visible(maximum > 1 and value > 1)
        self.next_button.set_visible(maximum > 1 and value < maximum - 1)

    def _page(self, direction: int) -> None:
        adjustment = self.scroller.get_hadjustment()
        page = adjustment.get_page_size() * 0.9
        maximum = adjustment.get_upper() - adjustment.get_page_size()
        target = min(max(0.0, adjustment.get_value() + direction * page), maximum)
        if self._animation is not None:
            self._animation.skip()
        target_obj = Adw.PropertyAnimationTarget.new(adjustment, "value")
        self._animation = Adw.TimedAnimation.new(self, adjustment.get_value(), target, 450,
                                                 target_obj)
        self._animation.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._animation.play()
