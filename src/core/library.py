"""Network-backed library operations used by the UI.

Combines the iTunes lookup (fast, recent episodes) with the full RSS feed
(complete history and show notes) and keeps the database up to date.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gio, GObject

from .. import config
from ..api import http
from ..api.apple_service import AppleService, ChartEpisode, ChartPodcast
from ..api.itunes_service import ItunesService
from ..api.rss_parser import FeedError, fetch_feed
from ..models import Episode, Podcast, SearchResults, itunes_podcast_id
from ..utils import tasks

log = logging.getLogger(__name__)

SUBSCRIPTION_MAX_AGE = 3600
DETAIL_MAX_AGE = 1800


def normalize_feed_url(url: str) -> str:
    url = url.strip()
    for scheme in ("itpc://", "pcast://", "podcast://", "feed://"):
        if url.lower().startswith(scheme):
            return "https://" + url[len(scheme):]
    if "://" not in url:
        return "https://" + url
    return url


class Library(GObject.Object):
    __gtype_name__ = "PodFlowLibrary"

    __gsignals__ = {
        "online-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "refresh-started": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "refresh-finished": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "subscriptions-refreshing": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, db, apple: AppleService | None = None, itunes: ItunesService | None = None):
        super().__init__()
        self.db = db
        self.itunes = itunes or ItunesService()
        self.apple = apple or AppleService(db, self.itunes)
        self._refreshing: set[str] = set()
        self._refreshing_all = False
        self._monitor = Gio.NetworkMonitor.get_default()
        self._online = (not config.OFFLINE) and self._monitor.get_network_available()
        self._monitor.connect("network-changed", self._on_network_changed)

    # -- connectivity ----------------------------------------------------------------

    @property
    def online(self) -> bool:
        return self._online

    def _on_network_changed(self, _monitor, available: bool) -> None:
        online = available and not config.OFFLINE
        if online != self._online:
            self._online = online
            self.emit("online-changed", online)

    def report_network_result(self, error: BaseException | None) -> None:
        """Views call this after a request: real failures beat the monitor's guess."""
        if error is None and not self._online and not config.OFFLINE:
            self._online = True
            self.emit("online-changed", True)

    # -- refresh -----------------------------------------------------------------------

    def is_refreshing(self, podcast_id: str) -> bool:
        return podcast_id in self._refreshing

    @property
    def refreshing_subscriptions(self) -> bool:
        return self._refreshing_all

    def refresh_podcast(self, podcast_id: str, full: bool = True, force: bool = False) -> bool:
        """Start a background refresh. Returns False if nothing was started."""
        if podcast_id in self._refreshing or config.OFFLINE:
            return False
        podcast = self.db.get_podcast(podcast_id)
        if podcast is None:
            return False
        if not force and podcast.last_refreshed and podcast.episode_count:
            if time.time() - podcast.last_refreshed < DETAIL_MAX_AGE:
                return False
        self._refreshing.add(podcast_id)
        self.emit("refresh-started", podcast_id)

        def done(_new: int) -> None:
            self._refreshing.discard(podcast_id)
            self.report_network_result(None)
            self.emit("refresh-finished", podcast_id, "")

        def failed(error: BaseException) -> None:
            self._refreshing.discard(podcast_id)
            self.emit("refresh-finished", podcast_id, str(error) or type(error).__name__)

        tasks.run_async(self._refresh_sync, podcast, full, on_done=done, on_error=failed)
        return True

    def _refresh_sync(self, podcast: Podcast, full: bool) -> int:
        new = 0
        feed_url = podcast.feed_url
        got_itunes = False
        if podcast.itunes_id:
            try:
                info, episodes = self.itunes.lookup_podcast_with_episodes(
                    podcast.itunes_id, limit=200 if full else 25)
                if info is not None:
                    info.id = podcast.id
                    info.description = ""  # the lookup has none; keep ours
                    self.db.upsert_podcast(info)
                    feed_url = info.feed_url or feed_url
                new += self.db.upsert_episodes(podcast.id, episodes)
                got_itunes = bool(episodes)
            except http.NetworkError as error:
                if not feed_url:
                    raise
                log.info("Lookup falhou para %s: %s", podcast.title, error)
        if feed_url and (full or not got_itunes):
            try:
                parsed = fetch_feed(feed_url, podcast.id)
                meta = parsed.podcast
                meta.id = podcast.id
                meta.itunes_id = podcast.itunes_id
                if podcast.itunes_id:
                    # Apple's artwork scales on the CDN and its genre is localized.
                    meta.artwork_url = ""
                    meta.genre = ""
                self.db.upsert_podcast(meta)
                new += self.db.upsert_episodes(podcast.id, parsed.episodes)
            except (http.NetworkError, FeedError) as error:
                if not got_itunes:
                    raise
                log.info("Feed RSS falhou para %s: %s", podcast.title, error)
        self.db.set_podcast_refreshed(podcast.id)
        return new

    def refresh_subscriptions(self, force: bool = False) -> None:
        if self._refreshing_all or not self._online:
            return
        podcasts = (self.db.list_subscriptions() if force
                    else self.db.podcasts_needing_refresh(SUBSCRIPTION_MAX_AGE))
        if not podcasts:
            return
        self._refreshing_all = True
        self.emit("subscriptions-refreshing", True)

        def finished(_result=None) -> None:
            self._refreshing_all = False
            self.emit("subscriptions-refreshing", False)

        tasks.run_async(self._refresh_many, podcasts, on_done=finished,
                        on_error=lambda _e: finished())

    def _refresh_many(self, podcasts: list[Podcast]) -> None:
        def one(podcast: Podcast) -> None:
            try:
                self._refresh_sync(podcast, full=False)
            except Exception as error:
                log.info("Atualização falhou para %s: %s", podcast.title, error)

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="podflow-refresh") as pool:
            list(pool.map(one, podcasts))

    # -- catalog -> library ----------------------------------------------------------

    def ensure_chart_podcast(self, item: ChartPodcast) -> Podcast:
        existing = self.db.get_podcast(item.podcast_id)
        if existing is not None:
            return existing
        return self.db.upsert_podcast(Podcast(
            id=item.podcast_id, itunes_id=item.itunes_id, title=item.title, author=item.author,
            artwork_url=item.artwork_url, genre=item.genre, genre_id=item.genre_id,
            apple_url=item.apple_url, description=item.summary))

    def ensure_podcast(self, podcast: Podcast) -> Podcast:
        existing = self.db.get_podcast(podcast.id)
        return existing if existing is not None else self.db.upsert_podcast(podcast)

    def ensure_episode(self, episode: Episode) -> Episode:
        stored = self.db.get_episode(episode.id)
        if stored is not None:
            return stored
        if self.db.get_podcast(episode.podcast_id) is None:
            itunes_id = None
            if episode.podcast_id.startswith("itunes:"):
                itunes_id = int(episode.podcast_id.split(":", 1)[1])
            self.db.upsert_podcast(Podcast(
                id=episode.podcast_id, itunes_id=itunes_id,
                title=episode.podcast_title or "Podcast",
                artwork_url=episode.podcast_artwork or episode.artwork_url))
        self.db.upsert_episodes(episode.podcast_id, [episode])
        return self.db.get_episode(episode.id) or episode

    def resolve_chart_episode(self, item: ChartEpisode, on_done, on_error) -> None:
        cached = self.db.find_episode_by_track(item.itunes_track_id)
        if cached is not None:
            on_done(cached)
            return
        tasks.run_async(self._resolve_sync, item, on_done=on_done, on_error=on_error)

    def _resolve_sync(self, item: ChartEpisode) -> Episode:
        if not item.podcast_itunes_id:
            raise LookupError("Programa desconhecido")
        info, episodes = self.itunes.lookup_podcast_with_episodes(item.podcast_itunes_id, 200)
        podcast = info or Podcast(id=itunes_podcast_id(item.podcast_itunes_id),
                                  itunes_id=item.podcast_itunes_id,
                                  title=item.podcast_title or item.author,
                                  artwork_url=item.artwork_url)
        if self.db.get_podcast(podcast.id) is None:
            self.db.upsert_podcast(podcast)
        self.db.upsert_episodes(podcast.id, episodes)
        episode = self.db.find_episode_by_track(item.itunes_track_id)
        if episode is None:
            raise LookupError("Episódio indisponível")
        return episode

    def latest_episode(self, podcast_id: str, on_done, on_error) -> None:
        """Newest episode of a show, refreshing first if we know nothing recent."""
        podcast = self.db.get_podcast(podcast_id)
        cached = self.db.latest_episode(podcast_id)
        if cached is not None and (podcast and podcast.last_refreshed or not self._online):
            on_done(cached)
            return
        if podcast is None:
            on_error(LookupError("Programa desconhecido"))
            return

        def work() -> Episode:
            self._refresh_sync(podcast, full=False)
            episode = self.db.latest_episode(podcast_id)
            if episode is None:
                raise LookupError("Nenhum episódio disponível")
            return episode

        tasks.run_async(work, on_done=on_done, on_error=on_error)

    def set_subscribed(self, podcast_id: str, subscribed: bool) -> None:
        self.db.set_subscribed(podcast_id, subscribed)
        if subscribed and self.db.count_episodes(podcast_id) == 0:
            self.refresh_podcast(podcast_id, full=False, force=True)

    # -- search & feeds --------------------------------------------------------------

    def search(self, term: str, on_done, on_error) -> None:
        tasks.run_async(self._search_sync, term, on_done=on_done, on_error=on_error)

    def _search_sync(self, term: str) -> SearchResults:
        podcasts = self.itunes.search_podcasts(term, limit=24)
        try:
            episodes = self.itunes.search_episodes(term, limit=20)
        except http.NetworkError:
            episodes = []
        return SearchResults(podcasts=podcasts, episodes=episodes)

    def search_local(self, term: str) -> SearchResults:
        podcasts, episodes = self.db.search_local(term)
        return SearchResults(podcasts=podcasts, episodes=episodes)

    def add_feed(self, url: str, on_done, on_error) -> None:
        tasks.run_async(self._add_feed_sync, normalize_feed_url(url),
                        on_done=on_done, on_error=on_error)

    def add_feed_now(self, url: str) -> Podcast:
        """Blocking :meth:`add_feed` for worker threads (used by sync)."""
        return self._add_feed_sync(normalize_feed_url(url))

    def _add_feed_sync(self, url: str) -> Podcast:
        existing = self.db.find_podcast_by_feed(url)
        if existing is not None:
            return existing
        parsed = fetch_feed(url)
        podcast = self.db.upsert_podcast(parsed.podcast)
        self.db.upsert_episodes(podcast.id, parsed.episodes)
        self.db.set_podcast_refreshed(podcast.id)
        return self.db.get_podcast(podcast.id) or podcast
