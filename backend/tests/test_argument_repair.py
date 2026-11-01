"""The tool-argument repair undoes a provider's re-encoding, and nothing else.

An OpenAI-compatible provider re-encodes tool arguments three ways: a field's
value arrives wrapped as {"item": value}, an empty array arrives as "", and a
whole number arrives as a string. Strict input models reject all three, and a
rejected finish ends a research run.

The repair is deliberately narrow. It undoes exactly those re-encodings and
leaves everything else alone: a string that merely looks numeric stays a
string, a float stays a float, a boolean stays a boolean, and a list that
holds more than one element is never mistaken for a single object. The tests
below pin both halves of that: what must be repaired, and what must not.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.tools.registry import repair_arguments


class Inner(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    n: int
    s: str


class Outer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    inner: Inner
    items: tuple[Inner, ...]
    names: tuple[str, ...]
    count: int
    label: str
    maybe: int | None = None


class HasItem(BaseModel):
    """A model that really does have a field called "item"."""
    model_config = ConfigDict(extra="forbid", strict=True)
    item: str
    n: int


def _repair(args: dict) -> dict:
    return repair_arguments(dict(args), Outer)


def _valid(args: dict) -> bool:
    repaired = _repair(args)
    full = {"inner": {"n": 1, "s": "a"}, "items": (), "names": (), "count": 1,
                "label": "x", **repaired}
    try:
        Outer.model_validate(full)
        return True
    except ValidationError:
        return False


# ---------------------------------------------------------------- integers


@pytest.mark.parametrize(("value", "expected"), [
    ("3", 3),
    ({"item": "3"}, 3),
    ("-5", -5),
    ({"item": "-5"}, -5),
    ("0", 0),
])
def test_a_whole_number_sent_as_a_string_is_repaired(value, expected):
    """A provider that stringifies an integer is undone, not rejected."""
    assert _repair({"count": value})["count"] == expected


@pytest.mark.parametrize("value", [
    "3.5",      # a float, not a whole number
    " 3 ",      # padded: not the token the provider sent
    "3a",       # not a number at all
    "",         # an empty value is not zero
    "123456789012345678901",  # too wide to be an int
    "0x10",
])
def test_a_value_that_is_not_a_whole_number_is_left_alone(value):
    """Only a whole number is coerced; everything else still fails validation."""
    assert _repair({"count": value})["count"] == value


def test_a_boolean_is_not_coerced_to_an_integer():
    """True is not 1 here; strict validation must still reject it."""
    assert _repair({"count": True})["count"] is True


def test_a_numeric_string_for_a_string_field_stays_a_string():
    """The repair is guided by the declared type, not by the value's shape."""
    assert _repair({"label": "3"})["label"] == "3"
    assert _repair({"label": {"item": "3"}})["label"] == "3"


def test_an_optional_integer_is_repaired_too():
    assert _repair({"maybe": "4"})["maybe"] == 4


# ------------------------------------------------------------------ models


def test_one_object_sent_as_a_one_element_array_is_repaired():
    """A provider that wraps a lone object in an array is undone."""
    repaired = _repair({"inner": [{"n": 1, "s": "a"}]})
    assert repaired["inner"] == {"n": 1, "s": "a"}


def test_a_two_element_array_is_never_mistaken_for_one_object():
    """Two objects are two objects; the repair must not silently drop one."""
    args = {"inner": [{"n": 1, "s": "a"}, {"n": 2, "s": "b"}]}
    assert _repair(args)["inner"] == args["inner"]


def test_an_array_of_scalars_is_never_mistaken_for_one_object():
    args = {"inner": ["a"]}
    assert _repair(args)["inner"] == args["inner"]


def test_a_wrapped_object_is_unwrapped_for_a_model_field():
    repaired = _repair({"inner": {"item": {"n": 1, "s": "a"}}})
    assert repaired["inner"] == {"n": 1, "s": "a"}


def test_a_model_with_a_real_item_field_is_not_unwrapped():
    """The wrapper name only counts when the model does not declare it."""
    repaired = repair_arguments({"item": {"item": "x"}, "n": 1}, HasItem)
    assert repaired == {"item": "x", "n": 1}
    HasItem.model_validate(repaired)


# ------------------------------------------------------------------ lists


def test_an_empty_array_sent_as_an_empty_string_becomes_an_empty_list():
    assert _repair({"names": ""})["names"] == ()


def test_a_single_value_wrapped_for_a_list_becomes_a_one_element_list():
    assert _repair({"names": {"item": "a"}})["names"] == ("a",)


def test_a_tuple_field_becomes_a_tuple_after_repair():
    """A tuple field is handed back as a tuple, so strict validation accepts it."""
    repaired = _repair({"items": [{"n": 1, "s": "a"}]})
    assert isinstance(repaired["items"], tuple)
    assert isinstance(repaired["items"][0], dict)


# --------------------------------------------------------------- limits


def test_an_unknown_key_is_still_rejected():
    """Repair undoes re-encodings; it does not widen what a tool accepts."""
    assert "nope" in _repair({"count": 1, "nope": 2})
    full = {"inner": {"n": 1, "s": "a"}, "items": (), "names": (), "count": 1,
            "label": "x", "nope": 2}
    with pytest.raises(ValidationError):
        Outer.model_validate(repair_arguments(full, Outer))


def test_repairing_is_idempotent():
    """Repairing an already-repaired value changes nothing."""
    once = _repair({"count": "3", "inner": [{"n": 1, "s": "a"}], "names": ""})
    twice = repair_arguments(dict(once), Outer)
    assert once == twice
