"""SQLite persistence: podcasts, episodes, playback progress, queue, caches.

One connection is shared by every thread and serialized with a re-entrant
lock (writes are short; WAL keeps readers fast). Change notifications are
GObject signals, always delivered on the GTK main thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gi.repository import GObject

from .. import config
from ..models import Episode, Podcast, itunes_podcast_id
from ..utils import tasks
from . import seed_data

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS podcasts (
    id             TEXT PRIMARY KEY,
    itunes_id      INTEGER,
    feed_url       TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    author         TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    artwork_url    TEXT NOT NULL DEFAULT '',
    genre          TEXT NOT NULL DEFAULT '',
    genre_id       INTEGER,
    apple_url      TEXT NOT NULL DEFAULT '',
    website        TEXT NOT NULL DEFAULT '',
    explicit       INTEGER NOT NULL DEFAULT 0,
    episode_count  INTEGER NOT NULL DEFAULT 0,
    accent_color   TEXT NOT NULL DEFAULT '',
    subscribed     INTEGER NOT NULL DEFAULT 0,
    subscribed_at  INTEGER,
    last_refreshed INTEGER,
    last_seen_at   INTEGER,
    is_seed        INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_podcasts_itunes ON podcasts(itunes_id) WHERE itunes_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_podcasts_feed ON podcasts(feed_url);

CREATE TABLE IF NOT EXISTS episodes (
    id              TEXT PRIMARY KEY,
    podcast_id      TEXT NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    guid            TEXT NOT NULL DEFAULT '',
    itunes_track_id INTEGER,
    title           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    audio_url       TEXT NOT NULL DEFAULT '',
    mime_type       TEXT NOT NULL DEFAULT '',
    file_size       INTEGER NOT NULL DEFAULT 0,
    duration        INTEGER NOT NULL DEFAULT 0,
    published       INTEGER NOT NULL DEFAULT 0,
    artwork_url     TEXT NOT NULL DEFAULT '',
    link            TEXT NOT NULL DEFAULT '',
    chapters_url    TEXT NOT NULL DEFAULT '',
    season          INTEGER,
    number          INTEGER,
    explicit        INTEGER NOT NULL DEFAULT 0,
    position        REAL NOT NULL DEFAULT 0,
    played          INTEGER NOT NULL DEFAULT 0,
    played_at       INTEGER,
    last_played_at  INTEGER,
    saved           INTEGER NOT NULL DEFAULT 0,
    saved_at        INTEGER,
    download_state  TEXT NOT NULL DEFAULT '',
    download_path   TEXT NOT NULL DEFAULT '',
    download_size   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast ON episodes(podcast_id, published DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_played_at ON episodes(last_played_at) WHERE last_played_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_episodes_track ON episodes(itunes_track_id) WHERE itunes_track_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS queue (
    episode_id TEXT PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
    position   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    payload    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Episode actions waiting to be uploaded to the sync server (latest per episode).
CREATE TABLE IF NOT EXISTS sync_actions (
    episode_id TEXT PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
    action     TEXT NOT NULL,
    started    INTEGER NOT NULL DEFAULT -1,
    position   INTEGER NOT NULL DEFAULT -1,
    total      INTEGER NOT NULL DEFAULT -1,
    created_at INTEGER NOT NULL
);
"""

_EPISODE_SELECT = """
SELECT e.*, p.title AS podcast_title, p.author AS podcast_author,
       p.artwork_url AS podcast_artwork, p.accent_color AS podcast_accent
FROM episodes e JOIN podcasts p ON p.id = e.podcast_id
"""

_NEW_COUNT = """
(SELECT COUNT(*) FROM episodes e
  WHERE e.podcast_id = p.id AND e.played = 0
    AND e.published > COALESCE(p.last_seen_at, p.subscribed_at, 0)) AS new_count
"""

# Non-empty incoming values win; empty ones keep what is stored.
_KEEP_TEXT = "{col} = CASE WHEN excluded.{col} != '' THEN excluded.{col} ELSE {table}.{col} END"


def _now() -> int:
    return int(time.time())


def _podcast(row: sqlite3.Row) -> Podcast:
    keys = row.keys()
    return Podcast(
        id=row["id"],
        itunes_id=row["itunes_id"],
        feed_url=row["feed_url"],
        title=row["title"],
        author=row["author"],
        description=row["description"],
        artwork_url=row["artwork_url"],
        genre=row["genre"],
        genre_id=row["genre_id"],
        apple_url=row["apple_url"],
        website=row["website"],
        explicit=bool(row["explicit"]),
        episode_count=row["episode_count"],
        accent_color=row["accent_color"],
        subscribed=bool(row["subscribed"]),
        subscribed_at=row["subscribed_at"],
        last_refreshed=row["last_refreshed"],
        last_seen_at=row["last_seen_at"],
        is_seed=bool(row["is_seed"]),
        new_count=row["new_count"] if "new_count" in keys else 0,
    )


def _episode(row: sqlite3.Row) -> Episode:
    keys = row.keys()
    return Episode(
        id=row["id"],
        podcast_id=row["podcast_id"],
        title=row["title"],
        guid=row["guid"],
        itunes_track_id=row["itunes_track_id"],
        summary=row["summary"],
        description=row["description"],
        audio_url=row["audio_url"],
        mime_type=row["mime_type"],
        file_size=row["file_size"],
        duration=row["duration"],
        published=row["published"],
        artwork_url=row["artwork_url"],
        link=row["link"],
        chapters_url=row["chapters_url"],
        season=row["season"],
        number=row["number"],
        explicit=bool(row["explicit"]),
        position=row["position"],
        played=bool(row["played"]),
        played_at=row["played_at"],
        last_played_at=row["last_played_at"],
        saved=bool(row["saved"]),
        saved_at=row["saved_at"],
        download_state=row["download_state"],
        download_path=row["download_path"],
        download_size=row["download_size"],
        podcast_title=row["podcast_title"] if "podcast_title" in keys else "",
        podcast_author=row["podcast_author"] if "podcast_author" in keys else "",
        podcast_artwork=row["podcast_artwork"] if "podcast_artwork" in keys else "",
        podcast_accent=row["podcast_accent"] if "podcast_accent" in keys else "",
    )


class Database(GObject.Object):
    __gtype_name__ = "PodFlowDatabase"

    __gsignals__ = {
        "podcast-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "subscriptions-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "episodes-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "episode-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "queue-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "history-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "downloads-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "sync-actions-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, path: str | Path | None = None, seed: bool = True):
        super().__init__()
        self.path = str(path or config.db_path())
        self._lock = threading.RLock()
        # Set by the sync manager while an account is connected.
        self.sync_recording = False
        self._conn = sqlite3.connect(self.path, check_same_thread=False,
                                     isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._migrate()
            if seed:
                self._seed()

    # -- infrastructure ------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).rowcount

    def _emit(self, signal: str, *args: Any) -> None:
        if tasks.is_main_thread():
            self.emit(signal, *args)
        else:
            tasks.idle(self.emit, signal, *args)

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            # Every statement is idempotent, so this both creates and upgrades.
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _seed(self) -> None:
        applied = int(self.get_setting("seed_version", 0))
        if applied >= seed_data.SEED_VERSION:
            return
        first_run = applied == 0
        now = _now()
        with self._tx() as conn:
            for item in seed_data.SEED_PODCASTS:
                conn.execute(
                    """INSERT INTO podcasts (id, itunes_id, feed_url, title, author, description,
                           artwork_url, genre, genre_id, apple_url, accent_color, is_seed,
                           subscribed, subscribed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET is_seed = 1""",
                    (itunes_podcast_id(item["itunes_id"]), item["itunes_id"], item["feed_url"],
                     item["title"], item["author"], item["description"], item["artwork_url"],
                     item["genre"], item["genre_id"], item["apple_url"], item["accent_color"],
                     1 if first_run else 0, now if first_run else None),
                )
        self.set_setting("seed_version", seed_data.SEED_VERSION)

    # -- podcasts ------------------------------------------------------------------

    def upsert_podcast(self, podcast: Podcast) -> Podcast:
        text_cols = ("feed_url", "title", "author", "description", "artwork_url", "genre",
                     "apple_url", "website", "accent_color")
        updates = ",\n".join(_KEEP_TEXT.format(col=c, table="podcasts") for c in text_cols)
        sql = f"""
            INSERT INTO podcasts (id, itunes_id, feed_url, title, author, description, artwork_url,
                                  genre, genre_id, apple_url, website, explicit, episode_count,
                                  accent_color)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                {updates},
                itunes_id = COALESCE(excluded.itunes_id, podcasts.itunes_id),
                genre_id = COALESCE(excluded.genre_id, podcasts.genre_id),
                explicit = excluded.explicit,
                episode_count = MAX(excluded.episode_count, podcasts.episode_count)
        """
        self._exec(sql, (podcast.id, podcast.itunes_id, podcast.feed_url, podcast.title,
                         podcast.author, podcast.description, podcast.artwork_url, podcast.genre,
                         podcast.genre_id, podcast.apple_url, podcast.website,
                         int(podcast.explicit), podcast.episode_count, podcast.accent_color))
        self._emit("podcast-changed", podcast.id)
        stored = self.get_podcast(podcast.id)
        assert stored is not None
        return stored

    def get_podcast(self, podcast_id: str) -> Podcast | None:
        row = self._one(f"SELECT p.*, {_NEW_COUNT} FROM podcasts p WHERE p.id = ?", (podcast_id,))
        return _podcast(row) if row else None

    def find_podcast_by_feed(self, feed_url: str) -> Podcast | None:
        row = self._one(f"SELECT p.*, {_NEW_COUNT} FROM podcasts p WHERE p.feed_url = ?",
                        (feed_url,))
        return _podcast(row) if row else None

    def list_subscriptions(self, order: str = "recent") -> list[Podcast]:
        order_by = "p.title COLLATE NOCASE" if order == "title" else "p.subscribed_at DESC, p.title"
        rows = self._query(f"SELECT p.*, {_NEW_COUNT} FROM podcasts p "
                           f"WHERE p.subscribed = 1 ORDER BY {order_by}")
        return [_podcast(r) for r in rows]

    def list_seed_podcasts(self) -> list[Podcast]:
        rows = self._query(f"SELECT p.*, {_NEW_COUNT} FROM podcasts p WHERE p.is_seed = 1 "
                           "ORDER BY p.rowid")
        return [_podcast(r) for r in rows]

    def podcasts_needing_refresh(self, max_age: int) -> list[Podcast]:
        rows = self._query(
            f"SELECT p.*, {_NEW_COUNT} FROM podcasts p WHERE p.subscribed = 1 AND "
            "(p.last_refreshed IS NULL OR p.last_refreshed < ?) ORDER BY p.last_refreshed",
            (_now() - max_age,))
        return [_podcast(r) for r in rows]

    def set_subscribed(self, podcast_id: str, subscribed: bool) -> None:
        self._exec("UPDATE podcasts SET subscribed = ?, subscribed_at = ?, last_seen_at = ? "
                   "WHERE id = ?",
                   (int(subscribed), _now() if subscribed else None,
                    _now() if subscribed else None, podcast_id))
        self._emit("podcast-changed", podcast_id)
        self._emit("subscriptions-changed")

    def mark_podcast_seen(self, podcast_id: str) -> None:
        if self._exec("UPDATE podcasts SET last_seen_at = ? WHERE id = ? AND subscribed = 1",
                      (_now(), podcast_id)):
            self._emit("subscriptions-changed")

    def set_podcast_refreshed(self, podcast_id: str) -> None:
        self._exec("UPDATE podcasts SET last_refreshed = ? WHERE id = ?", (_now(), podcast_id))

    def set_podcast_accent(self, podcast_id: str, color: str) -> None:
        self._exec("UPDATE podcasts SET accent_color = ? WHERE id = ?", (color, podcast_id))

    # -- episodes ------------------------------------------------------------------

    def upsert_episodes(self, podcast_id: str, episodes: list[Episode]) -> int:
        """Insert or refresh metadata; never touches playback/library state.
        Returns how many episodes were new."""
        if not episodes:
            return 0
        text_cols = ("guid", "title", "summary", "audio_url", "mime_type", "artwork_url",
                     "link", "chapters_url")
        updates = ",\n".join(_KEEP_TEXT.format(col=c, table="episodes") for c in text_cols)
        sql = f"""
            INSERT INTO episodes (id, podcast_id, guid, itunes_track_id, title, summary,
                                  description, audio_url, mime_type, file_size, duration,
                                  published, artwork_url, link, chapters_url, season, number,
                                  explicit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                {updates},
                description = CASE WHEN length(excluded.description) >= length(episodes.description)
                                   THEN excluded.description ELSE episodes.description END,
                itunes_track_id = COALESCE(excluded.itunes_track_id, episodes.itunes_track_id),
                file_size = CASE WHEN excluded.file_size > 0 THEN excluded.file_size ELSE episodes.file_size END,
                duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE episodes.duration END,
                published = CASE WHEN excluded.published > 0 THEN excluded.published ELSE episodes.published END,
                season = COALESCE(excluded.season, episodes.season),
                number = COALESCE(excluded.number, episodes.number),
                explicit = MAX(excluded.explicit, episodes.explicit)
        """
        rows = [(e.id, podcast_id, e.guid, e.itunes_track_id, e.title, e.summary, e.description,
                 e.audio_url, e.mime_type, e.file_size, e.duration, e.published, e.artwork_url,
                 e.link, e.chapters_url, e.season, e.number, int(e.explicit)) for e in episodes]
        with self._tx() as conn:
            before = conn.execute("SELECT COUNT(*) FROM episodes WHERE podcast_id = ?",
                                  (podcast_id,)).fetchone()[0]
            conn.executemany(sql, rows)
            after = conn.execute("SELECT COUNT(*) FROM episodes WHERE podcast_id = ?",
                                 (podcast_id,)).fetchone()[0]
            conn.execute("UPDATE podcasts SET episode_count = MAX(episode_count, ?) WHERE id = ?",
                         (after, podcast_id))
        self._emit("episodes-changed", podcast_id)
        return after - before

    def get_episode(self, episode_id: str) -> Episode | None:
        row = self._one(_EPISODE_SELECT + " WHERE e.id = ?", (episode_id,))
        return _episode(row) if row else None

    def find_episode_by_track(self, track_id: int) -> Episode | None:
        row = self._one(_EPISODE_SELECT + " WHERE e.itunes_track_id = ?", (int(track_id),))
        return _episode(row) if row else None

    def _episode_filter(self, kind: str) -> str:
        if kind == "unplayed":
            return " AND e.played = 0"
        if kind == "downloaded":
            return " AND e.download_state = 'done'"
        return ""

    def list_episodes(self, podcast_id: str, kind: str = "all", limit: int = 50,
                      offset: int = 0) -> list[Episode]:
        rows = self._query(_EPISODE_SELECT + " WHERE e.podcast_id = ?" + self._episode_filter(kind)
                           + " ORDER BY e.published DESC LIMIT ? OFFSET ?",
                           (podcast_id, limit, offset))
        return [_episode(r) for r in rows]

    def count_episodes(self, podcast_id: str, kind: str = "all") -> int:
        row = self._one("SELECT COUNT(*) FROM episodes e WHERE e.podcast_id = ?"
                        + self._episode_filter(kind), (podcast_id,))
        return row[0] if row else 0

    def latest_episode(self, podcast_id: str, unplayed: bool = False) -> Episode | None:
        rows = self.list_episodes(podcast_id, "unplayed" if unplayed else "all", limit=1)
        return rows[0] if rows else None

    def list_in_progress(self, limit: int = 12) -> list[Episode]:
        rows = self._query(_EPISODE_SELECT + " WHERE e.played = 0 AND e.position >= 15 "
                           "ORDER BY COALESCE(e.last_played_at, 0) DESC LIMIT ?", (limit,))
        return [_episode(r) for r in rows]

    def list_new_episodes(self, days: int = 21, limit: int = 40) -> list[Episode]:
        rows = self._query(_EPISODE_SELECT + " WHERE p.subscribed = 1 AND e.played = 0 "
                           "AND e.published >= ? ORDER BY e.published DESC LIMIT ?",
                           (_now() - days * 86400, limit))
        return [_episode(r) for r in rows]

    def list_saved(self) -> list[Episode]:
        rows = self._query(_EPISODE_SELECT + " WHERE e.saved = 1 ORDER BY e.saved_at DESC")
        return [_episode(r) for r in rows]

    def list_downloaded(self) -> list[Episode]:
        rows = self._query(_EPISODE_SELECT + " WHERE e.download_state = 'done' "
                           "ORDER BY e.published DESC")
        return [_episode(r) for r in rows]

    def list_history(self, limit: int = 200) -> list[Episode]:
        rows = self._query(_EPISODE_SELECT + " WHERE e.last_played_at IS NOT NULL "
                           "ORDER BY e.last_played_at DESC LIMIT ?", (limit,))
        return [_episode(r) for r in rows]

    def search_local(self, term: str, limit: int = 30) -> tuple[list[Podcast], list[Episode]]:
        like = f"%{term.strip()}%"
        podcasts = [_podcast(r) for r in self._query(
            f"SELECT p.*, {_NEW_COUNT} FROM podcasts p WHERE p.title LIKE ? OR p.author LIKE ? "
            "ORDER BY p.subscribed DESC, p.title LIMIT ?", (like, like, limit))]
        episodes = [_episode(r) for r in self._query(
            _EPISODE_SELECT + " WHERE e.title LIKE ? ORDER BY e.published DESC LIMIT ?",
            (like, limit))]
        return podcasts, episodes

    # -- playback state --------------------------------------------------------------

    def save_progress(self, episode_id: str, position: float, duration: float = 0) -> None:
        self._exec("UPDATE episodes SET position = ?, "
                   "duration = CASE WHEN ? > 0 AND duration = 0 THEN ? ELSE duration END "
                   "WHERE id = ?", (max(0.0, position), int(duration), int(duration), episode_id))
        self._emit("episode-changed", episode_id)

    def mark_started(self, episode_id: str) -> None:
        self._exec("UPDATE episodes SET last_played_at = ? WHERE id = ?", (_now(), episode_id))
        self._emit("episode-changed", episode_id)
        self._emit("history-changed")

    def set_played(self, episode_id: str, played: bool = True, *, record: bool = True,
                   at: int | None = None) -> None:
        """Mark (un)played. ``record`` queues the change for sync; ``at`` backdates it."""
        if played and at is None:
            self._exec("UPDATE episodes SET played = 1, played_at = ?, position = 0, "
                       "last_played_at = COALESCE(last_played_at, ?) WHERE id = ?",
                       (_now(), _now(), episode_id))
        elif played:
            self._exec("UPDATE episodes SET played = 1, played_at = ?, position = 0, "
                       "last_played_at = MAX(COALESCE(last_played_at, 0), ?) WHERE id = ?",
                       (at, at, episode_id))
        else:
            self._exec("UPDATE episodes SET played = 0, played_at = NULL WHERE id = ?",
                       (episode_id,))
        if record:
            row = self._one("SELECT duration FROM episodes WHERE id = ?", (episode_id,))
            duration = int(row["duration"]) if row else 0
            if played:
                self.sync_record(episode_id, "play", 0, duration, duration)
            else:
                self.sync_record(episode_id, "new")
        self._emit("episode-changed", episode_id)
        self._emit("history-changed")

    def mark_all_played(self, podcast_id: str) -> None:
        ids = [r["id"] for r in self._query(
            "SELECT id FROM episodes WHERE podcast_id = ? AND played = 0", (podcast_id,))]
        self._exec("UPDATE episodes SET played = 1, played_at = ?, position = 0 "
                   "WHERE podcast_id = ? AND played = 0", (_now(), podcast_id))
        for episode_id in ids if self.sync_recording else ():
            row = self._one("SELECT duration FROM episodes WHERE id = ?", (episode_id,))
            if row and row["duration"]:
                self.sync_record(episode_id, "play", 0, row["duration"], row["duration"])
        self._emit("episodes-changed", podcast_id)

    def set_saved(self, episode_id: str, saved: bool) -> None:
        self._exec("UPDATE episodes SET saved = ?, saved_at = ? WHERE id = ?",
                   (int(saved), _now() if saved else None, episode_id))
        self._emit("episode-changed", episode_id)

    def clear_history(self) -> None:
        self._exec("UPDATE episodes SET last_played_at = NULL WHERE last_played_at IS NOT NULL")
        self._emit("history-changed")

    # -- downloads -------------------------------------------------------------------

    def set_download(self, episode_id: str, state: str, path: str = "", size: int = 0) -> None:
        self._exec("UPDATE episodes SET download_state = ?, download_path = ?, download_size = ? "
                   "WHERE id = ?", (state, path, size, episode_id))
        self._emit("episode-changed", episode_id)
        self._emit("downloads-changed")

    def reset_interrupted_downloads(self) -> list[str]:
        rows = self._query("SELECT id FROM episodes WHERE download_state IN "
                           "('queued', 'downloading')")
        self._exec("UPDATE episodes SET download_state = '' WHERE download_state IN "
                   "('queued', 'downloading')")
        return [r["id"] for r in rows]

    # -- queue -----------------------------------------------------------------------

    def queue_ids(self) -> list[str]:
        return [r["episode_id"] for r in self._query(
            "SELECT episode_id FROM queue ORDER BY position")]

    def queue_list(self) -> list[Episode]:
        rows = self._query(_EPISODE_SELECT.replace(
            "FROM episodes e", "FROM queue q JOIN episodes e ON e.id = q.episode_id")
            + " ORDER BY q.position")
        return [_episode(r) for r in rows]

    def _write_queue(self, ids: list[str]) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM queue")
            conn.executemany("INSERT INTO queue (episode_id, position) VALUES (?, ?)",
                             [(eid, index) for index, eid in enumerate(ids)])
        self._emit("queue-changed")

    def queue_add(self, episode_id: str, front: bool = False) -> None:
        ids = [i for i in self.queue_ids() if i != episode_id]
        if front:
            ids.insert(0, episode_id)
        else:
            ids.append(episode_id)
        self._write_queue(ids)

    def queue_remove(self, episode_id: str) -> None:
        if self._exec("DELETE FROM queue WHERE episode_id = ?", (episode_id,)):
            self._emit("queue-changed")

    def queue_move(self, episode_id: str, index: int) -> None:
        ids = [i for i in self.queue_ids() if i != episode_id]
        index = max(0, min(index, len(ids)))
        ids.insert(index, episode_id)
        self._write_queue(ids)

    def queue_clear(self) -> None:
        self._exec("DELETE FROM queue")
        self._emit("queue-changed")

    def in_queue(self, episode_id: str) -> bool:
        return self._one("SELECT 1 FROM queue WHERE episode_id = ?", (episode_id,)) is not None

    # -- cache & settings ------------------------------------------------------------

    def cache_get(self, key: str) -> tuple[Any, float] | None:
        row = self._one("SELECT payload, fetched_at FROM cache WHERE key = ?", (key,))
        if row is None:
            return None
        try:
            return json.loads(row["payload"]), row["fetched_at"]
        except ValueError:
            return None

    def cache_put(self, key: str, payload: Any) -> None:
        self._exec("INSERT INTO cache (key, fetched_at, payload) VALUES (?, ?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET fetched_at = excluded.fetched_at, "
                   "payload = excluded.payload",
                   (key, time.time(), json.dumps(payload, ensure_ascii=False)))

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO settings (key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, json.dumps(value)))

    def delete_settings(self, *keys: str) -> None:
        with self._tx() as conn:
            conn.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])

    # -- sync ------------------------------------------------------------------------

    def sync_record(self, episode_id: str, action: str, started: float = -1,
                    position: float = -1, total: float = -1) -> None:
        """Queue an episode action for upload (only while an account is connected)."""
        if not self.sync_recording:
            return
        self._exec("INSERT INTO sync_actions (episode_id, action, started, position, total, "
                   "created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(episode_id) DO UPDATE SET "
                   "action = excluded.action, started = excluded.started, "
                   "position = excluded.position, total = excluded.total, "
                   "created_at = excluded.created_at",
                   (episode_id, action, int(started), int(position), int(total), _now()))
        self._emit("sync-actions-changed")

    def sync_pending(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT s.*, e.audio_url, e.guid, p.feed_url FROM sync_actions s "
            "JOIN episodes e ON e.id = s.episode_id JOIN podcasts p ON p.id = e.podcast_id "
            "ORDER BY s.created_at LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def sync_count_pending(self) -> int:
        row = self._one("SELECT COUNT(*) FROM sync_actions")
        return row[0] if row else 0

    def sync_done(self, sent: list[dict[str, Any]]) -> None:
        """Drop uploaded actions unless the episode changed again meanwhile."""
        with self._tx() as conn:
            conn.executemany("DELETE FROM sync_actions WHERE episode_id = ? AND created_at = ? "
                             "AND action = ? AND position = ?",
                             [(a["episode_id"], a["created_at"], a["action"], a["position"])
                              for a in sent])

    def sync_clear(self) -> None:
        self._exec("DELETE FROM sync_actions")

    def sync_queue_history(self, limit: int = 300) -> int:
        """Queue the listening history, so a first sync shares what was already heard."""
        rows = self._query("SELECT id, played, position, duration FROM episodes "
                           "WHERE last_played_at IS NOT NULL AND (played = 1 OR position >= 15) "
                           "ORDER BY last_played_at DESC LIMIT ?", (limit,))
        count = 0
        for row in rows:
            if row["played"] and row["duration"]:
                self.sync_record(row["id"], "play", 0, row["duration"], row["duration"])
            elif not row["played"]:
                self.sync_record(row["id"], "play", 0, row["position"], row["duration"] or -1)
            else:
                continue
            count += 1
        return count

    def subscribed_feeds(self) -> dict[str, str]:
        """feed URL -> podcast id for every followed show that has a feed."""
        rows = self._query("SELECT id, feed_url FROM podcasts WHERE subscribed = 1 "
                           "AND feed_url != ''")
        return {r["feed_url"]: r["id"] for r in rows}

    def seed_feeds(self) -> set[str]:
        """Feeds of the pre-installed catalog."""
        return {r["feed_url"] for r in self._query(
            "SELECT feed_url FROM podcasts WHERE is_seed = 1 AND feed_url != ''")}

    def find_episode_for_sync(self, feed_url: str, episode_url: str,
                              guid: str = "") -> Episode | None:
        if episode_url:
            row = self._one(_EPISODE_SELECT + " WHERE e.audio_url = ? ORDER BY p.subscribed DESC "
                            "LIMIT 1", (episode_url,))
            if row is not None:
                return _episode(row)
        if guid and feed_url:
            row = self._one(_EPISODE_SELECT + " WHERE e.guid = ? AND p.feed_url = ? LIMIT 1",
                            (guid, feed_url))
            if row is not None:
                return _episode(row)
        return None

    def apply_remote_progress(self, episode_id: str, position: float, at: int) -> None:
        """Position heard on another device (newer than ours)."""
        self._exec("UPDATE episodes SET position = ?, played = 0, played_at = NULL, "
                   "last_played_at = ? WHERE id = ?", (max(0.0, position), at, episode_id))
        self._emit("episode-changed", episode_id)
        self._emit("history-changed")
