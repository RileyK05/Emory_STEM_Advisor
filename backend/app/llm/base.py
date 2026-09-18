"""Provider interface. Every LLM call in the system goes through this."""

from typing import Protocol, TypeAlias

Message: TypeAlias = dict[str, str]  # {"role": ..., "content": ...}


class Provider(Protocol):
    def chat(
        self,
        messages: list[Message],
        *,
        max_new_tokens: int | None = None,
    ) -> str:
        """Run one chat completion and return the generated text."""
        ...
