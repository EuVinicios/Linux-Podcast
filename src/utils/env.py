"""Environment fixes applied before GTK starts."""

import os

_SNAP_SUFFIX = "_VSCODE_SNAP_ORIG"


def sanitize_environment() -> None:
    """Undo variables leaked by the VS Code snap into child processes.

    The snap points GIO_MODULE_DIR, GTK_PATH, LOCPATH, XDG_DATA_DIRS and friends
    at its own runtime, which crashes GTK/GStreamer apps started from its
    terminal. It keeps the original value in ``<NAME>_VSCODE_SNAP_ORIG``.
    """
    for key in [k for k in os.environ if k.endswith(_SNAP_SUFFIX)]:
        name = key[: -len(_SNAP_SUFFIX)]
        original = os.environ.pop(key)
        if original:
            os.environ[name] = original
        else:
            os.environ.pop(name, None)
