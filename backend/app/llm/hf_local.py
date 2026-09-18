"""Local Hugging Face provider for cheap experimentation.

Loads a small model (default: MiniCPM-2B-128k, Apache-2.0) via transformers
and answers through the Provider interface. Weights download from Hugging
Face on first use and are cached in the standard HF cache, so there is no
per-machine install step beyond pip.

Generation is greedy (do_sample=False) by default: experiment runs against
the eval set should be reproducible, not creative.
"""

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from app.config import Settings
from app.llm.base import Message

# Lazy singletons; the model loads on first use, not at import.
_tokenizer = None
_model = None


def _load(settings: Settings):
    global _tokenizer, _model
    if _model is None:
        _tokenizer = AutoTokenizer.from_pretrained(
            settings.hf_model_id, trust_remote_code=True
        )
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        _model = AutoModelForCausalLM.from_pretrained(
            settings.hf_model_id,
            torch_dtype=dtype,
            device_map="cuda" if torch.cuda.is_available() else "cpu",
            trust_remote_code=True,  # MiniCPM ships custom modeling code
        )
        _model.eval()
    return _tokenizer, _model


class HFLocalProvider:
    def __init__(self, settings: Settings):
        self._settings = settings

    def chat(self, messages: list[Message], *, max_new_tokens: int | None = None) -> str:
        tokenizer, model = _load(self._settings)
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self._settings.hf_max_context,
        ).to(model.device)
        with torch.no_grad():
            output = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens or self._settings.hf_max_new_tokens,
                do_sample=False,
            )
        generated = output[0][inputs["input_ids"].shape[1]:]
        return tokenizer.decode(generated, skip_special_tokens=True).strip()
