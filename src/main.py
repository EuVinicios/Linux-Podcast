"""PodFlow entry point: the Adw.Application and its global actions."""

from __future__ import annotations

import logging
import os
import sys

from gi.repository import Adw, Gdk, Gio, GLib, Gst, Gtk

from . import config
from .api.apple_service import AppleService
from .api.itunes_service import ItunesService
from .core.database import Database
from .core.downloader import DownloadManager
from .core.library import Library
from .core.mpris import MprisService
from .core.playback import PlaybackManager
from .core.player import AudioPlayer
from .core.sync import SyncManager
from .i18n import _
from .ui.preferences import PreferencesDialog, apply_accent, apply_color_scheme
from .utils import tasks
from .utils.cache import ImageCache

log = logging.getLogger("podflow")


class PodFlowApplication(Adw.Application):
    __gtype_name__ = "PodFlowApplication"

    def __init__(self):
        super().__init__(application_id=config.APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(config.APP_NAME)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Mostrar a versão e sair"), None)
        self.add_main_option("debug", ord("d"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Mostrar mensagens de depuração"), None)
        self.window = None
        self.db: Database | None = None
        self.images: ImageCache | None = None
        self.apple: AppleService | None = None
        self.library: Library | None = None
        self.playback: PlaybackManager | None = None
        self.downloads: DownloadManager | None = None
        self.mpris: MprisService | None = None
        self.sync: SyncManager | None = None

    # -- lifecycle -------------------------------------------------------------------

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("version"):
            print(f"{config.APP_NAME} {config.VERSION}")
            return 0
        level = logging.DEBUG if options.contains("debug") or os.environ.get("PODFLOW_DEBUG") \
            else logging.INFO
        logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
        return -1

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Gst.init(None)
        self._load_style()

        self.db = Database()
        self.images = ImageCache()
        itunes = ItunesService()
        self.apple = AppleService(self.db, itunes)
        self.library = Library(self.db, self.apple, itunes)
        self.playback = PlaybackManager(self.db, AudioPlayer(), app=self)
        self.downloads = DownloadManager(self.db)
        self.sync = SyncManager(self.db, self.library, self.playback)
        self.mpris = MprisService(self.playback, self.db, self.images, app=self)
        self.mpris.start()
        apply_color_scheme(self.db.get_setting("color_scheme", "system"))
        apply_accent(self.db.get_setting("accent", "brand"))

        self._install_actions()
        self.downloads.connect("finished", self._on_download_finished)
        self.downloads.connect("failed", lambda _d, _id, error: self._toast(
            _("O download falhou: {error}").format(error=error)))
        self.playback.connect("sleep-timer-changed", lambda *_: self._sync_sleep_action())
        self.playback.connect("rate-changed", lambda _pb, rate: self.lookup_action(
            "playback-rate").set_state(GLib.Variant("d", round(rate, 2))))
        self.sync.connect("changed", lambda *_: self._sync_sync_action())
        self.sync.connect("finished", self._on_sync_finished)
        self._sync_sync_action()

    def do_activate(self) -> None:
        if self.window is None:
            from .window import PodFlowWindow
            self.window = PodFlowWindow(self)
            self.playback.restore_session()
            GLib.timeout_add_seconds(2, self._initial_refresh)
            self.sync.start()
        self.window.present()

    def _initial_refresh(self) -> bool:
        self.library.refresh_subscriptions()
        return GLib.SOURCE_REMOVE

    def do_shutdown(self) -> None:
        try:
            if self.sync is not None:
                self.sync.stop()
            if self.mpris is not None:
                self.mpris.stop()
            if self.playback is not None:
                self.playback.shutdown()
            if self.downloads is not None:
                self.downloads.shutdown()
            if self.images is not None:
                self.images.shutdown()
            tasks.shutdown()
            if self.db is not None:
                self.db.close()
        finally:
            Adw.Application.do_shutdown(self)

    def _load_style(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(config.PKG_DIR / "ui" / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme = Gtk.IconTheme.get_for_display(display)
        theme.add_search_path(str(config.PKG_DIR / "icons"))
        source_data = config.source_data_dir()
        if source_data is not None:  # running from a git checkout
            theme.add_search_path(str(source_data / "icons"))

    # -- actions ---------------------------------------------------------------------

    def _install_actions(self) -> None:
        simple = {
            "quit": self.quit,
            "about": self._show_about,
            "preferences": lambda: PreferencesDialog().present(self.get_active_window()),
            "account": lambda: PreferencesDialog("sync").present(self.get_active_window()),
            "sync": lambda: self.sync.sync_now(manual=True),
            "shortcuts": self._show_shortcuts,
            "refresh": lambda: self.library.refresh_subscriptions(force=True),
            "add-feed": self._show_add_feed,
            "play-pause": self.playback.toggle,
            "skip-back": self.playback.skip_back,
            "skip-forward": self.playback.skip_forward,
            "next": self.playback.play_next,
        }
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

        episode_actions = {
            "play-episode": self._play_episode,
            "play-next": lambda e: self._enqueue(e, front=True),
            "enqueue": self._enqueue,
            "dequeue": lambda e: self.db.queue_remove(e.id),
            "toggle-saved": self._toggle_saved,
            "mark-played": lambda e: self.db.set_played(e.id, True),
            "mark-unplayed": lambda e: self.db.set_played(e.id, False),
            "download": self._download,
            "cancel-download": lambda e: self.downloads.cancel(e.id),
            "delete-download": self.downloads.delete,
            "copy-link": self._copy_link,
        }
        for name, callback in episode_actions.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", self._on_episode_action, callback)
            self.add_action(action)

        rate = Gio.SimpleAction.new_stateful("playback-rate", GLib.VariantType.new("d"),
                                             GLib.Variant("d", round(self.playback.rate, 2)))
        rate.connect("activate", lambda action, value: self.playback.set_rate(value.get_double()))
        self.add_action(rate)
        sleep = Gio.SimpleAction.new_stateful("sleep-timer", GLib.VariantType.new("i"),
                                              GLib.Variant("i", 0))
        sleep.connect("activate", self._on_sleep_action)
        self.add_action(sleep)

        accels = {
            "app.quit": ["<Control>q"],
            "app.preferences": ["<Control>comma"],
            "app.shortcuts": ["<Control>question"],
            "app.skip-back": ["<Control>Left"],
            "app.skip-forward": ["<Control>Right"],
            "app.next": ["<Control>n"],
            "win.search": ["<Control>f"],
            "win.toggle-queue": ["<Control>u"],
            "win.refresh": ["<Control>r", "F5"],
            "window.close": ["<Control>w"],
        }
        for action_name, keys in accels.items():
            self.set_accels_for_action(action_name, keys)

    def _on_episode_action(self, _action, value: GLib.Variant, callback) -> None:
        episode = self.db.get_episode(value.get_string())
        if episode is None:
            self._toast(_("Episódio indisponível"))
            return
        callback(episode)

    def _play_episode(self, episode) -> None:
        if not episode.audio_url and not episode.is_downloaded:
            self._toast(_("Este episódio não tem áudio disponível"))
            return
        self.playback.play_episode(episode)

    def _enqueue(self, episode, front: bool = False) -> None:
        if self.playback.current is None:
            self.playback.play_episode(episode)
            return
        self.playback.enqueue(episode, front=front)
        self._toast(_("Vai tocar em seguida") if front else _("Adicionado a A Seguir"))

    def _toggle_saved(self, episode) -> None:
        self.db.set_saved(episode.id, not episode.saved)
        self._toast(_("Episódio salvo") if not episode.saved else _("Removido dos salvos"))

    def _download(self, episode) -> None:
        self.downloads.download(episode)
        self._toast(_("Baixando “{title}”").format(title=episode.title))

    def _copy_link(self, episode) -> None:
        link = episode.link or episode.audio_url
        Gdk.Display.get_default().get_clipboard().set(link)
        self._toast(_("Link copiado"))

    def _on_sleep_action(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        minutes = value.get_int32()
        action.set_state(value)
        if minutes == -1:
            self.playback.set_sleep_timer(end_of_episode=True)
            self._toast(_("A reprodução para no fim do episódio"))
        elif minutes > 0:
            self.playback.set_sleep_timer(minutes)
            self._toast(_("Timer de sono: {minutes} min").format(minutes=minutes))
        else:
            self.playback.set_sleep_timer(None)

    def _sync_sleep_action(self) -> None:
        if self.playback.sleep_mode is None:
            action = self.lookup_action("sleep-timer")
            if action.get_state().get_int32() != 0:
                action.set_state(GLib.Variant("i", 0))

    def _sync_sync_action(self) -> None:
        self.lookup_action("sync").set_enabled(self.sync.configured and not self.sync.syncing)

    def _on_sync_finished(self, _sync, error: str, manual: bool, summary) -> None:
        window = self.window
        if not manual or window is None or window.get_visible_dialog() is not None:
            return
        if error:
            self._toast(_("Não foi possível sincronizar: {error}").format(error=error))
        else:
            details = summary.describe() if summary is not None else ""
            self._toast(_("Sincronizado: {details}").format(details=details) if details
                        else _("Tudo sincronizado"))

    def _on_download_finished(self, _downloads, episode_id: str) -> None:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            return
        window = self.get_active_window()
        if window is not None and window.is_active():
            self._toast(_("Download concluído: {title}").format(title=episode.title))
            return
        notification = Gio.Notification.new(_("Download concluído"))
        notification.set_body(episode.title)
        self.send_notification(f"download-{episode_id}", notification)

    def _toast(self, message: str) -> None:
        if self.window is not None:
            self.window.show_toast(message)

    # -- dialogs -----------------------------------------------------------------------

    def _show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=config.APP_NAME,
            application_icon=config.APP_ID,
            developer_name=config.DEVELOPER,
            version=config.VERSION,
            website=config.WEBSITE,
            issue_url=config.ISSUE_URL,
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 EuVinicios",
            comments=_("Um app de podcasts nativo para o GNOME, com rankings e novidades "
                       "do Brasil."),
            developers=[f"{config.DEVELOPER} https://github.com/EuVinicios"],
            support_url=f"{config.WEBSITE}#perguntas",
            release_notes_version=config.VERSION,
            release_notes=_(
                "<p>Contas e sincronização, visual novo e pacote snap.</p><ul>"
                "<li>Sincronize programas e progresso com gpodder.net, Nextcloud ou um "
                "servidor compatível com o gPodder</li>"
                "<li>Novo ícone e cor de destaque roxa (ou a do sistema, nas Preferências)</li>"
                "<li>Senhas guardadas no chaveiro do sistema</li></ul>"),
        )
        about.add_link(_("Código-fonte"), config.REPOSITORY)
        about.add_link(_("Política de privacidade"), f"{config.WEBSITE}privacidade.html")
        about.add_legal_section(
            _("Dados do catálogo"),
            _("Rankings, buscas e capas são fornecidos pelas APIs públicas da Apple. "
              "O PodFlow não é afiliado à Apple Inc."),
            Gtk.License.UNKNOWN, None)
        about.present(self.get_active_window())

    def _show_shortcuts(self) -> None:
        dialog = Adw.ShortcutsDialog()
        playback = Adw.ShortcutsSection.new(_("Reprodução"))
        for title, accel in ((_("Reproduzir / pausar"), "space"),
                             (_("Voltar 15 segundos"), "<Control>Left"),
                             (_("Avançar 30 segundos"), "<Control>Right"),
                             (_("Próximo de A Seguir"), "<Control>n"),
                             (_("Mostrar A Seguir"), "<Control>u")):
            playback.add(Adw.ShortcutsItem.new(title, accel))
        dialog.add(playback)
        general = Adw.ShortcutsSection.new(_("Geral"))
        for title, accel in ((_("Buscar"), "<Control>f"),
                             (_("Atualizar"), "<Control>r"),
                             (_("Voltar"), "<Alt>Left"),
                             (_("Preferências"), "<Control>comma"),
                             (_("Atalhos de teclado"), "<Control>question"),
                             (_("Fechar janela"), "<Control>w"),
                             (_("Sair"), "<Control>q")):
            general.add(Adw.ShortcutsItem.new(title, accel))
        dialog.add(general)
        dialog.present(self.get_active_window())

    def _show_add_feed(self) -> None:
        dialog = Adw.AlertDialog(heading=_("Adicionar podcast por feed RSS"),
                                 body=_("Cole o endereço do feed do podcast."))
        entry = Gtk.Entry(placeholder_text="https://exemplo.com/feed.xml", activates_default=True,
                          input_purpose=Gtk.InputPurpose.URL)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancelar"))
        dialog.add_response("add", _("Adicionar"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response: str) -> None:
            url = entry.get_text().strip()
            if response != "add" or not url:
                return
            self._toast(_("Carregando feed…"))

            def done(podcast) -> None:
                self.library.set_subscribed(podcast.id, True)
                if self.window is not None:
                    self.window.open_podcast(podcast.id)

            self.library.add_feed(url, done, lambda error: self._toast(
                _("Não foi possível adicionar o feed: {error}").format(error=error)))

        dialog.connect("response", on_response)
        dialog.present(self.get_active_window())


def main() -> int:
    app = PodFlowApplication()
    return app.run(sys.argv)
