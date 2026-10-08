"""Retrieval config guards: new keys are required, unknown keys and bad ranges fail."""

from pathlib import Path

import pytest

from app.retrieval import config as config_mod
from app.retrieval.config import ConfigError, load_retrieval_config

_BASE = config_mod._CONFIG_PATH.read_text(encoding="utf-8")


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "retrieval.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_current_config_loads():
    config = load_retrieval_config()
    assert config.fusion.min_score > 0.0
    assert config.fusion.dedupe is True
    assert 0.0 < config.seams.embed.min_similarity < 1.0


def test_unknown_embed_key_is_an_error(tmp_path):
    text = _BASE.replace("only_quota = 4", "only_quota = 4\nbogus = 1")
    with pytest.raises(ConfigError, match="unknown keys"):
        load_retrieval_config(_write(tmp_path, text))


def test_missing_fusion_key_is_an_error(tmp_path):
    text = _BASE.replace("min_score = 0.15", "")
    with pytest.raises(ConfigError, match="min_score"):
        load_retrieval_config(_write(tmp_path, text))


def test_min_similarity_out_of_range_rejected(tmp_path):
    text = _BASE.replace("min_similarity = 0.25", "min_similarity = 1.5")
    with pytest.raises(ConfigError, match="min_similarity"):
        load_retrieval_config(_write(tmp_path, text))


def test_min_score_out_of_range_rejected(tmp_path):
    text = _BASE.replace("min_score = 0.15", "min_score = 2.0")
    with pytest.raises(ConfigError, match="min_score"):
        load_retrieval_config(_write(tmp_path, text))
