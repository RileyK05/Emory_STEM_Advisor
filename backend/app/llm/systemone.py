"""A decision model behind Ollama's `/systemone` endpoint (for example `clef`).

The wire shape was checked against `clef` on 2026-11-17: `questions` is an object
keyed by question name (a list gets HTTP 400), and `answers` comes back keyed the
same way. See docs/plans/question-router.md, 1a.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.llm.base import (
    DecisionAnswer,
    DecisionQuestion,
    InvalidRequestError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    TransientProviderError,
    Usage,
)

PROVIDER = "systemone"


class SystemOneDecisionProvider:
    def __init__(self, client: httpx.Client, model_id: str, *, base_url: str,
                 api_key: str | None, timeout_seconds: float):
        self.client = client
        self.model_id = model_id
        self.url = base_url.rstrip("/") + "/systemone"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout_seconds

    def decide(self, state: dict[str, str],
               questions: Sequence[DecisionQuestion]) -> tuple[dict[str, DecisionAnswer], Usage]:
        payload = {"model": self.model_id, "state": state, "questions": {
            q.name: {"type": q.type, "instructions": q.instructions,
                     **({"criteria": q.criteria} if q.criteria else {})} for q in questions}}
        try:
            response = self.client.post(self.url, json=payload, headers=self.headers,
                                        timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("decision model timed out", provider=PROVIDER) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError("decision model unreachable", provider=PROVIDER) from exc
        if response.status_code == 429:
            raise RateLimitError("decision model is rate-limited", provider=PROVIDER)
        if response.status_code >= 500:
            raise TransientProviderError(f"decision model HTTP {response.status_code}",
                                         provider=PROVIDER)
        if response.status_code != 200:
            raise InvalidRequestError(f"decision model HTTP {response.status_code}", provider=PROVIDER)
        try:
            body = response.json()
            raw = body["answers"]
            answers = {}
            for q in questions:
                item = raw[q.name]
                if q.type == "noul":
                    answers[q.name] = DecisionAnswer(q.name, probability=float(item["noul"]))
                else:
                    probabilities = {str(k): float(v) for k, v in item["probabilities"].items()}
                    answers[q.name] = DecisionAnswer(q.name, choice=item.get("choice"),
                                                     probabilities=probabilities)
            used = body.get("usage") or {}
            usage = Usage(prompt_tokens=int(used["input_tokens"]),
                          completion_tokens=int(used.get("output_tokens") or 0), source="provider") \
                if "input_tokens" in used else Usage()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ProviderError("decision model returned an unreadable answer",
                                provider=PROVIDER) from exc
        return answers, usage
