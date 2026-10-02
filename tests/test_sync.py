"""Sync against an in-process fake gPodder / Nextcloud server."""

import base64
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from tests import FIXTURES
from src import config
from src.api import gpodder
from src.core.database import Database
from src.core.library import Library
from src.core.secrets import FILE, CredentialStore
from src.core.sync import SyncManager, iso_timestamp, parse_timestamp
from src.models import Podcast
from tests.helpers import run_until

USER, PASSWORD = "maria", "s3gredo"
OTHER_DEVICE = "android-phone"


class FakeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.clock = 100
        self.subscriptions: dict[str, tuple[str, int]] = {}
        self.actions: list[dict] = []
        self.polls = 0
        self.lock = threading.Lock()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def tick(self) -> int:
        self.clock += 1
        return self.clock

    def subscribe(self, url: str, state: str = "add") -> None:
        with self.lock:
            self.subscriptions[url] = (state, self.tick())

    def active(self) -> set[str]:
        return {url for url, (state, _ts) in self.subscriptions.items() if state == "add"}


class Handler(BaseHTTPRequestHandler):
    server: FakeServer

    def log_message(self, *_args):
        pass

    def _send(self, status: int, payload=None, raw: bytes | None = None) -> None:
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None
                                            else b"")
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == expected

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(length) if length else b""
        if self.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(data or b"null")
        return urllib.parse.parse_qs(data.decode())

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str) -> None:
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        since = int(query.get("since", ["0"])[0])
        path = url.path
        server = self.server
        if path == "/feed.xml":
            return self._send(200, raw=(FIXTURES / "sample_feed.xml").read_bytes())
        if path == "/index.php/login/v2" and method == "POST":
            return self._send(200, {"poll": {"token": "tok", "endpoint":
                                             server.base + "/index.php/login/v2/poll"},
                                    "login": server.base + "/login/v2/flow/abc"})
        if path == "/index.php/login/v2/poll" and method == "POST":
            server.polls += 1
            if self._body().get("token") != ["tok"] or server.polls < 2:
                return self._send(404)
            return self._send(200, {"server": server.base, "loginName": USER,
                                    "appPassword": PASSWORD})
        if not self._authorized():
            return self._send(401)

        nextcloud = path.startswith("/index.php/apps/gpoddersync/")
        if path.startswith("/api/2/auth/") or path.startswith("/api/2/devices/"):
            return self._send(200)
        if path.startswith("/api/2/subscriptions/") or path.endswith("/subscriptions") \
                or path.endswith("/subscription_change/create"):
            if method == "GET":
                with server.lock:
                    items = [(u, st) for u, (st, ts) in server.subscriptions.items() if ts > since]
                    return self._send(200, {"add": [u for u, st in items if st == "add"],
                                            "remove": [u for u, st in items if st == "remove"],
                                            "timestamp": server.clock})
            body = self._body()
            for url_ in body.get("add", []):
                server.subscribe(url_)
            for url_ in body.get("remove", []):
                server.subscribe(url_, "remove")
            return self._send(200, {"timestamp": server.clock, "update_urls": []})
        if path.startswith("/api/2/episodes/") or (nextcloud and "episode_action" in path):
            if method == "GET":
                with server.lock:
                    actions = [dict(a) for a in server.actions if a["_ts"] > since]
                for action in actions:
                    action.pop("_ts")
                return self._send(200, {"actions": actions, "timestamp": server.clock})
            with server.lock:
                for action in self._body():
                    action["_ts"] = server.tick()
                    server.actions.append(action)
            return self._send(200, {"timestamp": server.clock, "update_urls": []})
        return self._send(404)


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        patcher = mock.patch.object(config, "OFFLINE", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.feed_url = self.server.base + "/feed.xml"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()


class ClientTests(SyncTestCase):
    def test_gpodder_login_and_errors(self):
        client = gpodder.make_client(gpodder.GPODDER_NET, self.server.base, USER, PASSWORD, "d1")
        client.login("PodFlow em teste")
        wrong = gpodder.make_client(gpodder.CUSTOM, self.server.base, USER, "errada", "d1")
        with self.assertRaises(gpodder.AuthError):
            wrong.login("PodFlow")
        missing = gpodder.make_client(gpodder.CUSTOM, self.server.base + "/nada", USER,
                                      PASSWORD, "d1")
        with self.assertRaises(gpodder.ServiceMissingError):
            missing.get_subscriptions(0)

    def test_nextcloud_client_and_login_flow(self):
        client = gpodder.make_client(gpodder.NEXTCLOUD, self.server.base + "/index.php/", USER,
                                     PASSWORD, "d1")
        self.assertEqual(client.server, self.server.base)
        client.login("PodFlow")
        client.upload_subscriptions([self.feed_url], [])
        self.assertEqual(client.get_subscriptions(0).add, [self.feed_url])

        flow = gpodder.nextcloud_login_start(self.server.base)
        self.assertTrue(flow.login_url.endswith("/login/v2/flow/abc"))
        self.assertIsNone(gpodder.nextcloud_login_poll(flow))
        credentials = gpodder.nextcloud_login_poll(flow)
        self.assertEqual(credentials, {"server": self.server.base, "username": USER,
                                       "password": PASSWORD})

    def test_normalize_server(self):
        self.assertEqual(gpodder.normalize_server("", gpodder.GPODDER_NET), "https://gpodder.net")
        self.assertEqual(gpodder.normalize_server("nuvem.exemplo.com/"),
                         "https://nuvem.exemplo.com")
        self.assertEqual(gpodder.normalize_server("https://x.org/nc/index.php"),
                         "https://x.org/nc")

    def test_timestamps(self):
        self.assertEqual(iso_timestamp(0), "1970-01-01T00:00:00")
        self.assertEqual(parse_timestamp("2009-12-12T09:00:00"), 1260608400)
        self.assertEqual(parse_timestamp("2009-12-12T09:00:00Z"), 1260608400)
        self.assertEqual(parse_timestamp("ontem"), 0)


class SyncManagerTests(SyncTestCase):
    provider = gpodder.GPODDER_NET

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="podflow-sync-"))
        self.db = Database(":memory:", seed=False)
        self.library = Library(self.db)
        self.library._online = False  # no background syncs: the tests drive them
        self.store = CredentialStore(self.tmp / "credentials.json")
        self.sync = SyncManager(self.db, self.library, store=self.store)
        self.db.upsert_podcast(Podcast(id="feed:local", title="Local",
                                       feed_url="https://local.example/feed"))
        self.db.set_subscribed("feed:local", True)
        self.server.subscribe(self.feed_url)

    def tearDown(self):
        self.sync.stop()
        self.db.close()
        super().tearDown()

    def sign_in(self):
        result = {}
        self.sync.sign_in(self.provider, self.server.base, USER, PASSWORD,
                          on_done=lambda: result.setdefault("ok", True),
                          on_error=lambda e: result.setdefault("error", e))
        self.assertTrue(run_until(lambda: result, 10), "sign_in não terminou")
        self.assertNotIn("error", result)
        self.sync.stop()  # tests drive syncing by hand

    def run_sync(self):
        return self.sync._run(dict(self.sync.account), None)

    def test_subscriptions_round_trip(self):
        self.sign_in()
        self.assertEqual(self.sync.account["storage"], FILE)
        self.assertEqual(self.store.lookup(self.server.base, USER), PASSWORD)

        summary = self.run_sync()
        self.assertEqual(summary.added, 1)
        remote = self.db.find_podcast_by_feed(self.feed_url)
        self.assertTrue(remote.subscribed)
        self.assertGreater(self.db.count_episodes(remote.id), 0)
        self.assertEqual(self.server.active(), {self.feed_url, "https://local.example/feed"})

        # Unfollowing here removes it on the server…
        self.db.set_subscribed("feed:local", False)
        self.run_sync()
        self.assertEqual(self.server.active(), {self.feed_url})

        # …and unfollowing elsewhere removes it here.
        self.server.subscribe(self.feed_url, "remove")
        summary = self.run_sync()
        self.assertEqual(summary.removed, 1)
        self.assertFalse(self.db.get_podcast(remote.id).subscribed)

    def test_seed_catalog_stays_local_on_existing_accounts(self):
        self.db.upsert_podcast(Podcast(id="feed:seed", title="Semente",
                                       feed_url="https://seed.example/feed"))
        self.db._exec("UPDATE podcasts SET is_seed = 1 WHERE id = 'feed:seed'")
        self.db.set_subscribed("feed:seed", True)
        self.sign_in()
        self.run_sync()
        self.run_sync()
        self.assertNotIn("https://seed.example/feed", self.server.active())
        self.assertIn("https://local.example/feed", self.server.active())
        self.assertTrue(self.db.get_podcast("feed:seed").subscribed)

    def test_episode_actions_both_ways(self):
        self.sign_in()
        self.run_sync()
        podcast = self.db.find_podcast_by_feed(self.feed_url)
        episodes = {e.audio_url: e for e in self.db.list_episodes(podcast.id)}
        ep3 = episodes["https://exemplo.com.br/ep3.mp3"]
        ep2 = episodes["https://exemplo.com.br/ep2.m4a"]

        now = int(time.time())
        with self.server.lock:
            for audio, position, total in ((ep3.audio_url, 600, 3723),
                                           (ep2.audio_url, 2730, 2730)):
                self.server.actions.append({
                    "podcast": self.feed_url, "episode": audio, "action": "PLAY",
                    "device": OTHER_DEVICE, "timestamp": iso_timestamp(now), "started": 0,
                    "position": position, "total": total, "_ts": self.server.tick()})
        summary = self.run_sync()
        self.assertEqual(summary.applied, 2)
        self.assertEqual(self.db.get_episode(ep3.id).position, 600)
        self.assertTrue(self.db.get_episode(ep2.id).played)

        # Older news never overwrites newer local progress.
        self.db.save_progress(ep3.id, 900, 3723)
        self.db.mark_started(ep3.id)
        with self.server.lock:
            self.server.actions.append({
                "podcast": self.feed_url, "episode": ep3.audio_url, "action": "play",
                "device": OTHER_DEVICE, "timestamp": iso_timestamp(now - 3600),
                "position": 100, "total": 3723, "_ts": self.server.tick()})
        self.run_sync()
        self.assertEqual(self.db.get_episode(ep3.id).position, 900)

        # Local changes are uploaded once, tagged with this device.
        self.db.set_played(ep3.id, True)
        self.assertEqual(self.db.sync_count_pending(), 1)
        summary = self.run_sync()
        self.assertEqual(summary.uploaded, 1)
        self.assertEqual(self.db.sync_count_pending(), 0)
        mine = [a for a in self.server.actions if a.get("device") == self.sync.account["device_id"]]
        self.assertEqual(mine[-1]["episode"], ep3.audio_url)
        self.assertEqual(mine[-1]["action"], "play")
        self.assertEqual(mine[-1]["position"], mine[-1]["total"])
        self.assertEqual(mine[-1]["podcast"], self.feed_url)

    def test_sign_out_forgets_everything(self):
        self.sign_in()
        self.run_sync()
        self.sync.sign_out()
        self.assertFalse(self.sync.configured)
        self.assertFalse(self.db.sync_recording)
        self.assertIsNone(self.db.get_setting("sync_subs_known"))
        self.assertTrue(run_until(lambda: self.store.lookup(self.server.base, USER) is None, 5))

    def test_wrong_password(self):
        errors = []
        self.sync.sign_in(self.provider, self.server.base, USER, "errada",
                          on_done=lambda: errors.append("ok"), on_error=errors.append)
        self.assertTrue(run_until(lambda: errors, 10))
        self.assertIsInstance(errors[0], gpodder.AuthError)
        self.assertFalse(self.sync.configured)


class NextcloudSyncTests(SyncManagerTests):
    provider = gpodder.NEXTCLOUD


class CredentialStoreTests(unittest.TestCase):
    def test_file_fallback_is_private(self):
        path = Path(tempfile.mkdtemp(prefix="podflow-cred-")) / "credentials.json"
        store = CredentialStore(path)
        self.assertEqual(store.store("https://s", "ana", "x1"), FILE)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(store.lookup("https://s", "ana"), "x1")
        self.assertIsNone(store.lookup("https://s", "bia"))
        store.clear("https://s", "ana")
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
