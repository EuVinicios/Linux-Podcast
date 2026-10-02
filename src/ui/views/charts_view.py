"""Rankings (/br/charts): Top Podcasts and Top Episódios with a category filter."""

from __future__ import annotations

import time
from datetime import datetime

from gi.repository import Adw, Gtk

from ...api import genres
from ...api.apple_service import CHART_MAX_AGE, GENRE_MAX_AGE, ChartResult
from ...i18n import _
from ...utils import tasks
from ..components.chart_row import ChartRow
from ..helpers import clear, label, spinner_page, status_page
from .base import ViewPage

ALL = 0


class ChartsView(ViewPage):
    __gtype_name__ = "PodFlowChartsView"

    def __init__(self):
        super().__init__(_("Rankings"), "charts")
        self.stack = Adw.ViewStack(vexpand=True)
        self.switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        self.header.set_title_widget(self.switcher)

        self._genre_ids = [ALL] + list(genres.CHART_GENRES)
        names = [_("Todas as categorias")] + [genres.genre_name(g) for g in genres.CHART_GENRES]
        self.dropdown = Gtk.DropDown.new_from_strings(names)
        self.dropdown.set_tooltip_text(_("Categoria"))
        self.dropdown.connect("notify::selected", lambda *_: self.refresh())
        self.header.pack_end(self.dropdown)

        self.podcasts_page = self._make_page()
        self.episodes_page = self._make_page()
        page = self.stack.add_titled_with_icon(self.podcasts_page["root"], "podcasts",
                                               _("Top Podcasts"), "podflow-chart-symbolic")
        page.set_use_underline(False)
        self.stack.add_titled_with_icon(self.episodes_page["root"], "episodes",
                                        _("Top Episódios"), "media-playback-start-symbolic")
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        self.toolbar.add_bottom_bar(self.switcher_bar)
        self.set_body(self.stack)
        self._loaded_at = 0.0

    def _make_page(self) -> dict:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["page-content"])
        caption = label("", ("dim-label", "caption"))
        box.append(caption)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(body)
        clamp = Adw.Clamp(maximum_size=980, tightening_threshold=760, child=box)
        root = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp)
        return {"root": root, "caption": caption, "body": body}

    def set_compact(self, compact: bool) -> None:
        super().set_compact(compact)
        self.switcher_bar.set_reveal(compact)
        if compact:
            self.header.set_title_widget(None)
        else:
            self.header.set_title_widget(self.switcher)

    @property
    def genre_id(self) -> int:
        index = self.dropdown.get_selected()
        return self._genre_ids[index] if 0 <= index < len(self._genre_ids) else ALL

    def on_shown(self) -> None:
        if time.time() - self._loaded_at > CHART_MAX_AGE:
            self.refresh()

    def rebuild(self) -> None:
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        self._loaded_at = time.time()
        genre_id = self.genre_id
        apple = self.app.apple
        if genre_id == ALL:
            self._load(self.podcasts_page, apple.key_top_podcasts(),
                       lambda: apple.top_podcasts(force), self._render_podcasts, CHART_MAX_AGE,
                       force)
        else:
            self._load(self.podcasts_page, apple.key_genre(genre_id),
                       lambda: apple.genre_chart(genre_id, force), self._render_podcasts,
                       GENRE_MAX_AGE, force)
        self._load(self.episodes_page, apple.key_top_episodes(), lambda: apple.top_episodes(force),
                   self._render_episodes, CHART_MAX_AGE, force)

    def _load(self, page: dict, key: str, fetch, render, max_age: float, force: bool) -> None:
        apple = self.app.apple
        wanted_genre = self.genre_id
        cached = apple.peek(key)
        if cached is not None:
            render(page, cached)
            if not force and apple.is_fresh(key, max_age):
                return
        else:
            clear(page["body"])
            page["body"].append(spinner_page())

        def done(result: ChartResult) -> None:
            if wanted_genre == self.genre_id:
                self.app.library.report_network_result(result.error)
                render(page, result)

        def failed(_error) -> None:
            if cached is None and wanted_genre == self.genre_id:
                clear(page["body"])
                page["body"].append(status_page(
                    "network-offline-symbolic", _("Rankings indisponíveis"),
                    _("Verifique sua conexão com a internet e tente novamente.")))
                page["caption"].set_text("")

        tasks.run_async(fetch, on_done=done, on_error=failed)

    @staticmethod
    def _caption(result: ChartResult, category: str) -> str:
        when = datetime.fromtimestamp(result.fetched_at).strftime("%d/%m %H:%M")
        text = _("Brasil · {category} · atualizado em {when}").format(category=category, when=when)
        if result.error is not None:
            text += " · " + _("offline")
        return text

    def _listbox(self) -> Gtk.ListBox:
        listbox = Gtk.ListBox(css_classes=["chart-list"], selection_mode=Gtk.SelectionMode.NONE)
        listbox.connect("row-activated", lambda _lb, row: row.on_activate and row.on_activate())
        return listbox

    def _render_podcasts(self, page: dict, result: ChartResult) -> None:
        clear(page["body"])
        category = genres.genre_name(self.genre_id) if self.genre_id else _("Todas as categorias")
        page["caption"].set_text(self._caption(result, category))
        listbox = self._listbox()
        for item in result.items[:50]:
            listbox.append(ChartRow(
                item.rank, item.title, item.author, item.artwork_url, item.genre,
                on_activate=lambda i=item: self.open_podcast(i),
                on_play=lambda i=item: self.play_latest(i),
                secondary_icon="list-add-symbolic", secondary_tooltip=_("Seguir"),
                on_secondary=lambda i=item: self._follow(i)))
        page["body"].append(listbox)

    def _render_episodes(self, page: dict, result: ChartResult) -> None:
        clear(page["body"])
        genre_id = self.genre_id
        category = genres.genre_name(genre_id) if genre_id else _("Todas as categorias")
        page["caption"].set_text(self._caption(result, category))
        items = [i for i in result.items if not genre_id or i.genre_id == genre_id]
        if not items:
            page["body"].append(status_page(
                "media-playback-start-symbolic", _("Nenhum episódio nesta categoria"),
                _("Nenhum episódio de {category} está entre os 50 mais ouvidos agora.").format(
                    category=category)))
            return
        listbox = self._listbox()
        for rank, item in enumerate(items, start=1):
            subtitle = item.podcast_title or item.author
            listbox.append(ChartRow(
                rank, item.title, subtitle, item.artwork_url, item.genre, explicit=item.explicit,
                on_activate=lambda i=item: self.open_chart_episode_podcast(i),
                on_play=lambda i=item: self.play_chart_episode(i),
                secondary_icon="list-add-symbolic", secondary_tooltip=_("Adicionar a A Seguir"),
                on_secondary=lambda i=item: self.play_chart_episode(i, enqueue=True)))
        page["body"].append(listbox)

    def _follow(self, item) -> None:
        podcast = self.app.library.ensure_chart_podcast(item)
        if podcast.subscribed:
            self.toast(_("Você já segue {title}").format(title=podcast.title))
            return
        self.app.library.set_subscribed(podcast.id, True)
        self.toast(_("Seguindo {title}").format(title=podcast.title))
