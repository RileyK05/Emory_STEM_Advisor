"""The question router: kinds, distress tiers, the decision-model seam, and the
general-advice policy. The real config file is loaded; no question text is copied."""

import json

import httpx
import pytest

from app.advising.research_types import DecisionBlock, FinalDecision
from app.generation.answer_checks.perspective import perspective_problems
from app.generation.question_router import (
    combined,
    distress_tier,
    load_router_config,
    prefetch_searches,
    route_question,
)
from app.generation.research_final import order_blocks, with_support_text
from app.llm.base import ProviderError, TransientProviderError
from tests.fakes import FakeDecisionProvider

CONFIG = load_router_config()


def _levels(**values):
    return {"distress": {level: values.get(level, 0.0) for level in
                         ("none", "everyday_stress", "acute_distress", "possible_risk", "clear_risk")}}


@pytest.mark.parametrize(("question", "decider", "kinds", "source"), [
    # The rules add institutional even when the model scores only careers.
    ("Is CS 253 good for a job in software?", FakeDecisionProvider({"career_outlook": 0.9}),
     {"career_outlook", "institutional"}, "model"),
    # Under its threshold a kind drops out; two over their thresholds both count.
    ("I'm worried about jobs", FakeDecisionProvider({"career_outlook": 0.9, "choice": 0.4}),
     {"career_outlook"}, "model"),
    ("Should I major in it? I'm worried about jobs",
     FakeDecisionProvider({"career_outlook": 0.9, "choice": 0.8}),
     {"career_outlook", "choice", "institutional"}, "model"),
    # institutional turns on at a weaker signal than the rest.
    ("hmm", FakeDecisionProvider({"institutional": 0.35}), {"institutional"}, "model"),
    # A failed call, or no kind passing, falls back to the strict kind.
    ("I'm worried about jobs", FakeDecisionProvider(error=TransientProviderError("x", provider="t")),
     {"institutional"}, "fallback"),
    ("hello there", FakeDecisionProvider({"smalltalk": 0.2}), {"institutional"}, "fallback"),
    # No decision model configured: rules only, always institutional.
    ("write my essay", None, {"institutional"}, "rules"),
])
def test_route_kinds(question, decider, kinds, source):
    route = route_question(decider, question, config=CONFIG)
    assert (set(route.categories), route.source) == (kinds, source)


def test_career_picks_only_with_career_outlook_and_never_none():
    picks = {"none": 0.3, "15-1252": 0.5, "15-2051": 0.25, "15-1212": 0.1}
    on = route_question(FakeDecisionProvider({"career_outlook": 0.9}, {"career_field": picks}),
                        "jobs?", config=CONFIG)
    off = route_question(FakeDecisionProvider({"choice": 0.9}, {"career_field": picks}),
                         "which?", config=CONFIG)
    assert (on.career_soc, off.career_soc) == (("15-1252", "15-2051"), ())


@pytest.mark.parametrize(("levels", "tier"), [
    ({"none": 1.0}, "calm"),
    ({"none": 0.55, "acute_distress": 0.45}, "calm"),
    ({"none": 0.5, "acute_distress": 0.5}, "steady"),
    ({"acute_distress": 0.81, "possible_risk": 0.19}, "steady"),
    ({"acute_distress": 0.8, "possible_risk": 0.2}, "check_in"),
    ({"acute_distress": 0.21, "possible_risk": 0.79}, "check_in"),
    # Nearly all risk, but not clear risk: CAPS and 988 now, the answer still follows.
    ({"acute_distress": 0.2, "possible_risk": 0.8}, "support"),
    ({"possible_risk": 0.61, "clear_risk": 0.39}, "support"),
    ({"possible_risk": 0.6, "clear_risk": 0.4}, "crisis"),
])
def test_distress_tier_edges(levels, tier):
    assert distress_tier(_levels(**levels)["distress"], CONFIG) == tier


@pytest.mark.parametrize(("question", "decider", "previous", "tier"), [
    # Explicit self-harm wording raises calm to check_in but never lowers crisis.
    ("I'm not suicidal, just stressed", FakeDecisionProvider(choices=_levels(none=1.0)),
     "calm", "check_in"),
    ("I want to end my life", FakeDecisionProvider(choices=_levels(clear_risk=0.9)), "calm", "crisis"),
    # A failed call with explicit wording gives crisis; without it, the floor.
    ("I want to end my life", FakeDecisionProvider(error=ProviderError("x", provider="t")),
     "calm", "crisis"),
    ("does CS 170 fill up", FakeDecisionProvider(error=ProviderError("x", provider="t")),
     "calm", "calm"),
    # Escalation, not carry-over: a calm message after a check_in is just calm, the
    # same worry again steps in with support, and a first sign is only a check_in
    # unless nearly all its weight is risk.
    ("ok thanks", FakeDecisionProvider(choices=_levels(none=1.0)), "check_in", "calm"),
    ("still pointless", FakeDecisionProvider(choices=_levels(possible_risk=0.9)), "check_in",
     "support"),
    ("still pointless", FakeDecisionProvider(choices=_levels(possible_risk=0.9)), "support",
     "support"),
    ("what's the point", FakeDecisionProvider(choices=_levels(none=0.5, possible_risk=0.5)),
     "steady", "check_in"),
    ("what's the point", FakeDecisionProvider(choices=_levels(possible_risk=0.9)), "steady",
     "support"),
    ("I want to end my life", FakeDecisionProvider(choices=_levels(clear_risk=0.9)), "check_in",
     "crisis"),
    # Figures of speech are not the rule's business.
    ("this exam is killing me", FakeDecisionProvider(choices=_levels(everyday_stress=1.0)),
     "calm", "calm"),
])
def test_distress_floors(question, decider, previous, tier):
    assert route_question(decider, question, config=CONFIG, previous_tier=previous).distress == tier


def test_kinds_see_the_turn_before_but_distress_sees_only_the_latest_message():
    decider = FakeDecisionProvider()
    route_question(decider, "x" * 5000, config=CONFIG, previous_student="what about the minor?",
                   previous_reply=None)
    shapes = sorted(sorted(state) for state in decider.states)
    assert shapes == [["latest_message"], ["latest_message", "previous_student_message"]]
    assert all(len(s["latest_message"]) == CONFIG.state_max_chars for s in decider.states)


def _transport(status, body):
    def handler(request):
        payload = json.loads(request.content)
        assert isinstance(payload["questions"], dict)  # keyed by name; a list gets HTTP 400
        return httpx.Response(status, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(("status", "body", "error"), [
    (200, {"answers": {"institutional": {"type": "noul", "noul": 0.9},
                       "distress": {"type": "choice", "choice": "none",
                                    "probabilities": {"none": 0.9, "clear_risk": 0.1}}},
           "usage": {"input_tokens": 12, "output_tokens": 0}}, None),
    (503, {"error": "busy"}, TransientProviderError),
    (400, {"error": "questions must map question ids to questions"}, ProviderError),
    (200, {"answers": {"institutional": {"type": "noul", "noul": 0.9}}}, ProviderError),
])
def test_systemone_provider(status, body, error):
    from app.llm.base import DecisionQuestion
    from app.llm.systemone import SystemOneDecisionProvider

    provider = SystemOneDecisionProvider(_transport(status, body), "clef", base_url="https://x/v1",
                                         api_key="k", timeout_seconds=5)
    questions = (DecisionQuestion("institutional", "noul", "?", {"true": "a", "false": "b"}),
                 DecisionQuestion("distress", "choice", "?", {"none": "a", "clear_risk": "b"}))
    if error:
        with pytest.raises(error):
            provider.decide({"latest_message": "hi"}, questions)
        return
    answers, usage = provider.decide({"latest_message": "hi"}, questions)
    assert answers["institutional"].probability == 0.9
    assert answers["distress"].probabilities == {"none": 0.9, "clear_risk": 0.1}
    assert usage.prompt_tokens == 12


def _decision(*blocks, outcome="information"):
    return FinalDecision(outcome=outcome, completeness="complete",
                         blocks=tuple(DecisionBlock(kind=k, text=t, evidence_ids=e) for k, t, e in blocks))


PERSPECTIVE = ("perspective", "Many students find projects matter as much as grades.", ())
CAREER_FACT = ("fact", "Software developers earn a median wage of about that much.", ("e1",))


@pytest.mark.parametrize(("kinds", "decision", "searched", "found", "problem"), [
    # An Emory-facts route allows no general advice.
    ({"institutional"}, _decision(PERSPECTIVE), set(), {}, "needs records, not general advice"),
    # A career route allows it, once it searched career data and cites what it read.
    ({"career_outlook"}, _decision(CAREER_FACT, PERSPECTIVE), {"career_reference"},
     {"career_reference": {"e1"}}, None),
    ({"career_outlook"}, _decision(PERSPECTIVE), set(), {}, "search career_reference"),
    ({"career_outlook"}, _decision(PERSPECTIVE), {"career_reference"},
     {"career_reference": {"e1"}}, "answer from them first"),
    # Mixed routes add up: choice alone allows one block, with careers two.
    ({"choice"}, _decision(PERSPECTIVE, PERSPECTIVE), set(), {}, "at most 1"),
    ({"choice", "career_outlook"}, _decision(CAREER_FACT, PERSPECTIVE, PERSPECTIVE),
     {"career_reference"}, {"career_reference": {"e1"}}, None),
    # General advice states no numbers, courses or rules, and cites nothing.
    ({"choice"}, _decision(("perspective", "Most students take 15 credits.", ())), set(), {},
     "cannot state numbers"),
    ({"choice"}, _decision(("perspective", "Take CS 170 early.", ())), set(), {}, "cannot state"),
    ({"choice"}, _decision(("perspective", "The deadline matters most.", ())), set(), {},
     "cannot state"),
    ({"choice"}, _decision(("perspective", "Projects matter.", ("e1",))), set(), {}, "cites nothing"),
])
def test_general_advice_policy(kinds, decision, searched, found, problem):
    from app.generation.question_router import Route

    policy = combined(Route(frozenset(kinds), "model"))
    problems = perspective_problems(decision, allowed=policy.perspective_blocks,
                                    must_search=policy.must_search, searched=searched,
                                    must_cite=policy.must_cite_kinds, found=found)
    assert (problem is None and not problems) or any(problem in p for p in problems), problems


PLACE = ("perspective", "White Hall is Goodrich C. White Hall, pinned on the map below.", ())


@pytest.mark.parametrize(("question", "immigration"), [
    ("can I do CPT for my internship", True),
    ("how long is STEM OPT", True),
    ("my I-20 expires in May", True),
    ("will I lose my F-1 status if I drop to 9 credits", True),
    ("do I need a visa to study abroad in Spain", True),
    # Saying who you are is not an ask, and "opt out" is not OPT.
    ("I'm an international student on an F-1, what does the CS BS require?", False),
    ("can I opt out of the meal plan", False),
    ("what's the prereq for CS 326", False),
])
def test_an_immigration_ask_goes_to_isss(question, immigration):
    from app.generation.question_router import rule_hits

    assert ("immigration" in rule_hits(question)) is immigration


@pytest.mark.parametrize(("distress", "pins_place", "problem"), [
    # The map pins a named building; saying where it is needs no record to cite.
    ("calm", True, None),
    ("calm", False, "needs records, not general advice"),
    # A check-in still allows no general advice, map or not.
    ("check_in", True, "needs records, not general advice"),
])
def test_a_pinned_place_may_be_said_on_a_records_route(distress, pins_place, problem):
    from app.generation.question_router import Route

    policy = combined(Route(frozenset({"institutional"}), "model", distress=distress),
                      pins_place=pins_place)
    problems = perspective_problems(_decision(PLACE), allowed=policy.perspective_blocks,
                                    must_search=policy.must_search, searched=set(),
                                    must_cite=policy.must_cite_kinds, found={})
    assert (problem is None and not problems) or any(problem in p for p in problems), problems


ASKED = "what do people in this field earn?"
JOBS = [o.soc for o in CONFIG.occupations[:3]]
TITLES = [o.title for o in CONFIG.occupations[:2]]


@pytest.mark.parametrize(("kinds", "soc", "distress", "searches"), [
    # One scoped kind: code searches it before the first round, in the student's words...
    ({"career_outlook"}, (), "calm", [(ASKED, "career_reference")]),
    # ...or by the occupations on file, at most two.
    ({"career_outlook"}, JOBS, "calm", [(title, "career_reference") for title in TITLES]),
    # A check-in drops the obligations, not the scope: Emory support is still found first.
    ({"study_and_wellbeing"}, (), "check_in", [(ASKED, "policy")]),
    ({"career_outlook", "smalltalk"}, (), "calm", [(ASKED, "career_reference")]),
    # A mixed or Emory-facts route searches everything, as before.
    ({"career_outlook", "institutional"}, (), "calm", []),
    ({"choice", "study_and_wellbeing"}, (), "calm", []),
    ({"institutional"}, (), "calm", []),
])
def test_the_route_scopes_search_and_runs_the_first_one(kinds, soc, distress, searches):
    from app.generation.question_router import Route

    route = Route(frozenset(kinds), "model", career_soc=tuple(soc), distress=distress)
    policy = combined(route)
    found = prefetch_searches(route, policy, CONFIG, ASKED)
    assert [(s["query"], s["kind"]) for s in found] == searches
    assert policy.search_kind == (searches[0][1] if searches else None)


def test_records_come_first_and_the_tier_text_wraps_the_answer():
    decision = _decision(("limitation", "Ask your advisor.", ()), PERSPECTIVE, CAREER_FACT)
    wrapped = with_support_text(order_blocks(decision), first="Check in.", last=None)
    assert [b.kind for b in wrapped.blocks] == ["limitation", "fact", "perspective", "limitation"]
    assert wrapped.blocks[0].text == "Check in."
    # Applying it again (a corrected finish) does not stack a second copy.
    assert with_support_text(wrapped, first="Check in.").blocks == wrapped.blocks


@pytest.mark.parametrize(("first", "texts"), [
    # Before support: the model's referral goes, so venting gets no hotline.
    (None, [CAREER_FACT[1], "How are you doing beyond classes?"]),
    # At support: the contacts appear once, in the checked wording.
    ("CAPS is at 404-727-7450.", ["CAPS is at 404-727-7450.", CAREER_FACT[1],
                                  "How are you doing beyond classes?"]),
])
def test_support_contacts_come_only_from_code(first, texts):
    model_wrote = _decision(CAREER_FACT, ("limitation", "Talk to CAPS at 404-727-7450 or 988.", ()),
                            ("question", "How are you doing beyond classes?", ()))
    assert [b.text for b in with_support_text(model_wrote, first=first).blocks] == texts


def test_a_check_in_turn_allows_no_general_advice_and_drops_search_obligations():
    from app.generation.question_router import Route

    calm = combined(Route(frozenset({"study_and_wellbeing"}), "model"))
    check_in = combined(Route(frozenset({"study_and_wellbeing"}), "model", distress="support"))
    assert (calm.must_search, check_in.must_search) == (frozenset({"policy"}), frozenset())
    assert (calm.perspective_blocks, check_in.perspective_blocks) == (2, 0)


@pytest.mark.parametrize(("persona", "categories", "distress", "source", "advice", "file_turn", "expected"), [
    (None, {"choice"}, "calm", "model", True, False, "off"),
    ("unknown", {"choice"}, "calm", "model", True, False, "off"),
    ("swoop", {"choice"}, "check_in", "model", True, False, "off"),
    ("dooley", {"study_and_wellbeing"}, "support", "model", False, False, "off"),
    ("swoop", {"institutional"}, "crisis", "model", False, False, "off"),
    ("dooley", {"out_of_scope"}, "calm", "model", False, False, "off"),
    ("dooley", {"out_of_scope"}, "steady", "model", False, False, "off"),
    ("swoop", {"study_and_wellbeing", "choice"}, "steady", "model", True, False, "steady"),
    ("dooley", {"institutional"}, "steady", "model", False, True, "steady"),
    ("swoop", {"choice"}, "calm", "model", True, True, "light"),
    ("dooley", {"institutional"}, "calm", "model", False, False, "light"),
    ("swoop", {"choice"}, "calm", "model", False, False, "full"),
    ("dooley", {"career_outlook"}, "calm", "model", False, False, "full"),
    ("swoop", {"study_and_wellbeing"}, "calm", "model", False, False, "full"),
    ("dooley", {"smalltalk"}, "calm", "model", False, False, "full"),
    ("swoop", {"institutional"}, "calm", "rules", True, False, "full"),
])
def test_voice_level_scales_automatically(
        persona, categories, distress, source, advice, file_turn, expected):
    from app.generation.question_router import Route
    from app.generation.voice import voice_level

    route = Route(frozenset(categories), source, distress=distress)
    assert voice_level(persona, route, advice_question=advice, file_turn=file_turn) == expected


def test_system_prompt_and_repair_hint_respect_voice_levels():
    from types import SimpleNamespace

    from app.generation.answer_checks.safety import answer_safety_problems
    from app.generation.question_router import Route
    from app.generation.research_controller import SYSTEM, _system_prompt
    from app.generation.voice import VOICE_BASE, voice_repair_hint

    def session(persona, route, *, question="Does CS 253 require CS 171?", file_turn=None):
        return SimpleNamespace(persona=persona, route=route, question=question, file_turn=file_turn)

    lookup = Route(frozenset({"institutional"}), "model")
    choice = Route(frozenset({"choice"}), "model")
    steady = Route(frozenset({"study_and_wellbeing"}), "model", distress="steady")

    prompts = {}
    for persona in ("swoop", "dooley"):
        light = _system_prompt(session(persona, lookup))
        full = _system_prompt(session(persona, choice, question="Which CS electives fit ML?"))
        calm_steady = _system_prompt(session(persona, steady, question="I bombed my exam."))
        prefix = SYSTEM + VOICE_BASE[persona]
        assert light.startswith(prefix)
        assert full.startswith(prefix)
        assert calm_steady.startswith(prefix)
        assert len({light, full, calm_steady}) == 3
        assert len({voice_repair_hint(persona, lvl) for lvl in ("light", "full", "steady")}) == 3
        prompts[persona] = full

    assert prompts["swoop"] != prompts["dooley"]

    for suppressed in (
        session(None, choice),
        session("swoop", Route(frozenset({"institutional"}), "model", distress="check_in")),
        session("dooley", Route(frozenset({"study_and_wellbeing"}), "model", distress="support")),
        session("swoop", Route(frozenset({"institutional"}), "model", distress="crisis")),
        session("dooley", Route(frozenset({"out_of_scope"}), "model")),
    ):
        assert "VOICE:" not in _system_prompt(suppressed)

    assert voice_repair_hint(None, "full") == ""
    assert voice_repair_hint("swoop", "off") == ""
    assert voice_repair_hint("dooley", "off") == ""

    samples = [
        ("swoop", FinalDecision(outcome="information", completeness="complete", blocks=(
            DecisionBlock(kind="limitation", text=(
                "Anytime! Let me know whenever you want to map out next semester's classes.")),))),
        ("dooley", FinalDecision(outcome="information", completeness="complete", blocks=(
            DecisionBlock(kind="limitation", text=(
                "Glad that helped; the catalog will still be here whenever your next registration question strikes.")),))),
        ("swoop", FinalDecision(outcome="recommendation", completeness="complete", blocks=(
            DecisionBlock(kind="judgment", text=(
                "If you want hands-on modeling right away, starting with machine learning is a strong, "
                "doable step once you finish the introductory sequence.")),
            DecisionBlock(kind="perspective", text=(
                "You do not need to lock in every elective today; pick one core track that keeps your "
                "fall balanced and leave room to adjust after midterms.")),))),
        ("dooley", FinalDecision(outcome="recommendation", completeness="complete", blocks=(
            DecisionBlock(kind="judgment", text=(
                "Between the two tracks, the applied route gets you building faster while the theory "
                "route trades early flexibility for mathematical depth later on.")),
            DecisionBlock(kind="perspective", text=(
                "Students routinely agonize over finding one perfect elective list, when taking one solid "
                "anchor class and keeping your schedule survivable matters far more.")),))),
    ]
    for persona, decision in samples:
        assert answer_safety_problems(decision, instructions=prompts[persona], evidence=[]) == []
