"""Plain data objects shared by the API layer, the database and the UI."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def itunes_podcast_id(itunes_id: int) -> str:
    return f"itunes:{int(itunes_id)}"


def feed_podcast_id(feed_url: str) -> str:
    digest = hashlib.sha1(feed_url.strip().encode("utf-8")).hexdigest()[:16]
    return f"feed:{digest}"


def make_episode_id(podcast_id: str, guid: str = "", audio_url: str = "", title: str = "") -> str:
    """Stable episode key. iTunes' ``episodeGuid`` equals the RSS ``<guid>``,
    so episodes from both sources merge into the same row."""
    key = (guid or audio_url or title).strip()
    return "e" + hashlib.sha1(f"{podcast_id}\n{key}".encode("utf-8")).hexdigest()[:24]


@dataclass
class Podcast:
    id: str
    title: str
    author: str = ""
    description: str = ""
    artwork_url: str = ""
    feed_url: str = ""
    itunes_id: int | None = None
    genre: str = ""
    genre_id: int | None = None
    apple_url: str = ""
    website: str = ""
    explicit: bool = False
    episode_count: int = 0
    accent_color: str = ""
    # Library state
    subscribed: bool = False
    subscribed_at: int | None = None
    last_refreshed: int | None = None
    last_seen_at: int | None = None
    is_seed: bool = False
    # Computed by queries
    new_count: int = 0


@dataclass
class Episode:
    id: str
    podcast_id: str
    title: str
    guid: str = ""
    itunes_track_id: int | None = None
    summary: str = ""
    description: str = ""
    audio_url: str = ""
    mime_type: str = ""
    file_size: int = 0
    duration: int = 0
    published: int = 0
    artwork_url: str = ""
    link: str = ""
    chapters_url: str = ""
    season: int | None = None
    number: int | None = None
    explicit: bool = False
    # Playback / library state
    position: float = 0.0
    played: bool = False
    played_at: int | None = None
    last_played_at: int | None = None
    saved: bool = False
    saved_at: int | None = None
    download_state: str = ""
    download_path: str = ""
    download_size: int = 0
    # Joined from the podcast row
    podcast_title: str = ""
    podcast_author: str = ""
    podcast_artwork: str = ""
    podcast_accent: str = ""

    @property
    def is_downloaded(self) -> bool:
        return self.download_state == "done" and bool(self.download_path)

    @property
    def in_progress(self) -> bool:
        return not self.played and self.position >= 15

    @property
    def progress(self) -> float:
        if not self.duration:
            return 0.0
        return max(0.0, min(1.0, self.position / self.duration))

    @property
    def cover_url(self) -> str:
        return self.artwork_url or self.podcast_artwork


@dataclass
class SearchResults:
    podcasts: list[Podcast] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
