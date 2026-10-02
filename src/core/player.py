"""GStreamer audio engine (playbin3 + scaletempo).

The engine only knows about URIs; queue, progress persistence and sleep timer
live in :mod:`playback`. All signals are emitted on the GTK main thread
because the bus is watched from the default main context.
"""

from __future__ import annotations

import enum
import logging
import os

from gi.repository import GLib, GObject, Gst, GstAudio

log = logging.getLogger(__name__)

# GstPlayFlags is not introspectable; values from gstplay-enum.h.
_FLAG_AUDIO = 0x2
_FLAG_SOFT_VOLUME = 0x10
_FLAG_BUFFERING = 0x100

MIN_RATE = 0.5
MAX_RATE = 2.5


class PlayerState(enum.IntEnum):
    STOPPED = 0
    LOADING = 1
    PLAYING = 2
    PAUSED = 3


class AudioPlayer(GObject.Object):
    __gtype_name__ = "PodFlowAudioPlayer"

    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "position-changed": (GObject.SignalFlags.RUN_FIRST, None, (float, float)),
        "duration-changed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "seeked": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "buffering": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "eos": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, audio_sink: str | None = None):
        super().__init__()
        if not Gst.is_initialized():
            Gst.init(None)
        self._pipeline = (Gst.ElementFactory.make("playbin3", "podflow-player")
                          or Gst.ElementFactory.make("playbin", "podflow-player"))
        if self._pipeline is None:
            raise RuntimeError("GStreamer playbin não está disponível")
        self._pipeline.set_property("flags", _FLAG_AUDIO | _FLAG_SOFT_VOLUME | _FLAG_BUFFERING)
        tempo = Gst.ElementFactory.make("scaletempo", "tempo")
        if tempo is not None:  # keeps the pitch natural at other speeds
            self._pipeline.set_property("audio-filter", tempo)
        sink_name = audio_sink or os.environ.get("PODFLOW_AUDIO_SINK")
        if sink_name:
            sink = Gst.ElementFactory.make(sink_name, "audio-sink")
            if sink is not None:
                if sink.find_property("sync") is not None:
                    sink.set_property("sync", True)
                self._pipeline.set_property("audio-sink", sink)

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_handler = bus.connect("message", self._on_message)

        self._uri = ""
        self._state = PlayerState.STOPPED
        self._want_playing = False
        self._buffering = False
        self._prerolled = False
        self._pending_seek: float | None = None
        self._rate = 1.0
        self._volume = 1.0
        self._duration = 0.0
        self._duration_hint = 0.0
        self._position = 0.0
        self._poll_id = 0
        self._is_stream = False

    # -- properties ------------------------------------------------------------------

    @property
    def state(self) -> PlayerState:
        return self._state

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def duration(self) -> float:
        return self._duration or self._duration_hint

    @property
    def position(self) -> float:
        if self._pending_seek is not None:
            return self._pending_seek
        ok, position = self._pipeline.query_position(Gst.Format.TIME)
        if ok and position >= 0:
            self._position = position / Gst.SECOND
        return self._position

    @property
    def is_buffering(self) -> bool:
        return self._buffering

    # -- commands --------------------------------------------------------------------

    def load(self, uri: str, start: float = 0.0, autoplay: bool = True,
             duration_hint: float = 0.0) -> None:
        self._stop_polling()
        self._pipeline.set_state(Gst.State.NULL)
        self._uri = uri
        self._is_stream = uri.startswith(("http://", "https://"))
        self._prerolled = False
        self._buffering = False
        self._duration = 0.0
        self._duration_hint = float(duration_hint or 0)
        self._position = max(0.0, start)
        self._pending_seek = start if start > 1 else None
        self._want_playing = autoplay
        self._pipeline.set_property("uri", uri)
        self._set_state(PlayerState.LOADING)
        result = self._pipeline.set_state(Gst.State.PAUSED)
        if result == Gst.StateChangeReturn.FAILURE:
            self._fail("Não foi possível abrir o áudio")

    def play(self) -> None:
        if not self._uri:
            return
        self._want_playing = True
        if self._state == PlayerState.STOPPED:
            self.load(self._uri, self._position, autoplay=True, duration_hint=self._duration_hint)
            return
        if self._prerolled and not self._buffering:
            self._pipeline.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        self._want_playing = False
        if self._state in (PlayerState.PLAYING, PlayerState.LOADING):
            self._pipeline.set_state(Gst.State.PAUSED)
            if self._prerolled:
                self._set_state(PlayerState.PAUSED)
                self._emit_position()

    def toggle(self) -> None:
        if self._want_playing:
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._want_playing = False
        self._stop_polling()
        self._position = self.position if self._prerolled else self._position
        self._pipeline.set_state(Gst.State.NULL)
        self._prerolled = False
        self._set_state(PlayerState.STOPPED)

    def seek(self, seconds: float) -> None:
        duration = self.duration
        if duration > 0:
            seconds = min(seconds, max(0.0, duration - 0.5))
        seconds = max(0.0, seconds)
        if not self._prerolled:
            self._pending_seek = seconds
            self._position = seconds
            self.emit("position-changed", seconds, duration)
            return
        self._do_seek(seconds)
        self.emit("seeked", seconds)

    def seek_relative(self, delta: float) -> None:
        self.seek(self.position + delta)

    def set_rate(self, rate: float) -> None:
        rate = max(MIN_RATE, min(MAX_RATE, float(rate)))
        if abs(rate - self._rate) < 1e-3:
            return
        self._rate = rate
        if self._prerolled:
            self._do_seek(self.position)

    def set_volume(self, volume: float) -> None:
        """``volume`` is perceptual (cubic) in 0..1."""
        self._volume = max(0.0, min(1.0, float(volume)))
        linear = GstAudio.StreamVolume.convert_volume(
            GstAudio.StreamVolumeFormat.CUBIC, GstAudio.StreamVolumeFormat.LINEAR, self._volume)
        self._pipeline.set_property("volume", linear)

    def shutdown(self) -> None:
        self._stop_polling()
        self._pipeline.set_state(Gst.State.NULL)
        bus = self._pipeline.get_bus()
        bus.disconnect(self._bus_handler)
        bus.remove_signal_watch()

    # -- internals -------------------------------------------------------------------

    def _do_seek(self, seconds: float) -> None:
        self._position = seconds
        ok = self._pipeline.seek(
            self._rate, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            Gst.SeekType.SET, int(seconds * Gst.SECOND), Gst.SeekType.NONE, -1)
        if not ok:
            log.info("Seek para %.1fs recusado", seconds)
        self.emit("position-changed", seconds, self.duration)

    def _set_state(self, state: PlayerState) -> None:
        if state != self._state:
            self._state = state
            self.emit("state-changed", int(state))
        if state == PlayerState.PLAYING:
            self._start_polling()
        else:
            self._stop_polling()

    def _start_polling(self) -> None:
        if not self._poll_id:
            self._poll_id = GLib.timeout_add(250, self._poll)

    def _stop_polling(self) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0

    def _poll(self) -> bool:
        self._emit_position()
        return GLib.SOURCE_CONTINUE

    def _emit_position(self) -> None:
        self._query_duration()
        self.emit("position-changed", self.position, self.duration)

    def _query_duration(self) -> None:
        ok, duration = self._pipeline.query_duration(Gst.Format.TIME)
        if ok and duration > 0:
            seconds = duration / Gst.SECOND
            if abs(seconds - self._duration) > 0.5:
                self._duration = seconds
                self.emit("duration-changed", seconds)

    def _fail(self, message: str) -> None:
        self._want_playing = False
        self._stop_polling()
        self._pipeline.set_state(Gst.State.NULL)
        self._prerolled = False
        self._set_state(PlayerState.STOPPED)
        self.emit("error", message)

    def _on_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        kind = message.type
        if kind == Gst.MessageType.ASYNC_DONE:
            self._on_prerolled()
        elif kind == Gst.MessageType.STATE_CHANGED and message.src == self._pipeline:
            _old, new, _pending = message.parse_state_changed()
            if new == Gst.State.PLAYING:
                self._set_state(PlayerState.PLAYING)
            elif new == Gst.State.PAUSED and self._prerolled and not self._want_playing:
                self._set_state(PlayerState.PAUSED)
        elif kind == Gst.MessageType.BUFFERING:
            self._on_buffering(message.parse_buffering())
        elif kind == Gst.MessageType.DURATION_CHANGED:
            self._query_duration()
        elif kind == Gst.MessageType.EOS:
            self._want_playing = False
            self._stop_polling()
            self._position = 0.0
            self._pipeline.set_state(Gst.State.NULL)
            self._prerolled = False
            self._set_state(PlayerState.STOPPED)
            self.emit("eos")
        elif kind == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            log.warning("Erro do GStreamer: %s (%s)", error.message, debug)
            self._fail(error.message)
        elif kind == Gst.MessageType.CLOCK_LOST:
            if self._want_playing:  # recommended recovery: restart the clock
                self._pipeline.set_state(Gst.State.PAUSED)
                self._pipeline.set_state(Gst.State.PLAYING)

    def _on_prerolled(self) -> None:
        first = not self._prerolled
        self._prerolled = True
        self._query_duration()
        if first and (self._pending_seek is not None or abs(self._rate - 1.0) > 1e-3):
            target = self._pending_seek or 0.0
            self._pending_seek = None
            self._do_seek(target)
            return  # a new ASYNC_DONE follows the flushing seek
        self._pending_seek = None
        if self._want_playing and not self._buffering:
            self._pipeline.set_state(Gst.State.PLAYING)
        elif not self._want_playing:
            self._set_state(PlayerState.PAUSED)
            self.emit("position-changed", self.position, self.duration)

    def _on_buffering(self, percent: int) -> None:
        if not self._is_stream:
            return
        self.emit("buffering", percent)
        if percent < 100 and not self._buffering:
            self._buffering = True
            if self._want_playing and self._prerolled:
                self._pipeline.set_state(Gst.State.PAUSED)
        elif percent >= 100 and self._buffering:
            self._buffering = False
            if self._want_playing and self._prerolled:
                self._pipeline.set_state(Gst.State.PLAYING)
