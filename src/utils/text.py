"""Convert podcast HTML (show notes) into plain text or safe Pango markup."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_WS = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")

_BLOCK_TAGS = {"p", "div", "section", "article", "ul", "ol", "h1", "h2", "h3",
               "h4", "h5", "h6", "blockquote", "table", "tr"}
_BOLD_TAGS = {"b", "strong", "h1", "h2", "h3", "h4", "h5", "h6"}
_ITALIC_TAGS = {"i", "em", "cite"}
_SKIP_TAGS = {"script", "style", "head", "title"}


class _Converter(HTMLParser):
    def __init__(self, markup: bool):
        super().__init__(convert_charrefs=True)
        self.markup = markup
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.skip_depth = 0

    def _add_break(self, count: int) -> None:
        tail = "".join(self.parts[-3:])
        existing = len(tail) - len(tail.rstrip("\n"))
        if self.parts and existing < count:
            self.parts.append("\n" * (count - existing))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self._add_break(1)
            self.parts.append("• ")
        elif tag in _BLOCK_TAGS:
            self._add_break(2)
        if not self.markup:
            return
        if tag in _BOLD_TAGS:
            self.parts.append("<b>")
            self.open_tags.append("b")
        elif tag in _ITALIC_TAGS:
            self.parts.append("<i>")
            self.open_tags.append("i")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            # Pango forbids nested links; "" marks an anchor we did not emit.
            if href.startswith(("http://", "https://", "mailto:")) and "a" not in self.open_tags:
                self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
                self.open_tags.append("a")
            else:
                self.open_tags.append("")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        wanted = None
        if self.markup:
            if tag in _BOLD_TAGS:
                wanted = "b"
            elif tag in _ITALIC_TAGS:
                wanted = "i"
            elif tag == "a":
                wanted = "a"
        if wanted:
            # Close the most recent matching tag and everything opened after it.
            for index in range(len(self.open_tags) - 1, -1, -1):
                entry = self.open_tags[index]
                if entry == wanted or (wanted == "a" and entry == ""):
                    for closing in reversed(self.open_tags[index:]):
                        if closing:
                            self.parts.append(f"</{closing}>")
                    del self.open_tags[index:]
                    break
        if tag in _BLOCK_TAGS:
            self._add_break(2)

    def handle_data(self, data):
        if self.skip_depth:
            return
        data = _WS.sub(" ", data.replace("\n", " "))
        self.parts.append(html.escape(data, quote=False) if self.markup else data)

    def result(self) -> str:
        for closing in reversed(self.open_tags):
            if closing:
                self.parts.append(f"</{closing}>")
        text = "".join(self.parts)
        lines = [line.strip() for line in text.split("\n")]
        return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _convert(source: str | None, markup: bool) -> str:
    if not source:
        return ""
    if "<" not in source and "&" not in source:
        text = source.strip()
        return html.escape(text, quote=False) if markup else text
    parser = _Converter(markup)
    try:
        parser.feed(source)
        parser.close()
    except Exception:  # malformed HTML: fall back to plain text
        plain = re.sub(r"<[^>]+>", " ", source)
        plain = _WS.sub(" ", html.unescape(plain)).strip()
        return html.escape(plain, quote=False) if markup else plain
    return parser.result()


def html_to_text(source: str | None) -> str:
    """Strip tags, keeping paragraph breaks and bullet points."""
    return _convert(source, markup=False)


def html_to_markup(source: str | None) -> str:
    """Safe Pango markup: only <b>, <i> and <a href> survive."""
    return _convert(source, markup=True)


def summarize(source: str | None, limit: int = 300) -> str:
    """One-paragraph plain-text summary used in episode rows."""
    text = html_to_text(source)
    text = _WS.sub(" ", text.replace("\n", " ")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-") + "…"


def initials(title: str, count: int = 2) -> str:
    words = [w for w in re.split(r"[\s\-–—_:|]+", title or "") if w and w[0].isalnum()]
    if not words:
        return "?"
    letters = "".join(w[0] for w in words[:count]).upper()
    return letters
