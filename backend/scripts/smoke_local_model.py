"""Smoke test: load the local model and generate one completion.

Downloads ~4 GB of weights on first run (cached afterwards). On CPU a 2B
model generates a few tokens per second; this script takes minutes, not
seconds. It is a script, not a unit test, for exactly that reason.

Usage:  python -m scripts.smoke_local_model   (from the backend/ directory)
"""

from app.config import load_settings
from app.llm import get_provider


def main() -> None:
    settings = load_settings()
    provider = get_provider(settings)
    answer = provider.chat(
        [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "In one sentence, what is a vector space?"},
        ]
    )
    print(f"model: {settings.hf_model_id}")
    print(f"answer: {answer}")
    if not answer:
        raise SystemExit("empty generation")


if __name__ == "__main__":
    main()
