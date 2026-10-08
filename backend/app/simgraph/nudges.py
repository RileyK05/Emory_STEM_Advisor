"""Hand-written nudges to the similarity clusters (`configs/simgraph_nudges.toml`).

Nudges are human edits applied to the distance matrix before clustering, plus
label overrides. They are data, read deterministically; no model writes them.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.atlas.records import normalize_code

NUDGES_PATH = Path(__file__).resolve().parents[2] / "configs" / "simgraph_nudges.toml"


class NudgeError(ValueError):
    """A malformed nudges file."""


@dataclass(frozen=True)
class LabelNudge:
    level: int
    course: str
    text: str


@dataclass(frozen=True)
class Nudges:
    together: tuple[tuple[str, ...], ...] = ()
    apart: tuple[tuple[str, str], ...] = ()
    labels: tuple[LabelNudge, ...] = ()
    # sha256 of the file the nudges came from; None when there was no file.
    file_hash: str | None = None


def _code(value, where: str) -> str:
    code = normalize_code(value) if isinstance(value, str) else None
    if code is None:
        raise NudgeError(f"{where}: invalid course code {value!r}")
    return code


def _courses(entry: dict, where: str, *, exactly: int | None = None) -> tuple[str, ...]:
    if set(entry) != {"courses"}:
        raise NudgeError(f"{where}: expected only a 'courses' key")
    values = entry["courses"]
    if not isinstance(values, list) or len(values) < 2:
        raise NudgeError(f"{where}: 'courses' must list at least two codes")
    if exactly is not None and len(values) != exactly:
        raise NudgeError(f"{where}: 'courses' must list exactly {exactly} codes")
    return tuple(_code(value, where) for value in values)


def parse_nudges(raw: dict, file_hash: str | None = None) -> Nudges:
    unknown = set(raw) - {"together", "apart", "label"}
    if unknown:
        raise NudgeError(f"unknown nudge kinds: {sorted(unknown)}")
    together = tuple(
        _courses(entry, f"together[{i}]") for i, entry in enumerate(raw.get("together", []))
    )
    apart = tuple(
        _courses(entry, f"apart[{i}]", exactly=2) for i, entry in enumerate(raw.get("apart", []))
    )
    labels = []
    for i, entry in enumerate(raw.get("label", [])):
        where = f"label[{i}]"
        if set(entry) != {"level", "course", "text"}:
            raise NudgeError(f"{where}: expected keys level, course, text")
        level, text = entry["level"], entry["text"]
        if not isinstance(level, int) or level < 1:
            raise NudgeError(f"{where}: level must be an integer >= 1")
        if not isinstance(text, str) or not text.strip():
            raise NudgeError(f"{where}: text must be a non-empty string")
        labels.append(LabelNudge(level, _code(entry["course"], where), text.strip()))
    return Nudges(together, apart, tuple(labels), file_hash)


def load_nudges(path: Path | None = None) -> Nudges:
    """Read the nudges file. A missing file means no nudges."""
    path = path or NUDGES_PATH
    if not path.exists():
        return Nudges()
    data = path.read_bytes()
    return parse_nudges(tomllib.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest())
