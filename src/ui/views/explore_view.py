"""Explorar (/br/new): hero carousel, weekly highlights, trending and genre shelves."""

from __future__ import annotations

import time

from gi.repository import Adw, GLib, Gtk

from ...api import genres
from ...api.apple_service import (CHART_MAX_AGE, GENRE_MAX_AGE, ChartEpisode, ChartPodcast,
                                  ChartResult)
from ...i18n import _
from ...utils import tasks
from ...utils.text import summarize
from ..components.hero_carousel import HeroBanner, HeroCarousel
from ..components.podcast_card import EpisodeCard, PodcastCard
from ..components.shelf import Shelf
from ..helpers import clear, page_box, scrolled_page, section_header
from .base import ViewPage

HERO_COUNT = 6
SHELF_COUNT = 18


class ExploreView(ViewPage):
    __gtype_name__ = "PodFlowExploreView"

    def __init__(self):
        super().__init__(_("Explorar"), "explore")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Atualizar"))
        refresh.connect("clicked", lambda *_: self.refresh(force=True))
        self.header.pack_end(refresh)

        self.content = page_box(36)
        self.hero = HeroCarousel()
        self.content.append(self.hero)

        self.highlights_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.highlights_section.append(section_header(
            _("Destaques da Semana"), _("Os episódios mais ouvidos no Brasil agora")))
        self.highlights = Adw.WrapBox(child_spacing=18, line_spacing=18)
        self.highlights_section.append(self.highlights)
        self.content.append(self.highlights_section)

        self.trending = Shelf(_("Em Alta"), _("Os programas mais populares do Brasil"),
                              on_see_all=self._open_charts)
        self.content.append(self.trending)

        self.genre_shelves: dict[int, Shelf] = {}
        for genre_id in genres.FEATURED_GENRES:
            shelf = Shelf(genres.genre_name(genre_id))
            self.genre_shelves[genre_id] = shelf
            self.content.append(shelf)

        self.set_body(scrolled_page(self.content))
        self._loaded_at = 0.0
        self.app.library.connect("online-changed", lambda _l, online: online and self.refresh())

    def _open_charts(self) -> None:
        self.activate_action("win.show-section", GLib.Variant("s", "charts"))

    def set_compact(self, compact: bool) -> None:
        changed = compact != self.compact
        super().set_compact(compact)
        self.hero.set_compact(compact)
        if changed:
            self._render_highlights(self.app.apple.peek(self.app.apple.key_top_episodes()))

    def on_shown(self) -> None:
        if time.time() - self._loaded_at > CHART_MAX_AGE:
            self.refresh()

    def rebuild(self) -> None:
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        self._loaded_at = time.time()
        apple = self.app.apple
        self._load(apple.key_genre(None), lambda: apple.genre_chart(None, force), self._render_hero,
                   GENRE_MAX_AGE, force)
        self._load(apple.key_top_episodes(), lambda: apple.top_episodes(force),
                   self._render_highlights, CHART_MAX_AGE, force)
        self._load(apple.key_top_podcasts(), lambda: apple.top_podcasts(force),
                   self._render_trending, CHART_MAX_AGE, force)
        for genre_id in genres.FEATURED_GENRES:
            self._load(apple.key_genre(genre_id),
                       lambda gid=genre_id: apple.genre_chart(gid, force),
                       lambda result, gid=genre_id: self._render_genre(gid, result),
                       GENRE_MAX_AGE, force)

    def _load(self, key: str, fetch, render, max_age: float, force: bool) -> None:
        apple = self.app.apple
        cached = apple.peek(key)
        if cached is not None:
            render(cached)
        if cached is not None and not force and apple.is_fresh(key, max_age):
            return
        if not self.app.library.online and cached is None:
            render(None)
            return

        def done(result: ChartResult) -> None:
            self.app.library.report_network_result(result.error)
            render(result)

        def failed(_error) -> None:
            if cached is None:
                render(None)

        tasks.run_async(fetch, on_done=done, on_error=failed)

    # -- renderers -------------------------------------------------------------------

    def _render_hero(self, result: ChartResult | None) -> None:
        banners = []
        if result is not None and result.items:
            for item in result.items[:HERO_COUNT]:
                eyebrow = _("Nº {rank} no Brasil").format(rank=item.rank)
                if item.genre:
                    eyebrow = f"{eyebrow} · {item.genre}"
                banners.append(HeroBanner(
                    eyebrow, item.title, summarize(item.summary, 220) or item.author,
                    item.artwork_url, on_open=lambda i=item: self.open_podcast(i),
                    on_play=lambda i=item: self.play_latest(i)))
        else:
            for podcast in self.app.db.list_seed_podcasts()[:HERO_COUNT]:
                banners.append(HeroBanner(
                    _("Destaque brasileiro") + (f" · {podcast.genre}" if podcast.genre else ""),
                    podcast.title, podcast.description, podcast.artwork_url,
                    podcast.accent_color, on_open=lambda p=podcast: self.open_podcast(p.id),
                    on_play=lambda p=podcast: self.play_latest(p)))
        self.hero.set_banners(banners)
        self.hero.set_compact(self.compact)

    def _render_highlights(self, result: ChartResult | None) -> None:
        clear(self.highlights)
        items: list[ChartEpisode] = result.items[:6] if result is not None else []
        self.highlights_section.set_visible(bool(items))
        for item in items:
            eyebrow = item.podcast_title or item.author
            card = EpisodeCard(item.title, eyebrow, item.artwork_url,
                               meta=_("Nº {rank} entre os episódios").format(rank=item.rank),
                               width=290 if self.compact else 340,
                               cover_size=72 if self.compact else 80,
                               on_activate=lambda i=item: self.play_chart_episode(i))
            self.highlights.append(card)

    def _podcast_cards(self, items: list[ChartPodcast]) -> list[Gtk.Widget]:
        return [PodcastCard(item.title, item.author, item.artwork_url, size=160,
                            on_activate=lambda i=item: self.open_podcast(i),
                            on_play=lambda i=item: self.play_latest(i))
                for item in items[:SHELF_COUNT]]

    def _render_trending(self, result: ChartResult | None) -> None:
        if result is not None and result.items:
            self.trending.set_visible(True)
            self.trending.set_items(self._podcast_cards(result.items))
        else:
            self.trending.set_visible(False)

    def _render_genre(self, genre_id: int, result: ChartResult | None) -> None:
        shelf = self.genre_shelves[genre_id]
        if result is not None and result.items:
            shelf.set_visible(True)
            shelf.set_items(self._podcast_cards(result.items))
            return
        seeds = [p for p in self.app.db.list_seed_podcasts()
                 if genres.top_level(p.genre_id) == genre_id]
        shelf.set_visible(bool(seeds))
        shelf.set_items([PodcastCard(p.title, p.author, p.artwork_url, p.accent_color, size=160,
                                     on_activate=lambda pod=p: self.open_podcast(pod.id),
                                     on_play=lambda pod=p: self.play_latest(pod))
                         for p in seeds])
