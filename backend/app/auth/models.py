from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated identity; workspace is assigned by trusted policy."""

    workspace_key: str
    issuer: str | None
    subject: str
    display_name: str | None = None
    email: str | None = None
    mode: str = "oidc"
