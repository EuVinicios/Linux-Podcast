"""Cards: square podcast cards (grids/shelves) and wide episode cards."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Gtk

from ...i18n import _
from ...models import Episode
from ...utils.time_format import format_date, format_duration, format_time_left
from ..helpers import label
from .cover import CoverImage


class PodcastCard(Gtk.Box):
    """Cover + title + subtitle, with a play button revealed on hover."""

    __gtype_name__ = "PodFlowPodcastCard"

    def __init__(self, title: str, subtitle: str, artwork_url: str, accent: str = "",
                 size: int = 160, on_activate: Callable[[], None] | None = None,
                 on_play: Callable[[], None] | None = None, badge: int = 0,
                 rank: int | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["podcast-card"],
                         halign=Gtk.Align.START, valign=Gtk.Align.START)
        self._on_activate = on_activate
        overlay = Gtk.Overlay()
        self.append(overlay)

        button = Gtk.Button(css_classes=["card-button"], tooltip_text=title)
        button.connect("clicked", lambda *_: self._on_activate and self._on_activate())
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_size_request(size, -1)
        self.cover = CoverImage(size)
        self.cover.set_source(artwork_url, title, accent)
        content.append(self.cover)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading = title if rank is None else f"{rank}. {title}"
        title_label = label(heading, ("card-title",), wrap=True, lines=2, max_width_chars=8)
        text.append(title_label)
        if subtitle:
            text.append(label(subtitle, ("card-subtitle", "dim-label"), ellipsize=True,
                              max_width_chars=8))
        content.append(text)
        button.set_child(content)
        overlay.set_child(button)

        if on_play is not None:
            play = Gtk.Button(icon_name="media-playback-start-symbolic",
                              css_classes=["circular", "card-play"],
                              tooltip_text=_("Reproduzir episódio mais recente"),
                              halign=Gtk.Align.END, valign=Gtk.Align.START,
                              margin_top=size - 44, margin_end=8)
            play.connect("clicked", lambda *_: on_play())
            overlay.add_overlay(play)

        if badge > 0:
            pill = label(str(badge) if badge < 100 else "99+", ("badge-count",), xalign=0.5)
            pill.set_halign(Gtk.Align.END)
            pill.set_valign(Gtk.Align.START)
            pill.set_margin_top(6)
            pill.set_margin_end(6)
            pill.set_tooltip_text(_("Episódios novos"))
            pill.set_can_target(False)
            overlay.add_overlay(pill)


class EpisodeCard(Gtk.Button):
    """Wide card used by "Continuar Ouvindo" and "Destaques da Semana"."""

    __gtype_name__ = "PodFlowEpisodeCard"

    def __init__(self, title: str, eyebrow: str, artwork_url: str, accent: str = "",
                 meta: str = "", progress: float | None = None, width: int = 360,
                 cover_size: int = 88, on_activate: Callable[[], None] | None = None):
        super().__init__(css_classes=["card-button", "episode-card"], valign=Gtk.Align.START)
        self.set_size_request(width, -1)
        if on_activate is not None:
            self.connect("clicked", lambda *_: on_activate())
        box = Gtk.Box(spacing=14)
        cover = CoverImage(cover_size)
        cover.set_source(artwork_url, eyebrow or title, accent)
        box.append(cover)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True,
                       valign=Gtk.Align.CENTER)
        if eyebrow:
            text.append(label(eyebrow.upper(), ("episode-eyebrow", "dim-label"), ellipsize=True,
                              max_width_chars=10))
        text.append(label(title, ("card-title",), wrap=True, lines=2, max_width_chars=10))
        if progress is not None:
            bar = Gtk.ProgressBar(fraction=progress, css_classes=["episode-progress"],
                                  margin_top=4)
            text.append(bar)
        if meta:
            text.append(label(meta, ("caption", "dim-label"), ellipsize=True, max_width_chars=10))
        box.append(text)
        self.set_child(box)
        self.set_tooltip_text(title)

    @classmethod
    def for_episode(cls, episode: Episode, on_activate: Callable[[], None],
                    show_progress: bool = False, width: int = 360) -> "EpisodeCard":
        if show_progress and episode.duration:
            meta = format_time_left(episode.position, episode.duration)
            progress = episode.progress
        else:
            parts = [format_date(episode.published), format_duration(episode.duration)]
            meta = " · ".join(p for p in parts if p)
            progress = None
        return cls(episode.title, episode.podcast_title, episode.cover_url,
                   episode.podcast_accent, meta=meta, progress=progress, width=width,
                   on_activate=on_activate)
