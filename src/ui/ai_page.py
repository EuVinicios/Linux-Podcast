"""Preferences page for optional OpenRouter BYOK AI transcription and language study."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ..api import openrouter
from ..core.ai import FILE, mask_key_hint
from ..i18n import _
from ..utils import tasks
from ..utils.time_format import format_duration
from .helpers import get_app, label
from .sync_page import busy_button, link_row, open_uri

_CUSTOM = "__custom__"


def _format_usd(amount: float) -> str:
    if amount <= 0:
        return "US$ 0,00"
    if amount < 0.01:
        return f"US$ {amount:.4f}".replace(".", ",")
    return f"US$ {amount:.2f}".replace(".", ",")


class AIPage(Adw.PreferencesPage):
    __gtype_name__ = "PodFlowAIPage"

    def __init__(self, dialog: Adw.PreferencesDialog):
        super().__init__(title=_("IA e Transcrição"), name="ai",
                         icon_name="accessories-dictionary-symbolic")
        self.dialog = dialog
        app = get_app()
        self.ai = getattr(app, "ai", None)
        self.db = getattr(app, "db", None)
        self._groups: list[Adw.PreferencesGroup] = []
        self._pkce: openrouter.PKCECallbackServer | None = None
        self._configured: bool | None = None
        self._signals = [
            self.ai.connect("changed", lambda *_: self._refresh()),
            self.ai.connect("usage-changed", lambda *_: self._update_usage_row()),
        ]
        dialog.connect("closed", self._on_closed)
        self._refresh()

    def _on_closed(self, *_args) -> None:
        self._stop_pkce()
        for handler in self._signals:
            self.ai.disconnect(handler)
        self._signals = []

    def _add(self, group: Adw.PreferencesGroup) -> None:
        self.add(group)
        self._groups.append(group)

    def _refresh(self) -> None:
        configured = self.ai.configured
        if configured != self._configured:
            self._configured = configured
            for group in self._groups:
                self.remove(group)
            self._groups = []
            if configured:
                self._build_connected()
            else:
                self._build_disconnected()
        elif configured:
            self._update_account_subtitle()
            self._update_usage_row()

    # -- disconnected (100% optional) ------------------------------------------------

    def _build_disconnected(self) -> None:
        hero = Adw.PreferencesGroup()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["sync-hero"])
        box.append(Gtk.Image(icon_name="accessories-dictionary-symbolic", pixel_size=36,
                             halign=Gtk.Align.CENTER, css_classes=["sync-hero-icon"]))
        box.append(label(_("Transcrição ao vivo e estudo de idiomas"), ("title-2",),
                         xalign=0.5, wrap=True))
        box.append(label(_("Conecte sua própria conta do OpenRouter para transcrever episódios "
                           "em tempo real, ver tradução bilíngue e estudar expressões em inglês. "
                           "Totalmente opcional: feeds com transcrição nativa já funcionam grátis."),
                         ("dim-label",), xalign=0.5, wrap=True))
        hero.add(box)
        self._add(hero)

        oauth_group = Adw.PreferencesGroup(
            title=_("Conexão rápida"),
            description=_("Cada usuário usa seus próprios créditos direto no OpenRouter. "
                          "O PodFlow nunca compartilha nem envia sua chave a outros servidores."))
        oauth_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.oauth_button = Gtk.Button(label=_("Conectar com OpenRouter"),
                                       halign=Gtk.Align.CENTER,
                                       css_classes=["pill", "suggested-action"])
        self.oauth_button.connect("clicked", self._on_pkce_clicked)
        oauth_box.append(self.oauth_button)
        self.oauth_status = label(_("Abre o navegador para autorizar com segurança (OAuth PKCE)."),
                                  ("dim-label", "caption"), xalign=0.5, wrap=True)
        oauth_box.append(self.oauth_status)
        oauth_group.add(oauth_box)
        self._add(oauth_group)

        manual_group = Adw.PreferencesGroup(
            title=_("Ou cole sua chave de API"),
            description=_("Dica: você pode criar uma chave com limite mensal de gastos no painel "
                          "do OpenRouter."))
        self.key_entry = Adw.PasswordEntryRow(title=_("Chave do OpenRouter (sk-or-v1-…)"))
        self.key_entry.connect("changed", self._on_key_entry_changed)
        self.key_entry.connect("entry-activated", lambda *_: self._on_save_manual_key())
        manual_group.add(self.key_entry)

        save_row = Adw.PreferencesGroup()
        self.save_key_button = Gtk.Button(label=_("Salvar chave"), halign=Gtk.Align.CENTER,
                                          css_classes=["pill"])
        self.save_key_button.set_sensitive(False)
        self.save_key_button.connect("clicked", lambda *_: self._on_save_manual_key())
        save_row.add(self.save_key_button)
        self._add(manual_group)
        self._add(save_row)

        help_group = Adw.PreferencesGroup(title=_("Precisa de uma chave?"))
        help_group.add(link_row(_("Criar chave de API no OpenRouter"),
                                "openrouter.ai/settings/keys",
                                openrouter.KEYS_URL))
        help_group.add(link_row(_("Ver créditos e preços dos modelos"),
                                "openrouter.ai/settings/credits",
                                openrouter.CREDITS_URL))
        self._add(help_group)

    def _on_key_entry_changed(self, *_args) -> None:
        self.save_key_button.set_sensitive(bool(self.key_entry.get_text().strip()))

    def _on_save_manual_key(self) -> None:
        key = self.key_entry.get_text().strip()
        if not key:
            return
        self._stop_pkce()
        busy_button(self.save_key_button, True, _("Salvar chave"))
        self.key_entry.set_sensitive(False)
        self.ai.connect_with_key(key, on_done=self._on_connected, on_error=self._on_connect_error)

    def _on_pkce_clicked(self, _button) -> None:
        if self._pkce is not None:
            self._stop_pkce()
            return
        try:
            self._pkce = openrouter.PKCECallbackServer(self._on_pkce_callback)
        except OSError as error:
            self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(str(error))))
            return
        self.oauth_button.set_label(_("Cancelar"))
        self.oauth_button.remove_css_class("suggested-action")
        self.oauth_status.set_text(_("Aguardando autorização no navegador…"))
        open_uri(self, self._pkce.auth_url)

    def _on_pkce_callback(self, code: str, error: str) -> None:
        verifier = self._pkce.verifier if self._pkce is not None else ""
        tasks.idle(self._finish_pkce_callback, code, error, verifier)

    def _finish_pkce_callback(self, code: str, error: str, verifier: str) -> None:
        self._stop_pkce()
        if error or not code:
            msg = error or _("Autorização cancelada")
            self.oauth_status.set_text(msg)
            self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg)))
            return
        busy_button(self.oauth_button, True, _("Conectar com OpenRouter"))
        self.oauth_status.set_text(_("Trocando código pela chave de API…"))

        def worker() -> str:
            return openrouter.exchange_pkce_code(code, verifier)

        def got_key(api_key: str) -> None:
            self.ai.connect_with_key(api_key, on_done=self._on_connected,
                                     on_error=self._on_connect_error)

        tasks.run_async(worker, on_done=got_key, on_error=self._on_connect_error)

    def _stop_pkce(self) -> None:
        if self._pkce is not None:
            self._pkce.stop()
            self._pkce = None
        if getattr(self, "oauth_button", None) is not None and not self.ai.configured:
            busy_button(self.oauth_button, False, _("Conectar com OpenRouter"))
            self.oauth_button.add_css_class("suggested-action")
            self.oauth_status.set_text(
                _("Abre o navegador para autorizar com segurança (OAuth PKCE)."))

    def _on_connected(self, _info: openrouter.KeyInfo) -> None:
        self.dialog.add_toast(Adw.Toast(title=_("OpenRouter conectado com sucesso")))

    def _on_connect_error(self, error: BaseException) -> None:
        if getattr(self, "save_key_button", None) is not None and not self.ai.configured:
            busy_button(self.save_key_button, False, _("Salvar chave"))
            self.key_entry.set_sensitive(True)
            self._on_key_entry_changed()
        if getattr(self, "oauth_button", None) is not None and not self.ai.configured:
            busy_button(self.oauth_button, False, _("Conectar com OpenRouter"))
            self.oauth_button.add_css_class("suggested-action")
            self.oauth_status.set_text(str(error))
        self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(str(error)), timeout=5))

    # -- connected -------------------------------------------------------------------

    def _build_connected(self) -> None:
        account = Adw.PreferencesGroup(title=_("Conta OpenRouter"))
        self.account_row = Adw.ActionRow(title=mask_key_hint(self.ai.key_label))
        self.account_row.add_prefix(Gtk.Image(icon_name="dialog-password-symbolic"))

        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                      css_classes=["flat"], tooltip_text=_("Verificar saldo"))
        self.refresh_btn.connect("clicked", self._on_refresh_info)
        self.account_row.add_suffix(self.refresh_btn)

        disconnect_btn = Gtk.Button(label=_("Desconectar"), valign=Gtk.Align.CENTER,
                                    css_classes=["destructive-action"])
        disconnect_btn.connect("clicked", self._on_disconnect)
        self.account_row.add_suffix(disconnect_btn)
        account.add(self.account_row)
        account.add(link_row(_("Chaves e créditos no OpenRouter"),
                             "openrouter.ai/settings/keys",
                             openrouter.KEYS_URL))
        self._add(account)

        if self.ai.storage == FILE:
            warning = Adw.PreferencesGroup()
            row = Adw.ActionRow(
                title=_("Chave guardada em um arquivo privado"),
                subtitle=_("O chaveiro do sistema não está acessível. O arquivo tem permissão "
                           "restrita apenas ao seu usuário."),
                subtitle_selectable=True)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            warning.add(row)
            self._add(warning)

        # Transcription settings
        stt_group = Adw.PreferencesGroup(
            title=_("Transcrição ao vivo"),
            description=_("O áudio é enviado em blocos curtos à frente da reprodução e salvo no "
                          "cache local para nunca transcrever o mesmo trecho duas vezes."))

        stt_ids = [m[0] for m in openrouter.STT_MODELS] + [_CUSTOM]
        stt_labels = [m[1] for m in openrouter.STT_MODELS] + [_("Personalizado…")]
        self._stt_descs = {m[0]: m[2] for m in openrouter.STT_MODELS}
        self.stt_combo = Adw.ComboRow(title=_("Modelo de transcrição"),
                                      model=Gtk.StringList.new(stt_labels))
        cur_stt = self.ai.stt_model
        stt_idx = stt_ids.index(cur_stt) if cur_stt in stt_ids else len(stt_ids) - 1
        self.stt_combo.set_selected(stt_idx)
        self._update_stt_subtitle(stt_ids[stt_idx])
        self.stt_combo.connect("notify::selected", self._on_stt_model_selected, stt_ids)
        stt_group.add(self.stt_combo)

        self.stt_custom_row = Adw.EntryRow(title=_("ID do modelo de transcrição"),
                                           text=cur_stt)
        self.stt_custom_row.set_visible(stt_idx == len(stt_ids) - 1)
        self.stt_custom_row.connect("changed", self._on_stt_custom_changed)
        stt_group.add(self.stt_custom_row)

        src_codes = [c for c, _l in openrouter.SOURCE_LANGUAGES]
        src_labels = [_(lbl) for _c, lbl in openrouter.SOURCE_LANGUAGES]
        src_row = Adw.ComboRow(title=_("Idioma do áudio"),
                               subtitle=_("Melhora a precisão e a velocidade do reconhecimento"),
                               model=Gtk.StringList.new(src_labels))
        cur_src = self.ai.source_language
        src_row.set_selected(src_codes.index(cur_src) if cur_src in src_codes else 0)
        src_row.connect("notify::selected",
                        lambda r, _p: setattr(self.ai, "source_language",
                                              src_codes[r.get_selected()]))
        stt_group.add(src_row)

        auto_row = Adw.SwitchRow(
            title=_("Transcrever automaticamente"),
            subtitle=_("Desativado por padrão para evitar consumo inesperado de créditos"),
            active=self.ai.auto_transcribe)
        auto_row.connect("notify::active",
                         lambda r, _p: setattr(self.ai, "auto_transcribe", r.get_active()))
        stt_group.add(auto_row)
        self._add(stt_group)

        # Study Mode settings
        study_group = Adw.PreferencesGroup(
            title=_("Modo estudo de idiomas"),
            description=_("Usado para tradução bilíngue e explicação de gírias e expressões."))

        study_ids = [m[0] for m in openrouter.STUDY_MODELS] + [_CUSTOM]
        study_labels = [m[1] for m in openrouter.STUDY_MODELS] + [_("Personalizado…")]
        self._study_descs = {m[0]: m[2] for m in openrouter.STUDY_MODELS}
        self.study_combo = Adw.ComboRow(title=_("Modelo de estudo"),
                                        model=Gtk.StringList.new(study_labels))
        cur_study = self.ai.study_model
        study_idx = study_ids.index(cur_study) if cur_study in study_ids else len(study_ids) - 1
        self.study_combo.set_selected(study_idx)
        self._update_study_subtitle(study_ids[study_idx])
        self.study_combo.connect("notify::selected", self._on_study_model_selected, study_ids)
        study_group.add(self.study_combo)

        self.study_custom_row = Adw.EntryRow(title=_("ID do modelo de estudo"),
                                             text=cur_study)
        self.study_custom_row.set_visible(study_idx == len(study_ids) - 1)
        self.study_custom_row.connect("changed", self._on_study_custom_changed)
        study_group.add(self.study_custom_row)

        tgt_codes = [c for c, _l in openrouter.TARGET_LANGUAGES]
        tgt_labels = [lbl for _c, lbl in openrouter.TARGET_LANGUAGES]
        tgt_row = Adw.ComboRow(title=_("Idioma da tradução"),
                               subtitle=_("Usado nas legendas bilíngues e explicações"),
                               model=Gtk.StringList.new(tgt_labels))
        cur_tgt = self.ai.target_language
        tgt_row.set_selected(tgt_codes.index(cur_tgt) if cur_tgt in tgt_codes else 0)
        tgt_row.connect("notify::selected",
                        lambda r, _p: setattr(self.ai, "target_language",
                                              tgt_codes[r.get_selected()]))
        study_group.add(tgt_row)

        bilingual_row = Adw.SwitchRow(
            title=_("Modo bilíngue por padrão"),
            subtitle=_("Exibe a tradução abaixo de cada frase na transcrição"),
            active=self.ai.bilingual_default)
        bilingual_row.connect("notify::active",
                              lambda r, _p: setattr(self.ai, "bilingual_default", r.get_active()))
        study_group.add(bilingual_row)
        self._add(study_group)

        # Usage & Cache
        usage_group = Adw.PreferencesGroup(title=_("Uso e cache"))
        self.usage_row = Adw.ActionRow(title=_("Consumo estimado"))
        reset_btn = Gtk.Button(label=_("Zerar"), valign=Gtk.Align.CENTER)
        reset_btn.connect("clicked", self._on_reset_usage)
        self.usage_row.add_suffix(reset_btn)
        usage_group.add(self.usage_row)

        self.cache_row = Adw.ActionRow(title=_("Cache de transcrições"))
        clear_btn = Gtk.Button(label=_("Limpar"), valign=Gtk.Align.CENTER)
        clear_btn.connect("clicked", self._on_clear_transcripts)
        self.cache_row.add_suffix(clear_btn)
        usage_group.add(self.cache_row)
        self._add(usage_group)

        self._update_account_subtitle()
        self._update_usage_row()

    def _update_account_subtitle(self) -> None:
        if not getattr(self, "account_row", None):
            return
        self.account_row.set_title(mask_key_hint(self.ai.key_label))
        info = self.ai.key_info
        if info is None:
            self.account_row.set_subtitle(_("Chave conectada · clique em atualizar para ver o limite"))
            return
        parts = []
        if info.limit_remaining is not None:
            parts.append(_("Restante na chave: {v}").format(v=_format_usd(info.limit_remaining)))
        elif info.limit is not None:
            parts.append(_("Limite: {v}").format(v=_format_usd(info.limit)))
        parts.append(_("Usado na chave: {v}").format(v=_format_usd(info.usage)))
        self.account_row.set_subtitle(" · ".join(parts))

    def _update_usage_row(self) -> None:
        if not getattr(self, "usage_row", None) or not self.ai.configured:
            return
        session = _format_usd(self.ai.session_cost_usd)
        total = _format_usd(self.ai.total_cost_usd)
        secs = int(self.ai.total_transcribed_seconds)
        dur = format_duration(secs) if secs > 0 else "0 min"
        self.usage_row.set_subtitle(
            _("Sessão: {s} · Total: {t} · Áudio transcrito: {d}").format(s=session, t=total, d=dur))
        count = self.db.count_transcribed_episodes()
        self.cache_row.set_subtitle(
            _("{n} episódios em cache").format(n=count) if count != 1 else _("1 episódio em cache"))

    def _update_stt_subtitle(self, model_id: str) -> None:
        if model_id == _CUSTOM:
            self.stt_combo.set_subtitle(_("Informe o identificador do modelo abaixo"))
        else:
            self.stt_combo.set_subtitle(self._stt_descs.get(model_id, ""))

    def _update_study_subtitle(self, model_id: str) -> None:
        if model_id == _CUSTOM:
            self.study_combo.set_subtitle(_("Informe o identificador do modelo abaixo"))
        else:
            self.study_combo.set_subtitle(self._study_descs.get(model_id, ""))

    def _on_stt_model_selected(self, row: Adw.ComboRow, _pspec, ids: list[str]) -> None:
        selected = ids[row.get_selected()]
        is_custom = selected == _CUSTOM
        self.stt_custom_row.set_visible(is_custom)
        self._update_stt_subtitle(selected)
        if not is_custom:
            self.ai.stt_model = selected
            self.stt_custom_row.set_text(selected)

    def _on_stt_custom_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip()
        if text:
            self.ai.stt_model = text

    def _on_study_model_selected(self, row: Adw.ComboRow, _pspec, ids: list[str]) -> None:
        selected = ids[row.get_selected()]
        is_custom = selected == _CUSTOM
        self.study_custom_row.set_visible(is_custom)
        self._update_study_subtitle(selected)
        if not is_custom:
            self.ai.study_model = selected
            self.study_custom_row.set_text(selected)

    def _on_study_custom_changed(self, row: Adw.EntryRow) -> None:
        text = row.get_text().strip()
        if text:
            self.ai.study_model = text

    def _on_refresh_info(self, _button) -> None:
        self.refresh_btn.set_sensitive(False)
        self.ai.refresh_key_info(
            on_done=lambda _info: self._on_refresh_done(None),
            on_error=lambda err: self._on_refresh_done(str(err)),
        )

    def _on_refresh_done(self, error: str | None) -> None:
        if getattr(self, "refresh_btn", None) is not None:
            self.refresh_btn.set_sensitive(True)
        if error:
            self.dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(error), timeout=5))
        else:
            self._update_account_subtitle()
            self.dialog.add_toast(Adw.Toast(title=_("Informações da chave atualizadas")))

    def _on_reset_usage(self, _button) -> None:
        self.ai.reset_usage()
        self.dialog.add_toast(Adw.Toast(title=_("Contador de uso zerado")))

    def _on_clear_transcripts(self, _button) -> None:
        self.db.clear_all_transcripts()
        self._update_usage_row()
        self.dialog.add_toast(Adw.Toast(title=_("Cache de transcrições limpo")))

    def _on_disconnect(self, _button) -> None:
        alert = Adw.AlertDialog(
            heading=_("Desconectar OpenRouter?"),
            body=_("Sua chave da API será removida deste computador. As transcrições já salvas "
                   "no cache continuam disponíveis localmente."))
        alert.add_response("cancel", _("Cancelar"))
        alert.add_response("disconnect", _("Desconectar"))
        alert.set_response_appearance("disconnect", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_close_response("cancel")
        alert.connect("response", lambda _a, r: self._confirm_disconnect() if r == "disconnect" else None)
        alert.present(self.dialog)

    def _confirm_disconnect(self) -> None:
        app = get_app()
        if getattr(app, "transcription", None) is not None:
            app.transcription.stop_live()
        self.ai.sign_out()
        self.dialog.add_toast(Adw.Toast(title=_("OpenRouter desconectado")))
