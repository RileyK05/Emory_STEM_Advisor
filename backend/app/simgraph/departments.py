"""Fixed department membership. Never infer a boundary from vectors or an LLM.

The imported catalog currently retains subject codes, not department IDs. An
unmapped subject is its own fixed unit; explicit rules may join subjects under
a department once that membership is verified. These are not cluster nudges.
"""

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEPARTMENTS_PATH = Path(__file__).resolve().parents[2] / "configs" / "departments.toml"


@dataclass(frozen=True)
class Department:
    key: str
    label: str
    subjects: tuple[str, ...]


@dataclass(frozen=True)
class Departments:
    rules: tuple[Department, ...] = ()

    def for_subject(self, subject: str) -> Department:
        for rule in self.rules:
            if subject in rule.subjects:
                return rule
        if any(rule.key == subject for rule in self.rules):
            raise ValueError(f"subject {subject!r} collides with a configured department key")
        return Department(subject, subject, (subject,))

    @property
    def digest(self) -> str:
        value = [(d.key, d.label, sorted(d.subjects)) for d in sorted(
            self.rules, key=lambda d: d.key)]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def parse_departments(raw: dict) -> Departments:
    if set(raw) != {"version", "departments"} or raw["version"] != 1:
        raise ValueError("department config requires version = 1 and a departments table")
    rules, seen = [], set()
    table = raw["departments"]
    if not isinstance(table, dict):
        raise TypeError("departments must be a table")
    for key, entry in sorted(table.items()):
        if not key.strip() or not isinstance(entry, dict) or set(entry) != {"label", "subjects"}:
            raise ValueError(f"department {key!r} requires label and subjects")
        label, subjects = entry["label"], entry["subjects"]
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"department {key!r} requires a nonempty label")
        if (not isinstance(subjects, list) or not subjects
                or any(not isinstance(s, str) or not s.strip() or s != s.strip().upper()
                       for s in subjects)):
            raise ValueError(f"department {key!r} requires uppercase subject codes")
        if len(set(subjects)) != len(subjects) or seen.intersection(subjects):
            raise ValueError("each subject must belong to exactly one configured department")
        seen.update(subjects)
        rules.append(Department(key, label.strip(), tuple(subjects)))
    # A rule's key cannot also silently name a different fallback unit.
    for rule in rules:
        if rule.key in seen and rule.key not in rule.subjects:
            raise ValueError(f"department key {rule.key!r} belongs to another department")
    return Departments(tuple(rules))


def load_departments(path: Path | None = None) -> Departments:
    with (path or DEPARTMENTS_PATH).open("rb") as handle:
        return parse_departments(tomllib.load(handle))
