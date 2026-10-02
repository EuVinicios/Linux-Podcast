"""Color helpers: dominant artwork color, contrast and placeholder palettes."""

from __future__ import annotations

import colorsys
import hashlib

RGB = tuple[float, float, float]  # 0..1 components

# Brand-ish fallbacks for placeholders (violet → magenta family + friends).
_PALETTE = ("#8b3dff", "#e0338a", "#2a7de1", "#16a085", "#e67e22",
            "#c0392b", "#6c5ce7", "#00a3a3", "#d35400", "#7d4f2e")


def parse_hex(value: str | None) -> RGB | None:
    if not value:
        return None
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def to_hex(rgb: RGB) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb)


def relative_luminance(rgb: RGB) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def is_light(rgb: RGB) -> bool:
    return relative_luminance(rgb) > 0.45


def adjust(rgb: RGB, lightness: float | None = None, saturation: float | None = None,
           lightness_delta: float = 0.0) -> RGB:
    h, l, s = colorsys.rgb_to_hls(*rgb)
    if lightness is not None:
        l = lightness
    if saturation is not None:
        s = saturation
    l = min(1.0, max(0.0, l + lightness_delta))
    return colorsys.hls_to_rgb(h, l, s)


def darken(rgb: RGB, amount: float = 0.15) -> RGB:
    return adjust(rgb, lightness_delta=-amount)


def placeholder_color(seed: str) -> RGB:
    digest = hashlib.sha1((seed or "podflow").encode("utf-8")).digest()
    return parse_hex(_PALETTE[digest[0] % len(_PALETTE)])  # type: ignore[return-value]


def vivid_average(pixels: bytes, width: int, height: int, rowstride: int,
                  channels: int) -> RGB:
    """Average color weighted towards saturated, mid-lightness pixels."""
    acc_r = acc_g = acc_b = 0.0
    total = 0.0
    step = max(1, min(width, height) // 24)
    for y in range(0, height, step):
        row = y * rowstride
        for x in range(0, width, step):
            offset = row + x * channels
            r = pixels[offset] / 255
            g = pixels[offset + 1] / 255
            b = pixels[offset + 2] / 255
            _h, l, s = colorsys.rgb_to_hls(r, g, b)
            weight = (s ** 1.5) * max(0.0, 1 - abs(l - 0.5) * 1.6) + 0.02
            acc_r += r * weight
            acc_g += g * weight
            acc_b += b * weight
            total += weight
    if total <= 0:
        return (0.35, 0.35, 0.4)
    rgb = (acc_r / total, acc_g / total, acc_b / total)
    h, l, s = colorsys.rgb_to_hls(*rgb)
    l = min(max(l, 0.28), 0.52)
    s = min(1.0, s * 1.15 + 0.05)
    return colorsys.hls_to_rgb(h, l, s)
