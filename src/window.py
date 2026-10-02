"""Main window: sidebar + content navigation, queue drawer and player bar."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .i18n import _
from .ui.components.player_bar import PlayerBar
from .ui.components.queue_drawer import QueueDrawer
from .ui.sidebar import SECTION_NAMES, NavigationSidebar
from .ui.views.charts_view import ChartsView
from .ui.views.explore_view import ExploreView
from .ui.views.library_views import DownloadsView, FollowingView, HistoryView, SavedView
from .ui.views.listen_now_view import ListenNowView
from .ui.views.podcast_detail import PodcastDetailPage
from .ui.views.search_view import SearchView


class PodFlowWindow(Adw.ApplicationWindow):
    __gtype_name__ = "PodFlowWindow"

    def __init__(self, app):
        super().__init__(application=app, title="PodFlow")
        self.app = app
        width, height = app.db.get_setting("window_size", [1280, 840])
        self.set_default_size(max(360, int(width)), max(480, int(height)))
        self.set_size_request(360, 480)
        if app.db.get_setting("window_maximized", False):
            self.maximize()
        self._pages: dict[str, Gtk.Widget] = {}
        self._compact = False
        self._current_section = ""

        self._build()
        self._install_actions()
        self._install_breakpoints()

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)
        self.connect("close-request", self._on_close_request)

        app.playback.connect("episode-changed", lambda *_: self._sync_player_visibility())
        app.playback.connect("error", lambda _pb, message: self.show_toast(
            _("Não foi possível reproduzir: {error}").format(error=message)))
        app.library.connect("online-changed", lambda _l, online: self.banner.set_revealed(not online))
        app.db.connect("subscriptions-changed", lambda *_: self._update_badges())
        app.db.connect("episodes-changed", lambda *_: self._update_badges())
        self._update_badges()
        self._sync_player_visibility()

        last = app.db.get_setting("last_section", "listen-now")
        self.show_section(last if last in SECTION_NAMES and last != "search" else "listen-now")

    # -- layout ----------------------------------------------------------------------

    def _build(self) -> None:
        self.toast_overlay = Adw.ToastOverlay()
        outer = Adw.ToolbarView()

        self.banner = Adw.Banner(title=_("Você está offline. Mostrando o conteúdo salvo."),
                                 revealed=not self.app.library.online)
        outer.add_top_bar(self.banner)

        self.sidebar = NavigationSidebar(self.show_section)
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_title_widget(Adw.WindowTitle(title="PodFlow"))
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", primary=True,
                                     tooltip_text=_("Menu principal"))
        menu_button.set_menu_model(self._primary_menu())
        sidebar_header.pack_end(menu_button)
        sidebar_toolbar.add_top_bar(sidebar_header)
        sidebar_toolbar.set_content(self.sidebar.widget)
        sidebar_page = Adw.NavigationPage(title="PodFlow", tag="sidebar", child=sidebar_toolbar)

        self.content_nav = Adw.NavigationView()
        content_page = Adw.NavigationPage(title=_("Conteúdo"), tag="content", child=self.content_nav)

        self.split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=280,
                                             sidebar_width_fraction=0.22)
        self.split.set_sidebar(sidebar_page)
        self.split.set_content(content_page)

        self.queue_drawer = QueueDrawer()
        self.queue_split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END,
                                                show_sidebar=False, min_sidebar_width=300,
                                                max_sidebar_width=380)
        self.queue_split.set_content(self.split)
        self.queue_split.set_sidebar(self.queue_drawer)
        outer.set_content(self.queue_split)

        self.player_bar = PlayerBar()
        self.player_revealer = Gtk.Revealer(child=self.player_bar,
                                            transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        outer.add_bottom_bar(self.player_revealer)

        self.toast_overlay.set_child(outer)
        self.set_content(self.toast_overlay)

    def _primary_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        library = Gio.Menu()
        library.append(_("Atualizar biblioteca"), "app.refresh")
        library.append(_("Adicionar feed RSS…"), "app.add-feed")
        menu.append_section(None, library)
        sync = Gio.Menu()
        sync_now = Gio.MenuItem.new(_("Sincronizar agora"), "app.sync")
        sync_now.set_attribute_value("hidden-when", GLib.Variant("s", "action-disabled"))
        sync.append_item(sync_now)
        sync.append(_("Conta e sincronização…"), "app.account")
        menu.append_section(None, sync)
        general = Gio.Menu()
        general.append(_("Preferências"), "app.preferences")
        general.append(_("Atalhos de teclado"), "app.shortcuts")
        general.append(_("Sobre o PodFlow"), "app.about")
        menu.append_section(None, general)
        return menu

    def _install_breakpoints(self) -> None:
        medium = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 860sp"))
        medium.add_setter(self.split, "collapsed", True)
        medium.add_setter(self.queue_split, "collapsed", True)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        narrow.add_setter(self.split, "collapsed", True)
        narrow.add_setter(self.queue_split, "collapsed", True)
        narrow.add_setter(self.player_bar, "compact", True)
        narrow.connect("apply", lambda *_: self._set_compact(True))
        narrow.connect("unapply", lambda *_: self._set_compact(False))
        self.add_breakpoint(medium)
        self.add_breakpoint(narrow)

    def _set_compact(self, compact: bool) -> None:
        self._compact = compact
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        for page in self._pages.values():
            page.set_compact(compact)
        for page in self.content_nav.get_navigation_stack():
            if hasattr(page, "set_compact") and page not in self._pages.values():
                page.set_compact(compact)

    # -- actions ---------------------------------------------------------------------

    def _install_actions(self) -> None:
        def add(name: str, callback, parameter: str | None = None) -> None:
            action = Gio.SimpleAction.new(name, GLib.VariantType.new(parameter) if parameter else None)
            action.connect("activate", callback)
            self.add_action(action)

        add("show-section", lambda _a, v: self.show_section(v.get_string()), "s")
        add("open-podcast", lambda _a, v: self.open_podcast(v.get_string()), "s")
        add("open-current-podcast", lambda *_: self._open_current_podcast())
        add("search", lambda *_: self._focus_search())
        add("refresh", lambda *_: self._refresh_visible())
        self.add_action(Gio.PropertyAction.new("toggle-queue", self.queue_split, "show-sidebar"))

    def _on_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        if keyval != Gdk.KEY_space or state & (Gdk.ModifierType.CONTROL_MASK |
                                               Gdk.ModifierType.ALT_MASK):
            return False
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Editable, Gtk.Text)):
            return False
        if self.app.playback.current is None:
            return False
        self.app.playback.toggle()
        return True

    def _on_close_request(self, *_args) -> bool:
        db = self.app.db
        if not self.is_maximized():
            width, height = self.get_default_size()
            db.set_setting("window_size", [width, height])
        db.set_setting("window_maximized", self.is_maximized())
        return False

    # -- navigation ------------------------------------------------------------------

    def _page(self, name: str) -> Gtk.Widget:
        page = self._pages.get(name)
        if page is None:
            factories = {
                "listen-now": ListenNowView,
                "explore": ExploreView,
                "charts": ChartsView,
                "search": SearchView,
                "following": FollowingView,
                "saved": lambda: SavedView(self.app),
                "downloads": lambda: DownloadsView(self.app),
                "history": lambda: HistoryView(self.app),
            }
            page = factories[name]()
            page.set_compact(self._compact)
            self._pages[name] = page
        return page

    def show_section(self, name: str) -> None:
        if name not in SECTION_NAMES:
            name = "listen-now"
        page = self._page(name)
        stack = self.content_nav.get_navigation_stack()
        if self._current_section != name or stack.get_n_items() != 1:
            self.content_nav.replace([page])
        self._current_section = name
        self.sidebar.select(name)
        self.split.set_show_content(True)
        if name != "search":
            self.app.db.set_setting("last_section", name)

    def open_podcast(self, podcast_id: str) -> None:
        if self.app.db.get_podcast(podcast_id) is None:
            return
        visible = self.content_nav.get_visible_page()
        if isinstance(visible, PodcastDetailPage) and visible.podcast_id == podcast_id:
            return
        page = PodcastDetailPage(podcast_id)
        page.set_compact(self._compact)
        self.content_nav.push(page)
        self.split.set_show_content(True)

    def _open_current_podcast(self) -> None:
        current = self.app.playback.current
        if current is not None:
            self.open_podcast(current.podcast_id)

    def _focus_search(self) -> None:
        self.show_section("search")
        self._page("search").focus_entry()

    def _refresh_visible(self) -> None:
        visible = self.content_nav.get_visible_page()
        if hasattr(visible, "refresh"):
            visible.refresh(force=True)

    def _sync_player_visibility(self) -> None:
        self.player_revealer.set_reveal_child(self.app.playback.current is not None)

    def _update_badges(self) -> None:
        total = sum(p.new_count for p in self.app.db.list_subscriptions())
        self.sidebar.set_badge("following", total)

    def show_toast(self, message: str, timeout: int = 3) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=GLib.markup_escape_text(message),
                                               timeout=timeout))
