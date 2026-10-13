"""Backend response schemas must carry the same fields as the frontend contract types.

`frontend/src/api/types.ts` is the contract the HttpClient relies on. For every
Pydantic model in `app.api.schemas` that has a same-named TypeScript interface,
the field names (and which of them are optional) must agree, so a rename on one
side fails here instead of surfacing as an `undefined` in the browser.
"""

import re
from pathlib import Path

import pytest
from pydantic import BaseModel, create_model

from app.api import schemas

TYPES_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "types.ts"
_INTERFACE = re.compile(r"export interface (\w+)(?:\s+extends([^{]+))?\s*\{", re.MULTILINE)
_OMIT = re.compile(r"Omit<\s*(\w+)\s*,\s*([^>]+)>")
_PICK = re.compile(r"Pick<\s*(\w+)\s*,\s*([^>]+)>")
_FIELD = re.compile(r"^\s*(\w+)(\?)?\s*:", re.MULTILINE)


def _interfaces() -> dict[str, dict[str, bool]]:
    """Interface name -> {field name: optional}, for top-level fields only."""
    source = TYPES_TS.read_text(encoding="utf-8")
    found: dict[str, dict[str, bool]] = {}
    for match in _INTERFACE.finditer(source):
        depth, index = 1, match.end()
        while depth and index < len(source):
            depth += {"{": 1, "}": -1}.get(source[index], 0)
            index += 1
        body = source[match.end():index - 1]
        fields, nesting, line_start, depth_at_line_start = {}, 0, 0, 0
        for position, char in enumerate(body + "\n"):
            if char in "{[(":
                nesting += 1
            elif char in "}])":
                nesting -= 1
            elif char == "\n":
                # A top-level field may open a nested type on its own line, so the
                # depth that counts is the one where the line started.
                if depth_at_line_start == 0:
                    field = _FIELD.match(body[line_start:position])
                    if field:
                        fields[field.group(1)] = field.group(2) == "?"
                line_start = position + 1
                depth_at_line_start = nesting
        found[match.group(1)] = fields
        extends = match.group(2) or ""
        for parent in re.findall(r"\w+", _OMIT.sub("", _PICK.sub("", extends))):
            for inherited_name, optional in found.get(parent, {}).items():
                fields.setdefault(inherited_name, optional)
        for parent, names in _OMIT.findall(extends):
            omitted = set(re.findall(r"'(\w+)'", names))
            inherited = {k: v for k, v in found.get(parent, {}).items() if k not in omitted}
            fields.update({k: v for k, v in inherited.items() if k not in fields})
        for parent, names in _PICK.findall(extends):
            for picked in re.findall(r"'(\w+)'", names):
                fields[picked] = found.get(parent, {}).get(picked, True)
    return found


def _models() -> dict[str, type[BaseModel]]:
    return {name: value for name, value in vars(schemas).items()
            if isinstance(value, type) and issubclass(value, BaseModel)
            and value.__module__ == schemas.__name__}


def test_shared_models_exist_on_both_sides():
    shared = set(_interfaces()) & set(_models())
    # A parser regression would make the drift check below vacuous.
    assert {"SeamStatus", "SourceDoc", "CollectionDoc", "Contribution"} <= shared


def _contract_problems(interfaces, models):
    problems = []
    for name in sorted(set(interfaces) & set(models)):
        backend = set(models[name].model_fields)
        frontend = set(interfaces[name])
        if backend != frontend:
            problems.append(f"{name}: backend-only {sorted(backend - frontend)}, "
                            f"frontend-only {sorted(frontend - backend)}")
        for field in sorted(backend & frontend):
            optional = not models[name].model_fields[field].is_required()
            if optional != interfaces[name][field]:
                problems.append(f"{name}.{field}: backend optional={optional}, "
                                f"frontend optional={interfaces[name][field]}")
    return problems


def test_backend_schemas_match_frontend_contract_fields():
    problems = _contract_problems(_interfaces(), _models())
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(("default", "frontend_optional", "drift"), [
    (..., False, False), (..., True, True), (None, False, True), (None, True, False),
])
def test_contract_guard_distinguishes_omission_from_nullable_values(default, frontend_optional, drift):
    model = create_model("Example", value=(str | None, default))
    problems = _contract_problems({"Example": {"value": frontend_optional}}, {"Example": model})
    assert bool(problems) is drift
