"""Persistent bottom player bar."""

from __future__ import annotations

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ...core.player import PlayerState
from ...core.playback import RATES
from ...i18n import _
from ...utils.time_format import format_clock, format_remaining, format_speed
from ..helpers import get_app, label
from .cover import CoverImage
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


class MarqueeLabel(Gtk.ScrolledWindow):
    """Single-line label that slowly scrolls back and forth when it overflows."""

    __gtype_name__ = "PodFlowMarqueeLabel"

    def __init__(self, css_classes: tuple[str, ...] = ()):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                         vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                         propagate_natural_width=False, can_target=False, hexpand=True)
        self.label = Gtk.Label(xalign=0, css_classes=list(css_classes), single_line_mode=True)
        self.set_child(self.label)
        self._animation: Adw.TimedAnimation | None = None
        self._overflow = 0.0
        self.get_hadjustment().connect("changed", lambda *_: self._check())
        self.connect("map", lambda *_: self._restart())
        self.connect("unmap", lambda *_: self._stop())

    def set_text(self, text: str) -> None:
        self.label.set_text(text)
        self._overflow = -1
        GLib.idle_add(self._restart_once)

    def _restart_once(self) -> bool:
        self._restart()
        return GLib.SOURCE_REMOVE

    def _check(self) -> None:
        adjustment = self.get_hadjustment()
        overflow = adjustment.get_upper() - adjustment.get_page_size()
        if abs(overflow - self._overflow) > 2:
            self._restart()

    def _stop(self) -> None:
        if self._animation is not None:
            self._animation.reset()
            self._animation = None
        self.get_hadjustment().set_value(0)

    def _restart(self) -> None:
        self._stop()
        adjustment = self.get_hadjustment()
        self._overflow = adjustment.get_upper() - adjustment.get_page_size()
        if self._overflow <= 4 or not self.get_mapped():
            return
        duration = int(self._overflow / 28 * 1000) + 2000
        target = Adw.PropertyAnimationTarget.new(adjustment, "value")
        self._animation = Adw.TimedAnimation.new(self, 0, self._overflow, duration, target)
        self._animation.set_easing(Adw.Easing.EASE_IN_OUT_SINE)
        self._animation.set_alternate(True)
        self._animation.set_repeat_count(0)
        self._animation.play()


class PlayerBar(Gtk.Box):
    __gtype_name__ = "PodFlowPlayerBar"

    compact = GObject.Property(type=bool, default=False)

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

        row = Gtk.CenterBox(css_classes=["player-row"])
        self.append(row)

        # -- start: artwork + titles
        start = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        self.cover_button = Gtk.Button(css_classes=["flat", "player-cover"],
                                       action_name="win.open-current-podcast",
                                       tooltip_text=_("Ver programa"))
        self.cover = CoverImage(52, radius=8)
        self.cover_button.set_child(self.cover)
        start.append(self.cover_button)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True,
                         valign=Gtk.Align.CENTER)
        titles.set_size_request(120, -1)
        self.titles = titles
        self.title = MarqueeLabel(("player-title",))
        titles.append(self.title)
        self.subtitle = label("", ("player-subtitle", "dim-label"), ellipsize=True,
                              max_width_chars=24)
        titles.append(self.subtitle)
        self.titles_clamp = Adw.Clamp(maximum_size=320, tightening_threshold=260, child=titles)
        start.append(self.titles_clamp)
        row.set_start_widget(start)

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
        self.scale.set_size_request(260, -1)
        self.scale.connect("change-value", self._on_change_value)
        self.remaining = label("--:--", ("time-label", "numeric", "dim-label"))
        self.remaining.set_width_chars(8)
        for widget in (self.elapsed, self.scale, self.remaining):
            self.progress_row.append(widget)
        center.append(self.progress_row)
        row.set_center_widget(center)

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
        row.set_end_widget(end)

        self.connect("notify::compact", lambda *_: self._apply_compact())
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
        self.subtitle.set_text(episode.podcast_title or "")
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
        self.progress_row.set_visible(not compact)
        self.mini_scale.set_visible(compact)
        self.speed_button.set_visible(not compact)
        self.sleep_button.set_visible(not compact)
        self.volume_button.set_visible(not compact)
        self.overflow_button.set_visible(compact)
        self.back_button.set_visible(not compact)
        self.titles_clamp.set_maximum_size(180 if compact else 320)
        self.titles.set_size_request(60 if compact else 120, -1)
        self.cover.set_size(40 if compact else 52)
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
