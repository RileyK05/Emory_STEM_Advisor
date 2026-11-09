from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.connections.models import ConnectionUnavailable, StoredProfile
from app.connections.selection import StaleProfileSelection, resolve
from app.llm.routing import Candidate, RoutingConfig, choose_route, classify_role


def _profile(**changes):
    base = StoredProfile(
        profile_id=uuid4(), name="approved", provider="openai", model_id="model-a",
        base_url=None, account_id=None, secret_ciphertext=b"encrypted",
        provider_options={}, roles=frozenset({"standard", "complex"}),
        capabilities=frozenset({"structured_output"}),
        data_scopes=frozenset({"institutional_corpus"}), enabled=True, priority=1,
        revision=4, created_at=datetime.now(UTC), updated_at=datetime.now(UTC), eval_passed=True)
    return replace(base, **changes)


class Repo:
    def __init__(self, profile):
        self.profile = profile
    def list_routes(self, conn, *, suite_hash):
        assert suite_hash == "a" * 64
        return (self.profile,)
    def credentials(self, profile):
        return {"api_key": "secret"}
    def get(self, conn, profile_id):
        return self.profile if self.profile.profile_id == profile_id else None
    def get_current_revision(self, conn, profile_id, *, lock=False):
        assert lock
        return ((self.profile.enabled, self.profile.revision)
                if self.profile.profile_id == profile_id else None)


class Runtime:
    def __init__(self, conn):
        self.conn = conn
    def generation_provider(self, profile, **kwargs):
        assert self.conn.commits == 1
        return SimpleNamespace(selected=profile.profile_id)


class Conn:
    def __init__(self):
        self.commits = 0
    def commit(self):
        self.commits += 1


def _routing_file(tmp_path: Path):
    path = tmp_path / "routing.toml"
    path.write_text(
        'version = 1\ndefault_role = "standard"\nuncertain_role = "complex"\n'
        '[signals]\ncomplex_terms = ["compare"]\n'
        'fast_job_kinds = ["batch_generation"]\n'
        'complex_token_threshold = 100\n', encoding="utf-8")
    return path


def _settings(**changes):
    values = {"routing_enabled": True, "routing_eval_suite_hash": "a" * 64,
              "profile_encryption_key": "key", "llm_provider": "hf-local", "llm_model_id": "old"}
    values.update(changes)
    return SimpleNamespace(**values)


def test_resolve_uses_eval_approved_scope_and_role_then_allows_current_check(tmp_path):
    profile = _profile()
    repo, conn = Repo(profile), Conn()
    selected = resolve(_settings(), conn, "What is this?", data_scope="institutional_corpus",
        required_capabilities={"structured_output"}, profiles=repo, runtime=Runtime(conn),
        config_path=_routing_file(tmp_path))
    assert selected.provider.selected == profile.profile_id
    assert selected.settings.llm_provider == "openai"
    assert selected.settings.llm_model_id == "model-a"
    assert selected.role == "complex" and selected.profile_revision == 4
    selected.assert_current(conn)
    assert conn.commits == 1


@pytest.mark.parametrize("profile,scope,caps", [
    (_profile(eval_passed=False), "institutional_corpus", set()),
    (_profile(data_scopes=frozenset({"user_supplied_data"})), "institutional_corpus", set()),
    (_profile(capabilities=frozenset()), "institutional_corpus", {"structured_output"}),
])
def test_resolve_never_uses_unapproved_or_unauthorized_profiles(tmp_path, profile, scope, caps):
    conn = Conn()
    with pytest.raises(ConnectionUnavailable):
        resolve(_settings(), conn, "ordinary question", data_scope=scope,
                required_capabilities=caps, profiles=Repo(profile), runtime=Runtime(conn),
                config_path=_routing_file(tmp_path))


def test_resolve_does_not_fallback_to_environment_when_enabled(tmp_path):
    with pytest.raises(ConnectionUnavailable, match="evaluation suite hash"):
        resolve(_settings(routing_eval_suite_hash=None), Conn(), "q",
                data_scope="institutional_corpus", profiles=Repo(_profile()),
                config_path=_routing_file(tmp_path))
    assert resolve(_settings(routing_enabled=False), Conn(), "q",
                   data_scope="institutional_corpus") is None


def test_resolve_requires_every_requested_data_scope(tmp_path):
    conn = Conn()
    profile = _profile(data_scopes=frozenset({"institutional_corpus", "user_supplied_data"}))
    selected = resolve(_settings(), conn, "question", data_scope="institutional_corpus",
        required_data_scopes={"institutional_corpus", "user_supplied_data"},
        profiles=Repo(profile), runtime=Runtime(conn), config_path=_routing_file(tmp_path))
    assert selected.profile.profile_id == profile.profile_id
    with pytest.raises(ConnectionUnavailable):
        resolve(_settings(), Conn(), "question", data_scope="institutional_corpus",
            required_data_scopes={"institutional_corpus", "user_supplied_data"},
            profiles=Repo(_profile()), runtime=Runtime(Conn()), config_path=_routing_file(tmp_path))


def test_selected_profile_revision_is_rechecked_before_publication(tmp_path):
    profile = _profile()
    repo, conn = Repo(profile), Conn()
    selected = resolve(_settings(), conn, "ordinary question", data_scope="institutional_corpus",
                       profiles=repo, runtime=Runtime(conn), config_path=_routing_file(tmp_path))
    repo.profile = replace(profile, revision=5)
    with pytest.raises(StaleProfileSelection, match="changed during generation"):
        selected.assert_current(conn)


def _route_config():
    return RoutingConfig.load(Path(__file__).parents[1] / "configs" / "routing.toml")


def test_explicit_course_lookup_patterns_route_simple_and_leave_uncertainty_complex():
    config = _route_config()
    assert classify_role(config, query="What are the prerequisites for MATH 111?")[0] == "standard"
    assert classify_role(config, query="MATH 111")[0] == "standard"
    assert classify_role(config, query="Compare MATH 111 and MATH 112")[0] == "complex"
    assert classify_role(config, query="What are prerequisites for a math major?")[0] == "complex"
    assert classify_role(config, query="What is MATH 111 and how does it compare to MATH 112?")[0] == "complex"
    assert classify_role(config, query="x", job_kind="batch_generation")[0] == "fast_generation"


def test_router_requires_eval_passed_profile_and_defaults_uncertain_to_complex():
    config = RoutingConfig.load(Path(__file__).resolve().parents[1] / "configs" / "routing.toml")
    candidates = [Candidate("cheap", "standard", "openai", "small", eval_passed=True),
                 Candidate("strong", "complex", "anthropic", "large", eval_passed=True)]
    decision = choose_route(config, candidates, query="what is this?")
    assert decision.candidate.profile_id == "strong"


def test_router_cannot_select_nonpassing_or_wrong_scope_profile():
    config = RoutingConfig.load(Path(__file__).resolve().parents[1] / "configs" / "routing.toml")
    candidates = [Candidate("unsafe", "complex", "x", "m", data_scopes=frozenset({"internal"}), eval_passed=True),
                 Candidate("untested", "complex", "x", "m", data_scopes=frozenset({"user"}), eval_passed=False)]
    with pytest.raises(LookupError):
        choose_route(config, candidates, query="uncertain", data_scope="user")


def test_router_config_rejects_unknown_keys(tmp_path):
    path = tmp_path / "routing.toml"
    path.write_text("version=1\nsurprise=true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown routing keys"):
        RoutingConfig.load(path)
