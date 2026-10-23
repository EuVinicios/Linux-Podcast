"""Navigation sidebar (AdwSidebar): "Descobrir" and "Biblioteca" sections.

Labels follow the pt-BR titles of Apple Podcasts for macOS (TITLE_HOME, TITLE_CATALOG,
TITLE_TOP_CHARTS, TITLE_SHOWS, TITLE_LATEST_EPISODES, RECENTLY_PLAYED…). Section ids
stay the same, so saved settings keep working.
"""

from __future__ import annotations

from gi.repository import Adw, Gtk, Pango

from ..i18n import _

SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Descobrir", [
        ("listen-now", "Início", "go-home-symbolic"),
        ("explore", "Novidades", "view-app-grid-symbolic"),
        ("charts", "Top Charts", "podflow-chart-symbolic"),
        ("search", "Buscar", "system-search-symbolic"),
    ]),
    ("Biblioteca", [
        ("following", "Podcasts", "view-grid-symbolic"),
        ("saved", "Salvos", "starred-symbolic"),
        ("downloads", "Downloads", "folder-download-symbolic"),
        ("latest", "Últimos Episódios", "view-list-symbolic"),
        ("history", "Reproduções Recentes", "document-open-recent-symbolic"),
    ]),
]

SECTION_NAMES = [name for _title, items in SECTIONS for name, _label, _icon in items]


class SidebarAccountButton(Gtk.Button):
    """Account shortcut at the foot of the sidebar, like Apple's SidebarProfileButton.

    Shows the sync account (avatar, user and service) or an invitation to sign in,
    and opens "Conta e sincronização". Icon-only, just the avatar remains.
    """

    __gtype_name__ = "PodFlowSidebarAccountButton"

    def __init__(self, sync):
        super().__init__(css_classes=["flat", "sidebar-account"], action_name="app.account")
        self._sync = sync
        box = Gtk.Box(spacing=10)
        self.avatar = Adw.Avatar(size=32, show_initials=True)
        box.append(self.avatar)
        self.texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                             hexpand=True)
        self.name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                              css_classes=["sidebar-account-name"])
        self.caption = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["sidebar-account-caption", "dim-label"])
        self.texts.append(self.name)
        self.texts.append(self.caption)
        box.append(self.texts)
        self.set_child(box)
        sync.connect("changed", lambda *_: self.update())
        self.update()

    def set_compact(self, compact: bool) -> None:
        self.texts.set_visible(not compact)
        self.set_halign(Gtk.Align.CENTER if compact else Gtk.Align.FILL)

    def update(self) -> None:
        account = self._sync.account
        if account is None:
            self.avatar.set_text("")
            self.avatar.set_icon_name("avatar-default-symbolic")
            self.name.set_text(_("Entrar"))
            self.caption.set_text(_("Sincronize seus podcasts"))
            self.set_tooltip_text(_("Conta e sincronização"))
        else:
            username = account["username"]
            self.avatar.set_text(username)
            self.name.set_text(username)
            self.caption.set_text(self._sync.service_name())
            self.set_tooltip_text(f"{username} · {self._sync.service_name()}")


class NavigationSidebar:
    """Wraps an AdwSidebar and maps items to section names."""

    def __init__(self, on_activate):
        self.widget = Adw.Sidebar()
        self._on_activate = on_activate
        self._names: list[str] = []
        self._badges: dict[str, Gtk.Label] = {}
        self._badge_counts: dict[str, int] = {}
        self._sections: list[tuple[Adw.SidebarSection, str]] = []
        self._items: list[tuple[Adw.SidebarItem, str, str]] = []
        self._collapsed: bool = False

        for title, items in SECTIONS:
            section = Adw.SidebarSection(title=_(title))
            self._sections.append((section, title))
            for name, text, icon in items:
                badge = Gtk.Label(css_classes=["sidebar-badge", "numeric"], visible=False,
                                  valign=Gtk.Align.CENTER)
                self._badges[name] = badge
                self._badge_counts[name] = 0
                item = Adw.SidebarItem(title=_(text), icon_name=icon, suffix=badge)
                self._items.append((item, name, text))
                section.append(item)
                self._names.append(name)
            self.widget.append(section)
        self.widget.connect("activated", self._on_activated)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        if collapsed:
            self.widget.add_css_class("sidebar-icon-only")
            for section, _title in self._sections:
                section.set_title("")
            for item, _name, text in self._items:
                item.set_title("")
                item.set_tooltip(_(text))
            for badge in self._badges.values():
                badge.set_visible(False)
        else:
            self.widget.remove_css_class("sidebar-icon-only")
            for section, title in self._sections:
                section.set_title(_(title))
            for item, _name, text in self._items:
                item.set_title(_(text))
                item.set_tooltip("")
            for name, badge in self._badges.items():
                badge.set_visible(self._badge_counts.get(name, 0) > 0)

    def _on_activated(self, _sidebar, index: int) -> None:
        if 0 <= index < len(self._names):
            self._on_activate(self._names[index])

    def select(self, name: str) -> None:
        if name in self._names:
            index = self._names.index(name)
            if self.widget.get_selected() != index:
                self.widget.set_selected(index)

    def set_badge(self, name: str, count: int) -> None:
        self._badge_counts[name] = count
        badge = self._badges.get(name)
        if badge is None:
            return
        badge.set_text(str(count) if count < 100 else "99+")
        badge.set_visible(count > 0 and not self._collapsed)
