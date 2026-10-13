"""Loopback-only, opt-in local administration for connection profiles."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg import Connection
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api import deps
from app.connections.models import ProfileCreate, ProfileUpdate, ProfileValidationError
from app.connections.repository import ConcurrentProfileUpdate, ConnectionProfiles

router = APIRouter(prefix="/api/admin/connections", tags=["local admin"])


class ProfileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    provider: str
    model_id: str = Field(min_length=1, max_length=200)
    roles: set[str]
    capabilities: set[str] = Field(default_factory=set)
    data_scopes: set[str] = Field(default_factory=set)
    base_url: str | None = None
    account_id: str | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=1_000_000)


class ProfileCreateBody(ProfileWrite):
    secret: dict[str, SecretStr] | None = None


class ProfileUpdateBody(ProfileWrite):
    expected_revision: int = Field(ge=1)


class SecretReplaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    secret: dict[str, SecretStr]


class ProfileDeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class ProfileViewDto(BaseModel):
    profile_id: UUID
    name: str
    provider: str
    model_id: str
    base_url: str | None
    account_id: str | None
    secret_configured: bool
    provider_options: dict[str, Any]
    roles: list[str]
    capabilities: list[str]
    data_scopes: list[str]
    enabled: bool
    priority: int
    revision: int
    created_at: Any
    updated_at: Any
    eval_passed: bool


class DiagnosticDto(BaseModel):
    status: str
    message: str


class DeleteDto(BaseModel):
    deleted: bool


def _admin(request: Request):
    settings = request.app.state.settings
    if not getattr(settings, "admin_api_enabled", False):
        raise HTTPException(status_code=404, detail="not found")
    if any(name in request.headers for name in (
        "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
    )):
        raise HTTPException(status_code=403, detail="forwarded requests cannot access local administration")
    host_header = request.headers.get("host", "")
    if not _loopback_authority(host_header):
        raise HTTPException(status_code=403, detail="local administration requires a loopback Host")
    origin = request.headers.get("origin")
    if origin is not None:
        parsed_origin = urlsplit(origin)
        if (parsed_origin.scheme not in {"http", "https"} or parsed_origin.path not in {"", "/"}
                or parsed_origin.query or parsed_origin.fragment
                or parsed_origin.username or parsed_origin.password
                or not _loopback_hostname(parsed_origin.hostname)
                or parsed_origin.hostname.lower().rstrip(".") != _authority_hostname(host_header)):
            raise HTTPException(status_code=403, detail="local administration requires a loopback Origin")
    host = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise HTTPException(status_code=403, detail="local administration is available only on loopback")
    repository = getattr(request.app.state, "connection_profiles", None)
    if repository is None:
        repository = ConnectionProfiles(getattr(settings, "profile_encryption_key", None))
    return repository


def _loopback_authority(authority: str) -> bool:
    try:
        parsed = urlsplit(f"//{authority}")
        return (not parsed.username and not parsed.password and parsed.path == ""
                and not parsed.query and not parsed.fragment
                and _loopback_hostname(parsed.hostname))
    except ValueError:
        return False


def _authority_hostname(authority: str) -> str:
    try:
        return (urlsplit(f"//{authority}").hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


RepoDep = Depends(_admin)
ConnDep = Depends(deps.get_conn)


def _dto(profile) -> ProfileViewDto:
    return ProfileViewDto(
        profile_id=profile.profile_id, name=profile.name, provider=profile.provider,
        model_id=profile.model_id, base_url=profile.base_url, account_id=profile.account_id,
        secret_configured=profile.secret_configured, provider_options=dict(profile.provider_options),
        roles=sorted(profile.roles), capabilities=sorted(profile.capabilities),
        data_scopes=sorted(profile.data_scopes), enabled=profile.enabled,
        priority=profile.priority, revision=profile.revision,
        created_at=profile.created_at, updated_at=profile.updated_at,
        eval_passed=profile.eval_passed,
    )


def _invalidate(request: Request, profile_id: UUID | None = None) -> None:
    runtime = getattr(request.app.state, "connection_runtime", None)
    if runtime is not None:
        runtime.invalidate(profile_id)


def _raise_profile_error(exc: Exception):
    if isinstance(exc, ConcurrentProfileUpdate):
        raise HTTPException(status_code=409, detail="connection profile changed; reload and retry") from None
    if isinstance(exc, (ValueError, KeyError)):
        detail = "invalid connection profile configuration" if isinstance(exc, ProfileValidationError) else (
            "connection profile not found" if isinstance(exc, KeyError) else "connection profile operation failed")
        raise HTTPException(status_code=422, detail=detail) from None
    raise HTTPException(status_code=400, detail="connection profile operation failed") from None


@router.get("", response_model=list[ProfileViewDto])
def list_profiles(conn: Connection = ConnDep, repository: ConnectionProfiles = RepoDep):
    return [_dto(item) for item in repository.list_views(conn)]


@router.post("", response_model=ProfileViewDto, status_code=status.HTTP_201_CREATED)
def create_profile(body: ProfileCreateBody, request: Request, conn: Connection = ConnDep,
                   repository: ConnectionProfiles = RepoDep):
    data = ProfileCreate(
        name=body.name, provider=body.provider, model_id=body.model_id,
        roles=frozenset(body.roles), capabilities=frozenset(body.capabilities),
        data_scopes=frozenset(body.data_scopes), base_url=body.base_url,
        account_id=body.account_id, provider_options=body.provider_options,
        secret={key: value.get_secret_value() for key, value in (body.secret or {}).items()},
        enabled=body.enabled, priority=body.priority)
    try:
        result = repository.create(conn, data)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - keep storage or crypto details private
        conn.rollback()
        _raise_profile_error(exc)
    _invalidate(request)
    return _dto(result)


@router.put("/{profile_id}", response_model=ProfileViewDto)
def update_profile(profile_id: UUID, body: ProfileUpdateBody, request: Request,
                   conn: Connection = ConnDep, repository: ConnectionProfiles = RepoDep):
    data = ProfileUpdate(
        expected_revision=body.expected_revision, name=body.name, provider=body.provider,
        model_id=body.model_id, roles=frozenset(body.roles),
        capabilities=frozenset(body.capabilities), data_scopes=frozenset(body.data_scopes),
        base_url=body.base_url, account_id=body.account_id,
        provider_options=body.provider_options, enabled=body.enabled, priority=body.priority)
    try:
        result = repository.update(conn, profile_id, data)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _raise_profile_error(exc)
    _invalidate(request, profile_id)
    return _dto(result)


@router.put("/{profile_id}/secret", response_model=ProfileViewDto)
def replace_secret(profile_id: UUID, body: SecretReplaceBody, request: Request,
                   conn: Connection = ConnDep, repository: ConnectionProfiles = RepoDep):
    try:
        result = repository.replace_secret(conn, profile_id,
            expected_revision=body.expected_revision,
            secret={key: value.get_secret_value() for key, value in body.secret.items()})
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _raise_profile_error(exc)
    _invalidate(request, profile_id)
    return _dto(result)


@router.delete("/{profile_id}/secret", response_model=ProfileViewDto)
def clear_secret(profile_id: UUID, body: ProfileDeleteBody, request: Request,
                 conn: Connection = ConnDep, repository: ConnectionProfiles = RepoDep):
    try:
        result = repository.clear_secret(conn, profile_id,
            expected_revision=body.expected_revision)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _raise_profile_error(exc)
    _invalidate(request, profile_id)
    return _dto(result)


@router.delete("/{profile_id}", response_model=DeleteDto)
def delete_profile(profile_id: UUID, body: ProfileDeleteBody, request: Request,
                   conn: Connection = ConnDep, repository: ConnectionProfiles = RepoDep):
    try:
        repository.delete(conn, profile_id, expected_revision=body.expected_revision)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        _raise_profile_error(exc)
    _invalidate(request, profile_id)
    return DeleteDto(deleted=True)


@router.get("/{profile_id}/diagnose", response_model=DiagnosticDto)
def diagnose_profile(profile_id: UUID, conn: Connection = ConnDep,
                     repository: ConnectionProfiles = RepoDep):
    profile = repository.get(conn, profile_id)
    conn.commit()
    if profile is None:
        raise HTTPException(status_code=404, detail="connection profile not found")
    diagnostic = repository.diagnose(profile)
    return DiagnosticDto(status=diagnostic.status, message=diagnostic.message)
