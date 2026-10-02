"""MPRIS v2 D-Bus server: GNOME Shell media controls and multimedia keys."""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib

from .. import config
from .player import MAX_RATE, MIN_RATE, PlayerState

log = logging.getLogger(__name__)

OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
TRACK_PREFIX = "/io/github/euvinicios/PodFlow/Episode/"

INTROSPECTION_XML = """
<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition">
      <arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/>
    </method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""


def _usec(seconds: float) -> int:
    return int(max(0.0, seconds) * 1_000_000)


class MprisService:
    def __init__(self, playback, db, images=None, app=None,
                 bus_name: str = config.MPRIS_BUS_NAME):
        self.playback = playback
        self.db = db
        self.images = images
        self.app = app
        self.bus_name = bus_name
        self._connection: Gio.DBusConnection | None = None
        self._registrations: list[int] = []
        self._owner_id = 0
        self._handlers: list[tuple[object, int]] = []
        self._art_uri = ""
        self._art_for = ""

    # -- lifecycle -------------------------------------------------------------------

    def start(self, connection: Gio.DBusConnection | None = None) -> None:
        if self._connection is not None:
            return
        try:
            connection = (connection or (self.app.get_dbus_connection() if self.app else None)
                          or Gio.bus_get_sync(Gio.BusType.SESSION, None))
        except GLib.Error as error:
            log.warning("MPRIS indisponível: %s", error.message)
            return
        self._connection = connection
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        for interface in node.interfaces:
            self._registrations.append(connection.register_object(
                OBJECT_PATH, interface, self._on_method_call, self._on_get_property,
                self._on_set_property))
        self._owner_id = Gio.bus_own_name_on_connection(
            connection, self.bus_name, Gio.BusNameOwnerFlags.NONE, None, None)

        pb = self.playback
        self._connect(pb, "episode-changed", self._on_episode_changed)
        self._connect(pb, "state-changed", lambda *_: self._changed(
            PLAYER_IFACE, PlaybackStatus=self._status(), CanPlay=self._can_play(),
            CanPause=self._can_play(), CanSeek=self._can_play()))
        self._connect(pb, "seeked", lambda _pb, pos: self._emit_seeked(pos))
        self._connect(pb, "rate-changed", lambda _pb, rate: self._changed(
            PLAYER_IFACE, Rate=GLib.Variant("d", rate)))
        self._connect(pb, "volume-changed", lambda _pb, vol: self._changed(
            PLAYER_IFACE, Volume=GLib.Variant("d", vol)))
        self._connect(self.db, "queue-changed", lambda *_: self._changed(
            PLAYER_IFACE, CanGoNext=GLib.Variant("b", pb.has_next())))
        if pb.current is not None:
            self._on_episode_changed(pb, pb.current)

    def stop(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers.clear()
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        if self._connection is not None:
            for registration in self._registrations:
                self._connection.unregister_object(registration)
        self._registrations.clear()
        self._connection = None

    def _connect(self, obj, signal: str, callback) -> None:
        self._handlers.append((obj, obj.connect(signal, callback)))

    # -- properties ------------------------------------------------------------------

    def _status(self) -> GLib.Variant:
        if self.playback.current is None:
            return GLib.Variant("s", "Stopped")
        state = self.playback.state
        if state in (PlayerState.PLAYING, PlayerState.LOADING):
            return GLib.Variant("s", "Playing")
        return GLib.Variant("s", "Paused")

    def _can_play(self) -> GLib.Variant:
        return GLib.Variant("b", self.playback.current is not None)

    def _track_id(self) -> str:
        current = self.playback.current
        return TRACK_PREFIX + current.id if current is not None else NO_TRACK

    def _metadata(self) -> GLib.Variant:
        episode = self.playback.current
        if episode is None:
            return GLib.Variant("a{sv}", {"mpris:trackid": GLib.Variant("o", NO_TRACK)})
        artist = episode.podcast_author or episode.podcast_title or config.APP_NAME
        data = {
            "mpris:trackid": GLib.Variant("o", self._track_id()),
            "xesam:title": GLib.Variant("s", episode.title),
            "xesam:album": GLib.Variant("s", episode.podcast_title or ""),
            "xesam:artist": GLib.Variant("as", [artist]),
            "xesam:url": GLib.Variant("s", episode.audio_url or ""),
        }
        duration = self.playback.duration
        if duration > 0:
            data["mpris:length"] = GLib.Variant("x", _usec(duration))
        if self._art_uri:
            data["mpris:artUrl"] = GLib.Variant("s", self._art_uri)
        return GLib.Variant("a{sv}", data)

    def _properties(self, interface: str) -> dict[str, GLib.Variant]:
        if interface == ROOT_IFACE:
            return {
                "CanQuit": GLib.Variant("b", True),
                "CanRaise": GLib.Variant("b", True),
                "HasTrackList": GLib.Variant("b", False),
                "Identity": GLib.Variant("s", config.APP_NAME),
                "DesktopEntry": GLib.Variant("s", config.APP_ID),
                "SupportedUriSchemes": GLib.Variant("as", []),
                "SupportedMimeTypes": GLib.Variant("as", []),
            }
        pb = self.playback
        return {
            "PlaybackStatus": self._status(),
            "Rate": GLib.Variant("d", pb.rate),
            "Metadata": self._metadata(),
            "Volume": GLib.Variant("d", pb.volume),
            "Position": GLib.Variant("x", _usec(pb.position if pb.current else 0)),
            "MinimumRate": GLib.Variant("d", MIN_RATE),
            "MaximumRate": GLib.Variant("d", MAX_RATE),
            "CanGoNext": GLib.Variant("b", pb.has_next()),
            "CanGoPrevious": self._can_play(),
            "CanPlay": self._can_play(),
            "CanPause": self._can_play(),
            "CanSeek": self._can_play(),
            "CanControl": GLib.Variant("b", True),
        }

    def _on_get_property(self, _conn, _sender, _path, interface, name):
        value = self._properties(interface).get(name)
        if value is None:
            raise GLib.Error(f"Propriedade desconhecida: {name}")
        return value

    def _on_set_property(self, _conn, _sender, _path, interface, name, value) -> bool:
        if interface != PLAYER_IFACE:
            return False
        if name == "Rate":
            rate = value.get_double()
            if rate <= 0:
                self.playback.pause()
            else:
                self.playback.set_rate(rate)
            return True
        if name == "Volume":
            self.playback.set_volume(max(0.0, min(1.0, value.get_double())))
            return True
        return False

    # -- methods ---------------------------------------------------------------------

    def _on_method_call(self, _conn, _sender, _path, interface, method, params, invocation):
        pb = self.playback
        try:
            if interface == ROOT_IFACE:
                if method == "Raise" and self.app is not None:
                    self.app.activate()
                elif method == "Quit" and self.app is not None:
                    self.app.quit()
            elif method == "Play":
                pb.play()
            elif method == "Pause":
                pb.pause()
            elif method == "PlayPause":
                pb.toggle()
            elif method == "Stop":
                pb.stop()
            elif method == "Next":
                pb.play_next()
            elif method == "Previous":
                pb.restart()
            elif method == "Seek":
                offset = params.unpack()[0] / 1_000_000
                target = pb.position + offset
                if pb.duration and target >= pb.duration:
                    pb.play_next() or pb.seek(max(0.0, pb.duration - 1))
                else:
                    pb.seek(max(0.0, target))
            elif method == "SetPosition":
                track_id, position = params.unpack()
                seconds = position / 1_000_000
                if track_id == self._track_id() and 0 <= seconds <= (pb.duration or seconds):
                    pb.seek(seconds)
            elif method == "OpenUri":
                invocation.return_dbus_error("org.mpris.MediaPlayer2.Error.NotSupported",
                                             "OpenUri não é suportado")
                return
            invocation.return_value(None)
        except Exception as error:  # never leave a D-Bus call unanswered
            log.exception("Falha no método MPRIS %s", method)
            invocation.return_dbus_error("org.mpris.MediaPlayer2.Error.Failed", str(error))

    # -- change notification ---------------------------------------------------------

    def _changed(self, interface: str, **props: GLib.Variant) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(
                None, OBJECT_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (interface, props, [])))
        except GLib.Error as error:
            log.debug("PropertiesChanged falhou: %s", error.message)

    def _emit_seeked(self, position: float) -> None:
        if self._connection is None:
            return
        self._connection.emit_signal(None, OBJECT_PATH, PLAYER_IFACE, "Seeked",
                                     GLib.Variant("(x)", (_usec(position),)))

    def _on_episode_changed(self, _pb, episode) -> None:
        self._art_uri = ""
        self._art_for = episode.id if episode else ""
        self._changed(PLAYER_IFACE, Metadata=self._metadata(), PlaybackStatus=self._status(),
                      CanGoNext=GLib.Variant("b", self.playback.has_next()),
                      CanGoPrevious=self._can_play(), CanPlay=self._can_play(),
                      CanPause=self._can_play(), CanSeek=self._can_play())
        if episode is not None and self.images is not None and episode.cover_url:
            wanted = episode.id

            def on_file(path) -> None:
                if path is not None and self._art_for == wanted:
                    self._art_uri = path.as_uri()
                    self._changed(PLAYER_IFACE, Metadata=self._metadata())

            self.images.ensure_file(episode.cover_url, 600, on_file)
