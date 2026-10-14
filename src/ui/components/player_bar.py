"""Persistent bottom player bar."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, GObject, Graphene, Gsk, Gtk

from ...core.player import PlayerState
from ...core.playback import RATES
from ...i18n import _
from ...utils.time_format import format_clock, format_date, format_remaining, format_speed
from ..helpers import get_app, label
from .cover import CoverImage
from .marquee import MarqueeLabel
from .sleep_timer import SleepTimerButton, sleep_menu


def _skip_icon(icon_name: str, seconds: str) -> Gtk.Widget:
    """Circular arrow with the number of seconds drawn as real text."""
    overlay = Gtk.Overlay()
    overlay.set_child(Gtk.Image(icon_name=icon_name, pixel_size=26))
    number = Gtk.Label(label=seconds, css_classes=["skip-number"], halign=Gtk.Align.CENTER,
                       valign=Gtk.Align.CENTER, margin_top=2)
    number.set_can_target(False)
    overlay.add_overlay(number)
    return overlay


class PlayerRow(Gtk.Widget):
    """Lays out the bar's three columns: artwork and titles, transport, extra controls.

    Wide, the transport sits truly centred with a fixed share of the width, as in
    Apple Podcasts, and the two side columns split the rest evenly, so the title
    gets real room instead of its bare minimum. Compact, artwork and titles take
    every free pixel and the controls follow them on the right, like a mini player.
    """

    __gtype_name__ = "PodFlowPlayerRow"

    SPACING = 24
    COMPACT_SPACING = 8
    CENTER_SHARE = 0.42
    CENTER_MAX = 620

    def __init__(self, start: Gtk.Widget, center: Gtk.Widget, end: Gtk.Widget):
        super().__init__(css_classes=["player-row"])
        self._compact = False
        self._columns = (start, center, end)
        for child in self._columns:
            child.set_parent(self)

    def set_compact(self, compact: bool) -> None:
        if compact != self._compact:
            self._compact = compact
            self.queue_resize()

    @staticmethod
    def _width(child: Gtk.Widget, natural: bool = False) -> int:
        minimum, nat, _min_baseline, _nat_baseline = child.measure(
            Gtk.Orientation.HORIZONTAL, -1)
        return nat if natural else minimum

    def do_measure(self, orientation: Gtk.Orientation, for_size: int):
        if orientation == Gtk.Orientation.VERTICAL:
            sizes = [child.measure(orientation, -1)[:2] for child in self._columns]
            return max(s[0] for s in sizes), max(s[1] for s in sizes), -1, -1
        start, center, end = (self._width(child) for child in self._columns)
        minimum = start + center + end + 2 * self._spacing()
        natural = self._width(self._columns[1], natural=True) + 2 * (max(start, end)
                                                                    + self._spacing())
        return minimum, max(minimum, natural), -1, -1

    def _spacing(self) -> int:
        return self.COMPACT_SPACING if self._compact else self.SPACING

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        start, center, end = self._columns
        gap = self._spacing()
        start_min, center_min, end_min = (self._width(child) for child in self._columns)
        if self._compact:
            center_width = self._width(center, natural=True)
            end_width = self._width(end, natural=True)
            start_width = max(start_min, width - center_width - end_width - 2 * gap)
        else:
            side_min = max(start_min, end_min)
            center_width = max(center_min, min(width * self.CENTER_SHARE, self.CENTER_MAX))
            center_width = max(center_min, min(center_width, width - 2 * (side_min + gap)))
            start_width = end_width = (width - center_width) // 2 - gap
            if start_width < side_min:  # too narrow to stay centred: the title gets the rest
                center_width, end_width = center_min, end_min
                start_width = max(start_min, width - center_min - end_min - 2 * gap)
        center_width = int(center_width)
        for child, x, child_width in ((start, 0, start_width),
                                      (center, start_width + gap, center_width),
                                      (end, width - end_width, end_width)):
            child.allocate(int(child_width), height, -1,
                           Gsk.Transform.new().translate(Graphene.Point().init(x, 0)))

    def do_dispose(self) -> None:
        for child in self._columns:
            if child.get_parent() is self:
                child.unparent()
        super().do_dispose()


class PlayerBar(Gtk.Box):
    __gtype_name__ = "PodFlowPlayerBar"

    # Phone width: mini player with the progress as a thin line on top.
    compact = GObject.Property(type=bool, default=False)
    # Medium width: speed, sleep timer and volume move into the overflow menu.
    condensed = GObject.Property(type=bool, default=False)

    def __init__(self, queue_toggle_action: str = "win.toggle-queue"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["player-bar"])
        app = get_app()
        self.playback = app.playback
        self._user_seeking = False
        self._seek_source = 0
        self._release_source = 0

        self.adjustment = Gtk.Adjustment(lower=0, upper=1, step_increment=5, page_increment=30)
        self.mini_scale = Gtk.Scale(adjustment=self.adjustment, draw_value=False,
                                    css_classes=["mini-progress"], visible=False)
        self.mini_scale.connect("change-value", self._on_change_value)
        self.append(self.mini_scale)

        # -- start: artwork + titles
        start = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        self.cover_button = Gtk.Button(css_classes=["flat", "player-cover"],
                                       action_name="win.open-current-podcast",
                                       tooltip_text=_("Ver programa"))
        self.cover = CoverImage(52, radius=8)
        self.cover_button.set_child(self.cover)
        start.append(self.cover_button)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                         valign=Gtk.Align.CENTER)
        titles.set_size_request(140, -1)
        self.titles = titles
        self.title = MarqueeLabel(("player-title",))
        titles.append(self.title)
        self.subtitle = MarqueeLabel(("player-subtitle", "dim-label"))
        titles.append(self.subtitle)
        # Like the artwork, clicking the titles opens the show.
        open_show = Gtk.GestureClick()
        open_show.connect("released", lambda *_: self.activate_action(
            "win.open-current-podcast", None))
        titles.add_controller(open_show)
        titles.set_cursor_from_name("pointer")
        start.append(titles)

        # -- center: transport + progress
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         valign=Gtk.Align.CENTER, css_classes=["player-center"])
        controls = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER)
        self.back_button = Gtk.Button(css_classes=["flat", "circular", "transport"],
                                      action_name="app.skip-back",
                                      tooltip_text=_("Voltar 15 segundos"))
        self.back_button.set_child(_skip_icon("podflow-skip-back-symbolic", "15"))
        self.play_button = Gtk.Button(css_classes=["circular", "play-main"],
                                      action_name="app.play-pause", tooltip_text=_("Reproduzir"))
        self.play_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.play_icon = Gtk.Image(icon_name="media-playback-start-symbolic", pixel_size=20)
        self.play_stack.add_named(self.play_icon, "icon")
        self.play_stack.add_named(Adw.Spinner(), "spinner")
        self.play_button.set_child(self.play_stack)
        self.forward_button = Gtk.Button(css_classes=["flat", "circular", "transport"],
                                         action_name="app.skip-forward",
                                         tooltip_text=_("Avançar 30 segundos"))
        self.forward_button.set_child(_skip_icon("podflow-skip-forward-symbolic", "30"))
        for widget in (self.back_button, self.play_button, self.forward_button):
            controls.append(widget)
        center.append(controls)

        self.progress_row = Gtk.Box(spacing=10)
        self.elapsed = label("0:00", ("time-label", "numeric", "dim-label"), xalign=1.0)
        self.elapsed.set_width_chars(7)
        self.scale = Gtk.Scale(adjustment=self.adjustment, draw_value=False, hexpand=True,
                               css_classes=["player-scale"])
        self.scale.set_size_request(200, -1)
        self.scale.connect("change-value", self._on_change_value)
        self.remaining = label("--:--", ("time-label", "numeric", "dim-label"))
        self.remaining.set_width_chars(8)
        for widget in (self.elapsed, self.scale, self.remaining):
            self.progress_row.append(widget)
        center.append(self.progress_row)

        # -- end: speed, sleep timer, queue, volume
        end = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
        self.speed_button = Gtk.MenuButton(label=format_speed(1.0), css_classes=["flat", "speed"],
                                           tooltip_text=_("Velocidade de reprodução"),
                                           valign=Gtk.Align.CENTER)
        self.speed_button.set_menu_model(self._speed_menu())
        self.sleep_button = SleepTimerButton()
        self.queue_button = Gtk.ToggleButton(icon_name="view-list-bullet-symbolic",
                                             css_classes=["flat"], action_name=queue_toggle_action,
                                             tooltip_text=_("A Seguir"), valign=Gtk.Align.CENTER)
        self.volume_button = Gtk.ScaleButton(
            icons=["audio-volume-muted-symbolic", "audio-volume-high-symbolic",
                   "audio-volume-low-symbolic", "audio-volume-medium-symbolic"],
            adjustment=Gtk.Adjustment(lower=0, upper=1, step_increment=0.05, page_increment=0.1),
            valign=Gtk.Align.CENTER, tooltip_text=_("Volume"))
        self.volume_button.set_value(self.playback.volume)
        self.volume_button.connect("value-changed", self._on_volume_changed)
        self.overflow_button = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat"],
                                              visible=False, valign=Gtk.Align.CENTER,
                                              tooltip_text=_("Mais controles"))
        overflow = Gio.Menu()
        overflow.append_submenu(_("Velocidade"), self._speed_menu())
        overflow.append_submenu(_("Timer de sono"), sleep_menu())
        self.overflow_button.set_menu_model(overflow)
        for widget in (self.speed_button, self.sleep_button, self.queue_button,
                       self.volume_button, self.overflow_button):
            end.append(widget)

        self.row = PlayerRow(start, center, end)
        self.append(self.row)

        self.connect("notify::compact", lambda *_: self._apply_compact())
        self.connect("notify::condensed", lambda *_: self._apply_compact())
        pb = self.playback
        pb.connect("episode-changed", lambda _pb, ep: self._on_episode(ep))
        pb.connect("state-changed", lambda *_: self._sync_state())
        pb.connect("buffering-changed", lambda *_: self._sync_state())
        pb.connect("position-changed", self._on_position)
        pb.connect("rate-changed", lambda _pb, rate: self.speed_button.set_label(format_speed(rate)))
        pb.connect("volume-changed", self._on_volume_external)
        self.speed_button.set_label(format_speed(pb.rate))
        if pb.current is not None:
            self._on_episode(pb.current)

    @staticmethod
    def _speed_menu() -> Gio.Menu:
        menu = Gio.Menu()
        for rate in RATES:
            menu.append(format_speed(rate), f"app.playback-rate({rate:.2f})")
        return menu

    # -- playback → UI -----------------------------------------------------------------

    def _on_episode(self, episode) -> None:
        if episode is None:
            return
        self.title.set_text(episode.title)
        details = (episode.podcast_title, format_date(episode.published))
        self.subtitle.set_text(" · ".join(part for part in details if part))
        self.cover.set_source(episode.cover_url, episode.podcast_title, episode.podcast_accent)
        self._update_times(self.playback.position, self.playback.duration)
        self._sync_state()

    def _sync_state(self) -> None:
        state = self.playback.state
        loading = state == PlayerState.LOADING or self.playback.is_buffering
        playing = state in (PlayerState.PLAYING, PlayerState.LOADING)
        self.play_stack.set_visible_child_name("spinner" if loading else "icon")
        self.play_icon.set_from_icon_name(
            "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_button.set_tooltip_text(_("Pausar") if playing else _("Reproduzir"))
        # Long titles scroll while something plays and rest (hover still scrolls) when paused.
        self.title.set_running(playing)
        self.subtitle.set_running(playing)

    def _on_position(self, _pb, position: float, duration: float) -> None:
        if self._user_seeking:
            return
        self._update_times(position, duration)

    def _update_times(self, position: float, duration: float) -> None:
        upper = max(duration, position, 1.0)
        if abs(self.adjustment.get_upper() - upper) > 0.5:
            self.adjustment.set_upper(upper)
        self.adjustment.set_value(position)
        self.elapsed.set_text(format_clock(position))
        self.remaining.set_text(format_remaining(position, duration))

    # -- UI → playback -----------------------------------------------------------------

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

    def _on_volume_changed(self, _button, value: float) -> None:
        if abs(value - self.playback.volume) > 0.001:
            self.playback.set_volume(value)

    def _on_volume_external(self, _pb, volume: float) -> None:
        if abs(self.volume_button.get_value() - volume) > 0.001:
            self.volume_button.set_value(volume)

    def _apply_compact(self) -> None:
        compact = self.compact
        full = not (compact or self.condensed)
        self.progress_row.set_visible(not compact)
        self.mini_scale.set_visible(compact)
        self.speed_button.set_visible(full)
        self.sleep_button.set_visible(full)
        self.volume_button.set_visible(full)
        self.overflow_button.set_visible(not full)
        self.back_button.set_visible(not compact)
        self.row.set_compact(compact)
        self.scale.set_size_request(200 if full else 120, -1)
        self.titles.set_size_request(60 if compact else 140 if full else 120, -1)
        self.cover.set_size(40 if compact else 52)
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
