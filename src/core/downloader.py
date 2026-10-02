"""Episode downloads for offline listening."""

from __future__ import annotations

import logging
import mimetypes
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gi.repository import GObject

from .. import config
from ..api import http
from ..models import Episode
from ..utils import tasks

log = logging.getLogger(__name__)

CHUNK = 128 * 1024
_EXTENSIONS = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
               "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg",
               "audio/opus": ".opus", "audio/flac": ".flac", "video/mp4": ".mp4"}


class DownloadCancelled(Exception):
    pass


def _extension(episode: Episode) -> str:
    if episode.mime_type in _EXTENSIONS:
        return _EXTENSIONS[episode.mime_type]
    suffix = Path(urllib.parse.urlparse(episode.audio_url).path).suffix.lower()
    if suffix in {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".mp4", ".wav"}:
        return suffix
    return mimetypes.guess_extension(episode.mime_type or "") or ".mp3"


class DownloadManager(GObject.Object):
    __gtype_name__ = "PodFlowDownloadManager"

    __gsignals__ = {
        "progress": (GObject.SignalFlags.RUN_FIRST, None, (str, float)),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, db, directory: Path | None = None, parallel: int = 2):
        super().__init__()
        self.db = db
        self._directory = directory
        self._pool = ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="podflow-dl")
        self._cancel: dict[str, threading.Event] = {}
        self._progress: dict[str, float] = {}
        for episode_id in db.reset_interrupted_downloads():
            log.info("Download interrompido descartado: %s", episode_id)
        self._cleanup_partials()

    @property
    def directory(self) -> Path:
        return self._directory or config.downloads_dir()

    def _cleanup_partials(self) -> None:
        try:
            for partial in self.directory.rglob("*.part"):
                partial.unlink(missing_ok=True)
        except OSError:
            pass

    def is_active(self, episode_id: str) -> bool:
        return episode_id in self._cancel

    def progress(self, episode_id: str) -> float | None:
        return self._progress.get(episode_id)

    def download(self, episode: Episode) -> None:
        if not episode.audio_url or self.is_active(episode.id) or episode.is_downloaded:
            return
        event = threading.Event()
        self._cancel[episode.id] = event
        self._progress[episode.id] = 0.0
        self.db.set_download(episode.id, "queued")
        self._pool.submit(self._worker, episode, event)

    def cancel(self, episode_id: str) -> None:
        event = self._cancel.get(episode_id)
        if event is not None:
            event.set()

    def delete(self, episode: Episode) -> None:
        self.cancel(episode.id)
        if episode.download_path:
            try:
                Path(episode.download_path).unlink(missing_ok=True)
            except OSError as error:
                log.warning("Não foi possível apagar %s: %s", episode.download_path, error)
        self.db.set_download(episode.id, "")

    def delete_all(self) -> None:
        for episode in self.db.list_downloaded():
            self.delete(episode)

    def shutdown(self) -> None:
        for event in self._cancel.values():
            event.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _worker(self, episode: Episode, cancel: threading.Event) -> None:
        folder = self.directory / episode.podcast_id.replace(":", "_")
        target = folder / f"{episode.id}{_extension(episode)}"
        partial = target.with_name(target.name + ".part")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.db.set_download(episode.id, "downloading")
            with http.open_stream(episode.audio_url, timeout=30) as response, \
                    open(partial, "wb") as output:
                total = int(response.headers.get("Content-Length") or 0) or episode.file_size
                received = 0
                last_report = 0.0
                while True:
                    if cancel.is_set():
                        raise DownloadCancelled()
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if total and now - last_report > 0.25:
                        last_report = now
                        fraction = min(1.0, received / total)
                        self._progress[episode.id] = fraction
                        tasks.idle(self.emit, "progress", episode.id, fraction)
            if received == 0:
                raise http.NetworkError("Arquivo vazio")
            partial.replace(target)
            self.db.set_download(episode.id, "done", str(target), received)
            tasks.idle(self._finish, episode.id, None)
        except DownloadCancelled:
            partial.unlink(missing_ok=True)
            self.db.set_download(episode.id, "")
            tasks.idle(self._finish, episode.id, None, False)
        except Exception as error:  # network, disk full, permissions...
            log.warning("Download falhou (%s): %s", episode.title, error)
            partial.unlink(missing_ok=True)
            self.db.set_download(episode.id, "failed")
            tasks.idle(self._finish, episode.id, str(error))

    def _finish(self, episode_id: str, error: str | None, notify: bool = True) -> None:
        self._cancel.pop(episode_id, None)
        self._progress.pop(episode_id, None)
        if not notify:
            return
        if error is None:
            self.emit("finished", episode_id)
        else:
            self.emit("failed", episode_id, error)
