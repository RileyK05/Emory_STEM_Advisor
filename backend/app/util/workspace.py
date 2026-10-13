"""Shared workspace-key rule: a non-empty string of at most 200 characters."""

from __future__ import annotations

MAX_WORKSPACE_KEY_LENGTH = 200


def is_valid_workspace_key(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= MAX_WORKSPACE_KEY_LENGTH
