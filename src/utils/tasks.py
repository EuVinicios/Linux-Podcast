"""Run blocking work in background threads and deliver results on the GTK thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from gi.repository import GLib

log = logging.getLogger(__name__)

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="podflow-net")
        return _pool


def shutdown() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


def is_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def idle(func: Callable[..., Any], *args: Any) -> None:
    """Schedule ``func(*args)`` once on the main loop."""

    def _run() -> bool:
        try:
            func(*args)
        except Exception:
            log.exception("Erro em callback agendado")
        return GLib.SOURCE_REMOVE

    GLib.idle_add(_run)


def run_async(func: Callable[..., Any], *args: Any,
              on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[BaseException], None] | None = None,
              pool: ThreadPoolExecutor | None = None, **kwargs: Any) -> Future:
    """Run ``func`` in a worker thread; callbacks run on the GTK main thread."""
    future = (pool or executor()).submit(func, *args, **kwargs)

    def _deliver(fut: Future) -> None:
        if fut.cancelled():
            return
        error = fut.exception()
        if error is not None:
            if on_error is not None:
                idle(on_error, error)
            else:
                log.warning("Tarefa em segundo plano falhou: %s", error, exc_info=error)
        elif on_done is not None:
            idle(on_done, fut.result())

    future.add_done_callback(_deliver)
    return future
