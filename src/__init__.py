"""PodFlow — um aplicativo de podcasts nativo para o GNOME."""

from .utils.env import sanitize_environment

# Must run before any GTK/GIO library reads its environment.
sanitize_environment()

import gi  # noqa: E402

for _namespace, _version in (
    ("Gtk", "4.0"),
    ("Gdk", "4.0"),
    ("Gsk", "4.0"),
    ("Adw", "1"),
    ("Graphene", "1.0"),
    ("Pango", "1.0"),
    ("GdkPixbuf", "2.0"),
    ("Gst", "1.0"),
    ("GstAudio", "1.0"),
):
    gi.require_version(_namespace, _version)
