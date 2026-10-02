import os
import unittest

import tests  # noqa: F401
from gi.repository import Gio, GLib, Gst

from src.core.database import Database
from src.core.mpris import OBJECT_PATH, PLAYER_IFACE, ROOT_IFACE, MprisService
from src.core.playback import PlaybackManager
from src.core.player import AudioPlayer
from src.models import Episode, Podcast, make_episode_id
from tests.helpers import run_until


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "precisa de um barramento D-Bus")
class MprisTests(unittest.TestCase):
    def setUp(self):
        Gst.init(None)
        self.bus_name = f"org.mpris.MediaPlayer2.podflow_test_{os.getpid()}"
        self.db = Database(":memory:")
        self.db.upsert_podcast(Podcast(id="feed:p", title="Programa", author="Autora"))
        self.episode = Episode(id=make_episode_id("feed:p", "g"), podcast_id="feed:p", guid="g",
                               title="Episódio de teste", audio_url="https://x/e.mp3",
                               duration=1200)
        self.db.upsert_episodes("feed:p", [self.episode])
        self.playback = PlaybackManager(self.db, AudioPlayer(audio_sink="fakesink"))
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.mpris = MprisService(self.playback, self.db, bus_name=self.bus_name)
        self.mpris.start(self.connection)
        self.assertTrue(run_until(self._name_owned, 5), "nome MPRIS não registrado")

    def tearDown(self):
        self.mpris.stop()
        self.playback.shutdown()
        self.db.close()

    def _name_owned(self) -> bool:
        reply = self._call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                           "NameHasOwner", GLib.Variant("(s)", (self.bus_name,)))
        return bool(reply and reply.unpack()[0])

    def _call(self, dest, path, iface, method, params=None):
        """Async call + main loop spin (a sync call would block our own handlers)."""
        result = {}

        def done(conn, res):
            try:
                result["value"] = conn.call_finish(res)
            except GLib.Error as error:
                result["error"] = error

        self.connection.call(dest, path, iface, method, params, None, Gio.DBusCallFlags.NONE,
                             5000, None, done)
        run_until(lambda: result, 6)
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _get(self, iface, name):
        reply = self._call(self.bus_name, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", (iface, name)))
        return reply.unpack()[0]

    def test_identity(self):
        self.assertEqual(self._get(ROOT_IFACE, "Identity"), "PodFlow")
        self.assertEqual(self._get(ROOT_IFACE, "DesktopEntry"), "io.github.euvinicios.PodFlow")
        self.assertEqual(self._get(PLAYER_IFACE, "PlaybackStatus"), "Stopped")
        self.assertEqual(self._get(PLAYER_IFACE, "MaximumRate"), 2.5)

    def test_metadata_follows_current_episode(self):
        self.playback.cue(self.db.get_episode(self.episode.id))
        metadata = self._get(PLAYER_IFACE, "Metadata")
        self.assertEqual(metadata["xesam:title"], "Episódio de teste")
        self.assertEqual(metadata["xesam:album"], "Programa")
        self.assertEqual(metadata["xesam:artist"], ["Autora"])
        self.assertEqual(metadata["mpris:length"], 1200 * 1_000_000)
        self.assertTrue(metadata["mpris:trackid"].endswith(self.episode.id))
        self.assertEqual(self._get(PLAYER_IFACE, "PlaybackStatus"), "Paused")

    def test_methods_and_properties(self):
        self.playback.cue(self.db.get_episode(self.episode.id))
        self._call(self.bus_name, OBJECT_PATH, PLAYER_IFACE, "Seek",
                   GLib.Variant("(x)", (90 * 1_000_000,)))
        self.assertAlmostEqual(self.playback.position, 90, delta=0.5)
        self._call(self.bus_name, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Set",
                   GLib.Variant("(ssv)", (PLAYER_IFACE, "Rate", GLib.Variant("d", 1.5))))
        self.assertEqual(self.playback.rate, 1.5)
        self._call(self.bus_name, OBJECT_PATH, "org.freedesktop.DBus.Properties", "Set",
                   GLib.Variant("(ssv)", (PLAYER_IFACE, "Volume", GLib.Variant("d", 0.25))))
        self.assertEqual(self.playback.volume, 0.25)
        self.assertFalse(self._get(PLAYER_IFACE, "CanGoNext"))
        with self.assertRaises(GLib.Error):
            self._call(self.bus_name, OBJECT_PATH, PLAYER_IFACE, "OpenUri",
                       GLib.Variant("(s)", ("https://x",)))


if __name__ == "__main__":
    unittest.main()
