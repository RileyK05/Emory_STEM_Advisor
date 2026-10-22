"""Open local files past the legacy Windows path limit.

Windows rejects a normal absolute path of 260 characters, counting the
terminator. Artifact originals nest two SHA-256 names under the data
directory, so a long test or install path cannot be replaced, read, or
deleted unless the call uses the extended prefix. Shorter paths stay
unchanged.
"""

import os
from pathlib import Path

_LEGACY_LIMIT = 260


def io_path(path: Path | str) -> Path:
    """Return a path this process can create, read, stat, and unlink."""
    raw = os.fspath(path)
    if os.name == "nt" and raw.startswith("\\\\?\\"):
        return Path(raw)
    text = os.path.abspath(raw)
    if os.name != "nt" or len(text) < _LEGACY_LIMIT:
        return Path(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)
