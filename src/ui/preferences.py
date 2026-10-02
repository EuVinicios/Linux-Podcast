"""Preferences dialog: appearance and storage."""

from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from ..i18n import _
from ..utils.time_format import format_size
from .helpers import get_app

SCHEMES = (("system", "Seguir o sistema", Adw.ColorScheme.DEFAULT),
           ("light", "Claro", Adw.ColorScheme.FORCE_LIGHT),
           ("dark", "Escuro", Adw.ColorScheme.FORCE_DARK))


def apply_color_scheme(value: str) -> None:
    for key, _label, scheme in SCHEMES:
        if key == value:
            Adw.StyleManager.get_default().set_color_scheme(scheme)
            return
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)


class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "PodFlowPreferencesDialog"

    def __init__(self):
        super().__init__(title=_("Preferências"), search_enabled=False)
        app = get_app()
        page = Adw.PreferencesPage(title=_("Geral"), icon_name="preferences-system-symbolic")
        self.add(page)

        appearance = Adw.PreferencesGroup(title=_("Aparência"))
        page.add(appearance)
        theme = Adw.ComboRow(title=_("Tema"),
                             subtitle=_("O PodFlow acompanha o modo claro/escuro do GNOME"),
                             model=Gtk.StringList.new([_(label) for _k, label, _s in SCHEMES]))
        current = app.db.get_setting("color_scheme", "system")
        keys = [key for key, _label, _scheme in SCHEMES]
        theme.set_selected(keys.index(current) if current in keys else 0)
        theme.connect("notify::selected", self._on_theme, keys)
        appearance.add(theme)

        storage = Adw.PreferencesGroup(title=_("Armazenamento"))
        page.add(storage)
        self.cache_row = Adw.ActionRow(title=_("Cache de capas"))
        clear_cache = Gtk.Button(label=_("Limpar"), valign=Gtk.Align.CENTER)
        clear_cache.connect("clicked", self._on_clear_cache)
        self.cache_row.add_suffix(clear_cache)
        storage.add(self.cache_row)

        self.downloads_row = Adw.ActionRow(title=_("Episódios baixados"))
        open_folder = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                                 tooltip_text=_("Abrir pasta"), css_classes=["flat"])
        open_folder.connect("clicked", self._on_open_folder)
        self.downloads_row.add_suffix(open_folder)
        storage.add(self.downloads_row)

        about = Adw.PreferencesGroup(title=_("Dados"),
                                     description=_("Rankings, buscas e capas vêm das APIs públicas "
                                                   "da Apple para o Brasil. Os episódios são "
                                                   "reproduzidos direto dos feeds dos produtores."))
        page.add(about)
        self._update_sizes()

    def _on_theme(self, row: Adw.ComboRow, _pspec, keys: list[str]) -> None:
        value = keys[row.get_selected()]
        get_app().db.set_setting("color_scheme", value)
        apply_color_scheme(value)

    def _update_sizes(self) -> None:
        app = get_app()
        self.cache_row.set_subtitle(format_size(app.images.disk_usage()) or _("Vazio"))
        downloads = app.db.list_downloaded()
        total = sum(e.download_size for e in downloads)
        count = len(downloads)
        text = (_("{count} episódios").format(count=count) if count != 1 else _("1 episódio"))
        self.downloads_row.set_subtitle(f"{text} · {format_size(total)}" if total else _("Nenhum"))

    def _on_clear_cache(self, _button) -> None:
        get_app().images.clear()
        self._update_sizes()
        self.add_toast(Adw.Toast(title=_("Cache de capas limpo")))

    def _on_open_folder(self, _button) -> None:
        folder = Gio.File.new_for_path(str(get_app().downloads.directory))
        Gtk.FileLauncher.new(folder).launch(self.get_root(), None, None, None)
