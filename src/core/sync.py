"""Account and synchronization with a gPodder-compatible server.

What travels: the followed shows (feed URLs) and episode actions (where you
stopped, what you finished). Works with gpodder.net, Nextcloud GPodder Sync
and self-hosted servers, so PodFlow stays in step with AntennaPod, gPodder,
Kasts and friends.

Subscriptions are reconciled against the last set both sides agreed on, which
needs no change tracking and never deletes anything on a first sync. Episode
actions are queued locally (latest per episode) and uploaded in batches;
remote ones are applied only when newer than what this computer knows.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from gi.repository import GLib, GObject

from .. import config
from ..api import gpodder, http
from ..api.rss_parser import FeedError
from ..i18n import _
from ..utils import tasks
from .secrets import FILE, KEYRING, CredentialStore

log = logging.getLogger(__name__)

AUTO_INTERVAL = 30 * 60
MIN_GAP = 45  # seconds between automatic syncs
PLAYED_TAIL = 45
BATCH = 100

_ACCOUNT = "sync_account"
_STATE_KEYS = ("sync_subs_since", "sync_subs_known", "sync_aliases", "sync_pending_feeds",
               "sync_actions_since", "sync_last", "sync_error")


@dataclass
class SyncSummary:
    added: int = 0
    removed: int = 0
    applied: int = 0
    uploaded: int = 0
    failed_feeds: int = 0

    def describe(self) -> str:
        parts = []
        if self.added:
            parts.append(_("{n} programa(s) novo(s)").format(n=self.added))
        if self.removed:
            parts.append(_("{n} removido(s)").format(n=self.removed))
        if self.applied:
            parts.append(_("{n} episódio(s) atualizado(s)").format(n=self.applied))
        return ", ".join(parts)


def iso_timestamp(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value:
        return 0
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


class SyncManager(GObject.Object):
    __gtype_name__ = "PodFlowSyncManager"

    __gsignals__ = {
        # account connected/disconnected, sync started/stopped
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # a sync ended: error message ("" on success) and whether the user asked for it
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (str, bool, object)),
    }

    def __init__(self, db, library, playback=None, store: CredentialStore | None = None):
        super().__init__()
        self.db = db
        self.library = library
        self.playback = playback
        self.store = store or CredentialStore()
        self.syncing = False
        self._password: str | None = None
        self._timer = 0
        self._pending = 0
        self._last_finished = 0.0
        self.db.sync_recording = self.account is not None
        db.connect("subscriptions-changed", lambda *_: self.schedule(20))
        db.connect("sync-actions-changed", lambda *_: self.schedule(120))
        library.connect("online-changed",
                        lambda _l, online: self.schedule(10) if online else None)

    # -- state -----------------------------------------------------------------------

    @property
    def account(self) -> dict[str, str] | None:
        account = self.db.get_setting(_ACCOUNT)
        return account if isinstance(account, dict) and account.get("username") else None

    @property
    def configured(self) -> bool:
        return self.account is not None

    @property
    def auto_sync(self) -> bool:
        return bool(self.db.get_setting("sync_auto", True))

    @auto_sync.setter
    def auto_sync(self, value: bool) -> None:
        self.db.set_setting("sync_auto", bool(value))
        if value:
            self.schedule(5)

    @property
    def last_sync(self) -> int:
        return int(self.db.get_setting("sync_last", 0) or 0)

    @property
    def last_error(self) -> str:
        return str(self.db.get_setting("sync_error", "") or "")

    @property
    def storage(self) -> str:
        account = self.account or {}
        return account.get("storage", KEYRING)

    def service_name(self) -> str:
        account = self.account
        if account is None:
            return ""
        if account["provider"] == gpodder.GPODDER_NET:
            return "gpodder.net"
        host = urllib.parse.urlparse(account["server"]).hostname or ""
        name = "Nextcloud" if account["provider"] == gpodder.NEXTCLOUD else _("Servidor gPodder")
        return f"{name} · {host}" if host else name

    def device_caption(self) -> str:
        return _("PodFlow em {host}").format(host=GLib.get_host_name())

    # -- account -----------------------------------------------------------------------

    def sign_in(self, provider: str, server: str, username: str, password: str,
                on_done, on_error) -> None:
        server = gpodder.normalize_server(server, provider)
        username = username.strip()
        previous = self.account or {}
        device_id = previous.get("device_id") or f"podflow-{uuid.uuid4().hex[:10]}"
        caption = self.device_caption()

        def work() -> str:
            client = gpodder.make_client(provider, server, username, password, device_id)
            client.login(caption)
            return self.store.store(server, username, password)

        def done(storage: str) -> None:
            self.db.delete_settings(*_STATE_KEYS)
            self.db.set_setting(_ACCOUNT, {"provider": provider, "server": server,
                                           "username": username, "device_id": device_id,
                                           "storage": storage})
            self._password = password
            self.db.sync_clear()
            self.db.sync_recording = True
            self.db.sync_queue_history()
            self.emit("changed")
            on_done()
            self.sync_now(manual=False)

        tasks.run_async(work, on_done=done, on_error=on_error)

    def sign_out(self) -> None:
        account = self.account
        self.db.set_setting(_ACCOUNT, None)
        self.db.delete_settings(*_STATE_KEYS)
        self.db.sync_recording = False
        self.db.sync_clear()
        self._password = None
        if account is not None:
            tasks.run_async(self.store.clear, account["server"], account["username"])
        self.emit("changed")

    # -- scheduling ------------------------------------------------------------------

    def start(self) -> None:
        """Begin automatic syncing (call once the window is up)."""
        if self._timer == 0:
            self._timer = GLib.timeout_add_seconds(AUTO_INTERVAL, self._on_timer)
        self.schedule(6)

    def stop(self) -> None:
        for attr in ("_timer", "_pending"):
            source = getattr(self, attr)
            if source:
                GLib.source_remove(source)
                setattr(self, attr, 0)

    def _on_timer(self) -> bool:
        self.schedule(0)
        return GLib.SOURCE_CONTINUE

    def schedule(self, delay: int) -> None:
        """Debounced automatic sync."""
        if not self.configured or not self.auto_sync or config.OFFLINE:
            return
        since_last = time.monotonic() - self._last_finished
        delay = max(delay, int(MIN_GAP - since_last) if since_last < MIN_GAP else 0)
        if self._pending:
            GLib.source_remove(self._pending)

        def fire() -> bool:
            self._pending = 0
            self.sync_now(manual=False)
            return GLib.SOURCE_REMOVE

        self._pending = GLib.timeout_add_seconds(max(1, delay), fire)

    # -- syncing -----------------------------------------------------------------------

    def sync_now(self, manual: bool = True) -> bool:
        account = self.account
        if account is None or self.syncing:
            return False
        if config.OFFLINE or not self.library.online:
            if manual:
                self.emit("finished", _("Você está offline"), True, None)
            return False
        self.syncing = True
        self.emit("changed")
        current = self.playback.current.id if self.playback and self.playback.current else None

        def finish(error: str, summary: SyncSummary | None) -> None:
            self.syncing = False
            self._last_finished = time.monotonic()
            self.db.set_setting("sync_error", error)
            if not error:
                self.db.set_setting("sync_last", int(time.time()))
            self.emit("changed")
            self.emit("finished", error, manual, summary)

        def failed(error: BaseException) -> None:
            if not isinstance(error, (gpodder.SyncError, http.NetworkError)):
                log.warning("Sincronização falhou", exc_info=error)
            message = str(error) or type(error).__name__
            if isinstance(error, http.NetworkError) and not isinstance(error, http.OfflineError):
                message = _("Não foi possível conectar ao servidor")
            finish(message, None)

        tasks.run_async(self._run, dict(account), current,
                        on_done=lambda summary: finish("", summary), on_error=failed)
        return True

    def _run(self, account: dict[str, str], current_id: str | None) -> SyncSummary:
        password = self._password or self.store.lookup(account["server"], account["username"])
        if not password:
            raise gpodder.AuthError(_("Entre novamente na conta para continuar sincronizando"))
        self._password = password
        client = gpodder.make_client(account["provider"], account["server"], account["username"],
                                     password, account["device_id"])
        summary = SyncSummary()
        self._sync_subscriptions(client, summary)
        self._sync_episodes(client, account["device_id"], current_id, summary)
        return summary

    def _sync_subscriptions(self, client: gpodder.SyncClient, summary: SyncSummary) -> None:
        db = self.db
        since = int(db.get_setting("sync_subs_since", 0) or 0)
        first = since == 0
        known = set(db.get_setting("sync_subs_known", []) or [])
        aliases: dict[str, str] = dict(db.get_setting("sync_aliases", {}) or {})
        pending = set(db.get_setting("sync_pending_feeds", []) or [])

        changes = client.get_subscriptions(since)
        local = {aliases.get(url, url): pid for url, pid in db.subscribed_feeds().items()}
        if first and changes.add:
            # An existing account: keep the pre-installed catalog on this computer
            # instead of pushing a dozen shows to the user's other devices.
            known.update(url for url in db.seed_feeds() if url in local)

        for url in changes.remove:
            known.discard(url)
            pending.discard(url)
            if first:  # a first sync only ever adds: merge both libraries
                continue
            podcast_id = local.pop(url, None)
            if podcast_id is not None:
                db.set_subscribed(podcast_id, False)
                summary.removed += 1

        pending.update(changes.add)
        for url in sorted(pending):
            known.add(url)
            if url in local:
                pending.discard(url)
                continue
            podcast = db.find_podcast_by_feed(url)
            try:
                if podcast is None:
                    podcast = self.library.add_feed_now(url)
            except (http.NetworkError, FeedError) as error:
                log.info("Não foi possível seguir %s: %s", url, error)
                summary.failed_feeds += 1
                continue
            if podcast.feed_url and podcast.feed_url != url:
                aliases[podcast.feed_url] = url
            if not podcast.subscribed:
                db.set_subscribed(podcast.id, True)
                summary.added += 1
            local[url] = podcast.id
            pending.discard(url)

        to_add = sorted(url for url in local if url not in known)
        to_remove = [] if first else sorted(url for url in known
                                             if url not in local and url not in pending)
        timestamp = changes.timestamp
        if to_add or to_remove:
            result = client.upload_subscriptions(to_add, to_remove)
            known.update(to_add)
            known.difference_update(to_remove)
            for old, new in result.update_urls:
                if old in known:
                    known.discard(old)
                    known.add(new)
                for local_url, server_url in list(aliases.items()):
                    if server_url == old:
                        aliases[local_url] = new
                if old not in aliases.values():
                    aliases[old] = new
            timestamp = result.timestamp or timestamp
        db.set_setting("sync_subs_known", sorted(known))
        db.set_setting("sync_aliases", aliases)
        db.set_setting("sync_pending_feeds", sorted(pending))
        db.set_setting("sync_subs_since", max(1, timestamp))

    def _sync_episodes(self, client: gpodder.SyncClient, device_id: str, current_id: str | None,
                       summary: SyncSummary) -> None:
        db = self.db
        since = int(db.get_setting("sync_actions_since", 0) or 0)
        actions, timestamp = client.get_episode_actions(since)

        newest: dict[str, tuple[int, dict[str, Any]]] = {}
        for action in actions:
            key = str(action.get("episode") or action.get("guid") or "")
            at = parse_timestamp(action.get("timestamp"))
            if key and (key not in newest or at >= newest[key][0]):
                newest[key] = (at, action)
        for at, action in newest.values():
            if action.get("device") == device_id:
                continue
            if self._apply_action(action, at, current_id):
                summary.applied += 1

        aliases: dict[str, str] = dict(db.get_setting("sync_aliases", {}) or {})
        for _batch in range(50):
            rows = db.sync_pending(limit=BATCH)
            if not rows:
                break
            documents = [doc for doc in (self._action_document(r, device_id, aliases)
                                         for r in rows) if doc is not None]
            if documents:
                client.upload_episode_actions(documents)
                summary.uploaded += len(documents)
            db.sync_done(rows)
            if len(rows) < BATCH:
                break
        db.set_setting("sync_actions_since", max(1, timestamp))

    def _apply_action(self, action: dict[str, Any], at: int, current_id: str | None) -> bool:
        kind = str(action.get("action") or "").lower()
        if kind not in ("play", "new") or at <= 0:
            return False
        episode = self.db.find_episode_for_sync(str(action.get("podcast") or ""),
                                                str(action.get("episode") or ""),
                                                str(action.get("guid") or ""))
        if episode is None or episode.id == current_id:
            return False
        if at <= max(episode.last_played_at or 0, episode.played_at or 0):
            return False
        if kind == "new":
            if not episode.played:
                return False
            self.db.set_played(episode.id, False, record=False)
            return True
        position = _int(action.get("position"))
        if position < 0:
            return False
        total = _int(action.get("total"))
        total = total if total > 0 else episode.duration
        if total and position >= total - PLAYED_TAIL:
            if episode.played:
                return False
            self.db.set_played(episode.id, True, record=False, at=at)
            return True
        if not episode.played and abs(episode.position - position) < 3:
            return False
        self.db.apply_remote_progress(episode.id, float(position), at)
        return True

    @staticmethod
    def _action_document(row: dict[str, Any], device_id: str,
                         aliases: dict[str, str]) -> dict[str, Any] | None:
        if not row["feed_url"] or not row["audio_url"]:
            return None
        document: dict[str, Any] = {
            "podcast": aliases.get(row["feed_url"], row["feed_url"]),
            "episode": row["audio_url"],
            "action": row["action"],
            "timestamp": iso_timestamp(row["created_at"]),
            "device": device_id,
        }
        if row["guid"]:
            document["guid"] = row["guid"]
        if row["action"] == "play":
            position = int(row["position"])
            if position < 1:
                return None
            document["started"] = max(0, min(int(row["started"]), position))
            document["position"] = position
            if int(row["total"]) > 0:
                document["total"] = max(int(row["total"]), position)
        return document


__all__ = ["SyncManager", "SyncSummary", "KEYRING", "FILE", "iso_timestamp", "parse_timestamp"]
