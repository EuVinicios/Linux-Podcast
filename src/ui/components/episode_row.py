"""Episode list row laid out like an Apple Podcasts episode lockup.

Date eyebrow, title and summary, then a row of state controls: a capsule play
button that carries the duration or the time left (with a small progress bar),
an animated waveform while the episode plays, status glyphs and the ⋯ menu.
"""

from __future__ import annotations

import math

from gi.repository import GLib, Graphene, Gsk, Gtk

from ...core.player import PlayerState
from ...i18n import _
from ...models import Episode
from ...utils.text import html_to_markup
from ...utils.time_format import format_date, format_duration, format_size, format_time_left
from ..helpers import episode_menu, get_app, label
from .cover import CoverImage


class WaveformIndicator(Gtk.Widget):
    """Four bars that bounce while the episode plays (Apple's WaveformIndicatorView)."""

    __gtype_name__ = "PodFlowWaveformIndicator"

    BARS = 4
    BAR_WIDTH = 2.0
    # Resting heights, and per-bar speed and phase of the animation.
    REST = (0.45, 0.8, 0.6, 0.9)
    SPEED = (5.3, 6.7, 4.6, 5.9)
    PHASE = (0.0, 1.7, 0.9, 2.6)

    def __init__(self):
        super().__init__(css_classes=["waveform"], valign=Gtk.Align.CENTER)
        self._tick = 0

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int):
        size = 13 if orientation == Gtk.Orientation.HORIZONTAL else 12
        return size, size, -1, -1

    def set_running(self, running: bool) -> None:
        if running and not self._tick:
            self._tick = self.add_tick_callback(lambda *_: self.queue_draw() or True)
        elif not running and self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        color = self.get_color()
        clock = self.get_frame_clock() if self._tick else None
        seconds = clock.get_frame_time() / 1_000_000 if clock is not None else None
        gap = (width - self.BARS * self.BAR_WIDTH) / (self.BARS - 1)
        for index in range(self.BARS):
            if seconds is None:
                level = self.REST[index]
            else:
                wave = math.sin(seconds * self.SPEED[index] + self.PHASE[index])
                level = 0.3 + 0.7 * (0.5 + 0.5 * wave)
            bar = max(self.BAR_WIDTH, height * level)
            rect = Graphene.Rect().init(index * (self.BAR_WIDTH + gap), (height - bar) / 2,
                                        self.BAR_WIDTH, bar)
            rounded = Gsk.RoundedRect()
            rounded.init_from_rect(rect, self.BAR_WIDTH / 2)
            snapshot.push_rounded_clip(rounded)
            snapshot.append_color(color, rect)
            snapshot.pop()


class PlayPill(Gtk.Button):
    """Capsule play button: ▶ 1h 3min, ▶ ▬▬── Faltam 25 min, or the waveform while playing."""

    __gtype_name__ = "PodFlowPlayPill"

    def __init__(self):
        super().__init__(css_classes=["episode-play"], valign=Gtk.Align.CENTER,
                         halign=Gtk.Align.START, tooltip_text=_("Reproduzir"))
        box = Gtk.Box(spacing=6)
        self.glyph = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               valign=Gtk.Align.CENTER)
        self.icon = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=12)
        self.waveform = WaveformIndicator()
        self.glyph.add_named(self.icon, "icon")
        self.glyph.add_named(self.waveform, "waveform")
        box.append(self.glyph)
        self.bar = Gtk.ProgressBar(css_classes=["episode-play-progress"], valign=Gtk.Align.CENTER)
        self.bar.set_size_request(30, -1)
        box.append(self.bar)
        self.text = Gtk.Label(css_classes=["numeric"])
        box.append(self.text)
        self.set_child(box)

    def update(self, episode: Episode, current: bool, playing: bool,
               position: float | None = None) -> None:
        position = episode.position if position is None else position
        duration = episode.duration
        started = current or (episode.in_progress and duration > 0)
        if playing:
            text = format_time_left(position, duration) or _("Reproduzindo")
        elif episode.played and not current:
            text = _("Reproduzido")
        elif started and duration > 0:
            text = format_time_left(position, duration)
        else:
            text = format_duration(duration) or _("Reproduzir")
        if self.text.get_text() != text:
            self.text.set_text(text)
        show_bar = started and duration > 0 and not (episode.played and not current)
        self.bar.set_visible(show_bar)
        if show_bar:
            self.bar.set_fraction(max(0.0, min(1.0, position / duration)))
        icon = ("media-playlist-repeat-symbolic" if episode.played and not current
                else "media-playback-pause-symbolic" if current and playing
                else "media-playback-start-symbolic")
        self.icon.set_from_icon_name(icon)
        self.glyph.set_visible_child_name("waveform" if playing else "icon")
        self.waveform.set_running(playing)
        self.set_tooltip_text(_("Pausar") if playing else _("Reproduzir"))
        if current:
            self.add_css_class("current")
        else:
            self.remove_css_class("current")


class EpisodeRow(Gtk.ListBoxRow):
    """Date eyebrow, title, summary and state controls; click to expand the notes."""

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

        controls = Gtk.Box(spacing=10, css_classes=["episode-controls"])
        self.play = PlayPill()
        self.play.connect("clicked", self._on_play_clicked)
        controls.append(self.play)
        self.meta = label("", ("caption", "dim-label", "numeric"), ellipsize=True,
                          max_width_chars=10)
        controls.append(self.meta)
        self.download_icon = Gtk.Image(icon_name="folder-download-symbolic", pixel_size=12,
                                       css_classes=["dim-label"],
                                       tooltip_text=_("Disponível offline"))
        controls.append(self.download_icon)
        self.saved_icon = Gtk.Image(icon_name="starred-symbolic", pixel_size=12,
                                    css_classes=["accent"], tooltip_text=_("Salvo"))
        controls.append(self.saved_icon)
        controls.append(Gtk.Box(hexpand=True))
        self.menu_button = Gtk.MenuButton(icon_name="view-more-horizontal-symbolic",
                                          valign=Gtk.Align.CENTER,
                                          css_classes=["flat", "circular", "episode-more"],
                                          tooltip_text=_("Mais opções"))
        self.menu_button.set_create_popup_func(self._build_menu)
        controls.append(self.menu_button)
        body.append(controls)
        outer.append(body)
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
            (app.playback, app.playback.connect("position-changed", self._on_position)),
            (app.downloads, app.downloads.connect("progress", self._on_download_progress)),
        ]
        self._refresh()

    def do_unroot(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        self.play.waveform.set_running(False)
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

    def _on_position(self, playback, position: float, _duration: float) -> None:
        current = playback.current
        if current is not None and current.id == self.episode.id:
            _current, playing = self._is_current()
            self.play.update(self.episode, True, playing, position)

    def _is_current(self) -> tuple[bool, bool]:
        app = get_app()
        if app is None or app.playback.current is None:
            return False, False
        current = app.playback.current.id == self.episode.id
        return current, current and app.playback.state in (PlayerState.PLAYING,
                                                            PlayerState.LOADING)

    def _refresh_play(self) -> None:
        current, playing = self._is_current()
        position = get_app().playback.position if current else None
        self.play.update(self.episode, current, playing, position)

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

        app = get_app()
        downloading = app is not None and app.downloads.is_active(episode.id)
        if downloading:
            percent = int((app.downloads.progress(episode.id) or 0) * 100)
            meta = _("Baixando… {percent}%").format(percent=percent)
        elif episode.download_state == "failed":
            meta = _("Falha no download")
        elif episode.is_downloaded:
            meta = format_size(episode.download_size)
        else:
            meta = ""
        self.meta.set_text(meta)
        self.meta.set_visible(bool(meta))
        self.download_icon.set_visible(episode.is_downloaded)
        self.saved_icon.set_visible(episode.saved)
        if app is not None:
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
