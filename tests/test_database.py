import time
import unittest

import tests  # noqa: F401
from src.core import seed_data
from src.core.database import Database
from src.models import Episode, Podcast, make_episode_id


def episode(podcast_id, guid, title, published, duration=1800):
    return Episode(id=make_episode_id(podcast_id, guid), podcast_id=podcast_id, guid=guid,
                   title=title, audio_url=f"https://x/{guid}.mp3", published=published,
                   duration=duration)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.podcast = self.db.upsert_podcast(Podcast(id="feed:abc", title="Teste",
                                                      author="Autor", feed_url="https://x/f"))
        now = int(time.time())
        self.episodes = [episode("feed:abc", f"g{i}", f"Ep {i}", now - i * 86400) for i in range(5)]
        self.db.upsert_episodes("feed:abc", self.episodes)

    def tearDown(self):
        self.db.close()

    def test_seed(self):
        subscriptions = self.db.list_subscriptions()
        self.assertEqual(len(subscriptions), len(seed_data.SEED_PODCASTS))
        self.assertTrue(all(p.is_seed for p in subscriptions))
        nerdcast = self.db.get_podcast("itunes:381816509")
        self.assertEqual(nerdcast.title, "NerdCast")
        self.assertEqual(nerdcast.accent_color, "#7d4f2e")
        # Seeding again (e.g. next launch) is a no-op and keeps user choices.
        self.db.set_subscribed("itunes:381816509", False)
        self.db._seed()
        self.assertFalse(self.db.get_podcast("itunes:381816509").subscribed)

    def test_upsert_podcast_keeps_existing_text(self):
        self.db.upsert_podcast(Podcast(id="feed:abc", title="", author="", description="Nova"))
        stored = self.db.get_podcast("feed:abc")
        self.assertEqual(stored.title, "Teste")
        self.assertEqual(stored.author, "Autor")
        self.assertEqual(stored.description, "Nova")

    def test_upsert_episodes_preserves_state(self):
        target = self.episodes[1]
        self.db.save_progress(target.id, 321.5, 1800)
        self.db.set_saved(target.id, True)
        renamed = episode("feed:abc", "g1", "Ep 1 (corrigido)", target.published)
        renamed.description = "<p>notas longas</p>"
        new = self.db.upsert_episodes("feed:abc", [renamed, episode("feed:abc", "g9", "Novo", 1)])
        self.assertEqual(new, 1)
        stored = self.db.get_episode(target.id)
        self.assertEqual(stored.title, "Ep 1 (corrigido)")
        self.assertEqual(stored.position, 321.5)
        self.assertTrue(stored.saved)
        self.assertEqual(stored.description, "<p>notas longas</p>")
        self.assertEqual(self.db.count_episodes("feed:abc"), 6)

    def test_listing_and_filters(self):
        listed = self.db.list_episodes("feed:abc", limit=3)
        self.assertEqual([e.title for e in listed], ["Ep 0", "Ep 1", "Ep 2"])
        self.assertEqual(listed[0].podcast_title, "Teste")
        self.db.set_played(self.episodes[0].id, True)
        self.assertEqual(self.db.count_episodes("feed:abc", "unplayed"), 4)
        self.assertEqual(self.db.latest_episode("feed:abc", unplayed=True).title, "Ep 1")
        self.db.set_download(self.episodes[2].id, "done", "/tmp/x.mp3", 10)
        self.assertEqual([e.title for e in self.db.list_episodes("feed:abc", "downloaded")],
                         ["Ep 2"])

    def test_progress_history_and_played(self):
        first, second = self.episodes[0].id, self.episodes[1].id
        self.db.save_progress(first, 120, 1800)
        self.db.mark_started(first)
        self.db.save_progress(second, 5, 1800)  # below the "in progress" threshold
        self.assertEqual([e.id for e in self.db.list_in_progress()], [first])
        self.assertEqual([e.id for e in self.db.list_history()], [first])
        self.db.set_played(first, True)
        stored = self.db.get_episode(first)
        self.assertTrue(stored.played)
        self.assertEqual(stored.position, 0)
        self.assertEqual(self.db.list_in_progress(), [])
        self.db.clear_history()
        self.assertEqual(self.db.list_history(), [])

    def test_queue(self):
        ids = [e.id for e in self.episodes]
        self.db.queue_add(ids[0])
        self.db.queue_add(ids[1])
        self.db.queue_add(ids[2], front=True)
        self.assertEqual(self.db.queue_ids(), [ids[2], ids[0], ids[1]])
        self.db.queue_move(ids[1], 0)
        self.assertEqual(self.db.queue_ids(), [ids[1], ids[2], ids[0]])
        self.db.queue_add(ids[2])  # re-adding moves it to the end
        self.assertEqual(self.db.queue_ids(), [ids[1], ids[0], ids[2]])
        self.assertTrue(self.db.in_queue(ids[0]))
        self.db.queue_remove(ids[0])
        self.assertFalse(self.db.in_queue(ids[0]))
        self.assertEqual([e.id for e in self.db.queue_list()], [ids[1], ids[2]])
        self.db.queue_clear()
        self.assertEqual(self.db.queue_ids(), [])

    def test_new_episode_badge(self):
        self.db.set_subscribed("feed:abc", True)
        self.assertEqual(self.db.get_podcast("feed:abc").new_count, 0)
        fresh = episode("feed:abc", "fresh", "Recém-saído", int(time.time()) + 60)
        self.db.upsert_episodes("feed:abc", [fresh])
        self.assertEqual(self.db.get_podcast("feed:abc").new_count, 1)
        self.assertIn(fresh.id, [e.id for e in self.db.list_new_episodes()])
        self.db.mark_podcast_seen("feed:abc")
        time.sleep(0.01)

    def test_downloads_reset_and_signals(self):
        received = []
        self.db.connect("downloads-changed", lambda *_: received.append("downloads"))
        self.db.set_download(self.episodes[0].id, "downloading")
        self.assertEqual(self.db.reset_interrupted_downloads(), [self.episodes[0].id])
        self.assertEqual(self.db.get_episode(self.episodes[0].id).download_state, "")
        self.assertIn("downloads", received)

    def test_cache_settings_search(self):
        self.db.cache_put("k", [{"a": 1}])
        payload, fetched_at = self.db.cache_get("k")
        self.assertEqual(payload, [{"a": 1}])
        self.assertGreater(fetched_at, 0)
        self.assertIsNone(self.db.cache_get("missing"))
        self.db.set_setting("rate", 1.5)
        self.assertEqual(self.db.get_setting("rate"), 1.5)
        self.assertEqual(self.db.get_setting("nope", "x"), "x")
        podcasts, episodes = self.db.search_local("Ep 3")
        self.assertEqual([e.title for e in episodes], ["Ep 3"])
        podcasts, _ = self.db.search_local("nerd")
        self.assertIn("NerdCast", [p.title for p in podcasts])


if __name__ == "__main__":
    unittest.main()
