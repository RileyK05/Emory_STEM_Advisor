"""Enforcement: the prerequisite graph never touches a model."""

from pathlib import Path

_BANNED = ("app.llm", "get_provider", "transformers", "sentence_transformers")
_ROOT = Path(__file__).resolve().parents[1] / "app"


def _files():
    files = list((_ROOT / "atlas").glob("**/*.py"))
    files.append(_ROOT / "retrieval" / "seams" / "prereq.py")
    return files


def test_prereq_code_has_no_model_or_provider():
    for path in _files():
        source = path.read_text(encoding="utf-8")
        for banned in _BANNED:
            assert banned not in source, f"{banned!r} found in {path.name}"
