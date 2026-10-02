"""Explore hero: wide editorial banners in an auto-advancing carousel."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk

from ...i18n import _
from ...utils import colors
from ..helpers import get_app, label
from .cover import CoverImage

AUTO_ADVANCE_SECONDS = 7


def _rgba(rgb: colors.RGB, alpha: float = 1.0) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.red, color.green, color.blue = rgb
    color.alpha = alpha
    return color


class HeroBanner(Gtk.Box):
    """Gradient card (from the artwork's dominant color) with art, copy and actions."""

    __gtype_name__ = "PodFlowHeroBanner"

    def __init__(self, eyebrow: str, title: str, subtitle: str, artwork_url: str,
                 accent: str = "", on_open: Callable[[], None] | None = None,
                 on_play: Callable[[], None] | None = None):
        super().__init__(spacing=28, css_classes=["hero-banner"], hexpand=True)
        self._rgb = colors.parse_hex(accent) or colors.placeholder_color(title)
        self.set_size_request(-1, 280)

        self.cover = CoverImage(220, radius=14)
        self.cover.set_valign(Gtk.Align.CENTER)
        self.cover.set_source(artwork_url, title, accent)
        self.cover.add_css_class("hero-cover")
        self.append(self.cover)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True,
                       valign=Gtk.Align.CENTER)
        self.eyebrow = label(eyebrow.upper(), ("hero-eyebrow",), ellipsize=True, max_width_chars=10)
        self.title = label(title, ("hero-title",), wrap=True, lines=2, max_width_chars=10)
        self.subtitle = label(subtitle, ("hero-subtitle",), wrap=True, lines=3, max_width_chars=10)
        self.subtitle.set_visible(bool(subtitle))
        text.append(self.eyebrow)
        text.append(self.title)
        text.append(self.subtitle)

        actions = Gtk.Box(spacing=10, margin_top=10)
        self.secondary: Gtk.Button | None = None
        if on_play is not None:
            play = Gtk.Button(css_classes=["pill", "hero-play"])
            play.set_child(Adw.ButtonContent(icon_name="media-playback-start-symbolic",
                                             label=_("Ouvir agora")))
            play.connect("clicked", lambda *_: on_play())
            actions.append(play)
        if on_open is not None:
            more = Gtk.Button(label=_("Ver programa"), css_classes=["pill", "hero-secondary"])
            more.connect("clicked", lambda *_: on_open())
            actions.append(more)
            self.secondary = more
        text.append(actions)
        self.append(text)

        if on_open is not None:
            click = Gtk.GestureClick()
            click.connect("released", lambda *_: on_open())
            self.cover.add_controller(click)
            self.cover.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        self._apply_text_color()
        app = get_app()
        if app is not None and artwork_url:
            app.images.dominant_color(artwork_url, self._on_color)

    def _on_color(self, rgb: colors.RGB | None) -> None:
        if rgb is not None:
            self._rgb = rgb
            self._apply_text_color()
            self.queue_draw()

    def _apply_text_color(self) -> None:
        light_bg = colors.is_light(colors.adjust(self._rgb, lightness_delta=0.04))
        if light_bg:
            self.add_css_class("hero-on-light")
            self.remove_css_class("hero-on-dark")
        else:
            self.add_css_class("hero-on-dark")
            self.remove_css_class("hero-on-light")

    def set_compact(self, compact: bool) -> None:
        self.cover.set_size(96 if compact else 220)
        if self.secondary is not None:
            self.secondary.set_visible(not compact)
        self.set_spacing(16 if compact else 28)
        self.set_size_request(-1, 200 if compact else 280)
        self.subtitle.set_lines(2 if compact else 3)
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        rect = Graphene.Rect().init(0, 0, width, height)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, 20)
        snapshot.push_rounded_clip(rounded)
        start = colors.adjust(self._rgb, lightness_delta=0.06)
        end = colors.darken(self._rgb, 0.16)
        stops = []
        for offset, rgb in ((0.0, start), (1.0, end)):
            stop = Gsk.ColorStop()
            stop.offset = offset
            stop.color = _rgba(rgb)
            stops.append(stop)
        snapshot.append_linear_gradient(rect, Graphene.Point().init(0, 0),
                                        Graphene.Point().init(width, height), stops)
        # Soft highlight behind the artwork for depth.
        glow = Gsk.ColorStop()
        glow.offset = 0.0
        glow.color = _rgba((1.0, 1.0, 1.0), 0.14)
        fade = Gsk.ColorStop()
        fade.offset = 1.0
        fade.color = _rgba((1.0, 1.0, 1.0), 0.0)
        radius = max(height, 1) * 0.9
        snapshot.append_radial_gradient(rect, Graphene.Point().init(height * 0.55, height * 0.5),
                                        radius, radius, 0.0, 1.0, [glow, fade])
        snapshot.pop()
        Gtk.Box.do_snapshot(self, snapshot)


class HeroCarousel(Gtk.Box):
    __gtype_name__ = "PodFlowHeroCarousel"

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         css_classes=["hero-carousel"])
        overlay = Gtk.Overlay()
        self.carousel = Adw.Carousel(allow_long_swipes=False, spacing=16, hexpand=True,
                                     reveal_duration=300)
        overlay.set_child(self.carousel)
        self.prev_button = Gtk.Button(icon_name="go-previous-symbolic", halign=Gtk.Align.START,
                                      valign=Gtk.Align.CENTER, margin_start=10,
                                      css_classes=["circular", "osd", "hero-arrow"],
                                      tooltip_text=_("Anterior"))
        self.next_button = Gtk.Button(icon_name="go-next-symbolic", halign=Gtk.Align.END,
                                      valign=Gtk.Align.CENTER, margin_end=10,
                                      css_classes=["circular", "osd", "hero-arrow"],
                                      tooltip_text=_("Próximo"))
        self.prev_button.connect("clicked", lambda *_: self._step(-1))
        self.next_button.connect("clicked", lambda *_: self._step(1))
        overlay.add_overlay(self.prev_button)
        overlay.add_overlay(self.next_button)
        self.append(overlay)
        self.append(Adw.CarouselIndicatorLines(carousel=self.carousel))

        self._hovered = False
        self._timer = 0
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._set_hovered(True))
        motion.connect("leave", lambda *_: self._set_hovered(False))
        self.add_controller(motion)
        self.connect("map", lambda *_: self._start_timer())
        self.connect("unmap", lambda *_: self._stop_timer())

    def set_banners(self, banners: list[HeroBanner]) -> None:
        while self.carousel.get_n_pages():
            self.carousel.remove(self.carousel.get_nth_page(0))
        for banner in banners:
            self.carousel.append(banner)
        multiple = len(banners) > 1
        self.prev_button.set_visible(multiple)
        self.next_button.set_visible(multiple)

    def set_compact(self, compact: bool) -> None:
        for index in range(self.carousel.get_n_pages()):
            page = self.carousel.get_nth_page(index)
            if isinstance(page, HeroBanner):
                page.set_compact(compact)

    def _set_hovered(self, hovered: bool) -> None:
        self._hovered = hovered

    def _step(self, direction: int) -> None:
        pages = self.carousel.get_n_pages()
        if pages < 2:
            return
        index = (round(self.carousel.get_position()) + direction) % pages
        self.carousel.scroll_to(self.carousel.get_nth_page(index), True)

    def _start_timer(self) -> None:
        if not self._timer:
            self._timer = GLib.timeout_add_seconds(AUTO_ADVANCE_SECONDS, self._advance)

    def _stop_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _advance(self) -> bool:
        if not self._hovered and self.get_mapped():
            self._step(1)
        return GLib.SOURCE_CONTINUE
