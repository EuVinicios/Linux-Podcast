"""Episode list row with a circular play button that shows progress."""

from __future__ import annotations

import math

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk

from ...core.player import PlayerState
from ...i18n import _
from ...models import Episode
from ...utils.text import html_to_markup
from ...utils.time_format import format_date, format_duration, format_size, format_time_left
from ..helpers import episode_menu, get_app, label
from .cover import CoverImage


class _RingBox(Gtk.Box):
    """Draws a progress ring around its child icon."""

    __gtype_name__ = "PodFlowRingBox"

    def __init__(self):
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.fraction = 0.0
        self.icon = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=16,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True,
                              vexpand=True)
        self.append(self.icon)
        self.set_size_request(36, 36)
        # Keep the icon's expand flags from propagating up to the row layout.
        self.set_hexpand(False)
        self.set_vexpand(False)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        color = self.get_color()
        radius = min(width, height) / 2 - 1.5
        cx, cy = width / 2, height / 2
        track = Gdk.RGBA()
        track.red, track.green, track.blue, track.alpha = color.red, color.green, color.blue, 0.18
        builder = Gsk.PathBuilder.new()
        builder.add_circle(Graphene.Point().init(cx, cy), radius)
        snapshot.append_stroke(builder.to_path(), Gsk.Stroke.new(2.0), track)
        if 0.005 < self.fraction:
            fraction = min(self.fraction, 0.9999)
            angle = -math.pi / 2 + 2 * math.pi * fraction
            arc = Gsk.PathBuilder.new()
            arc.move_to(cx, cy - radius)
            arc.svg_arc_to(radius, radius, 0, fraction > 0.5, True,
                           cx + radius * math.cos(angle), cy + radius * math.sin(angle))
            stroke = Gsk.Stroke.new(2.5)
            stroke.set_line_cap(Gsk.LineCap.ROUND)
            snapshot.append_stroke(arc.to_path(), stroke, color)
        Gtk.Box.do_snapshot(self, snapshot)


class PlayButton(Gtk.Button):
    __gtype_name__ = "PodFlowPlayButton"

    def __init__(self):
        super().__init__(css_classes=["flat", "circular", "play-ring"],
                         valign=Gtk.Align.CENTER, tooltip_text=_("Reproduzir"))
        self.ring = _RingBox()
        self.set_child(self.ring)
        self.set_hexpand(False)
        self._playing = False

    def update(self, fraction: float, playing: bool) -> None:
        if abs(fraction - self.ring.fraction) > 0.001:
            self.ring.fraction = fraction
            self.ring.queue_draw()
        if playing != self._playing:
            self._playing = playing
            self.ring.icon.set_from_icon_name(
                "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
            self.set_tooltip_text(_("Pausar") if playing else _("Reproduzir"))
            if playing:
                self.add_css_class("playing")
            else:
                self.remove_css_class("playing")


class EpisodeRow(Gtk.ListBoxRow):
    """Date eyebrow, title, summary, meta line; click to expand the notes."""

    __gtype_name__ = "PodFlowEpisodeRow"

    def __init__(self, episode: Episode, show_cover: bool = False, show_podcast: bool = False,
                 compact: bool = False):
        super().__init__(css_classes=["episode-row"], activatable=True)
        self.episode = episode
        self._show_podcast = show_podcast
        self._expanded = False
        self._handlers: list[tuple[object, int]] = []

        outer = Gtk.Box(spacing=14)
        if show_cover:
            self.cover = CoverImage(48 if compact else 64)
            self.cover.set_valign(Gtk.Align.START)
            self.cover.set_source(episode.cover_url, episode.podcast_title, episode.podcast_accent)
            outer.append(self.cover)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        self.eyebrow = label("", ("episode-eyebrow", "dim-label"), ellipsize=True,
                             max_width_chars=10)
        self.title = label(episode.title, ("episode-title",), wrap=True,
                           lines=2 if compact else 3, max_width_chars=10)
        body.append(self.eyebrow)
        body.append(self.title)
        self.summary = label(episode.summary, ("episode-summary", "dim-label"), wrap=True,
                             lines=2, max_width_chars=10)
        self.summary.set_visible(bool(episode.summary) and not compact)
        body.append(self.summary)

        self.notes = label("", ("episode-notes",), wrap=True, selectable=True, max_width_chars=10)
        self.notes.set_visible(False)
        body.append(self.notes)

        meta = Gtk.Box(spacing=8, margin_top=4)
        self.progress = Gtk.ProgressBar(css_classes=["episode-progress"], valign=Gtk.Align.CENTER)
        self.progress.set_size_request(64, -1)
        meta.append(self.progress)
        self.meta = label("", ("caption", "dim-label", "numeric"), ellipsize=True,
                          max_width_chars=10)
        meta.append(self.meta)
        self.download_icon = Gtk.Image(icon_name="folder-download-symbolic", pixel_size=12,
                                       css_classes=["dim-label"],
                                       tooltip_text=_("Disponível offline"))
        meta.append(self.download_icon)
        self.saved_icon = Gtk.Image(icon_name="starred-symbolic", pixel_size=12,
                                    css_classes=["accent"], tooltip_text=_("Salvo"))
        meta.append(self.saved_icon)
        body.append(meta)
        outer.append(body)

        self.play = PlayButton()
        self.play.connect("clicked", self._on_play_clicked)
        outer.append(self.play)

        self.menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER,
                                          css_classes=["flat", "circular"],
                                          tooltip_text=_("Mais opções"))
        self.menu_button.set_create_popup_func(self._build_menu)
        outer.append(self.menu_button)
        self.set_child(outer)
        self._refresh()

    # -- lifecycle -------------------------------------------------------------------

    def do_root(self) -> None:
        Gtk.ListBoxRow.do_root(self)
        app = get_app()
        if app is None or self._handlers:
            return
        self._handlers = [
            (app.db, app.db.connect("episode-changed", self._on_episode_changed)),
            (app.playback, app.playback.connect("episode-changed", lambda *_: self._refresh_play())),
            (app.playback, app.playback.connect("state-changed", lambda *_: self._refresh_play())),
            (app.downloads, app.downloads.connect("progress", self._on_download_progress)),
        ]
        self._refresh()

    def do_unroot(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        Gtk.ListBoxRow.do_unroot(self)

    # -- state -----------------------------------------------------------------------

    def _on_episode_changed(self, db, episode_id: str) -> None:
        if episode_id != self.episode.id:
            return
        fresh = db.get_episode(episode_id)
        if fresh is not None:
            self.episode = fresh
            self._refresh()

    def _on_download_progress(self, _downloads, episode_id: str, fraction: float) -> None:
        if episode_id == self.episode.id:
            self.meta.set_text(_("Baixando… {percent}%").format(percent=int(fraction * 100)))

    def _is_current(self) -> tuple[bool, bool]:
        app = get_app()
        if app is None or app.playback.current is None:
            return False, False
        current = app.playback.current.id == self.episode.id
        return current, current and app.playback.state in (PlayerState.PLAYING,
                                                            PlayerState.LOADING)

    def _refresh_play(self) -> None:
        _current, playing = self._is_current()
        fraction = 1.0 if self.episode.played else self.episode.progress
        self.play.update(fraction, playing)

    def _refresh(self) -> None:
        episode = self.episode
        eyebrow = format_date(episode.published)
        if self._show_podcast and episode.podcast_title:
            eyebrow = f"{eyebrow} · {episode.podcast_title}" if eyebrow else episode.podcast_title
        self.eyebrow.set_text(eyebrow.upper())
        self.eyebrow.set_visible(bool(eyebrow))
        self.title.set_text(episode.title)
        if episode.played:
            self.title.add_css_class("played")
        else:
            self.title.remove_css_class("played")

        in_progress = episode.in_progress and episode.duration > 0
        self.progress.set_visible(in_progress)
        if in_progress:
            self.progress.set_fraction(episode.progress)

        app = get_app()
        downloading = app is not None and app.downloads.is_active(episode.id)
        if downloading:
            percent = int((app.downloads.progress(episode.id) or 0) * 100)
            meta = _("Baixando… {percent}%").format(percent=percent)
        elif episode.played:
            meta = _("Reproduzido")
        elif in_progress:
            meta = format_time_left(episode.position, episode.duration)
        else:
            meta = format_duration(episode.duration)
        if episode.download_state == "failed" and not downloading:
            meta = _("Falha no download")
        if episode.is_downloaded and episode.download_size:
            meta = f"{meta} · {format_size(episode.download_size)}" if meta else format_size(
                episode.download_size)
        self.meta.set_text(meta)
        self.download_icon.set_visible(episode.is_downloaded)
        self.saved_icon.set_visible(episode.saved)
        self._refresh_play()

    def toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        if self._expanded and not self.notes.get_label():
            markup = html_to_markup(self.episode.description) or GLib.markup_escape_text(
                self.episode.summary or _("Sem descrição."))
            try:
                self.notes.set_markup(markup)
            except GLib.Error:
                self.notes.set_text(self.episode.summary)
        self.notes.set_visible(self._expanded)
        self.summary.set_visible(not self._expanded and bool(self.episode.summary))
        self.title.set_lines(-1 if self._expanded else 3)

    # -- actions ---------------------------------------------------------------------

    def _on_play_clicked(self, _button) -> None:
        current, _playing = self._is_current()
        if current:
            self.activate_action("app.play-pause", None)
        else:
            self.activate_action("app.play-episode", GLib.Variant("s", self.episode.id))

    def _build_menu(self, button: Gtk.MenuButton) -> None:
        app = get_app()
        menu = episode_menu(self.episode, in_queue=app.db.in_queue(self.episode.id),
                            downloading=app.downloads.is_active(self.episode.id),
                            show_podcast=self._show_podcast)
        button.set_menu_model(menu)
