"""Search: iTunes Search online, local library offline; add feeds by URL."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ...i18n import _
from ...models import SearchResults
from ..components.episode_row import EpisodeRow
from ..components.podcast_card import PodcastCard
from ..components.shelf import Shelf
from ..helpers import (clear, debounce, page_box, scrolled_page, section_header,
                       spinner_page, status_page)
from .base import ViewPage


class SearchView(ViewPage):
    __gtype_name__ = "PodFlowSearchView"

    def __init__(self):
        super().__init__(_("Buscar"), "search")
        self.entry = Gtk.SearchEntry(placeholder_text=_("Programas e episódios"), hexpand=True)
        self.entry.connect("search-changed", lambda *_: self._on_changed())
        self.entry.connect("activate", lambda *_: self._search_now())
        clamp = Adw.Clamp(maximum_size=520, child=self.entry, hexpand=True)
        self.header.set_title_widget(clamp)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        intro = status_page("system-search-symbolic", _("Buscar no catálogo"),
                            _("Encontre programas e episódios do Brasil e do mundo, ou adicione "
                              "um podcast pelo endereço do feed RSS."),
                            _("Adicionar feed RSS"), "app.add-feed", compact=False)
        self.stack.add_named(intro, "intro")
        self.stack.add_named(spinner_page(), "loading")
        self.none_page = status_page("system-search-symbolic", _("Nada encontrado"),
                                     _("Tente outros termos."))
        self.stack.add_named(self.none_page, "none")
        self.results = page_box(28)
        self.stack.add_named(scrolled_page(self.results), "results")
        self.set_body(self.stack)
        self._debounce = 0
        self._generation = 0

    def on_shown(self) -> None:
        self.entry.grab_focus()

    def focus_entry(self) -> None:
        self.entry.grab_focus()

    def _on_changed(self) -> None:
        self._debounce = debounce(self._debounce, 350, self._search_now)

    def _search_now(self) -> None:
        if self._debounce:
            GLib.source_remove(self._debounce)
        self._debounce = 0
        term = self.entry.get_text().strip()
        self._generation += 1
        generation = self._generation
        if len(term) < 2:
            self.stack.set_visible_child_name("intro")
            return
        library = self.app.library
        if not library.online:
            self._show(library.search_local(term), offline=True)
            return
        self.stack.set_visible_child_name("loading")

        def done(results: SearchResults) -> None:
            if generation == self._generation:
                library.report_network_result(None)
                self._show(results)

        def failed(_error) -> None:
            if generation == self._generation:
                self._show(library.search_local(term), offline=True)

        library.search(term, done, failed)

    def _show(self, results: SearchResults, offline: bool = False) -> None:
        clear(self.results)
        if not results.podcasts and not results.episodes:
            self.none_page.set_description(
                _("Sem conexão: a busca offline procura só na sua biblioteca.") if offline
                else _("Tente outros termos."))
            self.stack.set_visible_child_name("none")
            return
        if offline:
            self.results.append(section_header(_("Resultados da sua biblioteca"),
                                               _("Você está offline")))
        if results.podcasts:
            shelf = Shelf(_("Programas"))
            shelf.set_items([PodcastCard(p.title, p.author, p.artwork_url, p.accent_color, size=150,
                                         on_activate=lambda pod=p: self.open_podcast(pod),
                                         on_play=lambda pod=p: self.play_latest(pod))
                             for p in results.podcasts])
            self.results.append(shelf)
        if results.episodes:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.append(section_header(_("Episódios")))
            listbox = Gtk.ListBox(css_classes=["boxed-list", "episode-list"],
                                  selection_mode=Gtk.SelectionMode.NONE)
            listbox.connect("row-activated", lambda _lb, row: row.toggle_expanded())
            library = self.app.library
            for episode in results.episodes:
                stored = library.ensure_episode(episode)
                listbox.append(EpisodeRow(stored, show_cover=True, show_podcast=True))
            box.append(listbox)
            self.results.append(box)
        self.stack.set_visible_child_name("results")
