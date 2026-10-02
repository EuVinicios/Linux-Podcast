"""Shared test utilities."""

from __future__ import annotations

import math
import struct
import time
import wave
from pathlib import Path

from gi.repository import GLib


def run_until(predicate, timeout: float = 10.0) -> bool:
    """Iterate the default main context until ``predicate()`` is true."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        context.iteration(False)
        time.sleep(0.005)
    return predicate()


def make_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> Path:
    """Write a quiet sine tone (no external tools needed)."""
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(frames)))
    return path
