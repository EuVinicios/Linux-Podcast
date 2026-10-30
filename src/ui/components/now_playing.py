"""Apple Podcasts-style Now Playing ("Em Reprodução") full sheet view."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk

from ...core.playback import RATES
from ...core.player import PlayerState
from ...i18n import _
from ...models import Episode
from ...utils import colors
from ...utils.text import html_to_markup
from ...utils.time_format import (format_clock, format_date, format_duration,
                                  format_long_date, format_remaining, format_size,
                                  format_speed)
from ..helpers import clear, episode_menu, get_app, label
from .cover import CoverImage
from .marquee import MarqueeLabel
from .player_bar import _skip_icon
from .queue_drawer import QueueRow
from .sleep_timer import SleepTimerButton


def _rgba(rgb: colors.RGB, alpha: float) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.red, color.green, color.blue = rgb
    color.alpha = alpha
    return color


class NowPlayingView(Gtk.Widget):
    """Expandable Now Playing screen with artwork, transport, notes and Up Next queue."""

    __gtype_name__ = "PodFlowNowPlayingView"

    def __init__(self):
        super().__init__(css_classes=["now-playing-sheet"])
        app = get_app()
        self.playback = app.playback
        self._compact = False
        self._user_seeking = False
        self._seek_source = 0
        self._release_source = 0
        self._pre_mute_volume = 1.0
        self._panel_mode: str | None = None  # None, "notes" or "queue"
        self._rgb: colors.RGB | None = None

        self.adjustment = Gtk.Adjustment(lower=0, upper=1, step_increment=5, page_increment=30)
        self.volume_adjustment = Gtk.Adjustment(lower=0, upper=1, step_increment=0.05,
                                                page_increment=0.1)

        self._root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 css_classes=["now-playing-root"])
        self._root_box.set_parent(self)

        # -- top header bar (collapse button + actions)
        header = Gtk.Box(spacing=8, css_classes=["now-playing-header"])
        self.close_button = Gtk.Button(icon_name="go-down-symbolic",
                                       css_classes=["flat", "circular", "np-header-btn"],
                                       action_name="win.close-now-playing",
                                       tooltip_text=_("Minimizar reprodutor"))
        header.append(self.close_button)
        header.append(Gtk.Box(hexpand=True))

        self.show_button = Gtk.Button(css_classes=["flat", "pill", "np-header-btn"],
                                      action_name="win.open-current-podcast",
                                      tooltip_text=_("Ver programa"))
        self.show_button.set_child(Adw.ButtonContent(icon_name="go-next-symbolic",
                                                     label=_("Ver programa")))
        header.append(self.show_button)

        self.more_button = Gtk.MenuButton(icon_name="view-more-horizontal-symbolic",
                                          css_classes=["flat", "circular", "np-header-btn"],
                                          tooltip_text=_("Mais opções"))
        self.more_button.set_create_popup_func(self._build_episode_menu)
        header.append(self.more_button)
        self._root_box.append(header)

        # -- main area
        self.body_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=36,
                                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                vexpand=True, hexpand=True, css_classes=["now-playing-body"])

        # Left / main player column
        player_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                             valign=Gtk.Align.CENTER, vexpand=True,
                             css_classes=["now-playing-player"])

        # Compact mini-header shown when Notes or Queue is active on narrow screens
        self.compact_header = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER, visible=False,
                                      css_classes=["np-compact-header"])
        self.compact_cover = CoverImage(48, radius=8)
        self.compact_header.append(self.compact_cover)
        compact_titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                                 valign=Gtk.Align.CENTER)
        self.compact_title = label("", ("np-compact-title",), ellipsize=True, max_width_chars=12)
        self.compact_subtitle = label("", ("caption", "dim-label"), ellipsize=True,
                                      max_width_chars=12)
        compact_titles.append(self.compact_title)
        compact_titles.append(self.compact_subtitle)
        self.compact_header.append(compact_titles)
        compact_click = Gtk.GestureClick()
        compact_click.connect("released", lambda *_: self._set_panel(None))
        self.compact_header.add_controller(compact_click)
        self.compact_header.set_cursor_from_name("pointer")
        player_box.append(self.compact_header)

        self.compact_panel_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, visible=False)
        player_box.append(self.compact_panel_revealer)

        # Large cover art
        self.cover_wrap = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                  css_classes=["now-playing-cover-wrap"])
        self.cover = CoverImage(280, radius=18)
        self.cover.set_halign(Gtk.Align.CENTER)
        self.cover_wrap.append(self.cover)
        player_box.append(self.cover_wrap)

        # Episode date, title, podcast link + save star
        self.meta_row = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER,
                                css_classes=["now-playing-meta"])
        titles_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True,
                             valign=Gtk.Align.CENTER)
        self.eyebrow = label("", ("np-eyebrow", "dim-label"), ellipsize=True, max_width_chars=12)
        titles_col.append(self.eyebrow)
        self.title = MarqueeLabel(("np-title",))
        titles_col.append(self.title)
        self.podcast_button = Gtk.Button(css_classes=["flat", "np-podcast-button"],
                                         halign=Gtk.Align.START,
                                         action_name="win.open-current-podcast",
                                         tooltip_text=_("Ver programa"))
        self.podcast_label = label("", ("np-podcast",), ellipsize=True, max_width_chars=24)
        self.podcast_button.set_child(self.podcast_label)
        titles_col.append(self.podcast_button)
        self.meta_row.append(titles_col)

        self.save_button = Gtk.Button(icon_name="non-starred-symbolic",
                                      css_classes=["flat", "circular", "np-icon-btn"],
                                      valign=Gtk.Align.CENTER, tooltip_text=_("Salvar episódio"))
        self.save_button.connect("clicked", self._on_toggle_saved)
        self.meta_row.append(self.save_button)
        player_box.append(self.meta_row)

        # Scrubber / progress bar
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                               css_classes=["now-playing-progress"])
        self.scale = Gtk.Scale(adjustment=self.adjustment, draw_value=False, hexpand=True,
                               css_classes=["player-scale", "now-playing-scale"])
        self.scale.connect("change-value", self._on_change_value)
        progress_box.append(self.scale)
        times_row = Gtk.Box( css_classes=["np-times-row"])
        self.elapsed = label("0:00", ("time-label", "numeric", "dim-label"), xalign=0.0)
        self.remaining = label("--:--", ("time-label", "numeric", "dim-label"), xalign=1.0)
        self.remaining.set_hexpand(True)
        times_row.append(self.elapsed)
        times_row.append(self.remaining)
        progress_box.append(times_row)
        player_box.append(progress_box)

        # Main transport controls
        transport = Gtk.Box(spacing=28, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                            css_classes=["now-playing-transport"])
        self.back_button = Gtk.Button(css_classes=["flat", "circular", "np-transport"],
                                      action_name="app.skip-back",
                                      tooltip_text=_("Voltar 15 segundos"))
        self.back_button.set_child(_skip_icon("podflow-skip-back-symbolic", "15", pixel_size=32,
                                              number_css="np-skip-number"))
        self.play_button = Gtk.Button(css_classes=["circular", "play-main", "np-play"],
                                      action_name="app.play-pause", tooltip_text=_("Reproduzir"))
        self.play_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.play_icon = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=26)
        self.play_stack.add_named(self.play_icon, "icon")
        self.play_stack.add_named(Adw.Spinner(), "spinner")
        self.play_button.set_child(self.play_stack)
        self.forward_button = Gtk.Button(css_classes=["flat", "circular", "np-transport"],
                                         action_name="app.skip-forward",
                                         tooltip_text=_("Avançar 30 segundos"))
        self.forward_button.set_child(_skip_icon("podflow-skip-forward-symbolic", "30",
                                                 pixel_size=32, number_css="np-skip-number"))
        for widget in (self.back_button, self.play_button, self.forward_button):
            transport.append(widget)
        player_box.append(transport)

        # Volume bar
        volume_row = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER,
                             css_classes=["now-playing-volume-row"])
        self.mute_button = Gtk.Button(icon_name="audio-volume-low-symbolic",
                                      css_classes=["flat", "circular", "dim-label", "np-vol-btn"],
                                      valign=Gtk.Align.CENTER, tooltip_text=_("Silenciar"))
        self.mute_button.connect("clicked", self._on_mute_clicked)
        self.volume_scale = Gtk.Scale(adjustment=self.volume_adjustment, draw_value=False,
                                      hexpand=True,
                                      css_classes=["player-scale", "now-playing-volume"])
        self.volume_adjustment.set_value(self.playback.volume)
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        self.max_vol_button = Gtk.Button(
            icon_name="audio-volume-high-symbolic",
            css_classes=["flat", "circular", "dim-label", "np-vol-btn"],
            valign=Gtk.Align.CENTER, tooltip_text=_("Volume máximo"))
        self.max_vol_button.connect("clicked", lambda *_: self.playback.set_volume(1.0))
        for widget in (self.mute_button, self.volume_scale, self.max_vol_button):
            volume_row.append(widget)
        player_box.append(volume_row)

        # Bottom utility buttons: Speed, Sleep timer, Episode Notes, Up Next Queue
        footer = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         css_classes=["now-playing-footer"])
        self.speed_label = Gtk.Label(label=format_speed(self.playback.rate))
        self.speed_button = Gtk.MenuButton(child=self.speed_label,
                                           css_classes=["flat", "pill", "np-pill", "speed"],
                                           tooltip_text=_("Velocidade de reprodução"),
                                           valign=Gtk.Align.CENTER)
        self.speed_button.set_menu_model(self._speed_menu())
        self.sleep_button = SleepTimerButton()
        self.sleep_button.add_css_class("pill")
        self.sleep_button.add_css_class("np-pill")

        self.notes_button = Gtk.ToggleButton(icon_name="dialog-information-symbolic",
                                             css_classes=["flat", "pill", "np-pill"],
                                             tooltip_text=_("Notas do episódio"),
                                             valign=Gtk.Align.CENTER)
        self._notes_handler = self.notes_button.connect(
            "toggled", lambda btn: self._on_panel_toggled("notes", btn.get_active()))

        self.queue_button = Gtk.ToggleButton(css_classes=["flat", "pill", "np-pill"],
                                             tooltip_text=_("A Seguir"),
                                             valign=Gtk.Align.CENTER)
        queue_btn_box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        queue_btn_box.append(Gtk.Image(icon_name="view-list-bullet-symbolic"))
        self.queue_badge = Gtk.Label(css_classes=["numeric", "caption-heading"], visible=False)
        queue_btn_box.append(self.queue_badge)
        self.queue_button.set_child(queue_btn_box)
        self._queue_handler = self.queue_button.connect(
            "toggled", lambda btn: self._on_panel_toggled("queue", btn.get_active()))

        for widget in (self.speed_button, self.sleep_button, self.notes_button, self.queue_button):
            footer.append(widget)
        player_box.append(footer)

        self.player_clamp = Adw.Clamp(maximum_size=420, tightening_threshold=340,
                                      hexpand=True, child=player_box)
        self.body_row.append(self.player_clamp)

        # -- Side panel (Episode Notes / Up Next Queue)
        self.panel_card = self._build_panel_card()
        self.side_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            child=self.panel_card, reveal_child=False)
        self.body_row.append(self.side_revealer)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                      vexpand=True, hexpand=True, child=self.body_row)
        self._root_box.append(scroller)

        # -- signals
        pb = self.playback
        pb.connect("episode-changed", lambda _pb, ep: self._on_episode(ep))
        pb.connect("state-changed", lambda *_: self._sync_state())
        pb.connect("buffering-changed", lambda *_: self._sync_state())
        pb.connect("position-changed", self._on_position)
        pb.connect("rate-changed",
                   lambda _pb, rate: self.speed_label.set_label(format_speed(rate)))
        pb.connect("volume-changed", self._on_volume_external)
        app.db.connect("episode-changed", self._on_db_episode_changed)
        app.db.connect("queue-changed", lambda *_: self._rebuild_queue())
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.queue_draw())
        self._update_volume_icon(pb.volume)
        self._rebuild_queue()
        if pb.current is not None:
            self._on_episode(pb.current)

    # -- panel construction ----------------------------------------------------------

    def _build_panel_card(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       css_classes=["now-playing-panel"])
        card.set_size_request(340, 380)

        self.panel_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                     vexpand=True, hexpand=True)

        # Notes page
        notes_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=True)
        notes_header = Gtk.Box(spacing=8)
        notes_header.append(label(_("NOTAS DO EPISÓDIO"), ("queue-section", "dim-label"),
                                   xalign=0.0))
        notes_header.append(Gtk.Box(hexpand=True))
        self.notes_meta = label("", ("caption", "dim-label", "numeric"), xalign=1.0)
        notes_header.append(self.notes_meta)
        notes_page.append(notes_header)

        notes_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                             css_classes=["np-notes-body"])
        self.notes_title = label("", ("title-4",), wrap=True, xalign=0.0, max_width_chars=10)
        self.notes_text = label("", ("np-notes-text",), wrap=True, selectable=True, xalign=0.0,
                                max_width_chars=10)
        notes_body.append(self.notes_title)
        notes_body.append(self.notes_text)
        notes_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          child=notes_body, vexpand=True)
        notes_page.append(notes_scroll)
        self.panel_stack.add_named(notes_page, "notes")

        # Queue page
        queue_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, vexpand=True)
        queue_header = Gtk.Box(spacing=8)
        queue_header.append(label(_("A SEGUIR"), ("queue-section", "dim-label"), xalign=0.0))
        queue_header.append(Gtk.Box(hexpand=True))
        self.clear_queue_button = Gtk.Button(label=_("Limpar"), css_classes=["flat", "caption"],
                                             tooltip_text=_("Limpar A Seguir"))
        self.clear_queue_button.connect("clicked", lambda *_: get_app().db.queue_clear())
        queue_header.append(self.clear_queue_button)
        queue_page.append(queue_header)

        self.queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        queue_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          child=self.queue_box, vexpand=True)
        queue_page.append(queue_scroll)
        self.panel_stack.add_named(queue_page, "queue")

        card.append(self.panel_stack)
        return card

    # -- panel toggling & responsiveness ---------------------------------------------

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.add_css_class("compact")
            if self.side_revealer.get_child() is self.panel_card:
                self.side_revealer.set_child(None)
            if self.compact_panel_revealer.get_child() is not self.panel_card:
                self.compact_panel_revealer.set_child(self.panel_card)
            self.panel_card.set_size_request(-1, 260)
        else:
            self.remove_css_class("compact")
            if self.compact_panel_revealer.get_child() is self.panel_card:
                self.compact_panel_revealer.set_child(None)
            if self.side_revealer.get_child() is not self.panel_card:
                self.side_revealer.set_child(self.panel_card)
            self.panel_card.set_size_request(360, 420)
        self._sync_panel_layout()

    def _on_panel_toggled(self, mode: str, active: bool) -> None:
        self._set_panel(mode if active else None)

    def _set_panel(self, mode: str | None) -> None:
        self._panel_mode = mode
        for btn, handler, target in ((self.notes_button, self._notes_handler, "notes"),
                                     (self.queue_button, self._queue_handler, "queue")):
            btn.handler_block(handler)
            btn.set_active(mode == target)
            btn.handler_unblock(handler)
        if mode is not None:
            self.panel_stack.set_visible_child_name(mode)
        self._sync_panel_layout()

    def _sync_panel_layout(self) -> None:
        open_panel = self._panel_mode is not None
        compact = self._compact
        self.side_revealer.set_visible(not compact and open_panel)
        self.side_revealer.set_reveal_child(not compact and open_panel)
        self.compact_panel_revealer.set_visible(compact and open_panel)
        self.compact_panel_revealer.set_reveal_child(compact and open_panel)
        self.compact_header.set_visible(compact and open_panel)
        self.cover_wrap.set_visible(not (compact and open_panel))
        self.meta_row.set_visible(not (compact and open_panel))
        self.cover.set_size(210 if compact else 240 if open_panel else 280)

    # -- playback & database updates -------------------------------------------------

    @staticmethod
    def _speed_menu() -> Gio.Menu:
        menu = Gio.Menu()
        for rate in RATES:
            menu.append(format_speed(rate), f"app.playback-rate({rate:.2f})")
        return menu

    def _on_episode(self, episode: Episode | None) -> None:
        if episode is None:
            return
        self.title.set_text(episode.title)
        self.compact_title.set_text(episode.title)
        self.podcast_label.set_text(episode.podcast_title or _("Podcast"))
        details = (episode.podcast_title, format_date(episode.published))
        self.compact_subtitle.set_text(" — ".join(part for part in details if part))

        eyebrow_parts = []
        if episode.published:
            eyebrow_parts.append(format_long_date(episode.published))
        if episode.season and episode.number:
            eyebrow_parts.append(f"T{episode.season} · E{episode.number}")
        elif episode.number:
            eyebrow_parts.append(_("Episódio {num}").format(num=episode.number))
        eyebrow_text = " · ".join(eyebrow_parts).upper()
        self.eyebrow.set_text(eyebrow_text)
        self.eyebrow.set_visible(bool(eyebrow_text))

        self.cover.set_source(episode.cover_url, episode.podcast_title, episode.podcast_accent)
        self.compact_cover.set_source(episode.cover_url, episode.podcast_title,
                                      episode.podcast_accent)
        self._update_saved_button(episode)
        self._update_notes(episode)
        self._update_times(self.playback.position, self.playback.duration)
        self._sync_state()
        self._rebuild_queue()

        rgb = (colors.parse_hex(episode.podcast_accent)
               or colors.placeholder_color(episode.podcast_title or episode.title))
        self._set_backdrop(rgb)
        if episode.cover_url:
            app = get_app()
            if app is not None and getattr(app, "images", None) is not None:
                app.images.dominant_color(episode.cover_url, self._on_color)

    def _on_color(self, rgb: colors.RGB | None) -> None:
        if rgb is not None:
            self._set_backdrop(rgb)

    def _set_backdrop(self, rgb: colors.RGB) -> None:
        self._rgb = rgb
        self.queue_draw()

    def _on_db_episode_changed(self, db, episode_id: str) -> None:
        current = self.playback.current
        if current is None or current.id != episode_id:
            return
        fresh = db.get_episode(episode_id)
        if fresh is not None:
            self.playback.current.saved = fresh.saved
            self.playback.current.played = fresh.played
            self.playback.current.download_state = fresh.download_state
            self.playback.current.download_path = fresh.download_path
            self.playback.current.download_size = fresh.download_size
            self._update_saved_button(fresh)
            self._update_notes(fresh)

    def _update_saved_button(self, episode: Episode) -> None:
        saved = bool(episode.saved)
        self.save_button.set_icon_name("starred-symbolic" if saved else "non-starred-symbolic")
        self.save_button.set_tooltip_text(
            _("Remover dos salvos") if saved else _("Salvar episódio"))
        if saved:
            self.save_button.add_css_class("accent")
        else:
            self.save_button.remove_css_class("accent")

    def _update_notes(self, episode: Episode) -> None:
        self.notes_title.set_text(episode.title)
        meta_parts = []
        if episode.duration:
            meta_parts.append(format_duration(episode.duration))
        if episode.is_downloaded and episode.download_size:
            meta_parts.append(format_size(episode.download_size))
        self.notes_meta.set_text(" · ".join(meta_parts))
        markup = html_to_markup(episode.description) or GLib.markup_escape_text(
            episode.summary or _("Sem descrição para este episódio."))
        try:
            self.notes_text.set_markup(markup)
        except GLib.Error:
            self.notes_text.set_text(episode.summary or _("Sem descrição para este episódio."))

    def _rebuild_queue(self) -> None:
        app = get_app()
        if app is None:
            return
        clear(self.queue_box)
        current = self.playback.current
        episodes = [e for e in app.db.queue_list() if current is None or e.id != current.id]
        count = len(episodes)
        self.clear_queue_button.set_sensitive(bool(episodes))
        self.queue_badge.set_text(str(count))
        self.queue_badge.set_visible(count > 0)
        if not episodes:
            empty = Adw.StatusPage(
                icon_name="view-list-bullet-symbolic",
                title=_("Nada em A Seguir"),
                description=_("Use o menu de um episódio para adicioná-lo à fila."),
                css_classes=["compact"], vexpand=True)
            self.queue_box.append(empty)
            return
        listbox = Gtk.ListBox(css_classes=["boxed-list", "queue-list"],
                              selection_mode=Gtk.SelectionMode.NONE)
        listbox.connect("row-activated", lambda _lb, row: row.activate_action(
            "app.play-episode", GLib.Variant("s", row.episode.id)))
        for index, episode in enumerate(episodes):
            listbox.append(QueueRow(episode, index))
        self.queue_box.append(listbox)

    def _sync_state(self) -> None:
        state = self.playback.state
        loading = state == PlayerState.LOADING or self.playback.is_buffering
        playing = state in (PlayerState.PLAYING, PlayerState.LOADING)
        self.play_stack.set_visible_child_name("spinner" if loading else "icon")
        self.play_icon.set_from_icon_name(
            "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_button.set_tooltip_text(_("Pausar") if playing else _("Reproduzir"))
        self.title.set_running(playing)
        if playing:
            self.cover_wrap.add_css_class("playing")
        else:
            self.cover_wrap.remove_css_class("playing")

    def _on_position(self, _pb, position: float, duration: float) -> None:
        if not self._user_seeking:
            self._update_times(position, duration)

    def _update_times(self, position: float, duration: float) -> None:
        upper = max(duration, position, 1.0)
        if abs(self.adjustment.get_upper() - upper) > 0.5:
            self.adjustment.set_upper(upper)
        self.adjustment.set_value(position)
        self.elapsed.set_text(format_clock(position))
        self.remaining.set_text(format_remaining(position, duration))

    # -- user interaction ------------------------------------------------------------

    def _on_change_value(self, _scale, _scroll, value: float) -> bool:
        self._user_seeking = True
        duration = self.playback.duration
        value = max(0.0, min(value, self.adjustment.get_upper()))
        self.elapsed.set_text(format_clock(value))
        self.remaining.set_text(format_remaining(value, duration))
        if self._seek_source:
            GLib.source_remove(self._seek_source)
        self._seek_source = GLib.timeout_add(150, self._commit_seek, value)
        return False

    def _commit_seek(self, value: float) -> bool:
        self._seek_source = 0
        self.playback.seek(value)
        if self._release_source:
            GLib.source_remove(self._release_source)
        self._release_source = GLib.timeout_add(450, self._release_seek)
        return GLib.SOURCE_REMOVE

    def _release_seek(self) -> bool:
        self._release_source = 0
        self._user_seeking = False
        return GLib.SOURCE_REMOVE

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        value = scale.get_value()
        if abs(value - self.playback.volume) > 0.001:
            self.playback.set_volume(value)
        self._update_volume_icon(value)

    def _on_volume_external(self, _pb, volume: float) -> None:
        if abs(self.volume_adjustment.get_value() - volume) > 0.001:
            self.volume_adjustment.set_value(volume)
        self._update_volume_icon(volume)

    def _on_mute_clicked(self, _button) -> None:
        if self.playback.volume > 0.01:
            self._pre_mute_volume = self.playback.volume
            self.playback.set_volume(0.0)
        else:
            self.playback.set_volume(self._pre_mute_volume if self._pre_mute_volume > 0.05 else 1.0)

    def _update_volume_icon(self, volume: float) -> None:
        icon = "audio-volume-muted-symbolic" if volume <= 0.001 else "audio-volume-low-symbolic"
        self.mute_button.set_icon_name(icon)

    def _on_toggle_saved(self, _button) -> None:
        current = self.playback.current
        if current is not None:
            self.activate_action("app.toggle-saved", GLib.Variant("s", current.id))

    def _build_episode_menu(self, button: Gtk.MenuButton) -> None:
        app = get_app()
        current = self.playback.current
        if app is None or current is None:
            return
        fresh = app.db.get_episode(current.id) or current
        menu = episode_menu(fresh, in_queue=app.db.in_queue(fresh.id),
                            downloading=app.downloads.is_active(fresh.id),
                            show_podcast=True)
        button.set_menu_model(menu)

    # -- GtkWidget vfuncs ------------------------------------------------------------

    def do_measure(self, orientation: Gtk.Orientation, for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            min_s, nat_s, _mb, _nb = self._root_box.measure(orientation, -1)
            # Keep minimum height modest so small windows (360×480) still fit, while
            # requesting a tall natural height so Adw.BottomSheet fills the window.
            return min(min_s, 320), max(nat_s, 4000), -1, -1
        min_s, nat_s, _mb, _nb = self._root_box.measure(orientation, -1)
        return min_s, nat_s, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self._root_box.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width > 0 and height > 0:
            rect = Graphene.Rect().init(0, 0, width, height)
            dark = Adw.StyleManager.get_default().get_dark()
            base = (0.11, 0.11, 0.13) if dark else (0.96, 0.96, 0.97)
            snapshot.append_color(_rgba(base, 1.0), rect)
            if self._rgb is not None:
                top = Gsk.ColorStop()
                top.offset = 0.0
                top.color = _rgba(self._rgb, 0.46 if dark else 0.28)
                mid = Gsk.ColorStop()
                mid.offset = 0.55
                mid.color = _rgba(self._rgb, 0.18 if dark else 0.10)
                bottom = Gsk.ColorStop()
                bottom.offset = 1.0
                bottom.color = _rgba(self._rgb, 0.04 if dark else 0.02)
                snapshot.append_linear_gradient(rect, Graphene.Point().init(0, 0),
                                                Graphene.Point().init(0, height),
                                                [top, mid, bottom])
        self.snapshot_child(self._root_box, snapshot)

    def do_dispose(self) -> None:
        if self._seek_source:
            GLib.source_remove(self._seek_source)
            self._seek_source = 0
        if self._release_source:
            GLib.source_remove(self._release_source)
            self._release_source = 0
        if self._root_box.get_parent() is self:
            self._root_box.unparent()
        super().do_dispose()
