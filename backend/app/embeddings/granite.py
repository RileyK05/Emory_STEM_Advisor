"""Granite embedding provider with a closed, data-only model assembly path."""

import json
import threading
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
from huggingface_hub import snapshot_download
from sentence_transformers import SentenceTransformer
from sentence_transformers.models import Normalize, Pooling, Transformer

from app.config import Settings

_models: dict[tuple[str, str, int], tuple[SentenceTransformer, int]] = {}
_load_lock = threading.Lock()
_MAX_JSON_BYTES = 256 * 1024
_ALLOWED_FILES = [
    "modules.json", "sentence_bert_config.json", "config.json", "**/config.json",
    "*.safetensors", "**/*.safetensors", "model.safetensors.index.json",
    "**/model.safetensors.index.json", "tokenizer.json", "**/tokenizer.json",
    "tokenizer_config.json", "**/tokenizer_config.json", "special_tokens_map.json",
    "**/special_tokens_map.json", "vocab.json", "**/vocab.json", "merges.txt",
    "**/merges.txt", "added_tokens.json", "**/added_tokens.json",
]

def _read_json(path: Path, limit: int = _MAX_JSON_BYTES) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("embedding model metadata is missing, unsafe, or too large")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate key in embedding model metadata")
            out[key] = value
        return out
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=unique)

def _inside(root: Path, relative: str, *, allow_root: bool = False) -> Path:
    if not isinstance(relative, str) or (not relative and not allow_root):
        raise ValueError("unsafe embedding module path")
    lexical = root / relative if relative else root
    base = root.resolve(strict=True)
    try:
        lexical.relative_to(root)
    except ValueError as exc:
        raise ValueError("embedding module path escapes model snapshot") from exc
    candidate = lexical.resolve(strict=True)
    # Hugging Face's Linux cache stores snapshot files as links into this repo's blobs dir.
    blobs = (root.parent.parent / "blobs").resolve()
    if candidate != base and base not in candidate.parents and blobs not in candidate.parents:
        raise ValueError("embedding module path escapes model snapshot")
    return candidate

def _safe_model(snapshot: Path, device: str) -> SentenceTransformer:
    snapshot = snapshot.resolve(strict=True)
    modules_path = _inside(snapshot, "modules.json")
    modules = _read_json(modules_path, 8192)
    if not isinstance(modules, list) or len(modules) != 3:
        raise ValueError("unsupported embedding module manifest")
    expected = [
        ("0", "", "sentence_transformers.models.Transformer"),
        ("1", "1_Pooling", "sentence_transformers.models.Pooling"),
        ("2", "2_Normalize", "sentence_transformers.models.Normalize"),
    ]
    checked = []
    for index, (item, spec) in enumerate(zip(modules, expected, strict=True)):
        if not isinstance(item, dict) or set(item) != {"idx", "name", "path", "type"}:
            raise ValueError("unsupported embedding module manifest")
        name, module_path, module_type = spec
        if (item["idx"], item["name"], item["path"], item["type"]) != (index, name, module_path, module_type):
            raise ValueError("unsupported embedding module type or path")
        if index < 2:
            checked.append(_inside(snapshot, module_path, allow_root=(index == 0)))
        else:
            pure = PurePosixPath(module_path)
            if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
                raise ValueError("unsafe embedding module path")
            checked.append(snapshot / module_path)
    sentence_cfg = _read_json(_inside(snapshot, "sentence_bert_config.json"), 8192)
    max_length = sentence_cfg.get("max_seq_length")
    lower = sentence_cfg.get("do_lower_case")
    if type(max_length) is not int or not 1 <= max_length <= 4096 or type(lower) is not bool:
        raise ValueError("invalid embedding tokenizer configuration")
    model_cfg = _read_json(_inside(snapshot, "config.json"))
    if not isinstance(model_cfg, dict) or "auto_map" in model_cfg:
        raise ValueError("custom model code is not supported")
    weight_files = list(snapshot.rglob("*.safetensors"))
    if not weight_files:
        raise ValueError("embedding snapshot must provide safetensors weights")
    for weight_file in weight_files:
        _inside(snapshot, weight_file.relative_to(snapshot).as_posix())
    for index_file in snapshot.rglob("*.safetensors.index.json"):
        index_path = _inside(snapshot, index_file.relative_to(snapshot).as_posix())
        weight_index = _read_json(index_path, 1024 * 1024)
        weight_map = weight_index.get("weight_map") if isinstance(weight_index, dict) else None
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError("invalid safetensors weight index")
        for filename in set(weight_map.values()):
            if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".safetensors"):
                raise ValueError("unsafe safetensors shard path")
            _inside(snapshot, filename)
    pooling_cfg = _read_json(_inside(checked[1], "config.json"), 8192)
    dim = pooling_cfg.get("word_embedding_dimension")
    if type(dim) is not int or not 1 <= dim <= 16384:
        raise ValueError("invalid embedding pooling dimension")
    flags = ("pooling_mode_cls_token", "pooling_mode_mean_tokens",
             "pooling_mode_max_tokens", "pooling_mode_mean_sqrt_len_tokens",
             "pooling_mode_weightedmean_tokens", "pooling_mode_lasttoken")
    modes = {key: pooling_cfg.get(key, False) for key in flags}
    if any(type(value) is not bool for value in modes.values()) or not any(modes.values()):
        raise ValueError("invalid embedding pooling modes")
    # Constructor kwargs are deliberately fixed: no model-authored import paths or kwargs.
    transformer = Transformer(
        str(snapshot), max_seq_length=max_length, do_lower_case=lower,
        config_args={"trust_remote_code": False, "local_files_only": True},
        model_args={"trust_remote_code": False, "use_safetensors": True, "local_files_only": True},
        tokenizer_args={"trust_remote_code": False, "local_files_only": True},
    )
    pooling = Pooling(dim, **modes)
    normalize = Normalize()
    return SentenceTransformer(modules=[transformer, pooling, normalize], device=device)

def _load(settings: Settings) -> SentenceTransformer:
    key = (settings.embedding_model_id, settings.embedding_device,
           settings.embedding_max_tokens)
    cached = _models.get(key)
    if cached is not None:
        return cached[0]
    with _load_lock:
        cached = _models.get(key)
        if cached is None:
            snapshot = snapshot_download(
                repo_id=settings.embedding_model_id, allow_patterns=_ALLOWED_FILES,
            )
            model = _safe_model(Path(snapshot), settings.embedding_device)
            native_ceiling = int(model.max_seq_length)
            if native_ceiling < 1:
                raise ValueError("embedding model has an invalid native token ceiling")
            ceiling = min(settings.embedding_max_tokens, native_ceiling)
            model.max_seq_length = ceiling
            _models[key] = (model, ceiling)
            cached = _models[key]
    return cached[0]

def _token_count(model: SentenceTransformer, text: str) -> int:
    return len(model.tokenizer.encode(text, add_special_tokens=True, truncation=False))

def _validate_length(settings: Settings, model: SentenceTransformer, text: str) -> None:
    count = _token_count(model, text)
    key = (settings.embedding_model_id, settings.embedding_device,
           settings.embedding_max_tokens)
    cached = _models.get(key)
    ceiling = cached[1] if cached else min(settings.embedding_max_tokens,
                                           int(model.max_seq_length))
    if count > ceiling:
        raise ValueError(
            f"embedding input has {count} tokens; configured model ceiling is {ceiling}"
        )

class GraniteEmbedder:
    def __init__(self, settings: Settings):
        self._settings = settings

    def count_tokens(self, text: str) -> int:
        return _token_count(_load(self._settings), text)

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        model = _load(self._settings)
        for text in texts:
            _validate_length(self._settings, model, text)
        return model.encode(texts, batch_size=32, normalize_embeddings=True,
                            convert_to_numpy=True).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        model = _load(self._settings)
        _validate_length(self._settings, model, text)
        return model.encode(text, normalize_embeddings=True,
                            convert_to_numpy=True).astype(np.float32)
