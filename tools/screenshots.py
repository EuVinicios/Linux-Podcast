#!/usr/bin/env python3
"""Drive PodFlow through its screens and save PNG screenshots.

Used for README/AppStream screenshots and as a UI smoke test:

    xvfb-run -a python3 tools/screenshots.py --out data/screenshots
    xvfb-run -a python3 tools/screenshots.py --smoke     # offline, fast, no files

Runs with an isolated data directory (.test-data/) so it never touches the
user's real library.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "screenshots")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=820)
    parser.add_argument("--dark", action="store_true", help="force the dark style")
    parser.add_argument("--light", action="store_true", help="force the light style")
    parser.add_argument("--narrow", action="store_true", help="also capture a phone-sized layout")
    parser.add_argument("--smoke", action="store_true",
                        help="offline smoke test: visit every screen, write nothing")
    parser.add_argument("--wait", type=float, default=5.0, help="seconds to wait per screen")
    return parser.parse_args()


ARGS = parse_args()
if ARGS.smoke:
    _tmp = tempfile.mkdtemp(prefix="podflow-smoke-")
    os.environ["XDG_DATA_HOME"] = os.path.join(_tmp, "data")
    os.environ["XDG_CACHE_HOME"] = os.path.join(_tmp, "cache")
    os.environ["PODFLOW_OFFLINE"] = "1"
    ARGS.wait = min(ARGS.wait, 0.6)
else:
    os.environ.setdefault("XDG_DATA_HOME", str(ROOT / ".test-data" / "data"))
    os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".test-data" / "cache"))
os.environ.setdefault("PODFLOW_AUDIO_SINK", "fakesink")
os.environ.setdefault("GSK_RENDERER", "cairo")
os.environ.setdefault("NO_AT_BRIDGE", "1")
sys.path.insert(0, str(ROOT))

import src  # noqa: E402,F401  (sets GI versions, cleans the environment)
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from src.main import PodFlowApplication  # noqa: E402

ERRORS: list[str] = []
_original_hook = sys.excepthook


def _excepthook(kind, value, tb) -> None:
    ERRORS.append("".join(traceback.format_exception(kind, value, tb)))
    _original_hook(kind, value, tb)


sys.excepthook = _excepthook

NERDCAST = "itunes:381816509"


class Director:
    def __init__(self, app: PodFlowApplication):
        self.app = app
        self.win = app.window
        self.steps: list[tuple[str, object, float]] = []

    def shot(self, name: str) -> None:
        if ARGS.smoke:
            return
        ARGS.out.mkdir(parents=True, exist_ok=True)
        # Render the whole window (it paints the background) and crop away the
        # client-side decoration margins around the content.
        ok, bounds = self.win.get_content().compute_bounds(self.win)
        if not ok:
            return
        width, height = int(bounds.get_width()), int(bounds.get_height())
        paintable = Gtk.WidgetPaintable.new(self.win)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, self.win.get_width(), self.win.get_height())
        node = snapshot.to_node()
        if node is None:
            return
        texture = self.win.get_native().get_renderer().render_texture(node, bounds)
        suffix = "-light" if ARGS.light else ""
        path = ARGS.out / f"{name}{suffix}.png"
        texture.save_to_png(str(path))
        print(f"saved {path} ({width}x{height})", flush=True)

    def add(self, label: str, action, wait: float | None = None) -> None:
        self.steps.append((label, action, ARGS.wait if wait is None else wait))

    def run(self) -> None:
        self._next()

    def _next(self) -> bool:
        if not self.steps:
            self.app.quit()
            return GLib.SOURCE_REMOVE
        label, action, wait = self.steps.pop(0)
        print(f"step: {label}", flush=True)
        try:
            action()
        except Exception:
            ERRORS.append(traceback.format_exc())
            traceback.print_exc()
        GLib.timeout_add(int(wait * 1000), self._next)
        return GLib.SOURCE_REMOVE

    # -- helpers ---------------------------------------------------------------------

    def section(self, name: str) -> None:
        self.win.show_section(name)

    def cue_latest(self) -> None:
        episode = self.app.db.latest_episode(NERDCAST)
        if episode is None:
            episode = next(iter(self.app.db.list_new_episodes(days=3650, limit=1)), None)
        if episode is not None:
            self.app.db.queue_clear()
            self.app.playback.cue(episode)
            episode.position = 0
            for other in self.app.db.list_episodes(NERDCAST, limit=6)[1:5]:
                self.app.db.queue_add(other.id)
            # A little progress makes "Continuar Ouvindo" meaningful.
            for index, other in enumerate(self.app.db.list_episodes(
                    "itunes:1477406521", limit=3)):
                self.app.db.save_progress(other.id, 600 + 420 * index, other.duration)
                self.app.db.mark_started(other.id)


def start(app: PodFlowApplication) -> None:
    if ARGS.dark:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    elif ARGS.light:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    app.window.set_default_size(ARGS.width, ARGS.height)
    d = Director(app)
    wait = ARGS.wait
    d.add("abrir NerdCast", lambda: d.win.open_podcast(NERDCAST), wait * 1.6)
    d.add("tocar último", d.cue_latest, 1.0)
    d.add("captura detalhe", lambda: d.shot("podcast"), 0.3)
    d.add("explorar", lambda: d.section("explore"), wait * 1.4)
    d.add("captura explorar", lambda: d.shot("explore"), 0.3)
    d.add("rankings", lambda: d.section("charts"), wait)
    d.add("captura rankings", lambda: d.shot("charts"), 0.3)
    d.add("top episódios", lambda: d.win._page("charts").stack.set_visible_child_name("episodes"),
          wait * 0.6)
    d.add("captura top episódios", lambda: d.shot("charts-episodes"), 0.3)
    d.add("ouvir agora", lambda: d.section("listen-now"), wait * 0.8)
    d.add("captura ouvir agora", lambda: d.shot("listen-now"), 0.3)
    d.add("fila", lambda: d.win.queue_split.set_show_sidebar(True), wait * 0.4)
    d.add("captura fila", lambda: d.shot("queue"), 0.3)
    d.add("fecha fila", lambda: d.win.queue_split.set_show_sidebar(False), 0.3)
    d.add("seguidos", lambda: d.section("following"), wait * 0.8)
    d.add("captura biblioteca", lambda: d.shot("library"), 0.3)
    for name in ("saved", "downloads", "history", "search"):
        d.add(name, lambda n=name: d.section(n), min(wait, 1.0))
    if ARGS.smoke:
        d.add("preferências", lambda: d.app.activate_action("preferences", None), 0.5)
        d.add("sobre", lambda: d.app.activate_action("about", None), 0.5)
        d.add("atalhos", lambda: d.app.activate_action("shortcuts", None), 0.5)
    if ARGS.narrow or ARGS.smoke:
        d.add("estreito", lambda: d.win.set_default_size(400, 820), wait)
        d.add("estreito explorar", lambda: d.section("explore"), wait)
        d.add("captura estreito", lambda: d.shot("narrow-explore"), 0.3)
        d.add("estreito detalhe", lambda: d.win.open_podcast(NERDCAST), wait)
        d.add("captura estreito detalhe", lambda: d.shot("narrow-podcast"), 0.3)
    d.run()


def main() -> int:
    app = PodFlowApplication()
    app.connect_after("activate", lambda a: GLib.timeout_add(800, lambda: start(a) or False))
    status = app.run([sys.argv[0]])
    if ERRORS:
        print(f"\n{len(ERRORS)} erro(s) durante a execução", file=sys.stderr)
        return 1
    return status


if __name__ == "__main__":
    sys.exit(main())
