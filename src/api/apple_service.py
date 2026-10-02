"""Apple Podcasts charts (Brazil) with an offline cache.

Sources (checked 2026-10-01):
* Top podcasts / episodes: Apple Marketing Tools RSS JSON. The documented host
  ``rss.applemarketingtools.com`` now redirects to ``rss.marketingtools.apple.com``.
* Per-genre charts: the legacy iTunes RSS (the Search API ignores ``genreId``).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .. import config
from . import genres, http
from .itunes_service import ItunesService

log = logging.getLogger(__name__)

V2_URL = "https://rss.marketingtools.apple.com/api/v2/{country}/podcasts/top/{limit}/{kind}.json"
LEGACY_URL = "https://itunes.apple.com/{country}/rss/toppodcasts/limit={limit}{genre}/json"

CHART_MAX_AGE = 6 * 3600
GENRE_MAX_AGE = 24 * 3600


@dataclass
class ChartPodcast:
    rank: int
    itunes_id: int
    title: str
    author: str = ""
    artwork_url: str = ""
    genre: str = ""
    genre_id: int | None = None
    apple_url: str = ""
    summary: str = ""

    @property
    def podcast_id(self) -> str:
        return f"itunes:{self.itunes_id}"


@dataclass
class ChartEpisode:
    rank: int
    itunes_track_id: int
    title: str
    podcast_itunes_id: int | None = None
    author: str = ""
    artwork_url: str = ""
    genre: str = ""
    genre_id: int | None = None
    apple_url: str = ""
    explicit: bool = False
    podcast_title: str = ""
    feed_url: str = ""

    @property
    def podcast_id(self) -> str | None:
        return f"itunes:{self.podcast_itunes_id}" if self.podcast_itunes_id else None


@dataclass
class ChartResult:
    items: list[Any] = field(default_factory=list)
    fetched_at: float = 0.0
    from_cache: bool = False
    error: Exception | None = None


def parse_apple_url(url: str | None) -> tuple[int | None, int | None]:
    """podcasts.apple.com URL → (podcast id, episode id)."""
    if not url:
        return None, None
    podcast = re.search(r"/id(\d+)", url)
    episode = re.search(r"[?&]i=(\d+)", url)
    return (int(podcast.group(1)) if podcast else None,
            int(episode.group(1)) if episode else None)


def parse_v2_podcasts(data: dict) -> list[ChartPodcast]:
    items = []
    for rank, result in enumerate(data.get("feed", {}).get("results", []), start=1):
        try:
            itunes_id = int(result["id"])
        except (KeyError, ValueError):
            continue
        genre = (result.get("genres") or [{}])[0]
        genre_id = int(genre["genreId"]) if str(genre.get("genreId", "")).isdigit() else None
        items.append(ChartPodcast(
            rank=rank,
            itunes_id=itunes_id,
            title=result.get("name") or "",
            author=result.get("artistName") or "",
            artwork_url=result.get("artworkUrl100") or "",
            genre=genre.get("name") or genres.genre_name(genre_id),
            genre_id=genre_id,
            apple_url=result.get("url") or "",
        ))
    return items


def parse_v2_episodes(data: dict) -> list[ChartEpisode]:
    items = []
    for rank, result in enumerate(data.get("feed", {}).get("results", []), start=1):
        try:
            track_id = int(result["id"])
        except (KeyError, ValueError):
            continue
        podcast_id, _episode_id = parse_apple_url(result.get("url"))
        genre_name = ((result.get("genres") or [{}])[0]).get("name") or ""
        items.append(ChartEpisode(
            rank=rank,
            itunes_track_id=track_id,
            title=result.get("name") or "",
            podcast_itunes_id=podcast_id,
            author=result.get("artistName") or "",
            artwork_url=result.get("artworkUrl100") or "",
            genre=genre_name,
            genre_id=genres.top_level_for_name(genre_name),
            apple_url=result.get("url") or "",
            explicit=(result.get("contentAdvisoryRating") or "").lower().startswith("expl"),
        ))
    return items


def parse_legacy_chart(data: dict) -> list[ChartPodcast]:
    entries = data.get("feed", {}).get("entry", [])
    if isinstance(entries, dict):  # a single entry is not wrapped in a list
        entries = [entries]
    items = []
    for rank, entry in enumerate(entries, start=1):
        try:
            itunes_id = int(entry["id"]["attributes"]["im:id"])
        except (KeyError, TypeError, ValueError):
            continue
        images = entry.get("im:image") or []
        artwork = images[-1]["label"] if images else ""
        category = (entry.get("category") or {}).get("attributes", {})
        genre_id = int(category["im:id"]) if str(category.get("im:id", "")).isdigit() else None
        items.append(ChartPodcast(
            rank=rank,
            itunes_id=itunes_id,
            title=(entry.get("im:name") or {}).get("label", ""),
            author=(entry.get("im:artist") or {}).get("label", ""),
            artwork_url=artwork,
            genre=category.get("label") or genres.genre_name(genre_id),
            genre_id=genre_id,
            apple_url=((entry.get("link") or {}).get("attributes") or {}).get("href", "").split("?")[0],
            summary=(entry.get("summary") or {}).get("label", ""),
        ))
    return items


class AppleService:
    """Chart fetching with a stale-while-revalidate cache stored in SQLite."""

    def __init__(self, db=None, itunes: ItunesService | None = None, country: str = config.COUNTRY):
        self.db = db
        self.itunes = itunes or ItunesService(country)
        self.country = country

    # -- network -----------------------------------------------------------------

    def fetch_top_podcasts(self, limit: int = 50) -> list[ChartPodcast]:
        url = V2_URL.format(country=self.country, limit=limit, kind="podcasts")
        return parse_v2_podcasts(http.get_json(url, timeout=20))

    def fetch_top_episodes(self, limit: int = 50) -> list[ChartEpisode]:
        url = V2_URL.format(country=self.country, limit=limit, kind="podcast-episodes")
        items = parse_v2_episodes(http.get_json(url, timeout=25, retries=3))
        self._enrich_episodes(items)
        return items

    def fetch_genre_chart(self, genre_id: int | None, limit: int = 50) -> list[ChartPodcast]:
        genre = f"/genre={int(genre_id)}" if genre_id else ""
        url = LEGACY_URL.format(country=self.country, limit=limit, genre=genre)
        return parse_legacy_chart(http.get_json(url, timeout=20))

    def _enrich_episodes(self, items: list[ChartEpisode]) -> None:
        """The episode chart has no show names; one batch lookup fills them in."""
        ids = [i.podcast_itunes_id for i in items if i.podcast_itunes_id]
        if not ids:
            return
        try:
            podcasts = self.itunes.lookup_podcasts(ids)
        except http.NetworkError as error:
            log.info("Não foi possível enriquecer o ranking de episódios: %s", error)
            return
        for item in items:
            podcast = podcasts.get(item.podcast_itunes_id or 0)
            if podcast:
                item.podcast_title = podcast.title
                item.feed_url = podcast.feed_url
                if item.genre_id is None:
                    item.genre_id = genres.top_level(podcast.genre_id)

    # -- cache ---------------------------------------------------------------------

    @staticmethod
    def key_top_podcasts() -> str:
        return "chart:top-podcasts"

    @staticmethod
    def key_top_episodes() -> str:
        return "chart:top-episodes"

    @staticmethod
    def key_genre(genre_id: int | None) -> str:
        return f"chart:genre:{genre_id or 'all'}"

    def peek(self, key: str) -> ChartResult | None:
        """Cached result without touching the network (safe on the main thread)."""
        if self.db is None:
            return None
        cached = self.db.cache_get(key)
        if cached is None:
            return None
        payload, fetched_at = cached
        decoder = ChartEpisode if key == self.key_top_episodes() else ChartPodcast
        try:
            items = [decoder(**item) for item in payload]
        except TypeError:
            return None  # schema changed since it was cached
        return ChartResult(items=items, fetched_at=fetched_at, from_cache=True)

    def is_fresh(self, key: str, max_age: float) -> bool:
        cached = self.db.cache_get(key) if self.db is not None else None
        return cached is not None and time.time() - cached[1] < max_age

    def _cached(self, key: str, max_age: float, fetch: Callable[[], list], force: bool) -> ChartResult:
        cached = self.peek(key)
        if cached and not force and time.time() - cached.fetched_at < max_age:
            return cached
        try:
            items = fetch()
        except http.NetworkError as error:
            if cached:
                cached.error = error
                return cached
            raise
        if self.db is not None and items:
            self.db.cache_put(key, [asdict(item) for item in items])
        return ChartResult(items=items, fetched_at=time.time())

    def top_podcasts(self, force: bool = False) -> ChartResult:
        return self._cached(self.key_top_podcasts(), CHART_MAX_AGE, self.fetch_top_podcasts, force)

    def top_episodes(self, force: bool = False) -> ChartResult:
        return self._cached(self.key_top_episodes(), CHART_MAX_AGE, self.fetch_top_episodes, force)

    def genre_chart(self, genre_id: int | None, force: bool = False) -> ChartResult:
        return self._cached(self.key_genre(genre_id), GENRE_MAX_AGE,
                            lambda: self.fetch_genre_chart(genre_id), force)
