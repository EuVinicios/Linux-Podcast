"""Rounded artwork with a generated placeholder while the image loads."""

from __future__ import annotations

from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango

from ...utils import colors
from ...utils.text import initials
from ..helpers import get_app


def _rgba(rgb: colors.RGB, alpha: float = 1.0) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.red, color.green, color.blue = rgb
    color.alpha = alpha
    return color


class CoverImage(Gtk.Widget):
    """Square cover drawn with GtkSnapshot (rounded clip, crisp scaling).

    The soft shadow comes from CSS (``.cover`` classes) so it follows the theme.
    """

    __gtype_name__ = "PodFlowCoverImage"

    def __init__(self, size: int = 64, radius: int | None = None):
        super().__init__()
        self._size = size
        self._radius = radius if radius is not None else self._default_radius(size)
        self._texture: Gdk.Texture | None = None
        self._url = ""
        self._title = ""
        self._accent: colors.RGB = colors.placeholder_color("")
        self._token = 0
        self._requested = 0
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.add_css_class("cover")
        self.add_css_class("cover-sm" if self._radius <= 8 else
                           "cover-md" if self._radius <= 12 else "cover-lg")
        self.set_accessible_role(Gtk.AccessibleRole.IMG)
        self.connect("notify::scale-factor", lambda *_: self._request())

    @staticmethod
    def _default_radius(size: int) -> int:
        if size <= 72:
            return 8
        if size <= 200:
            return 12
        return 16

    @property
    def size(self) -> int:
        return self._size

    def set_size(self, size: int) -> None:
        if size != self._size:
            self._size = size
            self.queue_resize()
            self._request()

    def set_source(self, url: str | None, title: str = "", accent: str | None = None) -> None:
        url = url or ""
        self._title = title
        self._accent = colors.parse_hex(accent) or colors.placeholder_color(title or url)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title or ""])
        if url == self._url and self._texture is not None:
            return
        self._url = url
        self._texture = None
        self._requested = 0
        self.queue_draw()
        self._request()

    def _request(self) -> None:
        if not self._url:
            return
        pixels = self._size * max(1, self.get_scale_factor())
        if self._texture is not None and pixels <= self._requested:
            return
        self._requested = pixels
        self._token += 1
        token = self._token
        app = get_app()
        if app is None or getattr(app, "images", None) is None:
            return
        cached = app.images.lookup(self._url, pixels)
        if cached is not None:
            self._texture = cached
            self.queue_draw()
            return
        app.images.load(self._url, pixels, lambda texture: self._on_texture(token, texture))

    def _on_texture(self, token: int, texture: Gdk.Texture | None) -> None:
        if token == self._token and texture is not None:
            self._texture = texture
            self.queue_draw()

    def do_measure(self, orientation, for_size):
        return self._size, self._size, -1, -1

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        side = min(width, height)
        if side <= 0:
            return
        x, y = (width - side) / 2, (height - side) / 2
        rect = Graphene.Rect().init(x, y, side, side)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, self._radius)
        snapshot.push_rounded_clip(rounded)
        if self._texture is not None:
            snapshot.append_scaled_texture(self._texture, Gsk.ScalingFilter.TRILINEAR, rect)
        else:
            self._draw_placeholder(snapshot, rect, side)
        snapshot.pop()

    def _draw_placeholder(self, snapshot: Gtk.Snapshot, rect: Graphene.Rect, side: float) -> None:
        start = colors.adjust(self._accent, lightness_delta=0.10)
        end = colors.darken(self._accent, 0.12)
        stop_a = Gsk.ColorStop()
        stop_a.offset = 0.0
        stop_a.color = _rgba(start)
        stop_b = Gsk.ColorStop()
        stop_b.offset = 1.0
        stop_b.color = _rgba(end)
        origin = rect.get_top_left()
        corner = rect.get_bottom_right()
        snapshot.append_linear_gradient(rect, origin, corner, [stop_a, stop_b])
        if side < 28:
            return
        layout = self.create_pango_layout(initials(self._title))
        font = Pango.FontDescription.from_string("Sans Bold")
        font.set_absolute_size(side * 0.30 * Pango.SCALE)
        layout.set_font_description(font)
        text_width, text_height = layout.get_pixel_size()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(
            rect.get_x() + (side - text_width) / 2, rect.get_y() + (side - text_height) / 2))
        snapshot.append_layout(layout, _rgba((1.0, 1.0, 1.0), 0.92))
        snapshot.restore()
