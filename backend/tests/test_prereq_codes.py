"""`_codes`: subjects are whole words; and/or are never subjects. No models."""

from app.retrieval.seams.prereq import _codes


def test_and_is_not_a_subject_and_subject_carries():
    assert _codes("MATH 111 and 112") == ["MATH 111", "MATH 112"]


def test_single_subject_word_is_not_a_course():
    # "141" has no valid subject before it, so nothing is returned.
    assert _codes("Physics 141") == []


def test_lowercase_whole_word_subject_matches():
    assert _codes("what do I need for econ301?") == ["ECON 301"]


def test_bare_number_without_a_subject_is_ignored():
    assert _codes("section 111 applies") == []


def test_full_codes_are_deduped_in_order():
    assert _codes("MATH 111 and MATH 111 or ECON 101") == ["MATH 111", "ECON 101"]
