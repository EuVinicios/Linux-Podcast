import re
import unittest

import tests  # noqa: F401
from gi.repository import Pango

from src.utils.text import html_to_markup, html_to_text, initials, summarize


def assert_valid_label_markup(markup: str) -> None:
    """GtkLabel handles <a href> itself and hands the rest to Pango."""
    links = re.findall(r"<a href=\"[^\"<>]*\">|</a>", markup)
    opened = sum(1 for tag in links if tag != "</a>")
    assert opened == len(links) - opened, f"links desbalanceados: {markup!r}"
    Pango.parse_markup(re.sub(r"<a href=\"[^\"<>]*\">|</a>", "", markup), -1, "\0")


class TextTests(unittest.TestCase):
    HTML = ('<p>Olá <b>mundo</b> &amp; <a href="https://gnome.org">GNOME</a></p>'
            '<ul><li>Um</li><li>Dois</li></ul><script>alert(1)</script>'
            '<p>Fim com &lt;tag&gt; e &nbsp;espaço</p>')

    def test_plain_text(self):
        text = html_to_text(self.HTML)
        self.assertIn("Olá mundo & GNOME", text)
        self.assertIn("• Um", text)
        self.assertIn("• Dois", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("<", text.replace("<tag>", ""))

    def test_markup_is_valid_pango(self):
        markup = html_to_markup(self.HTML)
        self.assertIn('<a href="https://gnome.org">GNOME</a>', markup)
        self.assertIn("<b>mundo</b>", markup)
        self.assertIn("&lt;tag&gt;", markup)
        assert_valid_label_markup(markup)

    def test_markup_handles_broken_nesting(self):
        for source in ("<b><i>texto</b></i>", "<a href='javascript:x'>link</a>",
                       "<a href='https://a'><a href='https://b'>x</a></a>", "texto & solto <"):
            assert_valid_label_markup(html_to_markup(source))

    def test_summarize(self):
        long = "<p>" + "palavra " * 100 + "</p>"
        summary = summarize(long, 50)
        self.assertLessEqual(len(summary), 51)
        self.assertTrue(summary.endswith("…"))
        self.assertEqual(summarize("Curto"), "Curto")

    def test_initials(self):
        self.assertEqual(initials("Ciência Sem Fim"), "CS")
        self.assertEqual(initials("NerdCast"), "N")
        self.assertEqual(initials(""), "?")


if __name__ == "__main__":
    unittest.main()
