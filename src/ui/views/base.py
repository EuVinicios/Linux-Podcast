"""Base class for top-level pages living in the content NavigationView."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ...api.apple_service import ChartEpisode, ChartPodcast
from ...i18n import _
from ...models import Episode, Podcast
from ..helpers import get_app


class ViewPage(Adw.NavigationPage):
    __gtype_name__ = "PodFlowViewPage"

    def __init__(self, title: str, tag: str | None = None):
        super().__init__(title=title)
        if tag:
            self.set_tag(tag)
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.toolbar.add_top_bar(self.header)
        self.set_child(self.toolbar)
        self.compact = False
        self._dirty = True
        self._rebuild_source = 0
        self.connect("showing", lambda *_: self._on_showing())

    @property
    def app(self):
        return get_app()

    @property
    def window(self):
        return self.get_root()

    def set_body(self, widget: Gtk.Widget) -> None:
        self.toolbar.set_content(widget)

    def set_compact(self, compact: bool) -> None:
        self.compact = compact

    # -- lazy rebuild ----------------------------------------------------------------

    def schedule_rebuild(self, delay_ms: int = 250) -> None:
        """Rebuild soon if visible, otherwise on the next time it is shown."""
        self._dirty = True
        if not self.get_mapped():
            return
        if self._rebuild_source:
            GLib.source_remove(self._rebuild_source)
        self._rebuild_source = GLib.timeout_add(delay_ms, self._run_rebuild)

    def _run_rebuild(self) -> bool:
        self._rebuild_source = 0
        self._dirty = False
        self.rebuild()
        return GLib.SOURCE_REMOVE

    def _on_showing(self) -> None:
        if self._dirty:
            self._dirty = False
            self.rebuild()
        self.on_shown()

    def rebuild(self) -> None:
        pass

    def on_shown(self) -> None:
        pass

    def refresh(self, force: bool = False) -> None:
        self.rebuild()

    # -- shared actions --------------------------------------------------------------

    def toast(self, message: str) -> None:
        window = self.window
        if window is not None and hasattr(window, "show_toast"):
            window.show_toast(message)

    def open_podcast(self, podcast: Podcast | ChartPodcast | str) -> None:
        library = self.app.library
        if isinstance(podcast, ChartPodcast):
            podcast_id = library.ensure_chart_podcast(podcast).id
        elif isinstance(podcast, Podcast):
            podcast_id = library.ensure_podcast(podcast).id
        else:
            podcast_id = podcast
        self.activate_action("win.open-podcast", GLib.Variant("s", podcast_id))

    def play_episode(self, episode: Episode) -> None:
        stored = self.app.library.ensure_episode(episode)
        self.activate_action("app.play-episode", GLib.Variant("s", stored.id))

    def play_latest(self, podcast: Podcast | ChartPodcast) -> None:
        library = self.app.library
        if isinstance(podcast, ChartPodcast):
            podcast = library.ensure_chart_podcast(podcast)
        else:
            podcast = library.ensure_podcast(podcast)
        library.latest_episode(
            podcast.id, on_done=self.play_episode,
            on_error=lambda error: self.toast(_("Não foi possível carregar o episódio")))

    def play_chart_episode(self, item: ChartEpisode, enqueue: bool = False) -> None:
        def done(episode: Episode) -> None:
            if enqueue:
                self.app.playback.enqueue(episode)
                self.toast(_("Adicionado a A Seguir"))
            else:
                self.play_episode(episode)

        self.app.library.resolve_chart_episode(
            item, on_done=done,
            on_error=lambda error: self.toast(_("Não foi possível carregar o episódio")))

    def open_chart_episode_podcast(self, item: ChartEpisode) -> None:
        if not item.podcast_itunes_id:
            return
        self.open_podcast(ChartPodcast(rank=0, itunes_id=item.podcast_itunes_id,
                                       title=item.podcast_title or item.author,
                                       author=item.author, artwork_url=item.artwork_url,
                                       genre=item.genre, genre_id=item.genre_id))
