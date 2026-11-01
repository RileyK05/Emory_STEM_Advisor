"""A program rule draft must not overstate what it actually establishes.

`generate_default_rules_tree` prepares the draft a reviewer starts from. It
deliberately produces no executable semantics: every clause comes back
unresolved, with the raw text and the section it came from, so a person
decides what each clause means.

Two things would mislead that person, and both are pinned here.

The first is the root. A tree whose every clause is unresolved must not claim
to be a supported all-of set: a reader (or an audit) that looks only at the
root would conclude the program's requirements are fully specified.

The second is the choose count. "Three additional 300-level CS courses" is a
count of three, and the audit engine needs that number to know the clause is
"any three of these" rather than "all of these".
"""

from __future__ import annotations

import pytest

from app.programs.requirements import choose_count
from app.programs.rules import generate_default_rules_tree


def _item(position: int, section: str, text: str, mentions, choose=None):
    return {"position": position, "section": section, "text": text,
            "mentions": list(mentions), "choose": choose, "evidence": []}


def _tree(items, title="Computer Science (BS)"):
    return generate_default_rules_tree({"title": title}, items)


def test_every_clause_of_a_draft_starts_unresolved():
    """The draft hands the reviewer the text, not a decision."""
    tree = _tree([
        _item(0, "CS BS Requirements", "Math 111", ["MATH 111"]),
        _item(1, "CS BS Requirements", "CS 171", ["CS 171"]),
    ])
    assert tree["nodeType"] == "all_of"
    for child in tree["children"]:
        assert child["nodeType"] == "unresolved"
        assert child["coverageDisposition"] == "unresolved"
        assert child["unresolvedReason"]


def test_a_draft_keeps_the_section_of_each_clause():
    """A program with tracks keeps them apart, so a reviewer can see them.

    "Computer Science BS Requirements" and "Computer Science BS with AI
    Concentration Requirements" are different requirement sets. The draft must
    not merge them into one indistinguishable list.
    """
    tree = _tree([
        _item(0, "Computer Science BS Requirements", "Math 111", ["MATH 111"]),
        _item(1, "Computer Science BS with AI Concentration Requirements",
              "CS 371W", ["CS 371W"]),
    ])
    sections = {child["section"] for child in tree["children"]}
    assert len(sections) == 2, "the two tracks were merged"


def test_a_draft_of_an_empty_program_is_unresolved():
    """A program with no clauses cannot be a supported all-of set."""
    tree = _tree([])
    assert tree["nodeType"] == "unresolved"
    assert tree["coverageDisposition"] == "unresolved"


def test_a_counted_clause_keeps_its_count():
    """A clause that says "one from the following" keeps the number."""
    tree = _tree([
        _item(0, "CS BS Requirements", "One from the following: CS 370, CS 371W",
              ["CS 370", "CS 371W"], choose=1),
    ])
    assert tree["children"][0]["count"] == 1


@pytest.mark.xfail(
    reason="the root reports a supported all_of even when every clause below it is "
           "unresolved, so a reader who looks only at the root would take the "
           "program's requirements for a settled all-of set",
    strict=False,
)
def test_a_draft_whose_clauses_are_all_unresolved_does_not_claim_to_be_supported():
    tree = _tree([
        _item(0, "CS BS Requirements", "Math 111", ["MATH 111"]),
        _item(1, "CS BS with AI Concentration Requirements", "CS 371W", ["CS 371W"]),
    ])
    assert tree["coverageDisposition"] != "supported"
    assert tree["unresolvedReason"]


@pytest.mark.xfail(
    reason="choose_count only recognises 'select N' and 'one/two ... chosen from'; "
           "the catalog's own 'Three ... courses' and '3 ... electives' wording is "
           "not read, so the count is lost on most clauses",
    strict=False,
)
@pytest.mark.parametrize(("text", "expected"), [
    ("Three additional 300-level CS courses", 3),
    ("Three 400-level CS courses", 3),
    ("3 Advanced QTM (300-level or above) electives", 3),
    ("Four (4) courses at the 300 level or above", 4),
])
def test_a_written_out_course_count_is_read(text, expected):
    """The catalog writes the count in words; the audit engine needs it."""
    assert choose_count(text) == expected


def test_the_counts_that_are_already_read_still_are():
    assert choose_count("One from the following: CS 370, CS 371W") == 1
    assert choose_count("Two courses chosen from: CS 442, CS 443") == 2
    assert choose_count("select 2 courses") == 2
