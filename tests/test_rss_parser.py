import unittest
from datetime import datetime, timezone

from tests import FIXTURES
from src.api.rss_parser import FeedError, parse_date, parse_feed
from src.models import make_episode_id


class RssParserTests(unittest.TestCase):
    def setUp(self):
        data = (FIXTURES / "sample_feed.xml").read_bytes()
        self.feed = parse_feed(data, "https://exemplo.com.br/feed.xml", podcast_id="feed:test")

    def test_channel(self):
        podcast = self.feed.podcast
        self.assertEqual(podcast.title, "Café com Código")
        self.assertEqual(podcast.author, "Estúdio Exemplo")
        self.assertEqual(podcast.artwork_url, "https://exemplo.com.br/capa.jpg")
        self.assertEqual(podcast.genre, "Tech News")
        self.assertEqual(podcast.website, "https://exemplo.com.br/podcast")
        self.assertEqual(podcast.description, "Conversas sobre tecnologia & carreira.")
        self.assertFalse(podcast.explicit)

    def test_episodes(self):
        titles = [e.title for e in self.feed.episodes]
        # Sorted newest first; the text-only post and the duplicate are skipped.
        self.assertEqual(titles, ["Episódio 3 — O futuro do Linux", "Episódio 2", "Episódio 1"])
        self.assertEqual(self.feed.podcast.episode_count, 3)

        latest = self.feed.episodes[0]
        self.assertEqual(latest.guid, "ep-003")
        self.assertEqual(latest.id, make_episode_id("feed:test", "ep-003"))
        self.assertEqual(latest.duration, 3723)
        self.assertEqual(latest.audio_url, "https://exemplo.com.br/ep3.mp3")
        self.assertEqual(latest.file_size, 45000000)
        self.assertEqual(latest.season, 2)
        self.assertEqual(latest.number, 3)
        self.assertTrue(latest.explicit)
        self.assertEqual(latest.artwork_url, "https://exemplo.com.br/ep3.jpg")
        self.assertEqual(latest.chapters_url, "https://exemplo.com.br/ep3-chapters.json")
        self.assertIn("<b>GNOME</b>", latest.description)
        self.assertEqual(latest.summary, "Resumo curto do episódio 3.")
        expected = datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(latest.published, int(expected))

        second = self.feed.episodes[1]
        self.assertEqual(second.guid, "https://exemplo.com.br/ep2.m4a")
        self.assertEqual(second.duration, 2730)
        self.assertEqual(second.mime_type, "audio/mp4")

    def test_repairs_broken_xml(self):
        broken = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
        <rss><channel><title>Not\xedcias &amp; Cia &nbsp; R&D</title>
        <item><title>Ep\x01 1</title><enclosure url="https://x/1.mp3?a=1&b=2" type="audio/mpeg"/>
        </item></channel></rss>"""
        feed = parse_feed(broken, "https://x/feed")
        self.assertEqual(feed.podcast.title, "Notícias & Cia \xa0 R&D")
        self.assertEqual(len(feed.episodes), 1)
        self.assertEqual(feed.episodes[0].audio_url, "https://x/1.mp3?a=1&b=2")

    def test_rejects_non_rss(self):
        with self.assertRaises(FeedError):
            parse_feed(b"<html><body>404</body></html>", "https://x")
        with self.assertRaises(FeedError):
            parse_feed(b"   ", "https://x")

    def test_dates(self):
        self.assertEqual(parse_date("Fri, 25 Sep 2026 18:12:00 -0000"),
                         int(datetime(2026, 9, 25, 18, 12, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(parse_date("2026-09-23T10:30:00Z"),
                         int(datetime(2026, 9, 23, 10, 30, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(parse_date("ontem"), 0)
        self.assertEqual(parse_date(None), 0)


if __name__ == "__main__":
    unittest.main()
