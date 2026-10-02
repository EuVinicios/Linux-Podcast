"""iTunes Search / Lookup API: podcast details, recent episodes and search."""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime

from .. import config
from ..models import Episode, Podcast, itunes_podcast_id, make_episode_id
from ..utils.text import summarize
from . import genres, http

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"


def artwork_url(url: str | None, size: int) -> str:
    """Ask Apple's CDN for a square artwork of ``size`` pixels."""
    if not url:
        return ""
    if "mzstatic.com" in url:
        return re.sub(r"/[0-9]+x[0-9]+[a-z]*\.(?:jpg|jpeg|png|webp)$", f"/{size}x{size}bb.jpg", url)
    return url


def _iso_timestamp(value: str | None) -> int:
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def podcast_from_itunes(result: dict) -> Podcast | None:
    collection_id = result.get("collectionId") or result.get("trackId")
    if not collection_id:
        return None
    genre_ids = [int(g) for g in result.get("genreIds", []) if str(g).isdigit()]
    genre_id = next((g for g in genre_ids if g != genres.PODCASTS_ROOT_GENRE), None)
    return Podcast(
        id=itunes_podcast_id(collection_id),
        itunes_id=int(collection_id),
        title=result.get("collectionName") or result.get("trackName") or "",
        author=result.get("artistName") or "",
        artwork_url=result.get("artworkUrl600") or result.get("artworkUrl100") or "",
        feed_url=result.get("feedUrl") or "",
        genre=result.get("primaryGenreName") or genres.genre_name(genre_id),
        genre_id=genre_id,
        apple_url=(result.get("collectionViewUrl") or "").split("?")[0],
        explicit=result.get("collectionExplicitness") == "explicit",
        episode_count=int(result.get("trackCount") or 0),
    )


def _mime_for(result: dict) -> str:
    extension = (result.get("episodeFileExtension") or "").lower()
    kind = result.get("episodeContentType") or "audio"
    known = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "aac": "audio/aac",
             "mp4": f"{kind}/mp4", "ogg": "audio/ogg", "opus": "audio/opus"}
    return known.get(extension, f"{kind}/{extension}" if extension else "")


def episode_from_itunes(result: dict, podcast_id: str | None = None) -> Episode | None:
    audio_url = result.get("episodeUrl") or ""
    if not audio_url:
        return None
    podcast_id = podcast_id or itunes_podcast_id(result.get("collectionId") or 0)
    guid = (result.get("episodeGuid") or "").strip()
    title = result.get("trackName") or ""
    description = result.get("description") or ""
    return Episode(
        id=make_episode_id(podcast_id, guid, audio_url, title),
        podcast_id=podcast_id,
        title=title or "Episódio sem título",
        guid=guid,
        itunes_track_id=result.get("trackId"),
        summary=summarize(result.get("shortDescription") or description),
        description=description.replace("\n", "<br>") if "<" not in description else description,
        audio_url=audio_url,
        mime_type=_mime_for(result),
        duration=int((result.get("trackTimeMillis") or 0) / 1000),
        published=_iso_timestamp(result.get("releaseDate")),
        artwork_url=result.get("artworkUrl600") or "",
        link=(result.get("trackViewUrl") or "").split("&uo=")[0],
        explicit=result.get("contentAdvisoryRating") == "Explicit",
        podcast_title=result.get("collectionName") or "",
    )


class ItunesService:
    def __init__(self, country: str = config.COUNTRY):
        self.country = country

    def _url(self, base: str, **params) -> str:
        params.setdefault("country", self.country)
        return f"{base}?{urllib.parse.urlencode(params)}"

    def lookup_podcasts(self, ids: list[int]) -> dict[int, Podcast]:
        """Batch lookup (Apple accepts comma-separated IDs)."""
        found: dict[int, Podcast] = {}
        unique = list(dict.fromkeys(int(i) for i in ids if i))
        for start in range(0, len(unique), 100):
            chunk = unique[start:start + 100]
            data = http.get_json(self._url(LOOKUP_URL, id=",".join(map(str, chunk)), entity="podcast"))
            for result in data.get("results", []):
                podcast = podcast_from_itunes(result)
                if podcast and podcast.itunes_id:
                    found[podcast.itunes_id] = podcast
        return found

    def lookup_podcast_with_episodes(self, itunes_id: int, limit: int = 200
                                     ) -> tuple[Podcast | None, list[Episode]]:
        data = http.get_json(self._url(LOOKUP_URL, id=int(itunes_id),
                                       entity="podcastEpisode", limit=limit), timeout=20)
        podcast: Podcast | None = None
        episodes: list[Episode] = []
        podcast_id = itunes_podcast_id(itunes_id)
        for result in data.get("results", []):
            if result.get("wrapperType") == "track" and result.get("kind") == "podcast":
                podcast = podcast_from_itunes(result)
            elif result.get("wrapperType") == "podcastEpisode":
                episode = episode_from_itunes(result, podcast_id)
                if episode:
                    episodes.append(episode)
        episodes.sort(key=lambda e: e.published, reverse=True)
        return podcast, episodes

    def search_podcasts(self, term: str, limit: int = 25) -> list[Podcast]:
        data = http.get_json(self._url(SEARCH_URL, term=term, media="podcast",
                                       entity="podcast", limit=limit))
        results = [podcast_from_itunes(r) for r in data.get("results", [])]
        return [p for p in results if p and p.feed_url]

    def search_episodes(self, term: str, limit: int = 25) -> list[Episode]:
        data = http.get_json(self._url(SEARCH_URL, term=term, media="podcast",
                                       entity="podcastEpisode", limit=limit))
        episodes = []
        for result in data.get("results", []):
            episode = episode_from_itunes(result)
            if episode:
                episode.podcast_artwork = result.get("artworkUrl600") or ""
                episodes.append(episode)
        return episodes
