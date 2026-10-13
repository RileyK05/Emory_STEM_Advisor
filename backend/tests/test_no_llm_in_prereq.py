"""Enforcement: the prerequisite graph never touches a model."""

import ast
from pathlib import Path

import pytest

_BANNED_MODULES = ("app.llm", "app.embeddings.granite", "app.embeddings.factory",
                   "transformers", "sentence_transformers",
                   "torch", "openai", "anthropic", "google.genai")
_BANNED_NAMES = {"get_provider", "get_embedder"}
_ROOT = Path(__file__).resolve().parents[1] / "app"


def _files():
    files = list((_ROOT / "atlas").glob("**/*.py"))
    files.extend((_ROOT / "advising").glob("**/*.py"))
    files.append(_ROOT / "retrieval" / "seams" / "prereq.py")
    return files


def _offences(path):
    """Imports of model code and uses of provider factories (comments and strings don't count)."""
    found = []  # the Protocol modules (app.embeddings.base) are not models; concrete ones are
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
            found += [a.name for a in node.names if a.name in _BANNED_NAMES]
        found += [m for m in modules
                  if any(m == b or m.startswith(b + ".") for b in _BANNED_MODULES)]
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            found.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr in _BANNED_NAMES:
            found.append(node.attr)
    return found


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(_ROOT)))
def test_prereq_code_has_no_model_or_provider(path):
    assert _offences(path) == []


def test_the_guard_actually_detects_a_model_import(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from app.llm import get_provider\nimport openai\n", encoding="utf-8")
    assert set(_offences(bad)) == {"app.llm", "openai", "get_provider"}
