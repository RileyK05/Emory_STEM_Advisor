"""The question router: what kind of question this is, and how distressed the student sounds.

A decision model (`DECISION_MODEL_ID`, e.g. `clef`) answers the fixed questions in
`configs/question_router.toml` about the student's message; fixed rules run beside
it. Code, not the answering model, then decides what each kind may and must do
(`POLICIES`) and which distress tier applies. The answering model cannot change
the route, so it cannot skip research by calling a question "general".
See docs/plans/question-router.md.
"""

from __future__ import annotations

import re
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal, get_args

from app.atlas.requisites import canonical_codes_in
from app.llm.base import DecisionProvider, DecisionQuestion, ProviderError, Usage
from app.retrieval.config import ConfigError

CONFIGS = Path(__file__).resolve().parents[2] / "configs"
ROUTER_PATH = CONFIGS / "question_router.toml"
OCCUPATIONS_PATH = CONFIGS / "career_occupations.toml"
SUPPORT_PATH = CONFIGS / "support_replies.toml"

Category = Literal["institutional", "choice", "career_outlook", "study_and_wellbeing",
                   "smalltalk", "out_of_scope"]
# Ordered. `support` is reached by escalation (possible risk again on the turn after
# a check_in) or by strong risk on a first message (support_at). Nothing before it
# mentions counseling.
Tier = Literal["calm", "steady", "check_in", "support", "crisis"]
CATEGORIES: tuple[str, ...] = get_args(Category)
TIERS: tuple[str, ...] = get_args(Tier)
LEVELS = ("none", "everyday_stress", "acute_distress", "possible_risk", "clear_risk")


@dataclass(frozen=True)
class Occupation:
    soc: str
    title: str


@dataclass(frozen=True)
class RouterConfig:
    questions: tuple[DecisionQuestion, ...]
    thresholds: dict[str, float]
    occupations: tuple[Occupation, ...]
    crisis_at: float
    check_in_at: float
    steady_at: float
    support_at: float = 1.01
    state_max_chars: int = 600
    min_probability: float = 0.2
    max_picks: int = 3


@dataclass(frozen=True)
class Route:
    categories: frozenset[str]
    source: Literal["model", "rules", "fallback"]
    scores: dict[str, float] = field(default_factory=dict)
    rule_hits: tuple[str, ...] = ()
    career_soc: tuple[str, ...] = ()
    distress: str = "calm"
    distress_levels: dict[str, float] = field(default_factory=dict)
    usage: Usage | None = None
    model_id: str | None = None

    def details(self) -> dict:
        """What the run saves as its `route` step; the next turn reads `distress` back."""
        return {"categories": sorted(self.categories), "source": self.source,
                "scores": {k: round(v, 4) for k, v in self.scores.items()},
                "rule_hits": list(self.rule_hits), "career_soc": list(self.career_soc),
                "distress": self.distress,
                "distress_levels": {k: round(v, 4) for k, v in self.distress_levels.items()}}


# --- config --------------------------------------------------------------------

_KIND_KEYS = {"name", "threshold", "instructions", "true", "false"}
_TOP_KEYS = {"schema_version", "state_max_chars", "kind", "career_field", "distress"}


def _check_keys(table: dict, allowed: set[str], where: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")


def load_router_config(path: Path | None = None, occupations: Path | None = None) -> RouterConfig:
    """The validated router config; re-read only when either file changes."""
    path, occupations = Path(path or ROUTER_PATH), Path(occupations or OCCUPATIONS_PATH)
    return _load_router_config(path, path.stat().st_mtime_ns, occupations,
                               occupations.stat().st_mtime_ns)


@lru_cache(maxsize=4)
def _load_router_config(path: Path, _mtime: int, occupations_path: Path, _omtime: int) -> RouterConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    _check_keys(data, _TOP_KEYS, path.name)
    questions, thresholds = [], {}
    for kind in data["kind"]:
        _check_keys(kind, _KIND_KEYS, f"[[kind]] {kind.get('name')}")
        if kind["name"] not in CATEGORIES:
            raise ConfigError(f"[[kind]] {kind['name']!r} is not a router category")
        thresholds[kind["name"]] = float(kind["threshold"])
        questions.append(DecisionQuestion(kind["name"], "noul", kind["instructions"],
                                          {"true": kind["true"], "false": kind["false"]}))
    field_cfg = data["career_field"]
    _check_keys(field_cfg, {"instructions", "none", "min_probability", "max_picks"}, "[career_field]")
    occupations = tuple(Occupation(o["soc"], o["title"]) for o in
                        tomllib.loads(occupations_path.read_text(encoding="utf-8"))["occupation"])
    criteria = {"none": field_cfg["none"]}
    for o in tomllib.loads(occupations_path.read_text(encoding="utf-8"))["occupation"]:
        criteria[o["soc"]] = f"{o['title']}: {', '.join(o['paths'])}"
    questions.append(DecisionQuestion("career_field", "choice", field_cfg["instructions"], criteria))
    distress = data["distress"]
    _check_keys(distress, {"instructions", "crisis_at", "support_at", "check_in_at", "steady_at",
                           "levels"}, "[distress]")
    if tuple(distress["levels"]) != LEVELS:
        raise ConfigError(f"[distress.levels] must be exactly {list(LEVELS)}, in that order")
    questions.append(DecisionQuestion("distress", "choice", distress["instructions"],
                                      dict(distress["levels"])))
    return RouterConfig(
        questions=tuple(questions), thresholds=thresholds, occupations=occupations,
        crisis_at=float(distress["crisis_at"]), support_at=float(distress["support_at"]),
        check_in_at=float(distress["check_in_at"]),
        steady_at=float(distress["steady_at"]), state_max_chars=int(data.get("state_max_chars", 600)),
        min_probability=float(field_cfg["min_probability"]), max_picks=int(field_cfg["max_picks"]))


@dataclass(frozen=True)
class SupportReplies:
    crisis: str
    support: str
    reviewed: bool
    immigration: str = ""
    support_once: str = ""


def load_support_replies(path: Path | None = None) -> SupportReplies:
    path = Path(path or SUPPORT_PATH)
    return _load_support(path, path.stat().st_mtime_ns)


@lru_cache(maxsize=2)
def _load_support(path: Path, _mtime: int) -> SupportReplies:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    _check_keys(data, {"schema_version", "reviewed", "crisis", "support", "support_once",
                       "immigration"}, path.name)
    return SupportReplies(data["crisis"].strip(), data["support"].strip(),
                          bool(data.get("reviewed", False)), data["immigration"].strip(),
                          data["support_once"].strip())


# --- rules ---------------------------------------------------------------------

_POLICY_WORDS = re.compile(
    r"\b(deadlines?|withdraw\w*|declar\w*|credits?|credit hours|gpa|transfer\w*|ap|ib|"
    r"honor code|registration|register|prereq\w*|prerequisites?|requirements?|required|"
    r"major|minor|concentration|track|catalog|course atlas|syllabus|waitlist\w*|"
    r"add/drop|s/u|pass/fail|overload|graduat\w*)\b", re.IGNORECASE)
SELF_HARM = re.compile(
    r"\b(suicid\w*|kill(?:ing)? myself|end(?:ing)? my life|self[- ]?harm\w*|"
    r"cutting myself|cut myself|overdos\w*|want to die|wanna die)\b", re.IGNORECASE)

# Immigration and work-authorization asks belong to ISSS, not to an academic advisor:
# their rules change faster than any source here (the 2026 CPT guidance was stayed by
# a court ten days before testing found the advisor still calling it in effect). A
# student merely saying they are on an F-1 is not an ask; "opt out" is not OPT.
IMMIGRATION = re.compile(
    r"\b(cpt|(?:stem\s+)?opt(?!\s+(?:out|in|into|for)\b)|i-?20s?|sevis|sevp|visas?|h-?1b|"
    r"green\s+cards?|(?:work|employment)\s+authori[sz]ation|immigration|"
    r"(?:maintain|keep|lose|losing)\w*\s+(?:my\s+)?(?:f-?1\s+|j-?1\s+|visa\s+)?status|"
    r"reduced\s+course\s+load|dso)\b", re.IGNORECASE)


def rule_hits(question: str) -> tuple[str, ...]:
    hits = []
    if canonical_codes_in(question):
        hits.append("course_code")
    if _POLICY_WORDS.search(question):
        hits.append("policy_word")
    if SELF_HARM.search(question):
        hits.append("self_harm")
    if IMMIGRATION.search(question):
        hits.append("immigration")
    return tuple(hits)


# --- routing -------------------------------------------------------------------

def _rank(tier: str) -> int:
    return TIERS.index(tier)


def distress_tier(levels: dict[str, float], config: RouterConfig) -> str:
    """Probabilities added from the most severe level down; the highest tier whose
    share reaches its threshold. Doubt lands on check_in, not on crisis; but when
    nearly all the weight is risk ("they'd be better off without me" scored 0.95,
    with clear risk 0.15 to 0.41 from run to run), the student gets CAPS and 988 on
    the first message instead of a check-in that names no one."""
    risk = levels.get("possible_risk", 0.0) + levels.get("clear_risk", 0.0)
    if levels.get("clear_risk", 0.0) >= config.crisis_at:
        return "crisis"
    if risk >= config.support_at:
        return "support"
    if risk >= config.check_in_at:
        return "check_in"
    if sum(levels.get(level, 0.0) for level in LEVELS[2:]) >= config.steady_at:
        return "steady"
    return "calm"


def escalate(tier: str, previous_tier: str) -> str:
    """Step in only when it keeps coming up: a sign of possible risk right after a
    turn that already got a check_in (or more) becomes `support`. A first sign gets
    a human check-in, not a hotline; a calm message after one is just calm."""
    if tier == "check_in" and _rank(previous_tier) >= _rank("check_in"):
        return "support"
    return tier


def _decide(decider: DecisionProvider, state: dict[str, str], config: RouterConfig):
    """Two calls at once: the kinds with the turn before (to resolve "what about the
    minor?"), and distress on the latest message alone, so an earlier worry does
    not color a calm reply. Memory of earlier turns is code's saved tier."""
    kinds = tuple(q for q in config.questions if q.name != "distress")
    distress = tuple(q for q in config.questions if q.name == "distress")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="router-decide") as pool:
        first = pool.submit(decider.decide, state, kinds)
        second = pool.submit(decider.decide, {"latest_message": state["latest_message"]}, distress)
        (answers, used), (more, used_more) = first.result(), second.result()
    if used.prompt_tokens is not None and used_more.prompt_tokens is not None:
        usage = Usage(prompt_tokens=used.prompt_tokens + used_more.prompt_tokens,
                      completion_tokens=(used.completion_tokens or 0) + (used_more.completion_tokens or 0),
                      source="provider")
    else:
        usage = Usage()
    return {**answers, **more}, usage


def route_question(decider: DecisionProvider | None, question: str, *, config: RouterConfig,
                   previous_student: str | None = None, previous_reply: str | None = None,
                   previous_tier: str = "calm") -> Route:
    hits = rule_hits(question)
    from_rules = {"institutional"} if {"course_code", "policy_word"} & set(hits) else set()
    floor = "check_in" if "self_harm" in hits else "calm"
    if decider is None:
        return Route(frozenset(from_rules | {"institutional"}), "rules", rule_hits=hits,
                     distress=escalate(floor, previous_tier))
    limit = config.state_max_chars
    state = {"latest_message": question[:limit]}
    if previous_student:
        state["previous_student_message"] = previous_student[:limit]
    if previous_reply:
        state["previous_advisor_reply"] = previous_reply[:limit]
    try:
        answers, usage = _decide(decider, state, config)
    except ProviderError:
        tier = "crisis" if "self_harm" in hits else floor
        return Route(frozenset(from_rules | {"institutional"}), "fallback", rule_hits=hits,
                     distress=escalate(tier, previous_tier), model_id=decider.model_id)
    scores = {name: answers[name].probability or 0.0 for name in config.thresholds}
    picked = {name for name, threshold in config.thresholds.items() if scores[name] >= threshold}
    soc: tuple[str, ...] = ()
    if "career_outlook" in picked:
        probs = answers["career_field"].probabilities
        soc = tuple(code for code, p in sorted(probs.items(), key=lambda kv: -kv[1])
                    if code != "none" and p >= config.min_probability)[: config.max_picks]
    levels = answers["distress"].probabilities
    tier = distress_tier(levels, config)
    if _rank(tier) < _rank(floor):
        tier = floor
    tier = escalate(tier, previous_tier)
    source = "model"
    if not picked:
        picked, source = {"institutional"}, "fallback"
    return Route(frozenset(picked | from_rules), source, scores=scores, rule_hits=hits,
                 career_soc=soc, distress=tier, distress_levels=dict(levels), usage=usage,
                 model_id=decider.model_id)


# --- policies ------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    perspective_blocks: int = 0
    must_search: frozenset[str] = frozenset()
    must_cite_kinds: frozenset[str] = frozenset()
    # The source kind a plain search (kind=any) reads on this route. Measured
    # 2026-11-18: an unscoped "what do biologists earn" put no career record in
    # its top 24, because 24k catalog chunks outnumber 96 career ones. The model
    # can still name any other kind; only the default narrows.
    search_kind: str | None = None


# Searches code runs before the model's first round on a scoped route, so the
# round the model would spend asking for them is saved.
PREFETCH_LIMIT = 2
_QUERY_LIMIT = 300

POLICIES: dict[str, Policy] = {
    "institutional": Policy(),
    "choice": Policy(perspective_blocks=1),
    "career_outlook": Policy(perspective_blocks=2, must_search=frozenset({"career_reference"}),
                             must_cite_kinds=frozenset({"career_reference"}),
                             search_kind="career_reference"),
    "study_and_wellbeing": Policy(perspective_blocks=2, must_search=frozenset({"policy"}),
                                  must_cite_kinds=frozenset({"policy"}), search_kind="policy"),
    "smalltalk": Policy(),
    "out_of_scope": Policy(),
}

# One fixed line per kind for the research prompt, appended after the voice note.
PROMPT_NOTES = {
    "choice": "This question asks for a recommendation. Lead with what actually separates the "
              "options, lay out honest tradeoffs (workload, prerequisites, flexibility, and who "
              "each path suits), and commit to a concrete starting pick rather than hedging. You "
              "may add one perspective block of general advice (broader framing or tradeoff): "
              "no numbers, %, $, course codes or Emory rules in it, and no evidence_ids.",
    "career_outlook": "This question is about careers, pay or the job market. Search career data "
                      "first (advisor_search with kind=career_reference) and give its numbers, "
                      "dates and geography in fact blocks that cite it. Meet career worry "
                      "plainly and realistically—neither lecturing nor brushing it off. Then you "
                      "may add up to two perspective blocks of practical career leverage "
                      "(fundamentals, projects, internships, alumni networks) without inventing "
                      "job odds: no numbers, %, $, course codes or Emory rules, and no evidence_ids.",
    "study_and_wellbeing": "This question is about stress, workload, motivation or study habits. "
                           "Search Emory support and policy sources first (advisor_search with "
                           "kind=policy). Meet the student warmly and plainly in the opening of "
                           "your first block without sounding clinical or preachy, then cite "
                           "concrete Emory options in fact blocks. Then you may add up to two "
                           "perspective blocks of grounded, actionable ways to regain footing: "
                           "no numbers, %, $, course codes or Emory rules, and no evidence_ids.",
}
IMMIGRATION_NOTE = (
    "Part of this question is about visas, CPT/OPT, I-20s or other immigration or "
    "work-authorization rules. Those are outside an academic advisor's scope: code adds "
    "a fixed note sending the student to ISSS. Do not state, cite or guess any "
    "immigration rule, its status or its eligibility; answer only the Emory academic part "
    "(courses, requirements, registration).")
TIER_NOTES = {
    "steady": "Distress: the student is upset about a school situation, not at risk. Research and "
              "answer as usual. Open your first block with one calm, human sentence that names "
              "what they're facing, then give the single most useful next step. Do not mention "
              "counseling, CAPS, hotlines or police: a student venting about a class wants help "
              "with the class.",
    "check_in": "Distress: the student said something that may point past school. Respond the way "
                "an advisor who cares would, not with a referral: acknowledge it in one plain "
                "sentence at the start of your first block, research and answer what they asked, "
                "and end with one short question block asking, in your own words, how they're "
                "doing beyond classes. Do not mention counseling, CAPS, hotlines or police yet. "
                "Use no perspective blocks and no personality.",
    "support": "Distress: the student has come back to this more than once. Code has written a "
               "short, warm note with CAPS and 988 at the top of the reply; do not write your own "
               "contacts or repeat it. Still research and answer what they asked, plainly, with no "
               "personality and no perspective blocks; do not put it off until they have talked "
               "to someone.",
}


def combined(route: Route, *, pins_place: bool = False) -> Policy:
    """Permissions and obligations add up across the route's kinds.

    A check_in or support turn allows no general advice and drops the search and
    citation obligations: there the person comes first, and a refused answer
    would leave them with nothing.

    When the map under the reply pins a building the question names, the prompt
    tells the model to say where it is; no record cites a map pin, so that one
    sentence is general advice, which a records-only route would always refuse.
    """
    kinds = [k for k in route.categories if k in POLICIES]
    lenient = route.distress in {"check_in", "support"}
    # A default scope only when every substantive kind wants the same one: a
    # career question that also asks about a major still searches everything.
    scopes = {POLICIES[k].search_kind for k in kinds if k not in {"smalltalk", "out_of_scope"}}
    return Policy(
        search_kind=next(iter(scopes)) if len(scopes) == 1 else None,
        perspective_blocks=0 if lenient else
        max([int(pins_place), *(POLICIES[k].perspective_blocks for k in kinds)]),
        must_search=frozenset() if lenient else
        frozenset().union(*(POLICIES[k].must_search for k in kinds)),
        must_cite_kinds=frozenset() if lenient else
        frozenset().union(*(POLICIES[k].must_cite_kinds for k in kinds)))


def prefetch_searches(route: Route, policy: Policy, config: RouterConfig,
                      question: str) -> list[dict]:
    """The advisor_search calls code makes before research's first round.

    Only on a scoped route. A career question with occupations on file searches
    each by title (what the route note tells the model to do); anything else
    searches the student's words. The results reach the model as an ordinary
    tool round, so its handles are read and cited exactly like its own searches.
    """
    if not policy.search_kind:
        return []
    if policy.search_kind == "career_reference" and route.career_soc:
        titles = {o.soc: o.title for o in config.occupations}
        texts = [titles[soc] for soc in route.career_soc if soc in titles]
    else:
        texts = [" ".join(question.split())]
    searches = []
    for text in texts[:PREFETCH_LIMIT]:
        if len(text) > _QUERY_LIMIT:
            text = text[:_QUERY_LIMIT].rsplit(" ", 1)[0]
        if text:
            searches.append({"query": text, "kind": policy.search_kind})
    return searches


def only_out_of_scope(route: Route) -> bool:
    return route.categories == {"out_of_scope"}


def asks_immigration(route: Route) -> bool:
    return "immigration" in route.rule_hits


def only_immigration(route: Route) -> bool:
    """An immigration ask that names no course: ISSS's fixed reply, no research."""
    return asks_immigration(route) and "course_code" not in route.rule_hits


def route_note(route: Route, config: RouterConfig) -> str:
    """The route's prompt notes: one fixed line per kind, the career records, the tier."""
    lines = [PROMPT_NOTES[k] for k in CATEGORIES if k in route.categories and k in PROMPT_NOTES]
    if route.career_soc:
        titles = {o.soc: o.title for o in config.occupations}
        named = ", ".join(f"{titles.get(soc, soc)} ({soc})" for soc in route.career_soc)
        lines.append(f"Career records on file for this question: {named}. Search for each by title "
                     "with kind=career_reference and read its record.")
    if asks_immigration(route):
        lines.append(IMMIGRATION_NOTE)
    if route.distress in TIER_NOTES:
        lines.append(TIER_NOTES[route.distress])
    return ("\nQUESTION ROUTE\n" + "\n".join(f"- {line}" for line in lines)) if lines else ""
