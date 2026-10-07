"""Podcast detail: immersive header, description and filterable episode list."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk

from ...i18n import _
from ...utils import colors
from ...utils.text import html_to_text
from ...utils.time_format import format_long_date
from ..components.cover import CoverImage
from ..components.episode_row import EpisodeRow
from ..helpers import clear, get_app, label, status_page
from .base import ViewPage

BATCH = 40
FILTERS = (("all", "Todos"), ("unplayed", "Não ouvidos"), ("downloaded", "Baixados"))


def _rgba(rgb: colors.RGB, alpha: float) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.red, color.green, color.blue = rgb
    color.alpha = alpha
    return color


class _BackdropBox(Gtk.Box):
    """Vertical box painted with a soft gradient of the artwork's color."""

    __gtype_name__ = "PodFlowBackdropBox"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rgb: colors.RGB | None = None

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self.rgb is not None:
            width, height = self.get_width(), self.get_height()
            rect = Graphene.Rect().init(0, 0, width, height)
            top = Gsk.ColorStop()
            top.offset = 0.0
            top.color = _rgba(self.rgb, 0.42)
            bottom = Gsk.ColorStop()
            bottom.offset = 1.0
            bottom.color = _rgba(self.rgb, 0.0)
            snapshot.append_linear_gradient(rect, Graphene.Point().init(0, 0),
                                            Graphene.Point().init(0, height), [top, bottom])
        Gtk.Box.do_snapshot(self, snapshot)


class PodcastDetailPage(ViewPage):
    __gtype_name__ = "PodFlowPodcastDetailPage"

    def __init__(self, podcast_id: str):
        app = get_app()
        podcast = app.db.get_podcast(podcast_id)
        super().__init__(podcast.title if podcast else _("Podcast"), f"podcast:{podcast_id}")
        self.podcast_id = podcast_id
        self.podcast = podcast
        self._filter = "all"
        self._loaded = 0
        self._total = 0
        self._handlers: list[tuple[object, int]] = []

        self.refresh_stack = Gtk.Stack()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=_("Atualizar episódios"))
        refresh.connect("clicked", lambda *_: app.library.refresh_podcast(podcast_id, force=True))
        self.refresh_stack.add_named(refresh, "button")
        self.refresh_stack.add_named(Adw.Spinner(), "spinner")
        menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=_("Mais opções"))
        menu_button.set_menu_model(self._menu())
        self.header.pack_end(menu_button)
        self.header.pack_end(self.refresh_stack)
        self._install_actions()

        # -- header area
        self.backdrop = _BackdropBox(orientation=Gtk.Orientation.VERTICAL, css_classes=["detail-backdrop"])
        self.hero = Gtk.Box(spacing=28, css_classes=["detail-header"])
        self.cover = CoverImage(220, radius=16)
        self.cover.add_css_class("detail-cover")
        self.hero.append(self.cover)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                       hexpand=True)
        self.eyebrow = label("", ("detail-eyebrow", "dim-label"), ellipsize=True, max_width_chars=10)
        self.title_label = label("", ("detail-title",), wrap=True, max_width_chars=10)
        self.author = label("", ("detail-author",), wrap=True, max_width_chars=10)
        self.meta = label("", ("detail-meta", "dim-label"), wrap=True, max_width_chars=10)
        for widget in (self.eyebrow, self.title_label, self.author, self.meta):
            info.append(widget)
        actions = Gtk.Box(spacing=10, margin_top=12)
        self.follow_button = Gtk.Button(css_classes=["pill"])
        self.follow_button.connect("clicked", lambda *_: self._toggle_follow())
        actions.append(self.follow_button)
        self.latest_button = Gtk.Button(css_classes=["pill", "latest-button"])
        self.latest_button.set_child(Adw.ButtonContent(icon_name="media-playback-start-symbolic",
                                                       label=_("Último episódio")))
        self.latest_button.connect("clicked", lambda *_: self._play_latest())
        actions.append(self.latest_button)
        info.append(actions)
        self.hero.append(info)

        # -- body
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, css_classes=["detail-body"])
        self.description = label("", ("detail-description",), wrap=True, lines=3,
                                 max_width_chars=10)
        body.append(self.description)
        self.more_button = Gtk.Button(label=_("Ver mais"), css_classes=["flat", "see-all"],
                                      halign=Gtk.Align.START)
        self.more_button.connect("clicked", lambda *_: self._toggle_description())
        body.append(self.more_button)

        filters = Gtk.Box(spacing=12, margin_top=6)
        self.toggle_group = Adw.ToggleGroup(css_classes=["round"])
        for name, text in FILTERS:
            self.toggle_group.add(Adw.Toggle(name=name, label=_(text)))
        self.toggle_group.set_active_name("all")
        self.toggle_group.connect("notify::active-name", lambda *_: self._on_filter_changed())
        filters.append(self.toggle_group)
        self.count_label = label("", ("dim-label", "caption"), xalign=1.0)
        self.count_label.set_hexpand(True)
        self.count_label.set_valign(Gtk.Align.CENTER)
        filters.append(self.count_label)
        body.append(filters)

        self.list_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.list_slot)
        self.listbox = Gtk.ListBox(css_classes=["boxed-list", "episode-list"],
                                   selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", lambda _lb, row: row.toggle_expanded())
        self.load_more = Gtk.Button(label=_("Carregar mais episódios"), halign=Gtk.Align.CENTER,
                                    css_classes=["pill"], margin_top=12)
        self.load_more.connect("clicked", lambda *_: self._append_batch())

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.backdrop.append(Adw.Clamp(maximum_size=1100, tightening_threshold=900, child=self.hero))
        page.append(self.backdrop)
        page.append(Adw.Clamp(maximum_size=1100, tightening_threshold=900, child=body))
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=page,
                                           vexpand=True)
        self.scroller.connect("edge-reached", self._on_edge_reached)
        self.set_body(self.scroller)

        self._update_header()
        self._reload_list()

    # -- lifecycle -------------------------------------------------------------------

    def do_root(self) -> None:
        Adw.NavigationPage.do_root(self)
        app = get_app()
        if self._handlers:
            return
        self._handlers = [
            (app.db, app.db.connect("episodes-changed", self._on_episodes_changed)),
            (app.db, app.db.connect("podcast-changed", self._on_podcast_changed)),
            (app.library, app.library.connect("refresh-started", self._on_refresh_started)),
            (app.library, app.library.connect("refresh-finished", self._on_refresh_finished)),
        ]

    def do_unroot(self) -> None:
        for obj, handler in self._handlers:
            obj.disconnect(handler)
        self._handlers = []
        Adw.NavigationPage.do_unroot(self)

    def on_shown(self) -> None:
        app = self.app
        app.db.mark_podcast_seen(self.podcast_id)
        force = self._total == 0
        started = app.library.refresh_podcast(self.podcast_id, full=True, force=force)
        self.refresh_stack.set_visible_child_name(
            "spinner" if started or app.library.is_refreshing(self.podcast_id) else "button")

    def set_compact(self, compact: bool) -> None:
        super().set_compact(compact)
        self.hero.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        self.cover.set_size(180 if compact else 220)
        self.cover.set_halign(Gtk.Align.CENTER if compact else Gtk.Align.START)
        for widget in (self.eyebrow, self.title_label, self.author, self.meta):
            widget.set_xalign(0.5 if compact else 0.0)
            widget.set_justify(Gtk.Justification.CENTER if compact else Gtk.Justification.LEFT)
        self.latest_button.get_parent().set_halign(Gtk.Align.CENTER if compact else Gtk.Align.START)

    # -- header ----------------------------------------------------------------------

    def _update_header(self) -> None:
        podcast = self.app.db.get_podcast(self.podcast_id)
        if podcast is None:
            return
        self.podcast = podcast
        self.set_title(podcast.title)
        self.cover.set_source(podcast.artwork_url, podcast.title, podcast.accent_color)
        self.eyebrow.set_text((podcast.genre or "").upper())
        self.eyebrow.set_visible(bool(podcast.genre))
        self.title_label.set_text(podcast.title)
        self.author.set_text(podcast.author)
        self.author.set_visible(bool(podcast.author))
        count = self.app.db.count_episodes(podcast.id)
        latest = self.app.db.latest_episode(podcast.id)
        parts = []
        if count:
            parts.append(_("{count} episódios").format(count=count) if count != 1
                         else _("1 episódio"))
        if latest is not None and latest.published:
            parts.append(_("último em {date}").format(date=format_long_date(latest.published)))
        if podcast.explicit:
            parts.append(_("Explícito"))
        self.meta.set_text(" · ".join(parts))
        description = html_to_text(podcast.description)
        self.description.set_text(description)
        self.description.set_visible(bool(description))
        self.more_button.set_visible(len(description) > 240)
        self._update_follow()
        rgb = colors.parse_hex(podcast.accent_color)
        if rgb is not None:
            self._set_backdrop(rgb)
        if podcast.artwork_url:
            self.app.images.dominant_color(podcast.artwork_url, self._on_color)

    def _on_color(self, rgb) -> None:
        if rgb is None:
            return
        self._set_backdrop(rgb)
        hex_color = colors.to_hex(rgb)
        if self.podcast is not None and self.podcast.accent_color != hex_color:
            self.app.db.set_podcast_accent(self.podcast_id, hex_color)

    def _set_backdrop(self, rgb) -> None:
        self.backdrop.rgb = rgb
        self.backdrop.queue_draw()

    def _update_follow(self) -> None:
        subscribed = bool(self.podcast and self.podcast.subscribed)
        self.follow_button.set_child(Adw.ButtonContent(
            icon_name="object-select-symbolic" if subscribed else "list-add-symbolic",
            label=_("Seguindo") if subscribed else _("Seguir")))
        if subscribed:
            self.follow_button.remove_css_class("suggested-action")
        else:
            self.follow_button.add_css_class("suggested-action")

    def _toggle_follow(self) -> None:
        if self.podcast is None:
            return
        subscribe = not self.podcast.subscribed
        self.app.library.set_subscribed(self.podcast_id, subscribe)
        self.toast(_("Seguindo {title}").format(title=self.podcast.title) if subscribe
                   else _("Você deixou de seguir {title}").format(title=self.podcast.title))

    def _toggle_description(self) -> None:
        expanded = self.description.get_lines() == -1
        self.description.set_lines(3 if expanded else -1)
        self.more_button.set_label(_("Ver mais") if expanded else _("Ver menos"))

    def _play_latest(self) -> None:
        episode = self.app.db.latest_episode(self.podcast_id)
        if episode is None:
            self.toast(_("Nenhum episódio disponível ainda"))
            return
        self.play_episode(episode)

    # -- menu & actions --------------------------------------------------------------

    def _menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        menu.append(_("Marcar todos como ouvidos"), "podcast.mark-all-played")
        links = Gio.Menu()
        links.append(_("Copiar link do feed RSS"), "podcast.copy-feed")
        links.append(_("Abrir no Apple Podcasts"), "podcast.open-apple")
        links.append(_("Abrir site do programa"), "podcast.open-website")
        menu.append_section(None, links)
        return menu

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, callback in (("mark-all-played", self._mark_all_played),
                               ("copy-feed", self._copy_feed),
                               ("open-apple", lambda: self._open_uri(self.podcast.apple_url)),
                               ("open-website", lambda: self._open_uri(self.podcast.website))):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            group.add_action(action)
        self.insert_action_group("podcast", group)

    def _mark_all_played(self) -> None:
        self.app.db.mark_all_played(self.podcast_id)
        self.toast(_("Todos os episódios foram marcados como ouvidos"))

    def _copy_feed(self) -> None:
        if self.podcast and self.podcast.feed_url:
            self.get_clipboard().set(self.podcast.feed_url)
            self.toast(_("Link do feed copiado"))

    def _open_uri(self, uri: str) -> None:
        if not uri:
            self.toast(_("Link indisponível"))
            return
        Gtk.UriLauncher.new(uri).launch(self.get_root(), None, None, None)

    # -- episode list ----------------------------------------------------------------

    def _on_filter_changed(self) -> None:
        self._filter = self.toggle_group.get_active_name() or "all"
        self._reload_list()

    def _reload_list(self, keep: int = 0) -> None:
        db = self.app.db
        self._total = db.count_episodes(self.podcast_id, self._filter)
        clear(self.listbox)
        clear(self.list_slot)
        self._loaded = 0
        total_all = db.count_episodes(self.podcast_id)
        self.count_label.set_text(_("{count} episódios").format(count=self._total)
                                  if self._total != 1 else _("1 episódio"))
        if self._total == 0:
            if self._filter == "downloaded":
                page = status_page("folder-download-symbolic", _("Nenhum episódio baixado"),
                                   _("Baixe episódios pelo menu ⋯ para ouvir sem internet."))
            elif self._filter == "unplayed" and total_all:
                page = status_page("object-select-symbolic", _("Tudo em dia!"),
                                   _("Você já ouviu todos os episódios deste programa."))
            elif self.app.library.is_refreshing(self.podcast_id):
                page = status_page("view-refresh-symbolic", _("Carregando episódios…"))
            elif not self.app.library.online:
                page = status_page("network-offline-symbolic", _("Sem episódios salvos"),
                                   _("Conecte-se à internet para carregar os episódios."))
            else:
                page = status_page("audio-x-generic-symbolic", _("Nenhum episódio encontrado"))
            self.list_slot.append(page)
            return
        self.list_slot.append(self.listbox)
        self.list_slot.append(self.load_more)
        self._append_batch(max(BATCH, keep))

    def _append_batch(self, count: int = BATCH) -> None:
        episodes = self.app.db.list_episodes(self.podcast_id, self._filter, limit=count,
                                             offset=self._loaded)
        for episode in episodes:
            self.listbox.append(EpisodeRow(episode))
        self._loaded += len(episodes)
        self.load_more.set_visible(self._loaded < self._total)

    def _on_edge_reached(self, _scroller, position) -> None:
        if position == Gtk.PositionType.BOTTOM and self._loaded < self._total:
            self._append_batch()

    # -- signals ---------------------------------------------------------------------

    def _on_episodes_changed(self, _db, podcast_id: str) -> None:
        if podcast_id == self.podcast_id:
            self._update_header()
            self._reload_list(keep=self._loaded)

    def _on_podcast_changed(self, _db, podcast_id: str) -> None:
        if podcast_id == self.podcast_id:
            self._update_header()

    def _on_refresh_started(self, _library, podcast_id: str) -> None:
        if podcast_id == self.podcast_id:
            self.refresh_stack.set_visible_child_name("spinner")

    def _on_refresh_finished(self, _library, podcast_id: str, error: str) -> None:
        if podcast_id != self.podcast_id:
            return
        self.refresh_stack.set_visible_child_name("button")
        if error:
            self.toast(_("Não foi possível atualizar os episódios"))
        if self._total == 0:
            self._reload_list()
