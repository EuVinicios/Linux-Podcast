"""Asynchronous artwork cache: disk (~/.cache/podflow/covers) + in-memory LRU."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gi.repository import Gdk, GdkPixbuf, GLib

from .. import config
from ..api import http
from ..api.itunes_service import artwork_url
from . import colors

log = logging.getLogger(__name__)

SIZES = (100, 200, 300, 400, 600, 1000)
RETRY_AFTER = 600  # seconds before retrying an image that failed to load
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def bucket(size: int) -> int:
    """Round a pixel size up to one of a few cache buckets."""
    for candidate in SIZES:
        if size <= candidate:
            return candidate
    return SIZES[-1]


class ImageCache:
    def __init__(self, directory: Path | None = None, memory_items: int = 400):
        self._directory = directory
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="podflow-img")
        self._memory: OrderedDict[str, Gdk.Texture] = OrderedDict()
        self._memory_items = memory_items
        self._pending: dict[str, list[Callable]] = {}
        self._failed: dict[str, float] = {}
        self._failed_lock = threading.Lock()
        self._colors: dict[str, colors.RGB | None] = {}

    @property
    def directory(self) -> Path:
        return self._directory or config.covers_dir()

    @staticmethod
    def _key(url: str, size: int) -> str:
        return hashlib.sha1(f"{url}|{size}".encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.img"

    # -- public API (main thread) ----------------------------------------------------

    def lookup(self, url: str, size: int) -> Gdk.Texture | None:
        key = self._key(url, bucket(size))
        texture = self._memory.get(key)
        if texture is not None:
            self._memory.move_to_end(key)
        return texture

    def load(self, url: str | None, size: int, callback: Callable[[Gdk.Texture | None], None]) -> None:
        """Deliver a texture to ``callback`` (immediately when it is in memory)."""
        if not url:
            callback(None)
            return
        size = bucket(size)
        key = self._key(url, size)
        texture = self._memory.get(key)
        if texture is not None:
            self._memory.move_to_end(key)
            callback(texture)
            return
        waiting = self._pending.get(key)
        if waiting is not None:
            waiting.append(callback)
            return
        self._pending[key] = [callback]
        future = self._pool.submit(self._load_texture, url, size, key)
        future.add_done_callback(lambda fut: GLib.idle_add(self._deliver, key, fut))

    def ensure_file(self, url: str | None, size: int, callback: Callable[[Path | None], None]) -> None:
        if not url:
            callback(None)
            return
        size = bucket(size)
        future = self._pool.submit(self._ensure_file, url, size)

        def deliver(fut) -> bool:
            try:
                callback(fut.result())
            except Exception:
                callback(None)
            return GLib.SOURCE_REMOVE

        future.add_done_callback(lambda fut: GLib.idle_add(deliver, fut))

    def dominant_color(self, url: str | None, callback: Callable[[colors.RGB | None], None]) -> None:
        if not url:
            callback(None)
            return
        if url in self._colors:
            callback(self._colors[url])
            return
        future = self._pool.submit(self._compute_color, url)

        def deliver(fut) -> bool:
            try:
                rgb = fut.result()
            except Exception:
                rgb = None
            self._colors[url] = rgb
            callback(rgb)
            return GLib.SOURCE_REMOVE

        future.add_done_callback(lambda fut: GLib.idle_add(deliver, fut))

    def disk_usage(self) -> int:
        try:
            return sum(f.stat().st_size for f in self.directory.glob("*.img"))
        except OSError:
            return 0

    def clear(self) -> None:
        self._memory.clear()
        self._colors.clear()
        for file in self.directory.glob("*.img"):
            try:
                file.unlink()
            except OSError:
                pass

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- internals -------------------------------------------------------------------

    def _deliver(self, key: str, future) -> bool:
        try:
            texture = future.result()
        except Exception:
            texture = None
        if texture is not None:
            self._memory[key] = texture
            while len(self._memory) > self._memory_items:
                self._memory.popitem(last=False)
        for callback in self._pending.pop(key, []):
            try:
                callback(texture)
            except Exception:
                log.exception("Erro ao entregar imagem")
        return GLib.SOURCE_REMOVE

    def _load_texture(self, url: str, size: int, key: str) -> Gdk.Texture | None:
        path = self._ensure_file(url, size)
        if path is None:
            return None
        try:
            return Gdk.Texture.new_from_filename(str(path))
        except GLib.Error:
            path.unlink(missing_ok=True)
            return None

    def _ensure_file(self, url: str, size: int) -> Path | None:
        key = self._key(url, size)
        path = self._path(key)
        if path.is_file() and path.stat().st_size > 0:
            return path
        with self._failed_lock:
            failed_at = self._failed.get(key)
        if failed_at and time.time() - failed_at < RETRY_AFTER:
            return None
        try:
            if url.startswith("file://"):
                data = Path(GLib.filename_from_uri(url)[0]).read_bytes()
                self._write_scaled(data, size, path)
            elif "mzstatic.com" in url:
                data = http.get_bytes(artwork_url(url, size), timeout=20, retries=1,
                                      max_bytes=MAX_IMAGE_BYTES)
                self._write_atomic(path, data)
            else:
                data = http.get_bytes(url, timeout=20, retries=1, max_bytes=MAX_IMAGE_BYTES)
                self._write_scaled(data, size, path)
            return path
        except Exception as error:
            with self._failed_lock:
                self._failed[key] = time.time()
            log.debug("Capa indisponível (%s): %s", url, error)
            return None

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

    def _write_scaled(self, data: bytes, size: int, path: Path) -> None:
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(data)
        finally:
            loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None:
            raise ValueError("Imagem inválida")
        width, height = pixbuf.get_width(), pixbuf.get_height()
        side = min(width, height)
        if width != height:  # center-crop to a square
            pixbuf = pixbuf.new_subpixbuf((width - side) // 2, (height - side) // 2, side, side)
        if side > size:
            pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        pixbuf.savev(str(temporary), "png", [], [])
        temporary.replace(path)

    def _compute_color(self, url: str) -> colors.RGB | None:
        path = self._ensure_file(url, 100)
        if path is None:
            return None
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 32, 32, False)
        if pixbuf.get_has_alpha():
            pixbuf = pixbuf.composite_color_simple(32, 32, GdkPixbuf.InterpType.BILINEAR,
                                                   255, 32, 0xffffff, 0xffffff)
        return colors.vivid_average(pixbuf.get_pixels(), pixbuf.get_width(),
                                    pixbuf.get_height(), pixbuf.get_rowstride(),
                                    pixbuf.get_n_channels())
