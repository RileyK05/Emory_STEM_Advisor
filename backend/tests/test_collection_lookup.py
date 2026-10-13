import pytest

from app.config import load_settings
from app.db.collections import UnknownCollection, resolve_collection
from tests.helpers import collection_name


def test_named_collection_resolves(db, collection):
    name = collection_name(db, collection)
    assert resolve_collection(db, load_settings(), name) == (collection, name)


def test_unknown_collection_is_a_domain_error_that_job_routes_map_as_invalid_input(db, collection):
    # ValueError, so the batch route's (ValueError, TypeError) handler answers 422
    # instead of swallowing it as "temporarily unavailable".
    assert issubclass(UnknownCollection, ValueError)
    with pytest.raises(UnknownCollection, match="no-such-collection"):
        resolve_collection(db, load_settings(), "no-such-collection")

