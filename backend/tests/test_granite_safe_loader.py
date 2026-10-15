import json
from pathlib import Path

import pytest

from app.embeddings import granite


def _snapshot(tmp_path: Path) -> Path:
    (tmp_path / "1_Pooling").mkdir(parents=True)
    (tmp_path / "modules.json").write_text(json.dumps([
        {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
        {"idx": 1, "name": "1", "path": "1_Pooling", "type": "sentence_transformers.models.Pooling"},
        {"idx": 2, "name": "2", "path": "2_Normalize", "type": "sentence_transformers.models.Normalize"},
    ]))
    (tmp_path / "sentence_bert_config.json").write_text(json.dumps({"max_seq_length": 512, "do_lower_case": False}))
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "roberta", "architectures": ["RobertaModel"]}))
    (tmp_path / "model.safetensors").write_bytes(b"")
    (tmp_path / "1_Pooling" / "config.json").write_text(json.dumps({
        "word_embedding_dimension": 768, "pooling_mode_cls_token": True,
        "pooling_mode_mean_tokens": False, "pooling_mode_max_tokens": False,
        "pooling_mode_mean_sqrt_len_tokens": False,
    }))
    return tmp_path


def test_custom_manifest_never_imports_or_constructs_model(tmp_path, monkeypatch):
    marker = tmp_path / "executed"
    snapshot = _snapshot(tmp_path / "snapshot")
    manifest = json.loads((snapshot / "modules.json").read_text())
    manifest[0]["type"] = f"attacker.module.RunMe; marker={marker!s}"
    (snapshot / "modules.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(granite, "Transformer", lambda *a, **k: pytest.fail("constructed before validation"))
    with pytest.raises(ValueError, match="unsupported embedding module"):
        granite._safe_model(snapshot, "cpu")
    assert not marker.exists()


@pytest.mark.parametrize("bad_path", ["../outside", "C:/outside", "1_Pooling/../../outside"])
def test_manifest_path_traversal_rejected_before_construction(tmp_path, monkeypatch, bad_path):
    snapshot = _snapshot(tmp_path / "snapshot")
    manifest = json.loads((snapshot / "modules.json").read_text())
    manifest[1]["path"] = bad_path
    (snapshot / "modules.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(granite, "Transformer", lambda *a, **k: pytest.fail("constructed before validation"))
    with pytest.raises(ValueError):
        granite._safe_model(snapshot, "cpu")


def test_non_safetensors_model_is_rejected(tmp_path, monkeypatch):
    snapshot = _snapshot(tmp_path / "snapshot")
    (snapshot / "model.safetensors").unlink()
    (snapshot / "pytorch_model.bin").write_bytes(b"not loaded")
    monkeypatch.setattr(granite, "Transformer", lambda *a, **k: pytest.fail("loaded legacy weights"))
    with pytest.raises(ValueError, match="safetensors"):
        granite._safe_model(snapshot, "cpu")


def _symlinked_hf_cache(root: Path) -> Path:
    """A Linux-HF-style cache: snapshot entries are links into a sibling blobs dir."""
    repo = root / "models--ibm--granite-embedding-125m-english"
    blobs = repo / "blobs"
    blobs.mkdir(parents=True)
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "1_Pooling").mkdir()
    payloads = {
        "modules.json": json.dumps([
            {"idx": 0, "name": "0", "path": "", "type": "sentence_transformers.models.Transformer"},
            {"idx": 1, "name": "1", "path": "1_Pooling",
             "type": "sentence_transformers.models.Pooling"},
            {"idx": 2, "name": "2", "path": "2_Normalize",
             "type": "sentence_transformers.models.Normalize"},
        ]),
        "sentence_bert_config.json": json.dumps({"max_seq_length": 512, "do_lower_case": False}),
        "config.json": json.dumps({"model_type": "roberta", "architectures": ["RobertaModel"]}),
        "1_Pooling/config.json": json.dumps({
            "word_embedding_dimension": 768, "pooling_mode_cls_token": True,
            "pooling_mode_mean_tokens": False, "pooling_mode_max_tokens": False,
            "pooling_mode_mean_sqrt_len_tokens": False,
        }),
    }
    for relative, text in payloads.items():
        blob = blobs / relative.replace("/", "--")
        blob.write_text(text)
        target = snapshot / relative
        try:
            target.symlink_to(blob)
        except (OSError, NotImplementedError):  # pragma: no cover - Windows without privileges
            pytest.skip("symlinks are unavailable on this platform")
    weight = blobs / "model.safetensors"
    weight.write_bytes(b"")
    (snapshot / "model.safetensors").symlink_to(weight)
    return snapshot


def test_hf_style_symlinked_cache_loads_nested_module(tmp_path, monkeypatch):
    """EMO-002: the nested 1_Pooling/config.json symlink must resolve against blobs."""
    snapshot = _symlinked_hf_cache(tmp_path / "hub")
    captured = {}
    monkeypatch.setattr(granite, "Transformer", lambda *a, **k: captured.setdefault("transformer", (a, k)))
    monkeypatch.setattr(granite, "Pooling", lambda *a, **k: captured.setdefault("pooling", (a, k)))
    monkeypatch.setattr(granite, "Normalize", lambda *a, **k: object())
    monkeypatch.setattr(granite, "SentenceTransformer",
                        lambda modules, device: captured.setdefault("modules", modules))
    assert granite._safe_model(snapshot, "cpu") is not None
    assert captured["pooling"][0] == (768,)
    assert captured["pooling"][1]["pooling_mode_cls_token"] is True
