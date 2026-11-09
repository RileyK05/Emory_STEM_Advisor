"""Local provider cache and exact token-ceiling tests; no weights are loaded."""
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.llm.base import GenerationRequest, InvalidRequestError, Usage


def test_hf_rejects_a_different_requested_model_before_loading_weights():
    from app.llm.base import GenerationRequest, InvalidRequestError
    from app.llm.hf_local import HFLocalProvider

    provider = HFLocalProvider(SimpleNamespace(hf_model_id="configured-local"))
    with pytest.raises(InvalidRequestError, match="request model differs"):
        provider.generate(GenerationRequest(
            ({"role": "user", "content": "test"},), model="unloaded-local"))


def test_hf_timeout_preserves_measured_usage_without_decoding_partial_output(monkeypatch):
    import app.llm.hf_local as hf
    from app.llm.base import GenerationRequest, ProviderTimeoutError

    class Batch(dict):
        def to(self, device):
            return self

    class Tokenizer:
        def apply_chat_template(self, *_args, **_kwargs):
            return "prompt"

        def __call__(self, *_args, **_kwargs):
            return Batch(input_ids=hf.torch.tensor([[1, 2, 3]]))

        def decode(self, *_args, **_kwargs):
            raise AssertionError("timed-out candidate must not be decoded for publication")

    class Model:
        device = "cpu"

        def generate(self, **kwargs):
            kwargs["stopping_criteria"][0].expired = True
            return hf.torch.tensor([[1, 2, 3, 4, 5]])

    monkeypatch.setattr(hf, "_load", lambda _settings: (Tokenizer(), Model()))
    provider = hf.HFLocalProvider(SimpleNamespace(
        hf_model_id="fake-local", hf_max_context=4096, hf_max_new_tokens=256,
    ))
    with pytest.raises(ProviderTimeoutError) as caught:
        provider.generate(GenerationRequest(({"role": "user", "content": "test"},)))
    assert caught.value.usage.source == "provider"
    assert caught.value.usage.prompt_tokens == 3
    assert caught.value.usage.completion_tokens == 2


def _provider_fixture(monkeypatch, model, *, model_id="fake-local"):
    import app.llm.hf_local as hf

    class Batch(dict):
        def to(self, device):
            return self

    class Tokenizer:
        def apply_chat_template(self, *_args, **_kwargs):
            return "prompt"

        def __call__(self, *_args, **_kwargs):
            return Batch(input_ids=hf.torch.tensor([[1, 2, 3]]))

        def decode(self, token_ids, **_kwargs):
            skip_special_tokens = _kwargs.get("skip_special_tokens", False)
            return "".join({
                4: "hello ", 5: "world",
                6: "" if skip_special_tokens else "<|im_end|>",
            }.get(token, "") for token in token_ids)

        def get_vocab(self):
            return {"<|stop|>": 6}

        def convert_ids_to_tokens(self, token_id):
            return {6: "<|stop|>"}.get(token_id, "<unk>")

        unk_token_id = 0

    tokenizer = Tokenizer()
    monkeypatch.setattr(hf, "_load", lambda _settings: (tokenizer, model))
    settings = SimpleNamespace(
        hf_model_id=model_id, hf_max_context=4096, hf_max_new_tokens=256,
    )
    return hf, hf.HFLocalProvider(settings, timeout_seconds=2.0)


def test_hf_resolves_configured_exact_stop_and_unions_native_eos(monkeypatch):
    import app.llm.hf_local as hf

    tokenizer = SimpleNamespace(
        unk_token_id=0, get_vocab=lambda: {"<|stop|>": 6},
        convert_ids_to_tokens=lambda token_id: {6: "<|stop|>"}.get(token_id, "<unk>"),
    )
    model = SimpleNamespace(generation_config=SimpleNamespace(eos_token_id=2))
    settings = SimpleNamespace(hf_stop_token_strings=("<|stop|>",))
    assert hf._resolve_stop_token_ids(tokenizer, model, settings) == (2, 6)


def test_hf_configured_stop_is_passed_to_generate(monkeypatch):
    import app.llm.hf_local as hf
    from app.llm.base import GenerationRequest

    class Model:
        device = "cpu"
        generation_config = SimpleNamespace(eos_token_id=2)

        def __init__(self):
            self.eos_values = []

        def generate(self, **kwargs):
            self.eos_values.append(kwargs["eos_token_id"])
            return hf.torch.cat([kwargs["input_ids"], hf.torch.tensor([[6]])], dim=1)

    model = Model()
    _hf, provider = _provider_fixture(monkeypatch, model)
    provider._settings.hf_stop_token_strings = ("<|stop|>",)
    result = provider.generate(GenerationRequest(
        ({"role": "user", "content": "test"},), max_output_tokens=1,
    ))
    assert result.finish_reason == "stop"
    assert result.usage.completion_tokens == 1
    assert result.text == ""
    assert model.eos_values == [[2, 6]]


def test_hf_rejects_unavailable_configured_stop_before_generate(monkeypatch):
    import app.llm.hf_local as hf
    from app.llm.base import GenerationRequest, ProviderUnavailableError

    class Tokenizer:
        def get_vocab(self):
            return {}

    class Model:
        device = "cpu"
        generation_config = SimpleNamespace(eos_token_id=2)

        def generate(self, **_kwargs):
            raise AssertionError("unavailable stop token must fail before generation")

    monkeypatch.setattr(hf, "_load", lambda _settings: (Tokenizer(), Model()))
    settings = SimpleNamespace(
        hf_model_id="fake-local", hf_max_context=4096, hf_max_new_tokens=256,
        hf_stop_token_strings=("<|missing|>",),
    )
    provider = hf.HFLocalProvider(settings)
    with pytest.raises(ProviderUnavailableError, match="configured stop token"):
        provider.generate(GenerationRequest(({"role": "user", "content": "test"},)))


def test_hf_return_after_deadline_with_measured_output_is_withheld(monkeypatch):
    import app.llm.hf_local as hf
    from app.llm.base import GenerationRequest, ProviderTimeoutError

    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(hf, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class Model:
        device = "cpu"

        def generate(self, **kwargs):
            ids = kwargs["input_ids"]
            generated = hf.torch.cat([ids, hf.torch.tensor([[4]])], dim=1)
            # Cross the deadline without depending on host scheduling or calling
            # stopping criteria. Stay within the consumer's completion grace.
            clock.now = 0.02
            return generated

    _hf, provider = _provider_fixture(
        monkeypatch, Model(), model_id=f"fake-return-timeout-{time.monotonic_ns()}",
    )
    request = GenerationRequest(
        ({"role": "user", "content": "test"},), timeout_seconds=0.01,
    )
    with pytest.raises(ProviderTimeoutError) as caught:
        provider.generate(request)
    assert caught.value.usage.source == "provider"
    assert caught.value.usage.prompt_tokens == 3
    assert caught.value.usage.completion_tokens == 1


def test_granite_rejects_tokenizer_overflow_before_encode(monkeypatch):
    from app.embeddings import granite

    granite._models.clear()
    instances = []

    class Tokenizer:
        def encode(self, text, *, add_special_tokens, truncation):
            assert add_special_tokens is True and truncation is False
            return [0] * (len(text.split()) + 1)

    class Model:
        def __init__(self, model_id, device):
            self.model_id = model_id
            self.device = device
            self.max_seq_length = 3  # native ceiling, smaller than configured below
            self.tokenizer = Tokenizer()
            self.encoded = []

        def encode(self, texts, **_kwargs):
            self.encoded.append(texts)
            count = len(texts) if isinstance(texts, list) else 1
            return np.ones((count, 2), dtype=np.float32)

    def create(model_id, device):
        model = Model(model_id, device)
        instances.append(model)
        return model

    monkeypatch.setattr(granite, "snapshot_download", lambda repo_id, **_kw: repo_id)
    monkeypatch.setattr(granite, "_safe_model", lambda snapshot, device: create(snapshot, device))
    settings = SimpleNamespace(embedding_model_id="granite-a", embedding_device="cpu",
                               embedding_max_tokens=10)
    embedder = granite.GraniteEmbedder(settings)
    assert embedder.count_tokens("one two") == 3
    embedder.embed_query("one two")
    assert embedder.embed_query("one two").shape == (1, 2)
    assert instances[0].encoded == ["one two"]
    with pytest.raises(ValueError, match="4 tokens.*ceiling is 3"):
        embedder.embed_query("one two three")
    with pytest.raises(ValueError, match="ceiling is 3"):
        embedder.embed_documents(["one", "one two three"])
    assert len(instances) == 1
    assert instances[0].encoded == ["one two"]


def test_granite_cache_is_keyed_by_model_device_and_configured_ceiling(monkeypatch):
    from app.embeddings import granite

    granite._models.clear()
    loaded = []

    class Model:
        max_seq_length = 32
        tokenizer = SimpleNamespace(encode=lambda *_args, **_kwargs: [1])

    monkeypatch.setattr(granite, "snapshot_download", lambda repo_id, **_kw: repo_id)
    monkeypatch.setattr(
        granite, "_safe_model",
        lambda snapshot, device: loaded.append((snapshot.name, device)) or Model(),
    )
    first = SimpleNamespace(embedding_model_id="model-a", embedding_device="cpu",
                            embedding_max_tokens=8)
    other_model = SimpleNamespace(embedding_model_id="model-b", embedding_device="cpu",
                                  embedding_max_tokens=8)
    other_device = SimpleNamespace(embedding_model_id="model-a", embedding_device="cuda:0",
                                   embedding_max_tokens=8)
    other_limit = SimpleNamespace(embedding_model_id="model-a", embedding_device="cpu",
                                  embedding_max_tokens=12)
    assert granite._load(first) is granite._load(first)
    granite._load(other_model)
    granite._load(other_device)
    granite._load(other_limit)
    assert loaded == [("model-a", "cpu"), ("model-b", "cpu"),
                      ("model-a", "cuda:0"), ("model-a", "cpu")]


def test_hf_cache_is_keyed_by_model_id_and_actual_device(monkeypatch):
    import app.llm.hf_local as hf

    hf._models.clear()
    tokenizer_loads = []
    model_loads = []

    class Model:
        def eval(self):
            self.evaluated = True

    monkeypatch.setattr(hf.AutoTokenizer, "from_pretrained",
                        lambda model_id, **_kw: tokenizer_loads.append(model_id) or object())
    monkeypatch.setattr(hf.AutoModelForCausalLM, "from_pretrained",
                        lambda model_id, **kw: model_loads.append((model_id, kw["device_map"])) or Model())
    monkeypatch.setattr(hf.torch.cuda, "is_available", lambda: False)
    a = SimpleNamespace(hf_model_id="local-a")
    b = SimpleNamespace(hf_model_id="local-b")
    a_cpu = hf._load(a)
    assert hf._load(a) is a_cpu
    hf._load(b)
    monkeypatch.setattr(hf.torch.cuda, "is_available", lambda: True)
    hf._load(a)
    assert tokenizer_loads == ["local-a", "local-b", "local-a"]
    assert model_loads == [("local-a", "cpu"), ("local-b", "cpu"), ("local-a", "cuda")]


class _Tensor:
    def __init__(self, size):
        self.shape = (1, size)
        self.size = size

    def __getitem__(self, index):
        if index == 0:
            return self
        if isinstance(index, slice):
            return _Tensor(max(0, self.size - (index.start or 0)))
        raise IndexError(index)


class _Batch(dict):
    def to(self, _device):
        return self


class _Sequence:
    def __init__(self, size):
        self.size = size
        self.shape = (size,)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return _Sequence(max(0, self.size - (index.start or 0)))
        if isinstance(index, int) and -self.size <= index < self.size:
            return 9
        raise IndexError(index)


class _Output:
    def __init__(self, size):
        self.sequence = _Sequence(size)

    def __getitem__(self, index):
        if index == 0:
            return self.sequence
        raise IndexError(index)


def test_hf_local_reports_measured_usage_and_length_stop(monkeypatch):
    import app.llm.hf_local as local

    class Tokenizer:
        def apply_chat_template(self, *_args, **_kwargs):
            return "prompt"

        def __call__(self, *_args, **kwargs):
            assert kwargs["truncation"] is False
            return _Batch(input_ids=_Tensor(4))

        def decode(self, *_args, **_kwargs):
            return "done"

    class Model:
        device = "cpu"

        def generate(self, input_ids, max_new_tokens, **_kwargs):
            return _Output(input_ids.size + max_new_tokens)

    monkeypatch.setattr(local, "_load", lambda _settings: (Tokenizer(), Model()))
    settings = SimpleNamespace(hf_model_id="local", hf_max_context=8, hf_max_new_tokens=6)
    result = local.HFLocalProvider(settings).generate(GenerationRequest(
        [{"role": "user", "content": "question"}], max_output_tokens=3))
    assert result.usage == Usage(4, 3, 7, "provider")
    assert result.finish_reason == "length"
    assert result.text == "done"


def test_hf_local_rejects_context_overflow_instead_of_truncating(monkeypatch):
    import app.llm.hf_local as local

    class Tokenizer:
        def apply_chat_template(self, *_args, **_kwargs):
            return "long"

        def __call__(self, *_args, **kwargs):
            assert kwargs["truncation"] is False
            return _Batch(input_ids=_Tensor(8))

    monkeypatch.setattr(local, "_load", lambda _settings: (Tokenizer(), SimpleNamespace(device="cpu")))
    settings = SimpleNamespace(hf_model_id="local", hf_max_context=8, hf_max_new_tokens=4)
    with pytest.raises(InvalidRequestError, match="exceeds"):
        local.HFLocalProvider(settings).generate(GenerationRequest(
            [{"role": "user", "content": "question"}]))
