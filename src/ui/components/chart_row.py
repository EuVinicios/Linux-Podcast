"""Numbered ranking row (Top Podcasts / Top Episódios)."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk

from ...i18n import _
from ..helpers import label
from .cover import CoverImage


class ChartRow(Gtk.ListBoxRow):
    __gtype_name__ = "PodFlowChartRow"

    def __init__(self, rank: int, title: str, subtitle: str, artwork_url: str, genre: str = "",
                 accent: str = "", explicit: bool = False,
                 on_activate: Callable[[], None] | None = None,
                 on_play: Callable[[], None] | None = None,
                 secondary_icon: str | None = None, secondary_tooltip: str = "",
                 on_secondary: Callable[[], None] | None = None):
        super().__init__(css_classes=["chart-row"], activatable=True)
        self.on_activate = on_activate
        box = Gtk.Box(spacing=14)

        rank_label = label(str(rank), ("chart-rank", "numeric"), xalign=1.0)
        rank_label.set_width_chars(2)
        if rank <= 3:
            rank_label.add_css_class("top")
        box.append(rank_label)

        cover = CoverImage(64)
        cover.set_valign(Gtk.Align.CENTER)
        cover.set_source(artwork_url, title, accent)
        box.append(cover)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True,
                       valign=Gtk.Align.CENTER)
        title_row = Gtk.Box(spacing=6)
        title_row.append(label(title, ("chart-title",), wrap=True, lines=2, max_width_chars=10))
        if explicit:
            badge = label("E", ("explicit-badge",), xalign=0.5)
            badge.set_valign(Gtk.Align.START)
            badge.set_tooltip_text(_("Conteúdo explícito"))
            title_row.append(badge)
        text.append(title_row)
        if subtitle:
            text.append(label(subtitle, ("chart-subtitle", "dim-label"), ellipsize=True,
                              max_width_chars=10))
        if genre:
            pill = label(genre, ("genre-badge",))
            pill.set_halign(Gtk.Align.START)
            pill.set_margin_top(3)
            text.append(pill)
        box.append(text)

        if on_play is not None:
            play = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                              css_classes=["circular", "chart-play"], tooltip_text=_("Reproduzir"))
            play.connect("clicked", lambda *_: on_play())
            box.append(play)
        if on_secondary is not None and secondary_icon:
            extra = Gtk.Button(icon_name=secondary_icon, valign=Gtk.Align.CENTER,
                               css_classes=["flat", "circular"], tooltip_text=secondary_tooltip)
            extra.connect("clicked", lambda *_: on_secondary())
            box.append(extra)
        self.set_child(box)
