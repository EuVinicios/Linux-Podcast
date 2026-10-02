"""Horizontally scrolling shelf with a title and arrow buttons."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk

from ...i18n import _
from ..helpers import clear, label


class Shelf(Gtk.Box):
    __gtype_name__ = "PodFlowShelf"

    def __init__(self, title: str, subtitle: str | None = None,
                 on_see_all: Callable[[], None] | None = None, spacing: int = 18):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["shelf"])
        header = Gtk.Box(spacing=6)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                         valign=Gtk.Align.END)
        self.title_label = label(title, ("shelf-title",), ellipsize=True)
        titles.append(self.title_label)
        if subtitle:
            titles.append(label(subtitle, ("shelf-subtitle", "dim-label"), ellipsize=True))
        header.append(titles)
        if on_see_all is not None:
            see_all = Gtk.Button(label=_("Ver tudo"), css_classes=["flat", "see-all"],
                                 valign=Gtk.Align.END)
            see_all.connect("clicked", lambda *_: on_see_all())
            header.append(see_all)
        self.prev_button = Gtk.Button(icon_name="go-previous-symbolic",
                                      css_classes=["circular", "flat", "shelf-arrow"],
                                      tooltip_text=_("Anterior"), valign=Gtk.Align.END)
        self.next_button = Gtk.Button(icon_name="go-next-symbolic",
                                      css_classes=["circular", "flat", "shelf-arrow"],
                                      tooltip_text=_("Próximo"), valign=Gtk.Align.END)
        self.prev_button.connect("clicked", lambda *_: self._page(-1))
        self.next_button.connect("clicked", lambda *_: self._page(1))
        header.append(self.prev_button)
        header.append(self.next_button)
        self.append(header)

        self.row = Gtk.Box(spacing=spacing)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_height=True, child=self.row,
                                           css_classes=["shelf-scroller"])
        self.append(self.scroller)
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

    def _update_arrows(self) -> None:
        adjustment = self.scroller.get_hadjustment()
        maximum = adjustment.get_upper() - adjustment.get_page_size()
        scrollable = maximum > 1
        self.prev_button.set_visible(scrollable)
        self.next_button.set_visible(scrollable)
        self.prev_button.set_sensitive(adjustment.get_value() > 1)
        self.next_button.set_sensitive(adjustment.get_value() < maximum - 1)

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
