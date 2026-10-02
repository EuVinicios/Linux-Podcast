"""Biblioteca: followed shows, saved episodes, downloads and history."""

from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from ...i18n import _
from ...models import Episode
from ...utils.time_format import format_size
from ..components.episode_row import EpisodeRow
from ..components.podcast_card import PodcastCard
from ..helpers import clear, page_box, scrolled_page, status_page
from .base import ViewPage


class FollowingView(ViewPage):
    __gtype_name__ = "PodFlowFollowingView"

    def __init__(self):
        super().__init__(_("Programas Seguidos"), "following")
        self._order = self.app.db.get_setting("following_order", "recent")
        sort = Gtk.MenuButton(icon_name="view-sort-descending-symbolic",
                              tooltip_text=_("Ordenar"))
        menu = Gio.Menu()
        menu.append(_("Seguidos recentemente"), "following.order('recent')")
        menu.append(_("Ordem alfabética"), "following.order('title')")
        sort.set_menu_model(menu)
        group = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new_stateful("order", GLib.VariantType.new("s"),
                                               GLib.Variant("s", self._order))
        action.connect("activate", self._on_order)
        group.add_action(action)
        self.insert_action_group("following", group)
        self.header.pack_end(sort)
        self.header.pack_end(Gtk.Button(icon_name="list-add-symbolic", action_name="app.add-feed",
                                        tooltip_text=_("Adicionar feed RSS")))

        self.content = page_box(18)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=False,
                                min_children_per_line=2, max_children_per_line=12,
                                column_spacing=18, row_spacing=24, valign=Gtk.Align.START,
                                css_classes=["podcast-grid"])
        self.content.append(self.flow)
        self.empty = status_page("view-grid-symbolic", _("Nenhum programa seguido"),
                                 _("Siga programas para recebê-los aqui."),
                                 _("Explorar programas"), "win.show-section::explore")
        self.content.append(self.empty)
        self.set_body(scrolled_page(self.content))
        db = self.app.db
        db.connect("subscriptions-changed", lambda *_: self.schedule_rebuild())
        db.connect("episodes-changed", lambda *_: self.schedule_rebuild(800))

    def _on_order(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self._order = value.get_string()
        self.app.db.set_setting("following_order", self._order)
        self.rebuild()

    def rebuild(self) -> None:
        clear(self.flow)
        podcasts = self.app.db.list_subscriptions(self._order)
        self.empty.set_visible(not podcasts)
        self.flow.set_visible(bool(podcasts))
        size = 140 if self.compact else 170
        for podcast in podcasts:
            card = PodcastCard(podcast.title, podcast.author, podcast.artwork_url,
                               podcast.accent_color, size=size, badge=podcast.new_count,
                               on_activate=lambda p=podcast: self.open_podcast(p.id),
                               on_play=lambda p=podcast: self.play_latest(p))
            self.flow.append(card)

    def set_compact(self, compact: bool) -> None:
        changed = compact != self.compact
        super().set_compact(compact)
        if changed:
            self.schedule_rebuild(50)


class EpisodeListView(ViewPage):
    """Generic list of episodes (saved, downloads, history)."""

    __gtype_name__ = "PodFlowEpisodeListView"

    def __init__(self, title: str, tag: str, fetch: Callable[[], list[Episode]],
                 empty_icon: str, empty_title: str, empty_description: str,
                 signals: tuple[str, ...]):
        super().__init__(title, tag)
        self._fetch = fetch
        self.content = page_box(16)
        self.summary = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"])
        self.content.append(self.summary)
        self.listbox = Gtk.ListBox(css_classes=["boxed-list", "episode-list"],
                                   selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        self.listbox.connect("row-activated", lambda _lb, row: row.toggle_expanded())
        self.content.append(self.listbox)
        self.empty = status_page(empty_icon, empty_title, empty_description)
        self.content.append(self.empty)
        self.set_body(scrolled_page(self.content, maximum=1100))
        for signal in signals:
            self.app.db.connect(signal, lambda *_: self.schedule_rebuild())

    def describe(self, episodes: list[Episode]) -> str:
        count = len(episodes)
        return _("{count} episódios").format(count=count) if count != 1 else _("1 episódio")

    def rebuild(self) -> None:
        clear(self.listbox)
        episodes = self._fetch()
        self.empty.set_visible(not episodes)
        self.listbox.set_visible(bool(episodes))
        self.summary.set_visible(bool(episodes))
        self.summary.set_text(self.describe(episodes))
        for episode in episodes:
            self.listbox.append(EpisodeRow(episode, show_cover=True, show_podcast=True))


class SavedView(EpisodeListView):
    __gtype_name__ = "PodFlowSavedView"

    def __init__(self, app):
        super().__init__(_("Episódios Salvos"), "saved", app.db.list_saved, "starred-symbolic",
                         _("Nenhum episódio salvo"),
                         _("Use “Salvar episódio” no menu de um episódio para guardá-lo aqui."),
                         ("episode-changed",))


class DownloadsView(EpisodeListView):
    __gtype_name__ = "PodFlowDownloadsView"

    def __init__(self, app):
        super().__init__(_("Downloads"), "downloads", app.db.list_downloaded,
                         "folder-download-symbolic", _("Nenhum download"),
                         _("Episódios baixados ficam disponíveis mesmo sem internet."),
                         ("downloads-changed",))
        self.delete_all = Gtk.Button(icon_name="user-trash-symbolic",
                                     tooltip_text=_("Apagar todos os downloads"))
        self.delete_all.connect("clicked", lambda *_: self._confirm_delete_all())
        self.header.pack_end(self.delete_all)

    def describe(self, episodes: list[Episode]) -> str:
        total = sum(e.download_size for e in episodes)
        text = super().describe(episodes)
        return f"{text} · {format_size(total)}" if total else text

    def rebuild(self) -> None:
        super().rebuild()
        self.delete_all.set_sensitive(self.listbox.get_first_child() is not None)

    def _confirm_delete_all(self) -> None:
        dialog = Adw.AlertDialog(heading=_("Apagar todos os downloads?"),
                                 body=_("Os arquivos serão removidos do computador. "
                                        "Os episódios continuam disponíveis por streaming."))
        dialog.add_response("cancel", _("Cancelar"))
        dialog.add_response("delete", _("Apagar"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "delete"
                       and self.app.downloads.delete_all())
        dialog.present(self.get_root())


class HistoryView(EpisodeListView):
    __gtype_name__ = "PodFlowHistoryView"

    def __init__(self, app):
        super().__init__(_("Histórico"), "history", app.db.list_history,
                         "document-open-recent-symbolic", _("Nada ouvido ainda"),
                         _("Os episódios que você ouvir aparecem aqui."),
                         ("history-changed",))
        self.clear_button = Gtk.Button(icon_name="edit-clear-all-symbolic",
                                       tooltip_text=_("Limpar histórico"))
        self.clear_button.connect("clicked", lambda *_: self._confirm_clear())
        self.header.pack_end(self.clear_button)

    def rebuild(self) -> None:
        super().rebuild()
        self.clear_button.set_sensitive(self.listbox.get_first_child() is not None)

    def _confirm_clear(self) -> None:
        dialog = Adw.AlertDialog(heading=_("Limpar o histórico?"),
                                 body=_("O progresso dos episódios é mantido."))
        dialog.add_response("cancel", _("Cancelar"))
        dialog.add_response("clear", _("Limpar"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "clear"
                       and self.app.db.clear_history())
        dialog.present(self.get_root())
