"""Typed errors for artifact validation, conversion, and fidelity refusal."""


class ArtifactError(ValueError):
    def __init__(self, code: str, message: str, *, details: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or []


class UnsupportedFeatureError(ArtifactError):
    def __init__(self, features: list[str]):
        self.features = features
        super().__init__(
            "unsupported_feature",
            "This file contains features the editor cannot preserve safely.",
            details=features,
        )


class ArtifactLimitError(ArtifactError):
    def __init__(self, message: str):
        super().__init__("artifact_limit", message)
