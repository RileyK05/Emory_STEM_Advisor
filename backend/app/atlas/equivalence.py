"""Campus equivalence: an Oxford course is the other campus's offering of a course.

Emory College and Oxford College publish the same course under different
codes. `MATH 112` is "Calculus II" in Atlanta and `MATH_OX 112` is "Calculus
II" at Oxford. A student who took one has taken the other, and a requirement
that names either accepts either. The database has to say so.

Two sources state the relation, and neither is a guess:

* **The catalog requirement text.** A requirement that offers an Atlanta
  course and an Oxford course with the same course number in one alternative
  list states that they are the two campuses' offering of one course::

      "BIOL 142 or BIOL_OX 142 or BIOL_OX 142Q or BIOL_OX 142X or BIOL 240"
      "FILM 101 or FILM_OX 101 or equivalent transfer credit"
      "(MATH 211 or PHYS 211 or MATH_OX 211 or MATH 276) and ..."

  This is the Atlas speaking, so it outranks anything inferred.

* **The course records.** Two records with the same course number and
  compatible titles are the same course at the two campuses. The title is
  required, because the number alone proves nothing: `PE_OX 101` is "Weight
  Training/Conditioning" and `PE 101` is "Personal Health".

The relation is deliberately *not* `courses.canonical_code`. That column is
the retrieval deduplication key, and an Atlanta/Oxford pair sharing it would
delete one campus's passage from the candidate set, relabel the survivor as
"BIOL 142 / BIOL_OX 142", blind the answer's course-identity check to the
difference between the campuses, and leave the research search unable to open
the Oxford record at all. A campus equivalence is a statement about
satisfying a requirement, not about being the same passage.

Nothing here reads or writes the database, and no model is involved.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

# The subject suffix Oxford's codes carry.
OXFORD_SUFFIX = "_OX"

# The basis on which a pair was established. Both are the Atlas's own
# statements; "inferred" is deliberately absent.
CATALOG_BASIS = "catalog_requirement"
RECORD_BASIS = "course_record"

# Alternative lists separate the codes with these words.
_ALTERNATIVE = re.compile(r"\s*(?:or|/|&|\+)\s*", re.IGNORECASE)
_CLAUSE = re.compile(r"[;\n]|(?<=[.!?])\s+")

# The two campuses abbreviate each other's titles. Only these spellings are
# expanded; nothing else is guessed at.
_TITLE_EQUIVALENTS = {
    "intro": "introduction",
    "w": "with",
    "lab": "laboratory",
    "labs": "laboratory",
    "biol": "biology",
    "chem": "chemistry",
    "psych": "psychology",
    "hist": "history",
    "econ": "economics",
    "anth": "anthropology",
    "sociol": "sociology",
    "philos": "philosophy",
    "sci": "science",
    "sciences": "science",
}
_TITLE_STOP = frozenset({"of", "the", "in", "to", "and", "a", "an", "for", "i", "or"})

# The letters a course number carries when it names a variant of a course
# rather than the course itself: "BIOL 142Q" is a variant of "BIOL 142".
_SUFFIX_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class CampusEquivalent:
    """One Oxford course and the Atlanta course it is the offering of."""

    oxford_code: str
    atlanta_code: str
    basis: str


def course_number(code: str) -> str:
    """The number part of a code, with any letter suffix."""
    return code.partition(" ")[2]


def base_subject(code: str) -> str:
    """The subject of a code, without Oxford's suffix."""
    return code.partition(" ")[0].removesuffix(OXFORD_SUFFIX)


def is_oxford(code: str) -> bool:
    return code.partition(" ")[0].endswith(OXFORD_SUFFIX)


def title_words(title: str) -> tuple[str, ...]:
    """The content words of a title, in order, with abbreviations expanded."""
    text = "".join(ch.lower() if ch.isalnum() else " " for ch in title or "")
    return tuple(
        _TITLE_EQUIVALENTS.get(word, word)
        for word in text.split() if word not in _TITLE_STOP
    )


def titles_compatible(left: str | None, right: str | None) -> bool | None:
    """Whether two titles describe the same course, or None when unknown.

    The two campuses write the same course's title at different lengths
    ("Concepts And Methods in Biol Anth" and "Concepts & Methods in
    Biological Anthropology"), so one title's content words must be contained
    in the other's. The titles must also open with the same word, because that
    is where the campuses name the course's subject: "Introduction to Social
    and Political Philosophy" and "Political Philosophy" share words but are
    not the same course. Two courses with no words in common are not the same
    course whatever their number says.
    """
    if not left or not right:
        return None
    left_words, right_words = title_words(left), title_words(right)
    if not left_words or not right_words:
        return None
    if left_words[0] != right_words[0]:
        return False
    left_set, right_set = set(left_words), set(right_words)
    return left_set <= right_set or right_set <= left_set


def _codes_in(text: str, reader) -> list[str]:
    return list(reader(text))


def _clause_codes(text: str, reader) -> list[str]:
    """Every course code a clause names, in one alternative list.

    A clause is one alternative list. "MATH 211 or PHYS 211 or MATH_OX 211"
    names three codes that all satisfy the same requirement, so the codes in a
    clause are the ones that can stand for one another.
    """
    codes: list[str] = []
    for part in _ALTERNATIVE.split(text):
        for code in _codes_in(part, reader):
            if code not in codes:
                codes.append(code)
    return codes


def derive_campus_equivalents(
    courses: list[tuple[str, str]],
    requirement_texts: list[str],
    reader=None,
) -> tuple[CampusEquivalent, ...]:
    """Derive the campus equivalences the corpus states.

    ``courses`` is the corpus's ``(code, title)`` records; ``requirement_texts``
    is the requirement text of each course. ``reader`` reads course codes out
    of prose; it defaults to the shared identity reader and can be replaced in
    a test.
    """
    if reader is None:
        from app.atlas.requisites import canonical_codes_in

        reader = canonical_codes_in
    titles = {code: title for code, title in courses}
    atlanta_codes = {code for code in titles if not is_oxford(code)}
    atlanta_by_number: dict[str, list[str]] = defaultdict(list)
    for code in atlanta_codes:
        atlanta_by_number[course_number(code)].append(code)

    # Every Oxford code the catalog names, whether or not the master has a row
    # for it. The codes the master lacks are the ones that need resolving: the
    # catalog says "BIOL 142 or BIOL_OX 142" and the master carries only the
    # first of the two.
    oxford_codes = {code for code in titles if is_oxford(code)}
    for text in requirement_texts:
        for clause in _CLAUSE.split(text or ""):
            for code in _codes_in(clause, reader):
                if is_oxford(code):
                    oxford_codes.add(code)

    # The subject renames the records establish: Oxford writes ANTH where
    # Atlanta writes ANT, and so on. A rename is learned only where the two
    # campuses' records agree on the course, so it cannot be a coincidence.
    atlanta_subjects = {code.partition(" ")[0] for code in atlanta_codes}
    renames: dict[str, str] = {}
    record_pairs: dict[str, set[str]] = defaultdict(set)
    for code in sorted(oxford_codes):
        if code not in titles:
            continue  # No record, so no title to compare.
        oxford_subject = base_subject(code)
        candidates = [
            candidate for candidate in atlanta_by_number.get(course_number(code), ())
            if titles_compatible(titles[code], titles[candidate]) is True
        ]
        same_subject = [
            candidate for candidate in candidates
            if candidate.partition(" ")[0] == oxford_subject
        ]
        if same_subject:
            # CHEM_OX pairs with CHEM, never with another department that
            # happens to share a number and a generic title ("Honors Research").
            record_pairs[code].update(same_subject)
            continue
        if oxford_subject in atlanta_subjects:
            continue  # Same-subject Atlanta row exists but titles disagreed.
        # Cross-subject rename (ANTH_OX → ANT): only when every title match
        # points at one Atlanta subject.
        other_subjects = {candidate.partition(" ")[0] for candidate in candidates}
        if len(other_subjects) != 1:
            continue
        atlanta_subject = next(iter(other_subjects))
        if renames.setdefault(oxford_subject, atlanta_subject) != atlanta_subject:
            # The campuses' subjects disagree in more than one way;
            # no rename can be trusted for this subject.
            renames[oxford_subject] = ""
            continue
        record_pairs[code].update(candidates)

    def atlanta_subject_of(code: str) -> str:
        # Atlanta subjects are already Atlanta; renames map Oxford → Atlanta.
        return code.partition(" ")[0]

    def oxford_subject_of(code: str) -> str:
        subject = base_subject(code)
        mapped = renames.get(subject)
        return mapped if mapped else subject

    def variant_counterpart(code: str, atlanta_here: list[str]) -> str | None:
        """The Atlanta course an Oxford topic variant stands for.

        The catalog writes "BIOL 142 or BIOL_OX 142 or BIOL_OX 142Q or
        BIOL_OX 142X": the suffix names a variant of that one course. The
        Atlanta course must carry the same subject and the same number
        without the suffix, and it must be the only one that does.

        A code whose own number is an Atlanta course's number exactly - "MATH
        112Z" - is that course, not a variant of another one, so the fallback
        never applies to it.
        """
        number = course_number(code)
        # Another department's course with the same number (ART 142Q vs
        # BIOL_OX 142Q) must not block a topic-variant match in this clause.
        if any(atlanta_subject_of(candidate) == oxford_subject_of(code)
               for candidate in atlanta_by_number.get(number, ())):
            return None
        stripped = number.rstrip(_SUFFIX_LETTERS)
        if stripped == number:
            return None
        candidates = [candidate for candidate in atlanta_here
                      if course_number(candidate) == stripped
                      and atlanta_subject_of(candidate) == oxford_subject_of(code)]
        if len(candidates) != 1:
            return None
        return candidates[0]

    pairs: dict[tuple[str, str], str] = {}

    # Source 1: the catalog's own alternative lists.
    for text in requirement_texts:
        for clause in _CLAUSE.split(text or ""):
            codes = _clause_codes(clause, reader)
            oxford_here = [code for code in codes if code in oxford_codes]
            if not oxford_here:
                continue
            atlanta_here = [code for code in codes if code in atlanta_codes]
            for code in oxford_here:
                same_number = [candidate for candidate in atlanta_here
                               if course_number(candidate) == course_number(code)]
                if len(same_number) == 1:
                    chosen = same_number[0]
                elif same_number:
                    # The same number under two subjects: the campuses write
                    # the subject differently, and the records say which
                    # subject belongs to which campus.
                    matching = [candidate for candidate in same_number
                                if atlanta_subject_of(candidate) == oxford_subject_of(code)]
                    if len(matching) != 1:
                        continue
                    chosen = matching[0]
                else:
                    # A topic variant. The catalog writes the Oxford course
                    # with a suffix the Atlanta course does not carry - "BIOL
                    # 142 or BIOL_OX 142 or BIOL_OX 142Q or BIOL_OX 142X" - and
                    # the suffix names a variant of that one course.
                    chosen = variant_counterpart(code, atlanta_here)
                    if chosen is None:
                        continue
                pairs.setdefault((code, chosen), CATALOG_BASIS)

    # Source 2: the course records.
    for code, candidates in record_pairs.items():
        for candidate in candidates:
            pairs.setdefault((code, candidate), RECORD_BASIS)

    return tuple(
        CampusEquivalent(oxford_code=oxford, atlanta_code=atlanta, basis=basis)
        for (oxford, atlanta), basis in sorted(pairs.items())
    )

