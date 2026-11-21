"""Campus places named in a text, for the map under an answer.

Deterministic: the place index is written by `scripts.build_campus_map` from the
OpenStreetMap survey (`app/places/<campus>.json`), plus the names students use
from the hand-written `configs/campus_places.toml`. No model writes, guesses or
fills in a place, a name or a location; a name this module does not know is
simply not shown.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

INDEX_DIR = Path(__file__).resolve().parent
# Hand-written student names for places (Riley's), matched beside the OSM names.
ALIASES_PATH = Path(__file__).resolve().parents[2] / "configs" / "campus_places.toml"
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
    # Normalized name -> the place ids it names. An OSM name two buildings share is
    # left out; a hand-written name (configs/campus_places.toml) may name several.
    names: dict[str, tuple[str, ...]]
    # Place id -> the building's name as OSM gives it, for prompts and lists.
    titles: dict[str, str]
    # Normalized name -> words that, right after it, make it something else.
    not_followed_by: dict[str, frozenset[str]] = field(default_factory=dict)
    # Normalized name -> where in the building it is ("base floor"); the map
    # pins the building, research says the rest.
    inside: dict[str, str] = field(default_factory=dict)

    def places_in(self, *texts: str, limit: int = MAX_PLACES) -> tuple[str, ...]:
        """Place ids named in the texts, in order of first mention, at most `limit`."""
        ordered = [place for _, places in self.mentions(*texts) for place in places]
        return tuple(dict.fromkeys(ordered))[:limit]

    def mentions(self, *texts: str) -> list[tuple[str, tuple[str, ...]]]:
        """Each name found (normalized, as written in the index) and the places it
        names, in order of first mention; overlapping names keep the longest."""
        hits: list[tuple[int, int, str, tuple[str, ...]]] = []
        for order, text in enumerate(texts):
            normal = f" {_normal(text)} "
            spans: list[tuple[int, int, str, tuple[str, ...]]] = []
            for name, places in self.names.items():
                blocked = self.not_followed_by.get(name, frozenset())
                start = normal.find(f" {name} ")
                while start >= 0:
                    stop = start + len(name) + 1
                    following = normal[stop + 1:].split(" ", 1)[0]
                    if following not in blocked:
                        spans.append((start, stop, name, places))
                    start = normal.find(f" {name} ", start + 1)
            # The longest name wins where names overlap: "Emory University Hospital
            # Tower" is the tower, not also the hospital inside its name.
            spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
            end = -1
            for start, stop, name, places in spans:
                if start >= end:
                    hits.append((order, start, name, places))
                    end = stop
        hits.sort(key=lambda h: (h[0], h[1]))
        return [(name, places) for _, _, name, places in hits]


def index_from(data: dict, aliases: list[dict] = ()) -> PlaceIndex:
    """The index from the OSM-derived places, plus hand-written names on top."""
    owners: dict[str, set[str]] = {}
    for place in data["places"]:
        for name in place["match"]:
            owners.setdefault(name, set()).add(place["id"])
    names = {name: (next(iter(ids)),) for name, ids in owners.items() if len(ids) == 1}
    blocked: dict[str, frozenset[str]] = {}
    inside: dict[str, str] = {}
    known = {place["id"] for place in data["places"]}
    for entry in aliases:
        unknown = [place for place in entry["places"] if place not in known]
        if unknown:
            raise ValueError(f"campus_places: {entry['says']} names unknown places {unknown}")
        for said in entry["says"]:
            name = _normal(said)
            names[name] = tuple(entry["places"])
            if entry.get("not_followed_by"):
                blocked[name] = frozenset(_normal(w) for w in entry["not_followed_by"])
            if entry.get("inside"):
                inside[name] = entry["inside"]
    return PlaceIndex(campus=data["campus"], names=names,
                      titles={place["id"]: place["name"] for place in data["places"]},
                      not_followed_by=blocked, inside=inside)


_ALIAS_KEYS = {"says", "places", "not_followed_by", "inside", "note"}


def load_aliases(path: Path = ALIASES_PATH, campus: str = "atlanta") -> list[dict]:
    """configs/campus_places.toml: hand-written names for one campus. Unknown keys fail."""
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("campus") != campus:
        return []
    entries = data.get("name", [])
    for entry in entries:
        unknown = set(entry) - _ALIAS_KEYS
        if unknown or not entry.get("says") or not entry.get("places"):
            raise ValueError(f"campus_places: bad entry {entry.get('says')} {sorted(unknown)}")
    return entries


@lru_cache(maxsize=4)
def _load(path: Path, mtime: float, aliases_mtime: float) -> PlaceIndex:
    data = json.loads(path.read_text(encoding="utf-8"))
    return index_from(data, load_aliases(campus=data["campus"]))


def load_places(campus: str = "atlanta") -> PlaceIndex | None:
    path = INDEX_DIR / f"{campus}.json"
    if not path.exists():
        return None
    aliases_mtime = ALIASES_PATH.stat().st_mtime if ALIASES_PATH.exists() else 0.0
    return _load(path, path.stat().st_mtime, aliases_mtime)


def places_in(*texts: str, campus: str = "atlanta") -> list[str]:
    index = load_places(campus)
    return list(index.places_in(*texts)) if index else []


def place_mentions(text: str, campus: str = "atlanta") -> list[tuple[str, list[str], str]]:
    """What a text calls each place it names, with the building names and where
    inside, if the list says: ("dct", ["Emory Student Center"], "base floor"). For
    telling research what the map shows."""
    index = load_places(campus)
    if not index:
        return []
    return [(name, [index.titles[p] for p in places if p in index.titles],
             index.inside.get(name, ""))
            for name, places in index.mentions(text)]


