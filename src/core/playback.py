"""Playback coordination: queue ("A Seguir"), progress, sleep timer, inhibit.

Sits between the UI/MPRIS and the GStreamer engine, and keeps the database
in sync with what is playing.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from gi.repository import GLib, GObject, Gst, Gtk

from ..i18n import _
from ..models import Episode
from .player import MAX_RATE, MIN_RATE, AudioPlayer, PlayerState

log = logging.getLogger(__name__)

SKIP_BACK = 15
SKIP_FORWARD = 30
SAVE_INTERVAL = 5.0
PLAYED_TAIL = 45  # seconds left at which an episode counts as played
FADE_SECONDS = 10.0
RATES = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0)


class PlaybackManager(GObject.Object):
    __gtype_name__ = "PodFlowPlaybackManager"

    __gsignals__ = {
        "episode-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "position-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        "seeked": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "rate-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "volume-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "buffering-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "sleep-timer-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, db, player: AudioPlayer | None = None, app: Gtk.Application | None = None):
        super().__init__()
        self.db = db
        self.app = app
        self.player = player or AudioPlayer()
        self.current: Episode | None = None
        self._needs_load = False
        self._last_save = 0.0
        self._inhibit_cookie = 0
        self._sleep_mode: str | None = None  # None, "time" or "end"
        self._sleep_deadline = 0.0
        self._sleep_source = 0
        self._fade = 1.0
        self._volume_save_source = 0
        self._buffering = False

        self._volume = float(db.get_setting("volume", 1.0))
        self.player.set_volume(self._volume)
        self.player.set_rate(float(db.get_setting("rate", 1.0)))

        self.player.connect("state-changed", self._on_state_changed)
        self.player.connect("position-changed", self._on_position_changed)
        self.player.connect("duration-changed", self._on_duration_changed)
        self.player.connect("seeked", lambda _p, pos: self.emit("seeked", pos))
        self.player.connect("buffering", self._on_buffering)
        self.player.connect("eos", self._on_eos)
        self.player.connect("error", self._on_error)

    # -- state -------------------------------------------------------------------

    @property
    def state(self) -> PlayerState:
        if self._needs_load:
            return PlayerState.PAUSED
        return self.player.state

    @property
    def is_playing(self) -> bool:
        return self.player.state in (PlayerState.PLAYING, PlayerState.LOADING)

    @property
    def position(self) -> float:
        if self._needs_load and self.current:
            return self.current.position
        return self.player.position

    @property
    def duration(self) -> float:
        if self.current and (self._needs_load or not self.player.duration):
            return float(self.current.duration)
        return self.player.duration

    @property
    def rate(self) -> float:
        return self.player.rate

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def is_buffering(self) -> bool:
        return self._buffering

    @property
    def sleep_mode(self) -> str | None:
        return self._sleep_mode

    def sleep_remaining(self) -> int:
        if self._sleep_mode != "time":
            return -1
        return max(0, int(round(self._sleep_deadline - time.monotonic())))

    def has_next(self) -> bool:
        current = self.current.id if self.current else None
        return any(eid != current for eid in self.db.queue_ids())

    # -- commands ------------------------------------------------------------------

    def play_episode(self, episode: Episode, from_start: bool = False, autoplay: bool = True) -> None:
        if self.current and self.current.id == episode.id and not from_start:
            if autoplay:
                self.play()
            return
        self._save_progress(final=True)
        fresh = self.db.get_episode(episode.id) or episode
        start = 0.0 if (from_start or fresh.played) else float(fresh.position)
        self.current = fresh
        self._needs_load = False
        self.db.queue_remove(fresh.id)
        self.db.mark_started(fresh.id)
        self.db.set_setting("last_episode", fresh.id)
        self.player.load(self._uri_for(fresh), start, autoplay, duration_hint=fresh.duration)
        self.emit("episode-changed", fresh)

    def restore_session(self) -> None:
        """Show the last episode (paused at its position) without touching the network."""
        episode_id = self.db.get_setting("last_episode")
        episode = self.db.get_episode(episode_id) if episode_id else None
        if episode is not None and not episode.played:
            self.cue(episode)

    def cue(self, episode: Episode) -> None:
        """Make ``episode`` current and paused; audio loads on the first play()."""
        self._save_progress(final=True)
        self.player.stop()
        self.current = episode
        self._needs_load = True
        self.emit("episode-changed", episode)
        self.emit("state-changed", int(PlayerState.PAUSED))
        self.emit("position-changed", episode.position, float(episode.duration))

    def play(self) -> None:
        if self.current is None:
            return
        if self._needs_load:
            self._needs_load = False
            self.player.load(self._uri_for(self.current), float(self.current.position), True,
                             duration_hint=self.current.duration)
            return
        self.player.play()

    def pause(self) -> None:
        self.player.pause()
        self._save_progress()

    def toggle(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._save_progress(final=True)
        self.player.stop()

    def seek(self, seconds: float) -> None:
        if self._needs_load and self.current:
            self.current.position = max(0.0, seconds)
            self.db.save_progress(self.current.id, self.current.position)
            self.emit("position-changed", self.current.position, self.duration)
            return
        self.player.seek(seconds)
        self._save_progress()

    def skip_back(self) -> None:
        self.seek(max(0.0, self.position - SKIP_BACK))

    def skip_forward(self) -> None:
        self.seek(self.position + SKIP_FORWARD)

    def restart(self) -> None:
        self.seek(0.0)

    def set_rate(self, rate: float) -> None:
        rate = max(MIN_RATE, min(MAX_RATE, rate))
        self.player.set_rate(rate)
        self.db.set_setting("rate", rate)
        self.emit("rate-changed", rate)

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, volume))
        self.player.set_volume(self._volume * self._fade)
        self.emit("volume-changed", self._volume)
        if self._volume_save_source:
            GLib.source_remove(self._volume_save_source)
        self._volume_save_source = GLib.timeout_add(600, self._persist_volume)

    def _persist_volume(self) -> bool:
        self._volume_save_source = 0
        self.db.set_setting("volume", self._volume)
        return GLib.SOURCE_REMOVE

    def play_next(self) -> bool:
        current = self.current.id if self.current else None
        for episode in self.db.queue_list():
            if episode.id != current:
                self.play_episode(episode, autoplay=True)
                return True
        return False

    def enqueue(self, episode: Episode, front: bool = False) -> None:
        self.db.queue_add(episode.id, front=front)

    # -- sleep timer -----------------------------------------------------------------

    def set_sleep_timer(self, minutes: int | None = None, end_of_episode: bool = False) -> None:
        self._cancel_sleep_source()
        self._restore_fade()
        if end_of_episode:
            self._sleep_mode = "end"
        elif minutes:
            self._sleep_mode = "time"
            self._sleep_deadline = time.monotonic() + minutes * 60
            self._sleep_source = GLib.timeout_add(500, self._sleep_tick)
        else:
            self._sleep_mode = None
        self.emit("sleep-timer-changed")

    def _cancel_sleep_source(self) -> None:
        if self._sleep_source:
            GLib.source_remove(self._sleep_source)
            self._sleep_source = 0

    def _restore_fade(self) -> None:
        if self._fade != 1.0:
            self._fade = 1.0
            self.player.set_volume(self._volume)

    def _sleep_tick(self) -> bool:
        remaining = self._sleep_deadline - time.monotonic()
        if remaining <= 0:
            self._sleep_source = 0
            self._sleep_mode = None
            self.pause()
            self._restore_fade()
            self.emit("sleep-timer-changed")
            return GLib.SOURCE_REMOVE
        if remaining <= FADE_SECONDS and self.is_playing:
            self._fade = max(0.0, remaining / FADE_SECONDS)
            self.player.set_volume(self._volume * self._fade)
        self.emit("sleep-timer-changed")
        return GLib.SOURCE_CONTINUE

    # -- shutdown ----------------------------------------------------------------------

    def shutdown(self) -> None:
        self._save_progress(final=True)
        self._cancel_sleep_source()
        self._uninhibit()
        if self._volume_save_source:
            GLib.source_remove(self._volume_save_source)
            self._persist_volume()
        self.player.shutdown()

    # -- internals -------------------------------------------------------------------

    @staticmethod
    def _uri_for(episode: Episode) -> str:
        if episode.is_downloaded and Path(episode.download_path).is_file():
            return Gst.filename_to_uri(episode.download_path)
        return episode.audio_url

    def _save_progress(self, final: bool = False) -> None:
        if self.current is None or self._needs_load:
            return
        position = self.player.position
        duration = self.duration
        self._last_save = time.monotonic()
        if final and duration > 0 and position > 0 and duration - position <= PLAYED_TAIL:
            self.db.set_played(self.current.id, True)
            self.current.played = True
            self.current.position = 0.0
            return
        if position <= 0 and not final:
            return
        self.current.position = position
        self.db.save_progress(self.current.id, position, duration)

    def _inhibit(self) -> None:
        if self.app is None or self._inhibit_cookie:
            return
        try:
            self._inhibit_cookie = self.app.inhibit(
                self.app.get_active_window(), Gtk.ApplicationInhibitFlags.SUSPEND,
                _("Reproduzindo um podcast"))
        except Exception:  # no session manager / portal
            log.debug("Inibição de suspensão indisponível", exc_info=True)
            self._inhibit_cookie = 0

    def _uninhibit(self) -> None:
        if self.app is not None and self._inhibit_cookie:
            self.app.uninhibit(self._inhibit_cookie)
        self._inhibit_cookie = 0

    def _on_state_changed(self, _player, state: int) -> None:
        if state == PlayerState.PLAYING:
            self._inhibit()
        elif state in (PlayerState.PAUSED, PlayerState.STOPPED):
            self._uninhibit()
            if state == PlayerState.PAUSED:
                self._save_progress()
        self.emit("state-changed", state)

    def _on_position_changed(self, _player, position: float, duration: float) -> None:
        if self.current is not None and self.player.state == PlayerState.PLAYING:
            if time.monotonic() - self._last_save >= SAVE_INTERVAL:
                self._save_progress()
        self.emit("position-changed", position, duration)

    def _on_duration_changed(self, _player, duration: float) -> None:
        if self.current is not None and duration > 0 and not self.current.duration:
            self.current.duration = int(duration)

    def _on_buffering(self, _player, percent: int) -> None:
        buffering = percent < 100
        if buffering != self._buffering:
            self._buffering = buffering
            self.emit("buffering-changed", buffering)

    def _on_eos(self, _player) -> None:
        finished = self.current
        if finished is not None:
            self.db.set_played(finished.id, True)
            finished.played = True
            finished.position = 0.0
        if self._sleep_mode == "end":
            self._sleep_mode = None
            self.emit("sleep-timer-changed")
            return
        self.play_next()

    def _on_error(self, _player, message: str) -> None:
        self._uninhibit()
        self.emit("error", message)
