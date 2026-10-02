import json
import os
import unittest

from tests import FIXTURES
from src import config
from src.api import apple_service as apple
from src.api import genres, http
from src.api.itunes_service import (ItunesService, artwork_url, episode_from_itunes,
                                    podcast_from_itunes)
from src.core.database import Database
from src.models import make_episode_id


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ChartParsingTests(unittest.TestCase):
    def test_v2_podcasts(self):
        items = apple.parse_v2_podcasts(load("v2_top_podcasts.json"))
        self.assertEqual(len(items), 3)
        self.assertEqual([i.rank for i in items], [1, 2, 3])
        first = items[0]
        self.assertTrue(first.title)
        self.assertGreater(first.itunes_id, 0)
        self.assertEqual(first.podcast_id, f"itunes:{first.itunes_id}")
        self.assertIn("mzstatic.com", first.artwork_url)

    def test_v2_episodes(self):
        items = apple.parse_v2_episodes(load("v2_top_episodes.json"))
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertGreater(item.itunes_track_id, 1_000_000_000)
            self.assertIsNotNone(item.podcast_itunes_id, item.apple_url)
        # Subgenre names map to the top-level genre used by the filter.
        self.assertEqual(items[0].genre, "Política")
        self.assertEqual(items[0].genre_id, 1489)

    def test_legacy_chart(self):
        items = apple.parse_legacy_chart(load("legacy_genre_chart.json"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].itunes_id, 1763888661)
        self.assertEqual(items[0].genre_id, 1318)
        self.assertTrue(items[0].summary)
        self.assertTrue(items[0].artwork_url.endswith("170x170bb.png"))

    def test_single_legacy_entry(self):
        data = load("legacy_genre_chart.json")
        data["feed"]["entry"] = data["feed"]["entry"][0]
        self.assertEqual(len(apple.parse_legacy_chart(data)), 1)

    def test_urls(self):
        self.assertEqual(apple.parse_apple_url(
            "https://podcasts.apple.com/br/podcast/x/id1502134265?i=1000792512499"),
            (1502134265, 1000792512499))
        self.assertEqual(apple.parse_apple_url("https://podcasts.apple.com/br/podcast/x/id42"),
                         (42, None))
        self.assertEqual(apple.parse_apple_url(None), (None, None))
        self.assertEqual(artwork_url("https://is1-ssl.mzstatic.com/a/b.jpg/100x100bb.png", 600),
                         "https://is1-ssl.mzstatic.com/a/b.jpg/600x600bb.jpg")
        self.assertEqual(artwork_url("https://cdn.example/c.png", 600), "https://cdn.example/c.png")

    def test_genre_map(self):
        self.assertEqual(genres.top_level(1496), 1303)
        self.assertEqual(genres.top_level(1318), 1318)
        self.assertIsNone(genres.top_level(1311))
        self.assertEqual(genres.top_level_for_name("Crimes verídicos"), 1488)
        self.assertEqual(genres.top_level_for_name("notícias diárias"), 1489)
        self.assertNotIn(1311, genres.FEATURED_GENRES)
        self.assertNotIn(1315, genres.FEATURED_GENRES)
        self.assertEqual(len(genres.FEATURED_GENRES), 8)


class ItunesParsingTests(unittest.TestCase):
    def test_lookup(self):
        results = load("itunes_lookup.json")["results"]
        podcast = podcast_from_itunes(results[0])
        self.assertEqual(podcast.id, "itunes:381816509")
        self.assertEqual(podcast.title, "NerdCast")
        self.assertTrue(podcast.feed_url.startswith("https://"))
        self.assertEqual(podcast.genre_id, 1303)

        episode = episode_from_itunes(results[1], podcast.id)
        self.assertTrue(episode.audio_url.startswith("https://"))
        self.assertGreater(episode.duration, 60)
        self.assertGreater(episode.published, 1_700_000_000)
        # Same key as the RSS item with the same <guid>.
        self.assertEqual(episode.id, make_episode_id(podcast.id, results[1]["episodeGuid"]))


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.service = apple.AppleService(self.db)

    def tearDown(self):
        self.db.close()

    def test_stale_while_revalidate(self):
        calls = []

        def fetch():
            calls.append(1)
            return [apple.ChartPodcast(rank=1, itunes_id=1, title="Um")]

        first = self.service._cached("k", 3600, fetch, force=False)
        self.assertFalse(first.from_cache)
        second = self.service._cached("k", 3600, fetch, force=False)
        self.assertTrue(second.from_cache)
        self.assertEqual(len(calls), 1)
        self.assertEqual(second.items[0].title, "Um")

        def offline():
            raise http.NetworkError("offline")

        stale = self.service._cached("k", 0, offline, force=True)
        self.assertTrue(stale.from_cache)
        self.assertIsInstance(stale.error, http.NetworkError)
        with self.assertRaises(http.NetworkError):
            self.service._cached("missing", 0, offline, force=True)

    def test_offline_flag_blocks_requests(self):
        if not config.OFFLINE:
            self.skipTest("modo online (PODFLOW_OFFLINE=0)")
        with self.assertRaises(http.OfflineError):
            http.get_bytes("https://example.com")


@unittest.skipUnless(os.environ.get("PODFLOW_LIVE_TESTS") == "1", "PODFLOW_LIVE_TESTS=1 para rodar")
class LiveApiTests(unittest.TestCase):
    def setUp(self):
        self._offline = config.OFFLINE
        config.OFFLINE = False

    def tearDown(self):
        config.OFFLINE = self._offline

    def test_live_endpoints(self):
        service = apple.AppleService(None, ItunesService())
        self.assertEqual(len(service.fetch_top_podcasts()), 50)
        self.assertGreater(len(service.fetch_genre_chart(1318)), 10)
        podcast, episodes = service.itunes.lookup_podcast_with_episodes(381816509, limit=5)
        self.assertEqual(podcast.title, "NerdCast")
        self.assertTrue(episodes)


if __name__ == "__main__":
    unittest.main()
