"""The optional repo-root `.env` loader: fills gaps, never overrides the shell."""

import os

import pytest

from app.config import _load_dotenv


@pytest.fixture
def repo_root(monkeypatch, tmp_path):
    # _load_dotenv reads REPO_ROOT/.env; point the module at a temp root.
    import app.config as config_mod

    monkeypatch.setattr(config_mod, "_REPO_ROOT", tmp_path)
    return tmp_path


def test_env_file_fills_unset_keys(repo_root, monkeypatch):
    monkeypatch.delenv("ADVISOR_TEST_KEY", raising=False)
    (repo_root / ".env").write_text(
        "# a comment\nADVISOR_TEST_KEY=from-file\n\n", encoding="utf-8"
    )
    _load_dotenv()
    assert os.environ["ADVISOR_TEST_KEY"] == "from-file"


def test_shell_environment_wins(repo_root, monkeypatch):
    monkeypatch.setenv("ADVISOR_TEST_KEY", "from-shell")
    (repo_root / ".env").write_text("ADVISOR_TEST_KEY=from-file\n", encoding="utf-8")
    _load_dotenv()
    assert os.environ["ADVISOR_TEST_KEY"] == "from-shell"


def test_quotes_and_export_are_stripped(repo_root, monkeypatch):
    monkeypatch.delenv("ADVISOR_Q", raising=False)
    monkeypatch.delenv("ADVISOR_E", raising=False)
    (repo_root / ".env").write_text(
        'ADVISOR_Q="quoted value"\nexport ADVISOR_E=exported\n', encoding="utf-8"
    )
    _load_dotenv()
    assert os.environ["ADVISOR_Q"] == "quoted value"
    assert os.environ["ADVISOR_E"] == "exported"


def test_missing_file_is_a_noop(repo_root, monkeypatch):
    monkeypatch.delenv("ADVISOR_NOPE", raising=False)
    _load_dotenv()
    assert "ADVISOR_NOPE" not in os.environ
