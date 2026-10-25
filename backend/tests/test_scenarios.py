"""Scenario-coverage and system-prompt invariant tests.

We cannot make a live LLM deterministic in CI, so instead we lock down the two
things that DETERMINE the agent's behaviour:

  1. the scenario catalogue it reasons over (breadth + safety-critical do/don'ts),
  2. the system prompt that is actually sent to Gemini (triage order, honesty
     rules, retrieval-tool guidance, multilingual behaviour, and the exact
     high-risk first-aid do-NOTs).

A regression that quietly drops, say, the snakebite prohibitions or the
"never claim dispatch" rule fails here.
"""

from __future__ import annotations

import pytest

from app.services.emergency_reference import CATEGORIES as HELPLINE_CATEGORIES
from app.voice.prompts import build_system_instruction
from app.voice.scenarios import (
    CATEGORIES,
    SCENARIOS,
    categories,
    quick_reference_block,
    scenarios_in,
)

_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"}


# ── catalogue breadth ─────────────────────────────────────────────────────────
def test_all_scenario_families_present():
    present = {s.category for s in SCENARIOS}
    assert set(CATEGORIES).issubset(present)
    for fam in CATEGORIES:
        assert scenarios_in(fam), f"no scenarios in family {fam}"


def test_catalogue_is_broad():
    # The task asks for the major categories PLUS 20+ additional varied scenarios.
    assert len(SCENARIOS) >= 24
    assert len({s.id for s in SCENARIOS}) == len(SCENARIOS)  # unique ids


@pytest.mark.parametrize("sid", [
    "cardiac", "stroke", "breathing", "unconscious", "cpr", "choking", "bleeding",
    "burns", "seizure", "poisoning", "allergy", "diabetic", "snakebite", "childbirth",
    "mental_health", "road_accident", "fire", "gas_leak", "electrical", "drowning",
    "fall_fracture", "trapped_collapse", "flood", "earthquake", "cyclone_storm",
    "landslide", "lost_stranded", "violence_threat", "child_vulnerable", "low_resource",
])
def test_expected_scenarios_exist(sid):
    assert any(s.id == sid for s in SCENARIOS), f"missing scenario {sid}"


def test_every_scenario_has_an_immediate_action():
    for s in SCENARIOS:
        assert s.immediate_action.strip(), f"{s.id} has no immediate action"
        assert s.default_severity in _SEVERITIES
        assert s.helpline_category in HELPLINE_CATEGORIES, s.id


@pytest.mark.parametrize("sid", ["snakebite", "cpr", "bleeding", "burns", "choking",
                                 "gas_leak", "electrical", "poisoning"])
def test_high_risk_scenarios_declare_prohibitions(sid):
    s = next(x for x in SCENARIOS if x.id == sid)
    assert s.do_not, f"{sid} must declare do-NOT rules"


def test_snakebite_prohibitions_are_correct():
    s = next(x for x in SCENARIOS if x.id == "snakebite")
    joined = " ".join(s.do_not).lower()
    assert "cut" in joined
    assert "suck" in joined
    assert "tourniquet" in joined
    assert "catch" in joined or "kill" in joined


def test_categories_helper_matches_constant():
    assert categories() == CATEGORIES


# ── quick-reference block (injected into the prompt) ──────────────────────────
def test_quick_reference_covers_critical_cases():
    block = quick_reference_block().lower()
    for needle in ("snakebite", "cpr", "choking", "bleeding", "stroke"):
        assert needle in block or needle.replace("cpr", "cardiac") in block
    # And it must carry the snakebite prohibitions verbatim-ish.
    assert "suck out venom" in block
    assert "tourniquet" in block


# ── system prompt invariants ──────────────────────────────────────────────────
def test_prompt_substitutes_language_and_emergency_number():
    p = build_system_instruction("Marathi", emergency_number="108 / 112")
    assert "Marathi" in p
    assert "108 / 112" in p


@pytest.mark.parametrize("lang", ["English", "Hindi", "Marathi"])
def test_prompt_builds_for_each_primary_language(lang):
    p = build_system_instruction(lang)
    assert lang in p
    assert len(p) > 1500  # it's a substantial behavioural brief


def test_prompt_enforces_honesty_no_dispatch():
    p = build_system_instruction("English").lower()
    assert "do not dispatch" in p
    assert "notified" in p  # "...a contact 'has been notified'..." prohibition
    assert "recorded the details" in p
    # The explicit forbidden-claim list.
    for claim in ("on the way", "shared"):
        assert claim in p
    # The agent may queue email alerts, but must distinguish queued from delivered.
    assert "queued" in p
    assert "never" in p and "delivered" in p


def test_prompt_puts_immediate_action_first():
    p = build_system_instruction("English").lower()
    assert "immediate action" in p
    assert "triage" in p
    # Must tell the model not to block a life-saving step behind questions.
    assert "never delay" in p or "before asking" in p


def test_prompt_references_all_retrieval_tools():
    p = build_system_instruction("English")
    for tool in (
        "get_local_emergency_info",
        "get_weather_alerts",
        "find_nearby_hospitals",
        "get_disaster_advisories",
        "create_emergency_incident",
        "update_emergency_incident",
        "get_current_location",
    ):
        assert tool in p, f"prompt never mentions {tool}"


def test_prompt_instructs_retrieval_over_guessing():
    p = build_system_instruction("English").lower()
    assert "do not guess" in p
    # Must instruct honest fallback when retrieval fails.
    assert "ok:false" in p or "stale" in p


def test_prompt_covers_broad_scenarios_not_just_a_few():
    p = build_system_instruction("English").lower()
    for topic in ("stroke", "seizure", "flood", "earthquake", "gas",
                  "mental-health", "childbirth", "drowning"):
        assert topic in p, f"prompt never mentions {topic}"


def test_prompt_keeps_snakebite_safety_rules():
    p = build_system_instruction("English").lower()
    assert "snakebite" in p
    assert "suck out venom" in p
    assert "tourniquet" in p


def test_prompt_has_conversation_state_and_correction_rules():
    p = build_system_instruction("English").lower()
    assert "conversation state" in p
    assert "correct" in p  # supports user corrections
    assert "re-ask" in p or "re-ask" in p.replace("-", "")


def test_prompt_does_not_bake_in_live_facts():
    # It must tell the model to retrieve changing facts, and must NOT contain a
    # hard-coded current temperature / weather claim.
    p = build_system_instruction("English").lower()
    assert "facts that change" in p or "current facts" in p
    assert "°c" not in p and "degrees celsius" not in p
