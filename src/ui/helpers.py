"""Small UI helpers shared by views and components."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ..i18n import _
from ..models import Episode


def get_app():
    """The running PodFlowApplication (services live on it)."""
    return Gio.Application.get_default()


def label(text: str = "", css: Iterable[str] = (), xalign: float = 0.0, wrap: bool = False,
          lines: int = -1, ellipsize: bool = False, selectable: bool = False,
          max_width_chars: int = -1, markup: bool = False) -> Gtk.Label:
    widget = Gtk.Label(xalign=xalign, css_classes=list(css))
    if markup:
        widget.set_markup(text)
    else:
        widget.set_text(text)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        widget.set_natural_wrap_mode(Gtk.NaturalWrapMode.WORD)
    if lines > 0:
        widget.set_lines(lines)
    if ellipsize or lines > 0:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    if max_width_chars > 0:
        widget.set_max_width_chars(max_width_chars)
    widget.set_selectable(selectable)
    return widget


def clear(container: Gtk.Widget) -> None:
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        if isinstance(container, Gtk.FlowBox):
            container.remove(child)
        elif hasattr(container, "remove"):
            container.remove(child)
        else:
            child.unparent()
        child = following


def page_box(spacing: int = 32) -> Gtk.Box:
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing,
                   css_classes=["page-content"])


def scrolled_page(content: Gtk.Widget, maximum: int = 1400) -> Gtk.ScrolledWindow:
    clamp = Adw.Clamp(maximum_size=maximum, tightening_threshold=maximum - 200, child=content)
    return Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp,
                              vexpand=True)


def section_header(title: str, subtitle: str | None = None) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.append(label(title, ("shelf-title",)))
    if subtitle:
        box.append(label(subtitle, ("shelf-subtitle", "dim-label")))
    return box


def status_page(icon: str, title: str, description: str | None = None,
                button: str | None = None, action: str | None = None,
                compact: bool = True) -> Adw.StatusPage:
    page = Adw.StatusPage(icon_name=icon, title=title, description=description or "",
                          vexpand=True)
    if compact:
        page.add_css_class("compact")
    if button and action:
        widget = Gtk.Button(label=button, halign=Gtk.Align.CENTER,
                            css_classes=["pill", "suggested-action"])
        widget.set_detailed_action_name(action)
        page.set_child(widget)
    return page


def spinner_page() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, valign=Gtk.Align.CENTER)
    spinner = Adw.Spinner(width_request=32, height_request=32, halign=Gtk.Align.CENTER)
    box.append(spinner)
    return box


def menu_item(label_text: str, action: str, target: str | None = None) -> Gio.MenuItem:
    item = Gio.MenuItem.new(label_text, None)
    if target is None:
        item.set_detailed_action(action)
    else:
        item.set_action_and_target_value(action, GLib.Variant("s", target))
    return item


def episode_menu(episode: Episode, *, in_queue: bool, downloading: bool,
                 show_podcast: bool = True) -> Gio.Menu:
    menu = Gio.Menu()
    queue = Gio.Menu()
    queue.append_item(menu_item(_("Tocar em seguida"), "app.play-next", episode.id))
    if in_queue:
        queue.append_item(menu_item(_("Remover de A Seguir"), "app.dequeue", episode.id))
    else:
        queue.append_item(menu_item(_("Adicionar a A Seguir"), "app.enqueue", episode.id))
    menu.append_section(None, queue)

    state = Gio.Menu()
    if episode.played:
        state.append_item(menu_item(_("Marcar como não ouvido"), "app.mark-unplayed", episode.id))
    else:
        state.append_item(menu_item(_("Marcar como ouvido"), "app.mark-played", episode.id))
    state.append_item(menu_item(_("Remover dos salvos") if episode.saved else _("Salvar episódio"),
                                "app.toggle-saved", episode.id))
    if downloading:
        state.append_item(menu_item(_("Cancelar download"), "app.cancel-download", episode.id))
    elif episode.is_downloaded:
        state.append_item(menu_item(_("Remover download"), "app.delete-download", episode.id))
    else:
        state.append_item(menu_item(_("Baixar episódio"), "app.download", episode.id))
    menu.append_section(None, state)

    extra = Gio.Menu()
    if show_podcast:
        extra.append_item(menu_item(_("Ver programa"), "win.open-podcast", episode.podcast_id))
    if episode.link or episode.audio_url:
        extra.append_item(menu_item(_("Copiar link"), "app.copy-link", episode.id))
    menu.append_section(None, extra)
    return menu


def debounce(source_id: int, delay_ms: int, callback: Callable[[], None]) -> int:
    """Restart a GLib timeout. Returns the new source id."""
    if source_id:
        GLib.source_remove(source_id)

    def fire() -> bool:
        callback()
        return GLib.SOURCE_REMOVE

    return GLib.timeout_add(delay_ms, fire)
