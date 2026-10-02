"""Clients for the open gPodder sync protocol.

Two flavours share the same JSON documents:

* the gpodder.net API v2, also implemented by self-hosted servers such as
  oPodSync and mygpo (``/api/2/...``, one device per installation);
* the Nextcloud "GPodder Sync" app (``/index.php/apps/gpoddersync/...``),
  authenticated with an app password, which Nextcloud's Login Flow v2 hands out.

Every call blocks: run them from worker threads.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import http

GPODDER_NET = "gpodder"
NEXTCLOUD = "nextcloud"
CUSTOM = "custom"
PROVIDERS = (GPODDER_NET, NEXTCLOUD, CUSTOM)

GPODDER_NET_URL = "https://gpodder.net"
GPODDER_NET_REGISTER_URL = "https://gpodder.net/register/"
NEXTCLOUD_SIGNUP_URL = "https://nextcloud.com/sign-up/"
NEXTCLOUD_APP_URL = "https://apps.nextcloud.com/apps/gpoddersync"
OPODSYNC_URL = "https://github.com/kd2org/opodsync"


class SyncError(Exception):
    """A sync request failed; the message is shown to the user."""


class AuthError(SyncError):
    """The server rejected the credentials."""


class ServiceMissingError(SyncError):
    """The server answers, but not with a gPodder API (e.g. app not installed)."""


@dataclass
class SubscriptionChanges:
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    timestamp: int = 0


@dataclass
class UploadResult:
    timestamp: int = 0
    # (old, new) pairs: the server rewrote these feed URLs
    update_urls: list[tuple[str, str]] = field(default_factory=list)


def normalize_server(url: str, provider: str = CUSTOM) -> str:
    url = url.strip()
    if provider == GPODDER_NET and not url:
        return GPODDER_NET_URL
    if url and "://" not in url:
        url = "https://" + url
    url = url.rstrip("/")
    for suffix in ("/index.php", "/api/2"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _json(data: bytes) -> Any:
    if not data.strip():
        return None
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except ValueError as error:
        raise ServiceMissingError("O servidor não respondeu com a API do gPodder") from error


def _update_urls(document: Any) -> list[tuple[str, str]]:
    pairs = []
    if isinstance(document, dict):
        for item in document.get("update_urls") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] and item[1]:
                pairs.append((str(item[0]), str(item[1])))
    return pairs


def _timestamp(document: Any) -> int:
    if isinstance(document, dict):
        try:
            return int(document.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


class SyncClient:
    """Common transport; subclasses only know their URL layout."""

    provider = CUSTOM

    def __init__(self, server: str, username: str, password: str, device_id: str):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.device_id = device_id

    # -- transport -------------------------------------------------------------------

    def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None,
              body: Any = None) -> Any:
        url = self.server + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            _status, data = http.request(method, url, body=body, timeout=30,
                                         headers=_auth_header(self.username, self.password))
        except http.OfflineError:
            raise
        except http.NetworkError as error:
            if error.status in (401, 403):
                raise AuthError("Usuário ou senha incorretos") from error
            if error.status in (404, 405):
                raise ServiceMissingError(self._missing_message()) from error
            if error.status is not None:
                raise SyncError(f"O servidor respondeu com erro {error.status}") from error
            raise SyncError(f"Não foi possível conectar ao servidor: {error}") from error
        return _json(data)

    def _missing_message(self) -> str:
        return "Este endereço não tem um serviço de sincronização gPodder"

    # -- API ---------------------------------------------------------------------------

    def login(self, caption: str) -> None:
        raise NotImplementedError

    def get_subscriptions(self, since: int) -> SubscriptionChanges:
        raise NotImplementedError

    def upload_subscriptions(self, add: list[str], remove: list[str]) -> UploadResult:
        raise NotImplementedError

    def get_episode_actions(self, since: int) -> tuple[list[dict[str, Any]], int]:
        raise NotImplementedError

    def upload_episode_actions(self, actions: list[dict[str, Any]]) -> UploadResult:
        raise NotImplementedError

    @staticmethod
    def _changes(document: Any) -> SubscriptionChanges:
        if not isinstance(document, dict):
            raise ServiceMissingError("Resposta inesperada do servidor")
        return SubscriptionChanges(
            add=[str(u) for u in document.get("add") or [] if u],
            remove=[str(u) for u in document.get("remove") or [] if u],
            timestamp=_timestamp(document))

    @staticmethod
    def _actions(document: Any) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(document, dict):
            raise ServiceMissingError("Resposta inesperada do servidor")
        actions = [a for a in document.get("actions") or [] if isinstance(a, dict)]
        return actions, _timestamp(document)


class GpodderNetClient(SyncClient):
    """gpodder.net API v2 (and compatible servers such as oPodSync)."""

    provider = GPODDER_NET

    def _user(self) -> str:
        return urllib.parse.quote(self.username, safe="")

    def _device(self) -> str:
        return urllib.parse.quote(self.device_id, safe="")

    def login(self, caption: str) -> None:
        self._call("POST", f"/api/2/auth/{self._user()}/login.json")
        self._call("POST", f"/api/2/devices/{self._user()}/{self._device()}.json",
                   body={"caption": caption, "type": "desktop"})

    def get_subscriptions(self, since: int) -> SubscriptionChanges:
        return self._changes(self._call(
            "GET", f"/api/2/subscriptions/{self._user()}/{self._device()}.json",
            params={"since": since}))

    def upload_subscriptions(self, add: list[str], remove: list[str]) -> UploadResult:
        document = self._call("POST", f"/api/2/subscriptions/{self._user()}/{self._device()}.json",
                              body={"add": add, "remove": remove})
        return UploadResult(_timestamp(document), _update_urls(document))

    def get_episode_actions(self, since: int) -> tuple[list[dict[str, Any]], int]:
        return self._actions(self._call("GET", f"/api/2/episodes/{self._user()}.json",
                                        params={"since": since, "aggregated": "true"}))

    def upload_episode_actions(self, actions: list[dict[str, Any]]) -> UploadResult:
        document = self._call("POST", f"/api/2/episodes/{self._user()}.json", body=actions)
        return UploadResult(_timestamp(document), _update_urls(document))


class NextcloudClient(SyncClient):
    """Nextcloud with the GPodder Sync app (one shared list per account)."""

    provider = NEXTCLOUD
    BASE = "/index.php/apps/gpoddersync"

    def _missing_message(self) -> str:
        return "O app GPodder Sync não está instalado neste Nextcloud"

    def login(self, caption: str) -> None:
        # There is no login call: any authenticated read proves both the
        # credentials and that the app is installed.
        self.get_subscriptions(int(2 ** 31 - 1))

    def get_subscriptions(self, since: int) -> SubscriptionChanges:
        return self._changes(self._call("GET", f"{self.BASE}/subscriptions",
                                        params={"since": since}))

    def upload_subscriptions(self, add: list[str], remove: list[str]) -> UploadResult:
        document = self._call("POST", f"{self.BASE}/subscription_change/create",
                              body={"add": add, "remove": remove})
        return UploadResult(_timestamp(document), _update_urls(document))

    def get_episode_actions(self, since: int) -> tuple[list[dict[str, Any]], int]:
        return self._actions(self._call("GET", f"{self.BASE}/episode_action",
                                        params={"since": since}))

    def upload_episode_actions(self, actions: list[dict[str, Any]]) -> UploadResult:
        document = self._call("POST", f"{self.BASE}/episode_action/create", body=actions)
        return UploadResult(_timestamp(document), _update_urls(document))


def make_client(provider: str, server: str, username: str, password: str,
                device_id: str) -> SyncClient:
    cls = NextcloudClient if provider == NEXTCLOUD else GpodderNetClient
    return cls(normalize_server(server, provider), username, password, device_id)


# -- Nextcloud Login Flow v2 ---------------------------------------------------------


@dataclass
class LoginFlow:
    login_url: str
    poll_endpoint: str
    token: str


def nextcloud_login_start(server: str) -> LoginFlow:
    """Ask Nextcloud for a browser login page (Login Flow v2)."""
    url = normalize_server(server, NEXTCLOUD) + "/index.php/login/v2"
    try:
        _status, data = http.request("POST", url, timeout=20)
    except http.OfflineError:
        raise
    except http.NetworkError as error:
        if error.status is not None:
            raise ServiceMissingError("Este endereço não parece ser um Nextcloud") from error
        raise SyncError(f"Não foi possível conectar ao servidor: {error}") from error
    document = _json(data)
    try:
        return LoginFlow(login_url=document["login"], poll_endpoint=document["poll"]["endpoint"],
                         token=document["poll"]["token"])
    except (KeyError, TypeError) as error:
        raise ServiceMissingError("Este endereço não parece ser um Nextcloud") from error


def nextcloud_login_poll(flow: LoginFlow) -> dict[str, str] | None:
    """One poll: the credentials once the user approved, ``None`` while waiting."""
    try:
        _status, data = http.request("POST", flow.poll_endpoint, form={"token": flow.token},
                                     timeout=20)
    except http.NetworkError as error:
        if error.status == 404:
            return None
        raise SyncError(f"Falha ao aguardar o login: {error}") from error
    document = _json(data)
    if not isinstance(document, dict) or not document.get("appPassword"):
        return None
    return {"server": str(document.get("server") or ""),
            "username": str(document.get("loginName") or ""),
            "password": str(document["appPassword"])}
