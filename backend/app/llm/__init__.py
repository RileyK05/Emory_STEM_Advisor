"""Typed provider seam. Concrete providers are built only in `factory`."""

from app.llm.factory import get_provider

__all__ = ["get_provider"]
