"""Single-line label that scrolls like a marquee when its text does not fit.

Modelled on the now-playing titles of Apple Podcasts: the text rests at its
start for a few seconds, glides left until a repeated copy takes its place
(so every pass ends exactly where it began) and rests again. Instead of a
hard cut, the edges fade out softly; the left fade only appears while the
text is away from its resting position.
"""

from __future__ import annotations

from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk

FADE = 22           # width of each soft edge, in pixels
GAP = 56            # space between the end of the text and its repeated copy
SPEED = 30          # average scrolling speed, in pixels per second
REST_MS = 3000      # pause at the resting position before each pass
HOVER_MS = 350      # delay before a pass started by hovering the text


def _stop(offset: float, alpha: float) -> Gsk.ColorStop:
    stop = Gsk.ColorStop()
    stop.offset = offset
    stop.color = Gdk.RGBA(red=0, green=0, blue=0, alpha=alpha)
    return stop


class MarqueeLabel(Gtk.Widget):
    __gtype_name__ = "PodFlowMarqueeLabel"

    def __init__(self, css_classes: tuple[str, ...] = ()):
        super().__init__(css_classes=list(css_classes), hexpand=True)
        self._text = ""
        self._text_width = 0
        self._overflow = False
        self._offset = 0.0
        self._running = True
        self._hovered = False
        self._animation: Adw.TimedAnimation | None = None
        self._rest_source = 0

        self._label = Gtk.Label(xalign=0, single_line_mode=True)
        self._label.set_parent(self)
        # The copy that follows the text while it scrolls; screen readers skip it.
        self._echo = Gtk.Label(xalign=0, single_line_mode=True)
        self._echo.update_state([Gtk.AccessibleState.HIDDEN], [True])
        self._echo.set_child_visible(False)
        self._echo.set_parent(self)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._set_hovered(True))
        motion.connect("leave", lambda *_: self._set_hovered(False))
        self.add_controller(motion)
        self.connect("map", lambda *_: self._schedule(REST_MS))
        self.connect("unmap", lambda *_: self._reset())
        self._settings = Gtk.Settings.get_default()
        self._settings_handler = self._settings.connect(
            "notify::gtk-enable-animations", lambda *_: self._reset(REST_MS))

    # -- public API --------------------------------------------------------------------

    def get_text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self._label.set_text(text)
        self._echo.set_text(text)
        self._reset(REST_MS)
        self.queue_resize()

    def set_running(self, running: bool) -> None:
        """Scroll in passes while True; when False, finish the current pass and rest."""
        if running == self._running:
            return
        self._running = running
        if running and self._animation is None:
            self._schedule(REST_MS)

    # -- animation ---------------------------------------------------------------------

    def _wants_motion(self) -> bool:
        return (self._overflow and self.get_mapped() and (self._running or self._hovered)
                and self._settings.get_property("gtk-enable-animations"))

    def _set_hovered(self, hovered: bool) -> None:
        self._hovered = hovered
        if hovered and self._animation is None:
            self._schedule(HOVER_MS)

    def _cancel_rest(self) -> None:
        if self._rest_source:
            GLib.source_remove(self._rest_source)
            self._rest_source = 0

    def _reset(self, delay_ms: int | None = None) -> None:
        """Stop at the resting position; optionally start counting the rest again."""
        self._cancel_rest()
        if self._animation is not None:
            animation, self._animation = self._animation, None
            animation.reset()
        self._set_offset(0.0)
        if delay_ms is not None:
            self._schedule(delay_ms)

    def _schedule(self, delay_ms: int) -> None:
        self._cancel_rest()
        if self._animation is None and self._wants_motion():
            self._rest_source = GLib.timeout_add(delay_ms, self._start_pass)

    def _start_pass(self) -> bool:
        self._rest_source = 0
        if not self._wants_motion():
            return GLib.SOURCE_REMOVE
        distance = self._text_width + GAP
        target = Adw.CallbackAnimationTarget.new(self._set_offset)
        animation = Adw.TimedAnimation.new(self, 0.0, distance, int(distance / SPEED * 1000),
                                           target)
        animation.set_easing(Adw.Easing.EASE_IN_OUT_SINE)
        animation.connect("done", self._on_pass_done)
        self._animation = animation
        animation.play()
        return GLib.SOURCE_REMOVE

    def _on_pass_done(self, animation: Adw.TimedAnimation) -> None:
        if animation is not self._animation:
            return
        self._animation = None
        # The copy now sits exactly where the text started: jumping back is invisible.
        self._set_offset(0.0)
        self._schedule(REST_MS)

    def _set_offset(self, offset: float) -> None:
        if offset != self._offset:
            self._offset = offset
            self.queue_draw()

    # -- GtkWidget ---------------------------------------------------------------------

    def do_measure(self, orientation: Gtk.Orientation, for_size: int):
        minimum, natural, min_baseline, nat_baseline = self._label.measure(orientation, -1)
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, natural, -1, -1
        return minimum, natural, min_baseline, nat_baseline

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        text_width = self._label.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        overflow = text_width > width
        self._label.allocate(max(width, text_width), height, baseline, None)
        self._echo.set_child_visible(overflow)
        if overflow:
            shift = Gsk.Transform.new().translate(Graphene.Point().init(text_width + GAP, 0))
            self._echo.allocate(text_width, height, baseline, shift)
        if overflow != self._overflow or text_width != self._text_width:
            self._overflow = overflow
            self._text_width = text_width
            self.set_tooltip_text(self._text if overflow else None)
            self._reset(REST_MS)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if not self._overflow:
            self.snapshot_child(self._label, snapshot)
            return
        width, height = self.get_width(), self.get_height()
        if width <= 0:
            return
        bounds = Graphene.Rect().init(0, 0, width, height)
        fade = min(FADE, width / 3)
        # Fade the left edge in as the text leaves its resting place and out as the copy
        # arrives, so the first letter is never dimmed while the title is still.
        distance = self._text_width + GAP
        left = max(0.0, min(fade, self._offset, distance - self._offset))

        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_linear_gradient(
            bounds, Graphene.Point().init(0, 0), Graphene.Point().init(width, 0),
            [_stop(0.0, 0.0 if left > 0.5 else 1.0), _stop(left / width, 1.0),
             _stop(1.0 - fade / width, 1.0), _stop(1.0, 0.0)])
        snapshot.pop()
        snapshot.push_clip(bounds)
        snapshot.save()
        snapshot.translate(Graphene.Point().init(-self._offset, 0))
        self.snapshot_child(self._label, snapshot)
        self.snapshot_child(self._echo, snapshot)
        snapshot.restore()
        snapshot.pop()
        snapshot.pop()

    def do_dispose(self) -> None:
        self._reset()
        if self._settings_handler:
            self._settings.disconnect(self._settings_handler)
            self._settings_handler = 0
        for child in (self._label, self._echo):
            if child.get_parent() is self:
                child.unparent()
        super().do_dispose()
