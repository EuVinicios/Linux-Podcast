"""Ouvir Agora: continue listening, Up Next and new episodes from followed shows."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ...i18n import _
from ..components.episode_row import EpisodeRow
from ..components.podcast_card import EpisodeCard, PodcastCard
from ..components.shelf import Shelf
from ..helpers import clear, label, page_box, scrolled_page, section_header
from .base import ViewPage

NEW_EPISODES_LIMIT = 30


class ListenNowView(ViewPage):
    __gtype_name__ = "PodFlowListenNowView"

    def __init__(self):
        super().__init__(_("Ouvir Agora"), "listen-now")
        self.refresh_stack = Gtk.Stack()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", action_name="app.refresh",
                             tooltip_text=_("Atualizar programas seguidos"))
        self.refresh_stack.add_named(refresh, "button")
        self.refresh_stack.add_named(Adw.Spinner(), "spinner")
        self.header.pack_end(self.refresh_stack)

        self.content = page_box()
        self.set_body(scrolled_page(self.content))

        app = self.app
        for signal in ("queue-changed", "history-changed", "subscriptions-changed",
                       "episodes-changed"):
            app.db.connect(signal, lambda *_: self.schedule_rebuild(400))
        app.library.connect("subscriptions-refreshing", self._on_refreshing)
        self._on_refreshing(app.library, app.library.refreshing_subscriptions)

    def _on_refreshing(self, _library, active: bool) -> None:
        self.refresh_stack.set_visible_child_name("spinner" if active else "button")
        if not active:
            self.schedule_rebuild(100)

    def rebuild(self) -> None:
        db = self.app.db
        clear(self.content)

        in_progress = db.list_in_progress(12)
        if in_progress:
            shelf = Shelf(_("Continuar Ouvindo"))
            shelf.set_items([EpisodeCard.for_episode(
                episode, on_activate=lambda ep=episode: self.play_episode(ep), show_progress=True,
                width=300 if self.compact else 360) for episode in in_progress])
            self.content.append(shelf)

        queue = db.queue_list()
        current = self.app.playback.current
        queue = [e for e in queue if current is None or e.id != current.id]
        if queue:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            head = Gtk.Box()
            head.append(section_header(_("A Seguir"),
                                       _("{count} episódios na fila").format(count=len(queue))
                                       if len(queue) != 1 else _("1 episódio na fila")))
            head.get_first_child().set_hexpand(True)
            see_all = Gtk.Button(label=_("Ver tudo"), css_classes=["flat", "see-all"],
                                 action_name="win.toggle-queue", valign=Gtk.Align.END)
            head.append(see_all)
            section.append(head)
            listbox = self._episode_list()
            for episode in queue[:4]:
                listbox.append(EpisodeRow(episode, show_cover=True, show_podcast=True,
                                          compact=True))
            section.append(listbox)
            self.content.append(section)

        subscriptions = db.list_subscriptions()
        new_episodes = db.list_new_episodes(days=21, limit=NEW_EPISODES_LIMIT)
        if new_episodes:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            section.append(section_header(_("Novos Episódios"),
                                          _("Lançamentos recentes dos programas que você segue")))
            listbox = self._episode_list()
            for episode in new_episodes:
                listbox.append(EpisodeRow(episode, show_cover=True, show_podcast=True))
            section.append(listbox)
            self.content.append(section)

        if subscriptions:
            shelf = Shelf(_("Seus Programas"), on_see_all=lambda: self.activate_action(
                "win.show-section", GLib.Variant("s", "following")))
            shelf.set_items([PodcastCard(
                podcast.title, podcast.author, podcast.artwork_url, podcast.accent_color,
                size=150, badge=podcast.new_count,
                on_activate=lambda p=podcast: self.open_podcast(p.id),
                on_play=lambda p=podcast: self.play_latest(p)) for podcast in subscriptions[:20]])
            self.content.append(shelf)
            if not new_episodes and not in_progress:
                hint = label(_("Os episódios mais recentes dos seus programas aparecem aqui "
                               "assim que a biblioteca for atualizada."),
                             ("dim-label",), wrap=True, xalign=0.5)
                self.content.prepend(hint)
        else:
            page = Adw.StatusPage(icon_name="audio-headphones-symbolic",
                                  title=_("Comece a seguir programas"),
                                  description=_("Os novos episódios dos programas que você segue "
                                                "aparecem aqui."),
                                  vexpand=True)
            button = Gtk.Button(label=_("Explorar programas"), halign=Gtk.Align.CENTER,
                                css_classes=["pill", "suggested-action"],
                                action_name="win.show-section",
                                action_target=GLib.Variant("s", "explore"))
            page.set_child(button)
            self.content.append(page)

    def set_compact(self, compact: bool) -> None:
        changed = compact != self.compact
        super().set_compact(compact)
        if changed:
            self.schedule_rebuild(50)

    def _episode_list(self) -> Gtk.ListBox:
        listbox = Gtk.ListBox(css_classes=["boxed-list", "episode-list"],
                              selection_mode=Gtk.SelectionMode.NONE)
        listbox.connect("row-activated", lambda _lb, row: row.toggle_expanded())
        return listbox
