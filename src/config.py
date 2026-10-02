"""Application constants and data locations."""

from __future__ import annotations

import os
from pathlib import Path

from gi.repository import GLib

APP_ID = "io.github.euvinicios.PodFlow"
APP_NAME = "PodFlow"
VERSION = "0.1.0"
GETTEXT_DOMAIN = "podflow"
DEVELOPER = "EuVinicios"
WEBSITE = "https://github.com/EuVinicios/Linux-Podcast"
ISSUE_URL = f"{WEBSITE}/issues"
COUNTRY = "br"
MPRIS_BUS_NAME = "org.mpris.MediaPlayer2.podflow"
USER_AGENT = f"PodFlow/{VERSION} (+{WEBSITE})"

PKG_DIR = Path(__file__).resolve().parent

# PODFLOW_OFFLINE=1 disables every network request (used by tests and CI).
OFFLINE = os.environ.get("PODFLOW_OFFLINE") == "1"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    return _ensure(Path(GLib.get_user_data_dir()) / "podflow")


def cache_dir() -> Path:
    return _ensure(Path(GLib.get_user_cache_dir()) / "podflow")


def covers_dir() -> Path:
    return _ensure(cache_dir() / "covers")


def downloads_dir() -> Path:
    return _ensure(data_dir() / "downloads")


def db_path() -> Path:
    return data_dir() / "podflow.db"


def source_data_dir() -> Path | None:
    """The repository's data/ folder when running from a source checkout."""
    candidate = PKG_DIR.parent / "data"
    return candidate if (candidate / "icons").is_dir() else None
