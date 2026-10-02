import tempfile
import time
import unittest
from pathlib import Path

import tests  # noqa: F401
from gi.repository import Gst

from src.core.database import Database
from src.core.playback import PlaybackManager
from src.core.player import AudioPlayer, PlayerState
from src.models import Episode, Podcast, make_episode_id
from tests.helpers import make_wav, run_until


class AudioPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gst.init(None)
        cls.tmp = Path(tempfile.mkdtemp(prefix="podflow-audio-"))
        cls.uri = Gst.filename_to_uri(str(make_wav(cls.tmp / "tone.wav", seconds=3.0)))

    def setUp(self):
        self.player = AudioPlayer(audio_sink="fakesink")
        self.events = []
        self.player.connect("eos", lambda *_: self.events.append("eos"))
        self.player.connect("error", lambda _p, msg: self.events.append(f"error:{msg}"))

    def tearDown(self):
        self.player.shutdown()

    def test_play_seek_and_eos(self):
        self.player.load(self.uri)
        self.assertTrue(run_until(lambda: self.player.state == PlayerState.PLAYING, 5))
        self.assertTrue(run_until(lambda: abs(self.player.duration - 3.0) < 0.2, 3))
        self.player.seek(2.0)
        self.assertTrue(run_until(lambda: self.player.position >= 2.0, 3))
        self.assertTrue(run_until(lambda: "eos" in self.events, 5), self.events)
        self.assertEqual(self.player.state, PlayerState.STOPPED)

    def test_pending_seek_and_rate(self):
        self.player.set_rate(2.0)
        started = time.monotonic()
        self.player.load(self.uri, start=1.0)
        self.assertTrue(run_until(lambda: "eos" in self.events, 6), self.events)
        # 2 s of audio left at 2× speed: about one second of wall clock.
        self.assertLess(time.monotonic() - started, 2.6)

    def test_pause_resume(self):
        self.player.load(self.uri)
        self.assertTrue(run_until(lambda: self.player.state == PlayerState.PLAYING, 5))
        self.player.pause()
        self.assertTrue(run_until(lambda: self.player.state == PlayerState.PAUSED, 3))
        paused_at = self.player.position
        run_until(lambda: False, 0.4)
        self.assertAlmostEqual(self.player.position, paused_at, delta=0.05)
        self.player.play()
        self.assertTrue(run_until(lambda: self.player.state == PlayerState.PLAYING, 3))

    def test_missing_file_reports_error(self):
        self.player.load(Gst.filename_to_uri(str(self.tmp / "nao-existe.mp3")))
        self.assertTrue(run_until(lambda: any(e.startswith("error") for e in self.events), 5))
        self.assertEqual(self.player.state, PlayerState.STOPPED)


class PlaybackManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gst.init(None)
        cls.tmp = Path(tempfile.mkdtemp(prefix="podflow-playback-"))
        cls.files = [make_wav(cls.tmp / f"ep{i}.wav", seconds=1.5) for i in range(2)]

    def setUp(self):
        self.db = Database(":memory:")
        self.db.upsert_podcast(Podcast(id="feed:p", title="Programa"))
        self.episodes = []
        for index, path in enumerate(self.files):
            ep = Episode(id=make_episode_id("feed:p", f"g{index}"), podcast_id="feed:p",
                         guid=f"g{index}", title=f"Ep {index}", audio_url=Gst.filename_to_uri(str(path)),
                         published=1000 - index, duration=2)
            self.episodes.append(ep)
        self.db.upsert_episodes("feed:p", self.episodes)
        self.manager = PlaybackManager(self.db, AudioPlayer(audio_sink="fakesink"))
        self.changes = []
        self.manager.connect("episode-changed", lambda _m, ep: self.changes.append(ep.id))

    def tearDown(self):
        self.manager.shutdown()
        self.db.close()

    def test_queue_advances_and_marks_played(self):
        first, second = self.episodes
        self.manager.play_episode(first)
        self.manager.enqueue(second)
        self.assertTrue(run_until(lambda: self.changes == [first.id, second.id], 8), self.changes)
        self.assertTrue(self.db.get_episode(first.id).played)
        self.assertEqual(self.db.queue_ids(), [])
        self.assertTrue(run_until(lambda: self.db.get_episode(second.id).played, 6))

    def test_sleep_timer_end_of_episode_stops(self):
        first, second = self.episodes
        self.manager.play_episode(first)
        self.manager.enqueue(second)
        self.manager.set_sleep_timer(end_of_episode=True)
        self.assertTrue(run_until(lambda: self.db.get_episode(first.id).played, 6))
        run_until(lambda: False, 0.5)
        self.assertEqual(self.changes, [first.id])
        self.assertIsNone(self.manager.sleep_mode)
        self.assertEqual(self.db.queue_ids(), [second.id])

    def test_progress_saved_on_pause_and_resume_position(self):
        first = self.episodes[0]
        self.manager.play_episode(first)
        self.assertTrue(run_until(lambda: self.manager.player.position > 0.4, 5))
        self.manager.pause()
        saved = self.db.get_episode(first.id).position
        self.assertGreater(saved, 0.3)
        self.assertEqual(self.db.get_setting("last_episode"), first.id)
        self.assertEqual([e.id for e in self.db.list_history()], [first.id])

    def test_restore_session_is_lazy(self):
        first = self.episodes[0]
        self.db.save_progress(first.id, 0.5, 2)
        self.db.set_setting("last_episode", first.id)
        self.manager.restore_session()
        self.assertEqual(self.manager.current.id, first.id)
        self.assertEqual(self.manager.state, PlayerState.PAUSED)
        self.assertAlmostEqual(self.manager.position, 0.5)
        self.assertEqual(self.manager.player.state, PlayerState.STOPPED)  # nothing loaded yet

    def test_rate_and_volume_persist(self):
        self.manager.set_rate(1.5)
        self.assertEqual(self.db.get_setting("rate"), 1.5)
        self.manager.set_rate(9)
        self.assertEqual(self.manager.rate, 2.5)
        self.manager.set_volume(0.4)
        self.assertTrue(run_until(lambda: self.db.get_setting("volume") == 0.4, 2))


if __name__ == "__main__":
    unittest.main()
