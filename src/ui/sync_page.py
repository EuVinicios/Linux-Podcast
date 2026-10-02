"""Preferences page for the sync account: choose a service, sign in, sync."""

from __future__ import annotations

import time

from gi.repository import Adw, GLib, Gtk

from ..api import gpodder, http
from ..core.secrets import FILE
from ..i18n import _
from ..utils import tasks
from ..utils.time_format import format_relative
from .helpers import get_app, label

LOGIN_TIMEOUT = 15 * 60

PROVIDERS = {
    gpodder.GPODDER_NET: {
        "title": "gpodder.net",
        "subtitle": _("Gratuito e de código aberto · recomendado"),
        "icon": "network-server-symbolic",
    },
    gpodder.NEXTCLOUD: {
        "title": "Nextcloud",
        "subtitle": _("Na sua própria nuvem, com o app GPodder Sync"),
        "icon": "folder-remote-symbolic",
    },
    gpodder.CUSTOM: {
        "title": _("Outro servidor"),
        "subtitle": _("oPodSync, mygpo ou outro compatível com o gPodder"),
        "icon": "preferences-system-network-symbolic",
    },
}


def error_text(error: BaseException) -> str:
    if isinstance(error, http.OfflineError):
        return _("Você está offline")
    if isinstance(error, gpodder.SyncError):
        return str(error)
    if isinstance(error, http.NetworkError):
        return _("Não foi possível conectar ao servidor")
    return str(error) or type(error).__name__


def open_uri(widget: Gtk.Widget, uri: str) -> None:
    Gtk.UriLauncher.new(uri).launch(widget.get_root(), None, None, None)


def link_row(title: str, subtitle: str, uri: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
    row.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
    row.connect("activated", lambda r: open_uri(r, uri))
    return row


def busy_button(button: Gtk.Button, busy: bool, text: str) -> None:
    button.set_sensitive(not busy)
    if busy:
        button.set_child(Adw.Spinner(width_request=16, height_request=16))
    else:
        button.set_label(text)


class SyncPage(Adw.PreferencesPage):
    __gtype_name__ = "PodFlowSyncPage"

    def __init__(self, dialog: Adw.PreferencesDialog):
        super().__init__(title=_("Sincronização"), name="sync",
                         icon_name="podflow-sync-symbolic")
        self.dialog = dialog
        self.sync = get_app().sync
        self._groups: list[Adw.PreferencesGroup] = []
        self._signals = [self.sync.connect("changed", lambda *_: self._refresh()),
                         self.sync.connect("finished", self._on_finished)]
        dialog.connect("closed", self._disconnect)
        self._configured: bool | None = None
        self._refresh()

    def _disconnect(self, *_args) -> None:
        for handler in self._signals:
            self.sync.disconnect(handler)
        self._signals = []

    def _add(self, group: Adw.PreferencesGroup) -> None:
        self.add(group)
        self._groups.append(group)

    def _refresh(self) -> None:
        configured = self.sync.configured
        if configured != self._configured:
            self._configured = configured
            for group in self._groups:
                self.remove(group)
            self._groups = []
            if configured:
                self._build_account()
            else:
                self._build_providers()
        if configured:
            self._update_status()

    # -- signed out ------------------------------------------------------------------

    def _build_providers(self) -> None:
        hero = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      css_classes=["sync-hero"])
        box.append(Gtk.Image(icon_name="podflow-sync-symbolic", pixel_size=36,
                             halign=Gtk.Align.CENTER, css_classes=["sync-hero-icon"]))
        box.append(label(_("Seus podcasts em todos os lugares"), ("title-2",), xalign=0.5,
                         wrap=True))
        box.append(label(_("Crie uma conta gratuita para sincronizar os programas que você "
                           "segue e onde parou em cada episódio, entre computadores e apps "
                           "como AntennaPod, gPodder e Kasts."),
                         ("dim-label",), xalign=0.5, wrap=True))
        hero.add(box)
        self._add(hero)

        services = Adw.PreferencesGroup(
            title=_("Escolha um serviço"),
            description=_("O PodFlow usa o protocolo aberto do gPodder: seus dados vão só "
                          "para o servidor que você escolher."))
        for provider, info in PROVIDERS.items():
            row = Adw.ActionRow(title=info["title"], subtitle=info["subtitle"],
                                activatable=True)
            row.add_prefix(Gtk.Image(icon_name=info["icon"]))
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, p=provider: self._open_login(p))
            services.add(row)
        self._add(services)

    def _open_login(self, provider: str) -> None:
        self.dialog.push_subpage(LoginPage(self.dialog, provider))

    # -- signed in ---------------------------------------------------------------------

    def _build_account(self) -> None:
        account = self.sync.account or {}
        group = Adw.PreferencesGroup(title=_("Conta"))
        row = Adw.ActionRow(title=account.get("username", ""), subtitle=self.sync.service_name(),
                            subtitle_selectable=True)
        row.add_prefix(Adw.Avatar(size=40, text=account.get("username", ""), show_initials=True))
        sign_out = Gtk.Button(label=_("Sair"), valign=Gtk.Align.CENTER,
                              css_classes=["destructive-action"])
        sign_out.connect("clicked", self._on_sign_out)
        row.add_suffix(sign_out)
        group.add(row)
        self._add(group)

        status = Adw.PreferencesGroup(title=_("Sincronização"))
        self.status_row = Adw.ActionRow(title=_("Última sincronização"))
        self.sync_button = Gtk.Button(label=_("Sincronizar agora"), valign=Gtk.Align.CENTER)
        self.sync_button.connect("clicked", lambda *_: self.sync.sync_now(manual=True))
        self.status_row.add_suffix(self.sync_button)
        status.add(self.status_row)

        auto = Adw.SwitchRow(title=_("Sincronizar automaticamente"),
                             subtitle=_("A cada 30 minutos e sempre que algo mudar"),
                             active=self.sync.auto_sync)
        auto.connect("notify::active", lambda r, _p: setattr(self.sync, "auto_sync",
                                                              r.get_active()))
        status.add(auto)
        status.add(Adw.ActionRow(title=_("Este computador"), subtitle=self.sync.device_caption()))
        self._add(status)

        if self.sync.storage == FILE:
            warning = Adw.PreferencesGroup()
            row = Adw.ActionRow(
                title=_("Senha guardada em um arquivo privado"),
                subtitle=_("O chaveiro do sistema não está acessível. No Snap, libere-o com "
                           "“sudo snap connect podflow:password-manager-service” e entre de novo."),
                subtitle_selectable=True)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            warning.add(row)
            self._add(warning)

        what = Adw.PreferencesGroup(
            description=_("São sincronizados os programas seguidos e o progresso de cada "
                          "episódio. Downloads, a fila A Seguir e as preferências ficam só "
                          "neste computador."))
        self._add(what)

    def _update_status(self) -> None:
        if self.sync.syncing:
            self.status_row.set_subtitle(_("Sincronizando…"))
            busy_button(self.sync_button, True, _("Sincronizar agora"))
            return
        busy_button(self.sync_button, False, _("Sincronizar agora"))
        error = self.sync.last_error
        last = self.sync.last_sync
        if error:
            self.status_row.set_subtitle(_("Falhou: {error}").format(error=error))
            self.status_row.add_css_class("error")
        else:
            self.status_row.remove_css_class("error")
            self.status_row.set_subtitle(format_relative(last) if last else _("Ainda não"))

    def _on_finished(self, _sync, error: str, manual: bool, summary) -> None:
        if not manual:
            return
        if error:
            self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(error)))
            return
        details = summary.describe() if summary is not None else ""
        self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(
            _("Sincronizado: {details}").format(details=details) if details
            else _("Tudo sincronizado"))))

    def _on_sign_out(self, _button) -> None:
        alert = Adw.AlertDialog(
            heading=_("Sair da conta?"),
            body=_("Seus programas e o progresso continuam neste computador. A sincronização "
                   "para até você entrar de novo."))
        alert.add_response("cancel", _("Cancelar"))
        alert.add_response("sign-out", _("Sair"))
        alert.set_response_appearance("sign-out", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_close_response("cancel")
        alert.connect("response", lambda _a, r: self.sync.sign_out() if r == "sign-out" else None)
        alert.present(self.dialog)


class LoginPage(Adw.NavigationPage):
    """Sign-in form for one provider (pushed as a preferences subpage)."""

    __gtype_name__ = "PodFlowLoginPage"

    def __init__(self, dialog: Adw.PreferencesDialog, provider: str):
        info = PROVIDERS[provider]
        super().__init__(title=_("Entrar no {service}").format(service=info["title"])
                         if provider != gpodder.CUSTOM else _("Entrar no servidor"))
        self.dialog = dialog
        self.provider = provider
        self._flow: gpodder.LoginFlow | None = None
        self._poll_source = 0
        self._polling = False
        self._poll_inflight = False
        self._deadline = 0.0

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        self.set_child(toolbar)
        self.connect("hidden", lambda *_: self._stop_polling())

        self.server_row = None
        if provider != gpodder.GPODDER_NET:
            server = Adw.PreferencesGroup(
                title=_("Servidor"),
                description=(_("O Nextcloud precisa ter o app GPodder Sync instalado.")
                             if provider == gpodder.NEXTCLOUD else
                             _("Endereço de um servidor compatível com a API do gPodder.")))
            self.server_row = Adw.EntryRow(
                title=_("Endereço do Nextcloud") if provider == gpodder.NEXTCLOUD
                else _("Endereço do servidor"), input_purpose=Gtk.InputPurpose.URL)
            self.server_row.connect("changed", lambda *_: self._validate())
            server.add(self.server_row)
            page.add(server)

        if provider == gpodder.NEXTCLOUD:
            browser = Adw.PreferencesGroup()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.browser_button = Gtk.Button(label=_("Entrar com o navegador"),
                                             halign=Gtk.Align.CENTER,
                                             css_classes=["pill", "suggested-action"])
            self.browser_button.connect("clicked", self._on_browser_login)
            box.append(self.browser_button)
            self.browser_status = label(_("Você aprova o acesso no Nextcloud e volta para cá."),
                                        ("dim-label", "caption"), xalign=0.5, wrap=True)
            box.append(self.browser_status)
            browser.add(box)
            page.add(browser)

        credentials = Adw.PreferencesGroup(
            title=_("Ou use uma senha de app") if provider == gpodder.NEXTCLOUD
            else _("Sua conta"))
        self.user_row = Adw.EntryRow(title=_("Usuário"))
        self.password_row = Adw.PasswordEntryRow(
            title=_("Senha de app") if provider == gpodder.NEXTCLOUD else _("Senha"))
        for row in (self.user_row, self.password_row):
            row.connect("changed", lambda *_: self._validate())
            row.connect("entry-activated", lambda *_: self._on_sign_in())
            credentials.add(row)
        page.add(credentials)

        actions = Adw.PreferencesGroup()
        self.sign_in_button = Gtk.Button(
            label=_("Entrar"), halign=Gtk.Align.CENTER,
            css_classes=["pill"] if provider == gpodder.NEXTCLOUD
            else ["pill", "suggested-action"])
        self.sign_in_button.connect("clicked", lambda *_: self._on_sign_in())
        actions.add(self.sign_in_button)
        page.add(actions)

        help_group = Adw.PreferencesGroup(title=_("Ainda não tem conta?"))
        if provider == gpodder.GPODDER_NET:
            help_group.add(link_row(_("Criar conta gratuita"), "gpodder.net/register",
                                    gpodder.GPODDER_NET_REGISTER_URL))
        elif provider == gpodder.NEXTCLOUD:
            help_group.add(link_row(_("Criar conta Nextcloud gratuita"),
                                    _("Provedores parceiros com plano grátis"),
                                    gpodder.NEXTCLOUD_SIGNUP_URL))
            help_group.add(link_row(_("Instalar o app GPodder Sync"),
                                    "apps.nextcloud.com/apps/gpoddersync",
                                    gpodder.NEXTCLOUD_APP_URL))
        else:
            help_group.add(link_row(_("Hospedar o seu próprio servidor"),
                                    _("oPodSync: leve, livre e fácil de instalar"),
                                    gpodder.OPODSYNC_URL))
        page.add(help_group)
        self._validate()

    # -- form ------------------------------------------------------------------------

    def _server(self) -> str:
        if self.server_row is None:
            return gpodder.GPODDER_NET_URL
        return self.server_row.get_text().strip()

    def _validate(self) -> None:
        ready = bool(self._server() and self.user_row.get_text().strip()
                     and self.password_row.get_text())
        self.sign_in_button.set_sensitive(ready)
        if self.provider == gpodder.NEXTCLOUD and not self._polling:
            self.browser_button.set_sensitive(bool(self._server()))

    def _set_busy(self, busy: bool) -> None:
        busy_button(self.sign_in_button, busy, _("Entrar"))
        for row in (self.user_row, self.password_row, self.server_row):
            if row is not None:
                row.set_sensitive(not busy)
        if not busy:
            self._validate()

    def _on_sign_in(self) -> None:
        if not self.sign_in_button.get_sensitive():
            return
        self._sign_in(self._server(), self.user_row.get_text(), self.password_row.get_text())

    def _sign_in(self, server: str, username: str, password: str) -> None:
        self._set_busy(True)
        get_app().sync.sign_in(self.provider, server, username, password,
                               on_done=lambda: self._signed_in(username),
                               on_error=self._failed)

    def _signed_in(self, username: str) -> None:
        self._stop_polling()
        self._set_busy(False)
        self.dialog.pop_subpage()
        self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(
            _("Conectado como {user}. Sincronizando…").format(user=username.strip()))))

    def _failed(self, error: BaseException) -> None:
        self._set_busy(False)
        self._stop_polling()
        self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(error_text(error)),
                                        timeout=5))

    # -- Nextcloud Login Flow v2 ---------------------------------------------------

    def _on_browser_login(self, _button) -> None:
        if self._polling:
            self._stop_polling()
            return
        busy_button(self.browser_button, True, _("Entrar com o navegador"))
        tasks.run_async(gpodder.nextcloud_login_start, self._server(),
                        on_done=self._on_flow_started, on_error=self._on_flow_error)

    def _on_flow_started(self, flow: gpodder.LoginFlow) -> None:
        self._flow = flow
        self._polling = True
        self._deadline = time.monotonic() + LOGIN_TIMEOUT
        busy_button(self.browser_button, False, _("Cancelar"))
        self.browser_button.remove_css_class("suggested-action")
        self.browser_status.set_text(_("Aguardando a aprovação no navegador…"))
        open_uri(self, flow.login_url)
        self._poll_source = GLib.timeout_add_seconds(2, self._poll)

    def _on_flow_error(self, error: BaseException) -> None:
        busy_button(self.browser_button, False, _("Entrar com o navegador"))
        self._failed(error)

    def _poll(self) -> bool:
        if not self._polling or self._flow is None:
            self._poll_source = 0
            return GLib.SOURCE_REMOVE
        if time.monotonic() > self._deadline:
            self._poll_source = 0
            self._stop_polling()
            self.browser_status.set_text(_("O tempo para entrar acabou. Tente de novo."))
            return GLib.SOURCE_REMOVE
        if not self._poll_inflight:
            self._poll_inflight = True
            tasks.run_async(gpodder.nextcloud_login_poll, self._flow, on_done=self._on_poll,
                            on_error=self._on_poll_error)
        return GLib.SOURCE_CONTINUE

    def _on_poll_error(self, error: BaseException) -> None:
        self._poll_inflight = False
        if self._polling:
            self._on_flow_error(error)

    def _on_poll(self, credentials: dict[str, str] | None) -> None:
        self._poll_inflight = False
        if credentials is None or not self._polling:
            return
        self._stop_polling()
        self.browser_status.set_text(_("Acesso aprovado. Conectando…"))
        self._sign_in(credentials["server"] or self._server(), credentials["username"],
                      credentials["password"])

    def _stop_polling(self) -> None:
        self._polling = False
        self._flow = None
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0
        if self.provider == gpodder.NEXTCLOUD:
            busy_button(self.browser_button, False, _("Entrar com o navegador"))
            self.browser_button.add_css_class("suggested-action")
            self._validate()
