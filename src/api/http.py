"""Small blocking HTTP client on top of urllib (call it from worker threads)."""

from __future__ import annotations

import gzip
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

from .. import config

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
MAX_BYTES = 64 * 1024 * 1024


class NetworkError(Exception):
    def __init__(self, message: str, *, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class OfflineError(NetworkError):
    pass


def _request(url: str, headers: dict[str, str] | None = None) -> urllib.request.Request:
    merged = {
        "User-Agent": config.USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
    }
    if headers:
        merged.update(headers)
    return urllib.request.Request(url, headers=merged)


def _decode_body(data: bytes, encoding: str) -> bytes:
    encoding = encoding.lower()
    if encoding == "gzip" or data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    if encoding == "deflate":
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def get_bytes(url: str, *, timeout: float = DEFAULT_TIMEOUT, retries: int = 2,
              headers: dict[str, str] | None = None, max_bytes: int = MAX_BYTES) -> bytes:
    if config.OFFLINE:
        raise OfflineError("Sem conexão (modo offline)", url=url)
    last_error: NetworkError | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(_request(url, headers), timeout=timeout) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise NetworkError("Resposta grande demais", url=url)
                return _decode_body(data, response.headers.get("Content-Encoding") or "")
        except urllib.error.HTTPError as error:
            last_error = NetworkError(f"HTTP {error.code}", status=error.code, url=url)
            if error.code < 500 and error.code not in (408, 429):
                raise last_error from error
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                OSError, zlib.error, EOFError) as error:
            reason = getattr(error, "reason", error)
            last_error = NetworkError(str(reason) or type(error).__name__, url=url)
        if attempt < retries:
            time.sleep(0.6 * (2 ** attempt))
    assert last_error is not None
    log.info("Falha ao baixar %s: %s", url, last_error)
    raise last_error


def get_json(url: str, **kwargs: Any) -> Any:
    data = get_bytes(url, **kwargs)
    try:
        return json.loads(data.decode("utf-8", errors="replace"))
    except ValueError as error:
        raise NetworkError("Resposta JSON inválida", url=url) from error


def request(method: str, url: str, *, body: Any = None, form: dict[str, str] | None = None,
            headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT,
            max_bytes: int = 16 * 1024 * 1024) -> tuple[int, bytes]:
    """One HTTP request without retries (for APIs with side effects).

    ``body`` is sent as JSON, ``form`` as urlencoded. Returns ``(status, body)``
    for 2xx responses and raises :class:`NetworkError` (with ``status``) otherwise.
    """
    if config.OFFLINE:
        raise OfflineError("Sem conexão (modo offline)", url=url)
    merged = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        merged["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        merged["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        merged.update(headers)
    req = _request(url, merged)
    req.method = method.upper()
    req.data = data
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise NetworkError("Resposta grande demais", url=url)
            return response.status, _decode_body(payload,
                                                 response.headers.get("Content-Encoding") or "")
    except urllib.error.HTTPError as error:
        raise NetworkError(f"HTTP {error.code}", status=error.code, url=url) from error
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError,
            zlib.error, EOFError) as error:
        raise NetworkError(str(getattr(error, "reason", error)) or type(error).__name__,
                           url=url) from error


def open_stream(url: str, *, timeout: float = 30, headers: dict[str, str] | None = None):
    """Open a streaming response (caller closes it). No transparent decompression."""
    if config.OFFLINE:
        raise OfflineError("Sem conexão (modo offline)", url=url)
    request_headers = {"Accept-Encoding": "identity"}
    if headers:
        request_headers.update(headers)
    try:
        return urllib.request.urlopen(_request(url, request_headers), timeout=timeout)
    except urllib.error.HTTPError as error:
        raise NetworkError(f"HTTP {error.code}", status=error.code, url=url) from error
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
        raise NetworkError(str(getattr(error, "reason", error)), url=url) from error
