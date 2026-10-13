"""The model's artifact candidate: its wire shape and the local validation of it.

The complete native spec is parsed and validated here; no partial JSON repair or
clipping is performed, and model-written provenance is never trusted.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts.models import ArtifactReference, DocumentSpec, WorkbookSpec
from app.artifacts.storage import validate_spec


class ElementBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    element_id: str = Field(min_length=1, max_length=300)
    source_slots: list[int] = Field(min_length=1, max_length=50)


class ArtifactCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec_json: str = Field(max_length=16_000_000)
    bindings: list[ElementBinding] = Field(max_length=1000)


def content_elements(spec):
    if isinstance(spec, DocumentSpec):
        return {b.id for b in spec.blocks if
                getattr(b, "text", "") or getattr(b, "items", ()) or getattr(b, "rows", ())
                or getattr(b, "headers", ())}
    if isinstance(spec, WorkbookSpec):
        return {f"{s.id}/{c.id}" for s in spec.sheets for c in s.cells
                if c.value is not None or c.formula is not None}
    return {"html"}


def validate_candidate(kind, text, chunks, *, mode):
    candidate = ArtifactCandidate.model_validate_json(text)
    spec = validate_spec(kind, json.loads(candidate.spec_json))
    evidence = {}
    elements = content_elements(spec)
    if not elements:
        raise ValueError("generated artifact has no content")
    if mode == "grounded":
        for binding in candidate.bindings:
            if binding.element_id not in elements or binding.element_id in evidence:
                raise ValueError("invalid or duplicate artifact element reference")
            if any(slot < 1 or slot > len(chunks) for slot in binding.source_slots):
                raise ValueError("artifact reference is outside the supplied context")
            refs = []
            for slot in dict.fromkeys(binding.source_slots):
                c = chunks[slot - 1]
                refs.append(ArtifactReference(
                    chunk_id=c.chunk_id, source_id=c.source_id, source_name=c.source_name,
                    locator_label=c.locator_label, text_snapshot=c.text,
                ))
            evidence[binding.element_id] = refs
        if set(evidence) != elements:
            raise ValueError("every content element requires supplied-source references")
    elif candidate.bindings:
        raise ValueError("authoring cannot invent corpus references")
    # Never accept model-written source metadata or trust/provenance fields.
    return spec.model_copy(update={"evidence": evidence, "provenance": {
        "generation_mode": mode, "authority": "generated_draft",
    }})
