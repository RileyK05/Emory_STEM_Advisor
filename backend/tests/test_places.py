"""Campus places named in a text: deterministic, from the OSM-derived index."""

import pytest

from app.places import index_from, load_aliases, match_names


def _place(place_id, name, **names):
    return {"id": place_id, "name": name, "match": match_names(name, **names)}


INDEX = index_from({"campus": "test", "places": [
    _place("white", "Goodrich C. White Hall"),
    _place("library", "Robert W. Woodruff Library"),
    _place("woodpec", "Woodruff Physical Education Center", local=("WoodPEC",)),
    _place("pais", "Psychology and Interdisciplinary Sciences"),
    _place("anthro", "Anthropology"),
    _place("clinic", "Clinic B"),
    _place("hospital", "Emory University Hospital"),
    _place("tower", "Emory University Hospital Tower"),
    _place("smith-1", "Smith Hall"),
    _place("smith-2", "Smith Hall"),
    _place("msc", "Mathematics and Science Center"),
    _place("atwood", "Atwood Chemistry Center"),
    _place("cox", "Cox Hall"),
    _place("depot", "The Depot"),
    _place("esc", "Emory Student Center"),
]}, aliases=[
    # Hand-written names students use (configs/campus_places.toml shapes).
    {"says": ["MSC"], "places": ["msc"]},
    {"says": ["Woodruff", "Libs"], "places": ["library"], "not_followed_by": ["scholars"]},
    {"says": ["Kaldi's"], "places": ["depot", "esc"]},
    {"says": ["DCT", "Dobbs Common Table"], "places": ["esc"], "inside": "base floor"},
])


@pytest.mark.parametrize(("texts", "expected"), [
    # Full names, and the short form after a middle initial.
    (("Office hours are in Goodrich C. White Hall.",), ("white",)),
    (("Meet me at White Hall", "It's on the Quad"), ("white",)),
    (("Study at the Woodruff Library",), ("library",)),
    # OSM local names count as they are; case and punctuation don't matter.
    (("is the WOODPEC open?",), ("woodpec",)),
    # A long name reads as a place without a building word.
    (("Psychology and Interdisciplinary Sciences, room 230",), ("pais",)),
    # A subject or a stray letter never pins a building.
    (("I want to major in anthropology",), ()),
    (("See clinic b for details",), ()),
    # The longest name wins where names overlap.
    (("Visit the Emory University Hospital Tower lobby",), ("tower",)),
    (("Emory University Hospital",), ("hospital",)),
    # A name two places share points at neither.
    (("Smith Hall",), ()),
    # Not a whole word: no match inside another word.
    (("Coxwell and Libsworth",), ()),
    # Hand-written names: short forms, a name in two places, and a blocking word.
    (("where's the MSC",), ("msc",)),
    (("studying at libs tonight",), ("library",)),
    (("is Woodruff open late?",), ("library",)),
    (("Should I apply to the Woodruff Scholars program?",), ()),
    (("grab coffee at Kaldi's",), ("depot", "esc")),
    (("lunch at DCT?",), ("esc",)),
    # A longer OSM name still wins over a short hand-written one inside it.
    (("the Woodruff Physical Education Center",), ("woodpec",)),
    # Order of first mention across texts, deduplicated, at most three.
    (("Where is Cox Hall?", ("Cox Hall is by the Atwood Chemistry Center and the "
                             "Mathematics and Science Center, near White Hall.")),
     ("cox", "atwood", "msc")),
])
def test_places_in(texts, expected):
    assert INDEX.places_in(*texts) == expected


def test_hand_written_names_must_name_buildings_the_index_has():
    import json

    from app.places import INDEX_DIR

    with pytest.raises(ValueError, match="unknown places"):
        index_from({"campus": "test", "places": [_place("cox", "Cox Hall")]},
                   aliases=[{"says": ["Cox"], "places": ["no-such-building"]}])
    # The shipped list loads against the shipped index.
    data = json.loads((INDEX_DIR / "atlanta.json").read_text(encoding="utf-8"))
    assert index_from(data, load_aliases(campus="atlanta")).names
