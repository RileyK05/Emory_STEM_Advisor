"""Campus places named in a text, for the map under an answer.

Deterministic: the place index is written by `scripts.build_campus_map` from the
OpenStreetMap survey (`app/places/<campus>.json`). No model writes, guesses or
fills in a place, a name or a location; a name this module does not know is
simply not shown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

INDEX_DIR = Path(__file__).resolve().parent
MAX_PLACES = 3

# A name is matched only when it reads as a building, so "Anthropology" (a
# building, and a major) or "Administration" never pin a map to a subject.
PLACE_WORDS = frozenset({
    "hall", "center", "centre", "library", "building", "house", "chapel", "deck", "museum",
    "hospital", "school", "pavilion", "plant", "complex", "church", "hotel", "tower", "facility",
    "booth", "annex", "lodge", "stadium", "field", "arena", "theater", "theatre", "gym",
    "gymnasium", "residence", "dormitory", "commons",
})
# "Robert W. Woodruff Library" is also "Woodruff Library": the words after a
# middle initial. Only names with an initial; anything looser is the gazetteer's job.
_INITIAL = re.compile(r"^(?:[A-Z][a-z]+ )+[A-Z]\. (.+)$")


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def reads_as_building(text: str) -> bool:
    """Two or more words with a building word ("Cox Hall"), or four or more words
    ("Psychology and Interdisciplinary Sciences"); either way at least one word of
    three letters that is not a building word, so "Building C" or "Clinic B" never
    match a stray letter."""
    words = _normal(text).split()
    distinctive = [w for w in words if len(w) >= 3 and w not in PLACE_WORDS]
    return bool(distinctive) and (len(words) >= 4 or (len(words) >= 2 and bool(PLACE_WORDS & set(words))))


def match_names(name: str, *, alternates: tuple[str, ...] = (), local: tuple[str, ...] = ()) -> list[str]:
    """The strings that name a building in prose: its name and its OSM
    official/alternate names when they read as a building, the short form after a
    middle initial, and OSM local names (what people call it) as they are."""
    out: list[str] = []
    for candidate in (name, *alternates):
        found = _INITIAL.match(candidate)
        for text in (candidate, found.group(1) if found else None):
            if text and reads_as_building(text):
                out.append(_normal(text))
    out.extend(_normal(text) for text in local if _normal(text))
    return list(dict.fromkeys(out))


@dataclass(frozen=True)
class PlaceIndex:
    campus: str
    # Normalized name -> place id. Names shared by two places are left out.
    names: dict[str, str]
    # Place id -> the building's name as OSM gives it, for prompts and lists.
    titles: dict[str, str]

    def places_in(self, *texts: str, limit: int = MAX_PLACES) -> tuple[str, ...]:
        """Place ids named in the texts, in order of first mention, at most `limit`."""
        hits: list[tuple[int, int, str]] = []
        for order, text in enumerate(texts):
            normal = f" {_normal(text)} "
            spans: list[tuple[int, int, str]] = []
            for name, place in self.names.items():
                start = normal.find(f" {name} ")
                while start >= 0:
                    spans.append((start, start + len(name) + 1, place))
                    start = normal.find(f" {name} ", start + 1)
            # The longest name wins where names overlap: "Emory University Hospital
            # Tower" is the tower, not also the hospital inside its name.
            spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
            end = -1
            for start, stop, place in spans:
                if start >= end:
                    hits.append((order, start, place))
                    end = stop
        ordered = [place for _, _, place in sorted(hits)]
        return tuple(dict.fromkeys(ordered))[:limit]


def index_from(data: dict) -> PlaceIndex:
    owners: dict[str, set[str]] = {}
    for place in data["places"]:
        for name in place["match"]:
            owners.setdefault(name, set()).add(place["id"])
    return PlaceIndex(campus=data["campus"],
                      names={name: next(iter(ids)) for name, ids in owners.items() if len(ids) == 1},
                      titles={place["id"]: place["name"] for place in data["places"]})


@lru_cache(maxsize=4)
def _load(path: Path, mtime: float) -> PlaceIndex:
    return index_from(json.loads(path.read_text(encoding="utf-8")))


def load_places(campus: str = "atlanta") -> PlaceIndex | None:
    path = INDEX_DIR / f"{campus}.json"
    if not path.exists():
        return None
    return _load(path, path.stat().st_mtime)


def places_in(*texts: str, campus: str = "atlanta") -> list[str]:
    index = load_places(campus)
    return list(index.places_in(*texts)) if index else []


def place_titles(ids: list[str], campus: str = "atlanta") -> list[str]:
    index = load_places(campus)
    return [index.titles[i] for i in ids if i in index.titles] if index else []
