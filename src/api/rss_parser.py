"""Podcast RSS parser built on the standard library.

Handles the iTunes, content, Podcasting 2.0 and Media RSS namespaces, the usual
duration/date formats, and repairs common breakage (HTML entities, stray
ampersands, control characters) before giving up on a feed.
"""

from __future__ import annotations

import html.entities
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..models import Episode, Podcast, feed_podcast_id, make_episode_id
from ..utils.text import summarize
from ..utils.time_format import parse_duration
from . import http

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "podcast": "https://podcastindex.org/namespace/1.0",
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "googleplay": "http://www.google.com/schemas/play-podcasts/1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}

MAX_FEED_BYTES = 60 * 1024 * 1024

_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_STRAY_AMP_RE = re.compile(r"&(?!(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DECL_RE = re.compile(rb"^\s*<\?xml[^>]*?encoding=[\"']([A-Za-z0-9_.\-]+)[\"'][^>]*\?>", re.I)


class FeedError(Exception):
    pass


@dataclass
class ParsedFeed:
    podcast: Podcast
    episodes: list[Episode] = field(default_factory=list)


def _replace_entity(match: re.Match) -> str:
    name = match.group(1)
    if name in _XML_ENTITIES:
        return match.group(0)
    codepoint = html.entities.name2codepoint.get(name)
    return f"&#{codepoint};" if codepoint else f"&amp;{name};"


def _repair(data: bytes) -> bytes:
    declared = _DECL_RE.match(data)
    encoding = declared.group(1).decode("ascii") if declared else "utf-8"
    try:
        text = data.decode(encoding, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    text = _CONTROL_RE.sub("", text)
    text = _ENTITY_RE.sub(_replace_entity, text)
    text = _STRAY_AMP_RE.sub("&amp;", text)
    return ('<?xml version="1.0" encoding="utf-8"?>' + text).encode("utf-8")


def _parse_xml(data: bytes) -> ET.Element:
    data = data.lstrip(b"\xef\xbb\xbf\x00 \t\r\n")
    if not data:
        raise FeedError("Feed vazio")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        try:
            return ET.fromstring(_repair(data))
        except ET.ParseError as error:
            raise FeedError(f"XML inválido: {error}") from error


def _text(element: ET.Element | None, path: str) -> str:
    if element is None:
        return ""
    value = element.findtext(path, default="", namespaces=NS)
    return (value or "").strip()


def _attr(element: ET.Element | None, path: str, name: str) -> str:
    if element is None:
        return ""
    found = element.find(path, NS)
    if found is None:
        return ""
    return (found.get(name) or "").strip()


def _int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _explicit(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "explicit")


def parse_date(value: str | None) -> int:
    """RFC 822 (pubDate) or ISO 8601 → Unix timestamp (0 when unknown)."""
    if not value:
        return 0
    value = value.strip()
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        moment = None
    if moment is None:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return int(moment.timestamp())
    except (OverflowError, OSError, ValueError):
        return 0


def _parse_item(item: ET.Element, podcast_id: str, channel_image: str) -> Episode | None:
    audio_url = _attr(item, "enclosure", "url")
    mime = _attr(item, "enclosure", "type")
    size = _int(_attr(item, "enclosure", "length")) or 0
    if not audio_url:
        audio_url = _attr(item, "media:content", "url")
        mime = mime or _attr(item, "media:content", "type")
    if not audio_url:
        return None  # text-only posts are not playable episodes

    title = _text(item, "title") or _text(item, "itunes:title") or "Episódio sem título"
    guid = _text(item, "guid") or audio_url
    description = (_text(item, "content:encoded") or _text(item, "description")
                   or _text(item, "itunes:summary"))
    summary_source = _text(item, "itunes:summary") or _text(item, "description") or description
    published = parse_date(_text(item, "pubDate") or _text(item, "dc:date")
                           or _text(item, "atom:published"))
    image = _attr(item, "itunes:image", "href") or _attr(item, "media:thumbnail", "url")

    return Episode(
        id=make_episode_id(podcast_id, guid, audio_url, title),
        podcast_id=podcast_id,
        title=title,
        guid=guid,
        summary=summarize(summary_source),
        description=description,
        audio_url=audio_url,
        mime_type=mime,
        file_size=max(0, size),
        duration=parse_duration(_text(item, "itunes:duration")),
        published=published,
        artwork_url=image if image and image != channel_image else "",
        link=_text(item, "link"),
        chapters_url=_attr(item, "podcast:chapters", "url"),
        season=_int(_text(item, "itunes:season")),
        number=_int(_text(item, "itunes:episode")),
        explicit=_explicit(_text(item, "itunes:explicit")),
    )


def parse_feed(data: bytes, feed_url: str = "", podcast_id: str | None = None) -> ParsedFeed:
    if len(data) > MAX_FEED_BYTES:
        raise FeedError("Feed grande demais")
    root = _parse_xml(data)
    channel = root.find("channel")
    if channel is None:
        raise FeedError("O endereço não é um feed RSS de podcast")
    podcast_id = podcast_id or feed_podcast_id(feed_url)

    image = (_attr(channel, "itunes:image", "href") or _text(channel, "image/url")
             or _attr(channel, "media:thumbnail", "url"))
    category = channel.find("itunes:category", NS)
    genre = ""
    if category is not None:
        sub = category.find("itunes:category", NS)
        genre = (sub.get("text") if sub is not None else category.get("text")) or ""

    podcast = Podcast(
        id=podcast_id,
        title=_text(channel, "title") or feed_url or "Podcast sem título",
        author=(_text(channel, "itunes:author") or _text(channel, "googleplay:author")
                or _text(channel, "itunes:owner/itunes:name") or _text(channel, "managingEditor")),
        description=_text(channel, "description") or _text(channel, "itunes:summary"),
        artwork_url=image,
        feed_url=feed_url,
        website=_text(channel, "link"),
        genre=genre.strip(),
        explicit=_explicit(_text(channel, "itunes:explicit")),
    )

    seen: set[str] = set()
    episodes: list[Episode] = []
    for item in channel.iter("item"):
        episode = _parse_item(item, podcast_id, image)
        if episode is None or episode.id in seen:
            continue
        seen.add(episode.id)
        episodes.append(episode)
    episodes.sort(key=lambda e: e.published, reverse=True)
    podcast.episode_count = len(episodes)
    return ParsedFeed(podcast=podcast, episodes=episodes)


def fetch_feed(feed_url: str, podcast_id: str | None = None, timeout: float = 30) -> ParsedFeed:
    data = http.get_bytes(feed_url, timeout=timeout, max_bytes=MAX_FEED_BYTES)
    return parse_feed(data, feed_url, podcast_id)
