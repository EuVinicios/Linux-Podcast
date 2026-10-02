"""Test package setup: isolated data directories, no network, silent audio.

Must run before ``src`` is imported (config reads the environment at import).
"""

import os
import tempfile
from pathlib import Path

_ROOT = tempfile.mkdtemp(prefix="podflow-tests-")
os.environ["XDG_DATA_HOME"] = os.path.join(_ROOT, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_ROOT, "cache")
os.environ.setdefault("PODFLOW_OFFLINE", "1")
os.environ.setdefault("PODFLOW_AUDIO_SINK", "fakesink")

FIXTURES = Path(__file__).resolve().parent / "fixtures"

import src  # noqa: E402,F401  (pins GI versions and cleans the environment)
