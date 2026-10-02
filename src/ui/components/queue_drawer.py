"""Retractable "A Seguir" (Up Next) panel with drag-and-drop reordering."""

from __future__ import annotations

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from ...i18n import _
from ...models import Episode
from ...utils.time_format import format_duration, format_time_left
from ..helpers import clear, get_app, label
from .cover import CoverImage


class QueueRow(Gtk.ListBoxRow):
    __gtype_name__ = "PodFlowQueueRow"

    def __init__(self, episode: Episode, index: int):
        super().__init__(css_classes=["queue-row"], activatable=True)
        self.episode = episode
        self.index = index
        box = Gtk.Box(spacing=10)
        handle = Gtk.Image(icon_name="list-drag-handle-symbolic", css_classes=["dim-label"],
                           tooltip_text=_("Arraste para reordenar"))
        box.append(handle)
        cover = CoverImage(44)
        cover.set_valign(Gtk.Align.CENTER)
        cover.set_source(episode.cover_url, episode.podcast_title, episode.podcast_accent)
        box.append(cover)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                       valign=Gtk.Align.CENTER)
        text.append(label(episode.title, ("queue-title",), wrap=True, lines=2, max_width_chars=8))
        meta = episode.podcast_title
        timing = (format_time_left(episode.position, episode.duration) if episode.in_progress
                  else format_duration(episode.duration))
        if timing:
            meta = f"{meta} · {timing}" if meta else timing
        text.append(label(meta, ("caption", "dim-label"), ellipsize=True, max_width_chars=8))
        box.append(text)
        remove = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"],
                            valign=Gtk.Align.CENTER, tooltip_text=_("Remover de A Seguir"),
                            action_name="app.dequeue", action_target=GLib.Variant("s", episode.id))
        box.append(remove)
        self.set_child(box)

        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_prepare)
        source.connect("drag-begin", self._on_drag_begin)
        self.add_controller(source)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_prepare(self, _source, _x, _y):
        return Gdk.ContentProvider.new_for_value(self.episode.id)

    def _on_drag_begin(self, source, _drag) -> None:
        source.set_icon(Gtk.WidgetPaintable.new(self), 20, 20)

    def _on_drop(self, _target, value: str, _x, _y) -> bool:
        if value and value != self.episode.id:
            get_app().db.queue_move(value, self.index)
            return True
        return False


class QueueDrawer(Adw.Bin):
    __gtype_name__ = "PodFlowQueueDrawer"

    def __init__(self, close_action: str = "win.toggle-queue"):
        super().__init__(css_classes=["queue-drawer"])
        self.toolbar = Adw.ToolbarView()
        self.set_child(self.toolbar)
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.set_title_widget(Adw.WindowTitle(title=_("A Seguir")))
        close = Gtk.Button(icon_name="sidebar-show-right-symbolic", action_name=close_action,
                           tooltip_text=_("Fechar"))
        header.pack_start(close)
        self.clear_button = Gtk.Button(icon_name="edit-clear-all-symbolic",
                                       tooltip_text=_("Limpar A Seguir"))
        self.clear_button.connect("clicked", lambda *_: get_app().db.queue_clear())
        header.pack_end(self.clear_button)
        self.toolbar.add_top_bar(header)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                           css_classes=["queue-content"])
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.box,
                                      vexpand=True)
        self.toolbar.set_content(scroller)

        app = get_app()
        app.db.connect("queue-changed", lambda *_: self.rebuild())
        app.playback.connect("episode-changed", lambda *_: self.rebuild())
        self.rebuild()

    def rebuild(self) -> None:
        app = get_app()
        clear(self.box)
        current = app.playback.current
        if current is not None:
            self.box.append(label(_("TOCANDO AGORA"), ("queue-section", "dim-label")))
            now = Gtk.Box(spacing=12, css_classes=["queue-now"])
            cover = CoverImage(56)
            cover.set_source(current.cover_url, current.podcast_title, current.podcast_accent)
            now.append(cover)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True,
                           valign=Gtk.Align.CENTER)
            text.append(label(current.title, ("queue-title",), wrap=True, lines=2,
                              max_width_chars=8))
            text.append(label(current.podcast_title, ("caption", "dim-label"), ellipsize=True,
                              max_width_chars=8))
            now.append(text)
            self.box.append(now)

        episodes = [e for e in app.db.queue_list() if current is None or e.id != current.id]
        self.clear_button.set_sensitive(bool(episodes))
        self.box.append(label(_("PRÓXIMOS"), ("queue-section", "dim-label")))
        if not episodes:
            empty = Adw.StatusPage(icon_name="view-list-bullet-symbolic",
                                   title=_("Nada em A Seguir"),
                                   description=_("Use o menu de um episódio para adicioná-lo à fila."),
                                   css_classes=["compact"], vexpand=True)
            self.box.append(empty)
            return
        listbox = Gtk.ListBox(css_classes=["boxed-list", "queue-list"],
                              selection_mode=Gtk.SelectionMode.NONE)
        listbox.connect("row-activated", lambda _lb, row: row.activate_action(
            "app.play-episode", GLib.Variant("s", row.episode.id)))
        for index, episode in enumerate(episodes):
            listbox.append(QueueRow(episode, index))
        self.box.append(listbox)
