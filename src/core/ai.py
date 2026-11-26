"""Optional AI & OpenRouter BYOK configuration and usage tracking.

Everything here is strictly opt-in: users without an OpenRouter API key never
hit external AI APIs and can still enjoy free `<podcast:transcript>` feeds.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from gi.repository import GObject

from ..api import openrouter
from ..utils import tasks
from .secrets import FILE, KEYRING, CredentialStore

log = logging.getLogger(__name__)

_SECRET_SERVER = "openrouter.ai"
_SECRET_USER = "api-key"


class AIManager(GObject.Object):
    __gtype_name__ = "PodFlowAIManager"

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "usage-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, db, store: CredentialStore | None = None):
        super().__init__()
        self.db = db
        self._store = store or CredentialStore()
        self._cached_key: str | None = None
        self._key_loaded = False
        self._session_cost: float = 0.0
        self._key_info: openrouter.KeyInfo | None = None

    # -- configuration properties ----------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.db.get_setting("ai_configured", False))

    @property
    def storage(self) -> str:
        return str(self.db.get_setting("ai_storage", KEYRING))

    @property
    def key_label(self) -> str:
        return str(self.db.get_setting("ai_key_label", ""))

    @property
    def key_info(self) -> openrouter.KeyInfo | None:
        return self._key_info

    @property
    def stt_model(self) -> str:
        return str(self.db.get_setting("ai_stt_model", openrouter.DEFAULT_STT_MODEL))

    @stt_model.setter
    def stt_model(self, value: str) -> None:
        cleaned = (value or "").strip() or openrouter.DEFAULT_STT_MODEL
        self.db.set_setting("ai_stt_model", cleaned)
        self.emit("changed")

    @property
    def study_model(self) -> str:
        return str(self.db.get_setting("ai_study_model", openrouter.DEFAULT_STUDY_MODEL))

    @study_model.setter
    def study_model(self, value: str) -> None:
        cleaned = (value or "").strip() or openrouter.DEFAULT_STUDY_MODEL
        self.db.set_setting("ai_study_model", cleaned)
        self.emit("changed")

    @property
    def source_language(self) -> str:
        return str(self.db.get_setting("ai_source_lang", "en"))

    @source_language.setter
    def source_language(self, value: str) -> None:
        self.db.set_setting("ai_source_lang", (value or "auto").strip())
        self.emit("changed")

    @property
    def target_language(self) -> str:
        return str(self.db.get_setting("ai_target_lang", "pt-BR"))

    @target_language.setter
    def target_language(self, value: str) -> None:
        self.db.set_setting("ai_target_lang", (value or "pt-BR").strip())
        self.emit("changed")

    @property
    def auto_transcribe(self) -> bool:
        return bool(self.db.get_setting("ai_auto_transcribe", False))

    @auto_transcribe.setter
    def auto_transcribe(self, value: bool) -> None:
        self.db.set_setting("ai_auto_transcribe", bool(value))
        self.emit("changed")

    @property
    def bilingual_default(self) -> bool:
        return bool(self.db.get_setting("ai_bilingual_default", False))

    @bilingual_default.setter
    def bilingual_default(self, value: bool) -> None:
        self.db.set_setting("ai_bilingual_default", bool(value))
        self.emit("changed")

    @property
    def session_cost_usd(self) -> float:
        return self._session_cost

    @property
    def total_cost_usd(self) -> float:
        return float(self.db.get_setting("ai_total_cost_usd", 0.0))

    @property
    def total_transcribed_seconds(self) -> float:
        return float(self.db.get_setting("ai_total_seconds", 0.0))

    # -- credentials -----------------------------------------------------------------

    def get_api_key(self) -> str | None:
        """Read the user's OpenRouter key (call from worker threads when uncached)."""
        if not self.configured:
            return None
        if self._key_loaded:
            return self._cached_key
        key = self._store.lookup(_SECRET_SERVER, _SECRET_USER)
        self._cached_key = key.strip() if key else None
        self._key_loaded = True
        return self._cached_key

    def connect_with_key(self, api_key: str, *,
                         on_done: Callable[[openrouter.KeyInfo], None] | None = None,
                         on_error: Callable[[BaseException], None] | None = None) -> None:
        """Validate ``api_key`` against OpenRouter and store it securely on success."""
        cleaned = (api_key or "").strip()

        def worker() -> tuple[openrouter.KeyInfo, str]:
            info = openrouter.validate_key(cleaned)
            where = self._store.store(_SECRET_SERVER, _SECRET_USER, cleaned)
            return info, where

        def finish(result: tuple[openrouter.KeyInfo, str]) -> None:
            info, where = result
            self._cached_key = cleaned
            self._key_loaded = True
            self._key_info = info
            self.db.set_setting("ai_configured", True)
            self.db.set_setting("ai_storage", where)
            self.db.set_setting("ai_key_label", info.label)
            self.db.set_setting("ai_connected_at", int(time.time()))
            self.emit("changed")
            if on_done is not None:
                on_done(info)

        tasks.run_async(worker, on_done=finish, on_error=on_error)

    def refresh_key_info(self, *,
                         on_done: Callable[[openrouter.KeyInfo], None] | None = None,
                         on_error: Callable[[BaseException], None] | None = None) -> None:
        """Refresh remaining credits / usage for the stored OpenRouter key."""
        if not self.configured:
            return

        def worker() -> openrouter.KeyInfo:
            key = self.get_api_key()
            if not key:
                raise openrouter.OpenRouterError("Chave da API não encontrada no chaveiro")
            return openrouter.validate_key(key)

        def finish(info: openrouter.KeyInfo) -> None:
            self._key_info = info
            if info.label:
                self.db.set_setting("ai_key_label", info.label)
            self.emit("changed")
            if on_done is not None:
                on_done(info)

        tasks.run_async(worker, on_done=finish, on_error=on_error)

    def sign_out(self) -> None:
        """Remove the stored OpenRouter key and disable cloud AI features."""
        self._cached_key = None
        self._key_loaded = False
        self._key_info = None
        self.db.delete_settings("ai_configured", "ai_storage", "ai_key_label", "ai_connected_at")
        tasks.run_async(self._store.clear, _SECRET_SERVER, _SECRET_USER)
        self.emit("changed")

    # -- usage tracking --------------------------------------------------------------

    def record_usage(self, cost_usd: float = 0.0, seconds: float = 0.0) -> None:
        cost = max(0.0, float(cost_usd or 0.0))
        secs = max(0.0, float(seconds or 0.0))
        if cost <= 0.0 and secs <= 0.0:
            return

        def _apply() -> None:
            self._session_cost += cost
            self.db.set_setting("ai_total_cost_usd", round(self.total_cost_usd + cost, 6))
            self.db.set_setting("ai_total_seconds", round(self.total_transcribed_seconds + secs, 2))
            self.emit("usage-changed")

        if tasks.is_main_thread():
            _apply()
        else:
            tasks.idle(_apply)

    def reset_usage(self) -> None:
        self._session_cost = 0.0
        self.db.set_setting("ai_total_cost_usd", 0.0)
        self.db.set_setting("ai_total_seconds", 0.0)
        self.emit("usage-changed")


def mask_key_hint(label: str) -> str:
    if label:
        return label
    return "sk-or-v1-••••"


__all__ = ["AIManager", "FILE", "KEYRING", "mask_key_hint"]
