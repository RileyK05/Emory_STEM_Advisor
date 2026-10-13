"""Plain source legends derived from immutable artifact evidence."""
from app.artifacts.storage import Spec


def source_legend(spec: Spec) -> list[tuple[str, str, str, str]]:
    rows = []
    for element_id, references in spec.evidence.items():
        for ref in references:
            rows.append((element_id, ref.source_name, ref.locator_label, str(ref.chunk_id)))
    return rows
