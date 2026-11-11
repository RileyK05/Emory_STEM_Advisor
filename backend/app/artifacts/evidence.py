"""Plain source legends derived from immutable artifact evidence."""
from app.artifacts.storage import Spec


def source_legend(spec: Spec) -> list[tuple[str, str]]:
    """Each cited (source name, locator) once, in citation order.

    The reader of an export needs where a fact came from, not the block ids and
    chunk ids that tie it to the evidence record.
    """
    rows: dict[tuple[str, str], None] = {}
    for references in spec.evidence.values():
        for ref in references:
            rows[(ref.source_name, ref.locator_label)] = None
    return list(rows)
