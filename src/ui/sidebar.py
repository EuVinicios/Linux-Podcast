"""Navigation sidebar (AdwSidebar): "Descobrir" and "Biblioteca" sections."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from ..i18n import _

SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Descobrir", [
        ("listen-now", "Ouvir Agora", "media-playback-start-symbolic"),
        ("explore", "Explorar", "view-app-grid-symbolic"),
        ("charts", "Rankings", "podflow-chart-symbolic"),
        ("search", "Buscar", "system-search-symbolic"),
    ]),
    ("Biblioteca", [
        ("following", "Programas Seguidos", "view-grid-symbolic"),
        ("saved", "Episódios Salvos", "starred-symbolic"),
        ("downloads", "Downloads", "folder-download-symbolic"),
        ("history", "Histórico", "document-open-recent-symbolic"),
    ]),
]

SECTION_NAMES = [name for _title, items in SECTIONS for name, _label, _icon in items]


class NavigationSidebar:
    """Wraps an AdwSidebar and maps items to section names."""

    def __init__(self, on_activate):
        self.widget = Adw.Sidebar()
        self._on_activate = on_activate
        self._names: list[str] = []
        self._badges: dict[str, Gtk.Label] = {}
        for title, items in SECTIONS:
            section = Adw.SidebarSection(title=_(title))
            for name, text, icon in items:
                badge = Gtk.Label(css_classes=["sidebar-badge", "numeric"], visible=False,
                                  valign=Gtk.Align.CENTER)
                self._badges[name] = badge
                section.append(Adw.SidebarItem(title=_(text), icon_name=icon, suffix=badge))
                self._names.append(name)
            self.widget.append(section)
        self.widget.connect("activated", self._on_activated)

    def _on_activated(self, _sidebar, index: int) -> None:
        if 0 <= index < len(self._names):
            self._on_activate(self._names[index])

    def select(self, name: str) -> None:
        if name in self._names:
            index = self._names.index(name)
            if self.widget.get_selected() != index:
                self.widget.set_selected(index)

    def set_badge(self, name: str, count: int) -> None:
        badge = self._badges.get(name)
        if badge is None:
            return
        badge.set_text(str(count) if count < 100 else "99+")
        badge.set_visible(count > 0)
