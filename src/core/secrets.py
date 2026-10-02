"""Where sync passwords live: the desktop keyring, or a private file as a fallback.

The keyring (Secret Service via libsecret, or the Secret portal inside Flatpak)
is always tried first. Sandboxes without access to it, such as a snap whose
``password-manager-service`` plug is not connected, fall back to a file only
the user can read. Calls block on D-Bus: use them from worker threads.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret
except (ImportError, ValueError):
    Secret = None

KEYRING = "keyring"
FILE = "file"

_SCHEMA = None


def _schema():
    global _SCHEMA
    if _SCHEMA is None and Secret is not None:
        _SCHEMA = Secret.Schema.new(
            f"{config.APP_ID}.Sync", Secret.SchemaFlags.NONE,
            {"server": Secret.SchemaAttributeType.STRING,
             "username": Secret.SchemaAttributeType.STRING})
    return _SCHEMA


def _keyring_enabled() -> bool:
    return Secret is not None and os.environ.get("PODFLOW_SECRETS") != "file"


class CredentialStore:
    def __init__(self, fallback_path: Path | None = None):
        self._path = fallback_path or (config.data_dir() / "credentials.json")

    # -- public API ------------------------------------------------------------------

    def store(self, server: str, username: str, password: str) -> str:
        """Save the password; returns where it went (KEYRING or FILE)."""
        if _keyring_enabled():
            try:
                Secret.password_store_sync(
                    _schema(), {"server": server, "username": username},
                    Secret.COLLECTION_DEFAULT, f"PodFlow: {username} em {server}", password, None)
                self._file_remove(server, username)
                return KEYRING
            except Exception as error:  # no Secret Service, sandbox denial, locked keyring…
                log.warning("Chaveiro indisponível, usando arquivo local: %s", error)
        self._file_write(server, username, password)
        return FILE

    def lookup(self, server: str, username: str) -> str | None:
        if _keyring_enabled():
            try:
                password = Secret.password_lookup_sync(
                    _schema(), {"server": server, "username": username}, None)
                if password is not None:
                    return password
            except Exception as error:
                log.info("Não foi possível ler o chaveiro: %s", error)
        return self._file_read().get(self._key(server, username))

    def clear(self, server: str, username: str) -> None:
        if _keyring_enabled():
            try:
                Secret.password_clear_sync(_schema(), {"server": server, "username": username},
                                           None)
            except Exception as error:
                log.info("Não foi possível limpar o chaveiro: %s", error)
        self._file_remove(server, username)

    # -- file fallback -----------------------------------------------------------------

    @staticmethod
    def _key(server: str, username: str) -> str:
        return f"{username}@{server}"

    def _file_read(self) -> dict[str, str]:
        try:
            data = json.loads(self._path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _file_save(self, data: dict[str, str]) -> None:
        if not data:
            self._path.unlink(missing_ok=True)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, self._path)

    def _file_write(self, server: str, username: str, password: str) -> None:
        data = self._file_read()
        data[self._key(server, username)] = password
        self._file_save(data)

    def _file_remove(self, server: str, username: str) -> None:
        data = self._file_read()
        if data.pop(self._key(server, username), None) is not None:
            self._file_save(data)
