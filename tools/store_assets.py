#!/usr/bin/env python3
"""Build the Snap Store listing media from the app screenshots.

The store page (https://snapcraft.io/podflow/listing) takes up to five
screenshots and a 3:1 banner, separately from the AppStream metainfo. This
frames data/screenshots/*.png on the brand gradient with a short headline:

    python3 tools/store_assets.py             # writes data/store/*.png

Needs Pillow and librsvg (gir1.2-rsvg-2.0); text uses the Inter font.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "screenshots"
ICON = ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / "io.github.euvinicios.PodFlow.svg"

SLIDE_SIZE = (1760, 1100)  # 16:10
BANNER_SIZE = (1800, 600)  # 3:1, as the store asks for the featured banner
MAX_BYTES = 2 * 1024 * 1024  # store limit per image
RADIUS = 14  # GNOME window corners at 1×


@dataclass(frozen=True)
class Window:
    file: str
    x: int
    y: int
    scale: float = 1.0
    width: int | None = None  # trims the capture's empty margin on the right


@dataclass(frozen=True)
class Slide:
    name: str
    title: str
    subtitle: str
    windows: tuple[Window, ...]


SLIDES = (
    Slide("1-explore", "Brazil’s podcasts, made for GNOME",
          "Featured shows, trending podcasts and genre shelves in a native Linux app.",
          (Window("explore.png", 245, 232),)),
    Slide("2-charts", "The top 50, updated every day",
          "Top Podcasts and Top Episodes in Brazil, filtered by category.",
          (Window("charts.png", 245, 232),)),
    Slide("3-listen-now", "Pick up right where you left off",
          "Continue listening, build your Up Next queue and catch every new episode.",
          (Window("queue.png", 245, 232),)),
    Slide("4-sync", "Your podcasts on every device",
          "Optional sync with gpodder.net, Nextcloud or your own server.",
          (Window("sync.png", 245, 232),)),
    Slide("5-adaptive", "Light or dark, wide or narrow",
          "Follows your system style and adapts to any window size.",
          (Window("podcast-light.png", 170, 250, 0.9), Window("narrow-podcast.png", 1222, 290, 0.9, width=390))),
)


def render_svg(svg: str, size: tuple[int, int]) -> Image.Image:
    """Render at the SVG's own width and height, which match `size`."""
    pixbuf = Rsvg.Handle.new_from_data(svg.encode()).get_pixbuf()
    assert (pixbuf.get_width(), pixbuf.get_height()) == size
    return Image.frombuffer("RGBA", size, pixbuf.get_pixels(), "raw", "RGBA",
                            pixbuf.get_rowstride(), 1)


def waves(cx: float, cy: float, radii: tuple[int, ...], start: float, end: float) -> str:
    """Faint sound-wave arcs, echoing the icon."""
    paths = []
    for r in radii:
        a, b = math.radians(start), math.radians(end)
        x1, y1 = cx + r * math.cos(a), cy + r * math.sin(a)
        x2, y2 = cx + r * math.cos(b), cy + r * math.sin(b)
        paths.append(f'<path d="M{x1:.1f} {y1:.1f}A{r} {r} 0 0 1 {x2:.1f} {y2:.1f}"/>')
    return ('<g fill="none" stroke="#ffffff" stroke-opacity="0.07" stroke-width="30" '
            f'stroke-linecap="round">{"".join(paths)}</g>')


def backdrop(size: tuple[int, int], content: str) -> str:
    width, height = size
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">
  <defs>
    <linearGradient id="bd-bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#22094d"/>
      <stop offset="0.55" stop-color="#4c1d95"/>
      <stop offset="1" stop-color="#7c2bd0"/>
    </linearGradient>
    <radialGradient id="bd-glow" cx="0.82" cy="0" r="0.8">
      <stop offset="0" stop-color="#e07bff" stop-opacity="0.42"/>
      <stop offset="1" stop-color="#e07bff" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{width}" height="{height}" fill="url(#bd-bg)"/>
  <rect width="{width}" height="{height}" fill="url(#bd-glow)"/>
  {waves(-60, height * 0.78, (260, 400, 540), -62, 62)}
  {waves(width + 60, height * 0.78, (260, 400, 540), 118, 242)}
  {content}
</svg>"""


def text(x: float, y: float, value: str, size: int, *, weight: str = "Regular",
         fill: str = "#ffffff", anchor: str = "middle", opacity: float = 1.0) -> str:
    family = "Inter SemiBold" if weight == "SemiBold" else "Inter"
    spacing = -0.02 * size if size >= 48 else 0
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'letter-spacing="{spacing:.1f}" fill="{fill}" fill-opacity="{opacity}" '
            f'text-anchor="{anchor}">{escape(value)}</text>')


def icon(x: int, y: int, size: int) -> str:
    body = ICON.read_text().split("?>")[-1]
    return body.replace("<svg ", f'<svg x="{x}" y="{y}" ', 1).replace(
        'width="128" height="128"', f'width="{size}" height="{size}"', 1)


def rounded(size: tuple[int, int], radius: float, outline: int = 0) -> Image.Image:
    """Antialiased rounded-rectangle mask (filled, or just the outline)."""
    k = 4
    big = Image.new("L", (size[0] * k, size[1] * k), 0)
    box = (0, 0, big.width - 1, big.height - 1)
    if outline:
        ImageDraw.Draw(big).rounded_rectangle(box, radius * k, outline=255, width=outline * k)
    else:
        ImageDraw.Draw(big).rounded_rectangle(box, radius * k, fill=255)
    return big.resize(size, Image.LANCZOS)


def layer(canvas: Image.Image, image: Image.Image, pos: tuple[int, int]) -> Image.Image:
    top = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    top.paste(image, pos)
    return Image.alpha_composite(canvas, top)


def place(canvas: Image.Image, window: Window) -> Image.Image:
    shot = Image.open(SHOTS / window.file).convert("RGBA")
    if window.width:
        shot = shot.crop((0, 0, window.width, shot.height))
    if window.scale != 1:
        shot = shot.resize((round(shot.width * window.scale), round(shot.height * window.scale)),
                           Image.LANCZOS)
    radius = RADIUS * window.scale
    mask = rounded(shot.size, radius)

    pad = 90
    blur = Image.new("L", (shot.width + 2 * pad, shot.height + 2 * pad), 0)
    blur.paste(mask, (pad, pad))
    blur = blur.filter(ImageFilter.GaussianBlur(32)).point(lambda v: int(v * 0.6))
    shadow = Image.new("RGBA", blur.size, (12, 2, 28, 0))
    shadow.putalpha(blur)
    canvas = layer(canvas, shadow, (window.x - pad, window.y - pad + 26))

    shot.putalpha(mask)
    canvas = layer(canvas, shot, (window.x, window.y))
    edge = Image.new("RGBA", shot.size, (255, 255, 255, 0))
    edge.putalpha(rounded(shot.size, radius, outline=1).point(lambda v: int(v * 0.16)))
    return layer(canvas, edge, (window.x, window.y))


def save(image: Image.Image, path: Path) -> None:
    image.convert("RGB").save(path, optimize=True)
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(f"{path.name} has {size} bytes, over the store limit of {MAX_BYTES}")
    print(f"{path.relative_to(ROOT)}  {image.width}×{image.height}  {size // 1024} KB")


def build_slide(slide: Slide, out: Path) -> None:
    cx = SLIDE_SIZE[0] / 2
    header = (text(cx, 112, slide.title, 60, weight="SemiBold")
              + text(cx, 170, slide.subtitle, 30, fill="#eadcff"))
    canvas = render_svg(backdrop(SLIDE_SIZE, header), SLIDE_SIZE)
    for window in slide.windows:
        canvas = place(canvas, window)
    save(canvas, out / f"screenshot-{slide.name}.png")


def build_banner(out: Path) -> None:
    content = (icon(110, 168, 250)
               + text(392, 300, "PodFlow", 128, weight="SemiBold", anchor="start")
               + text(398, 368, "Brazil’s podcasts, made for GNOME", 38, anchor="start")
               + text(398, 428, "Free and open source · No ads · No tracking", 27,
                      fill="#eadcff", anchor="start"))
    canvas = render_svg(backdrop(BANNER_SIZE, content), BANNER_SIZE)
    canvas = place(canvas, Window("explore.png", 1090, 128, 0.64))
    save(canvas, out / "banner.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "store")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for slide in SLIDES:
        build_slide(slide, args.out)
    build_banner(args.out)


if __name__ == "__main__":
    main()
