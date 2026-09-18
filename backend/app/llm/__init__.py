"""Provider seam: one interface for every model call in the system.

Task + messages in, text out. Which model answers is a config decision; no
model-specific code lives outside this package.
"""

from app.llm.base import Message, Provider
from app.llm.factory import get_provider

__all__ = ["Message", "Provider", "get_provider"]
