"""Routing that carries the scope the question named.

The router returned a skill name and nothing else, and the shell ran that
skill with no arguments. So "which controls are orphaned in the moderate
baseline?" routed correctly to `coverage` and then reported on all 1,408
in-scope requirements, under a heading reading "scope: all controls". The
scope was not misread — it was never carried.

That is the failure this project exists to avoid: a confidently wrong answer
to a narrower question than the one asked, with nothing on screen to show it
happened. Typing `/coverage moderate` by hand always worked, which is what
made it invisible.
"""

from __future__ import annotations

import json

from policyforge.llm.base import LLMResponse
from policyforge.zardoz.skills import (
    NO_SKILL,
    SKILLS,
    Routed,
    route,
    route_with_arguments,
    skill_tools,
)


class Router:
    """A provider answering the two calls routing now makes.

    First the routing decision, then — only for a skill that declares
    arguments — how to narrow it. Two calls rather than one is the whole
    design: see MEASUREMENTS.md epoch 14 for why combining them made routing
    measurably worse.
    """

    def __init__(self, analysis: str, arguments: dict | None = None, *, schema: bool = True):
        self.analysis = analysis
        self.arguments = arguments or {}
        self._schema = schema
        self.json_calls = 0

    def supports_schema(self) -> bool:
        return self._schema

    def generate_json(self, **kwargs) -> LLMResponse:
        self.json_calls += 1
        first = self.json_calls == 1
        payload = {"analysis": self.analysis} if first else self.arguments
        return LLMResponse(text=json.dumps(payload), model="test")

    def generate(self, **kwargs) -> LLMResponse:
        return LLMResponse(text=self.analysis, model="test")


# ---- the bug ------------------------------------------------------------


def test_a_named_baseline_reaches_the_analysis():
    routed = route_with_arguments(
        "which controls are orphaned in the moderate baseline?",
        Router("coverage", {"baseline": "moderate"}),
    )
    assert routed.skill == "coverage"
    assert routed.arguments == {"baseline": "moderate"}
    # And in the form `_coverage` already parses.
    assert routed.as_args() == ["moderate"]


def test_a_question_with_no_scope_fills_nothing():
    """Inventing a scope is worse than the bug it would paper over."""
    routed = route_with_arguments("which controls are orphaned?", Router("coverage"))
    assert routed.arguments == {}
    assert routed.as_args() == []


def test_a_multi_word_argument_survives_the_round_trip():
    routed = route_with_arguments(
        "what does IT Asset Management own?",
        Router("bundle", {"owner": "IT Asset Management"}),
    )
    assert routed.as_args() == ["IT", "Asset", "Management"]


def test_arguments_for_another_skill_are_discarded():
    """A model that fills the wrong skill's field must not reach `run`."""
    routed = route_with_arguments(
        "which controls are orphaned in the high baseline?",
        Router("coverage", {"baseline": "high", "owner": "Security", "requirement": "AC-2"}),
    )
    assert routed.arguments == {"baseline": "high"}


def test_an_empty_argument_is_treated_as_absent():
    routed = route_with_arguments("anything orphaned?", Router("coverage", {"baseline": ""}))
    assert routed.arguments == {}


def test_ordered_arguments_come_back_in_the_order_run_expects():
    """`_history` reads args[0] as tier and args[1] as name."""
    routed = route_with_arguments(
        "what versions of the access control standard are recorded?",
        Router("history", {"name": "access-control", "tier": "standard"}),
    )
    assert routed.as_args() == ["standard", "access-control"]


# ---- nothing else moved -------------------------------------------------


def test_route_still_returns_a_name():
    """The routing eval suite grades which analysis a question reaches, and
    that question is unchanged by any of this."""
    assert route("anything orphaned?", Router("coverage")) == "coverage"


def test_an_off_list_choice_is_still_documents():
    assert route_with_arguments("hello", Router("invented")).skill == NO_SKILL


def test_a_provider_that_cannot_be_held_to_a_schema_still_routes():
    """Most local models cannot, and a router that only worked on hosted
    models would be worse than the one that was already here."""
    routed = route_with_arguments("which controls are orphaned?", Router("coverage", schema=False))
    assert routed.skill == "coverage"
    assert routed.arguments == {}


def test_no_provider_falls_back_to_the_keyword_router():
    assert route_with_arguments("which controls does nobody own?").skill == "coverage"
    assert route_with_arguments("what is our review cadence?").skill == NO_SKILL


def test_a_failing_router_does_not_lose_the_question():
    class Broken:
        def supports_schema(self) -> bool:
            return True

        def generate_json(self, **kwargs):
            raise RuntimeError("upstream refused")

        def generate(self, **kwargs):
            raise RuntimeError("upstream refused")

    assert route_with_arguments("which controls does nobody own?", Broken()).skill == "coverage"


# ---- the tool definitions ----------------------------------------------


def test_every_skill_is_reachable_as_a_tool():
    """Generated from the registry, unlike the MCP list, because a skill
    missing here is a feature nobody can route to."""
    assert {tool["name"] for tool in skill_tools()} == set(SKILLS)


def test_tool_schemas_are_closed_and_describe_their_arguments():
    for tool in skill_tools():
        schema = tool["input_schema"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == [], f"{tool['name']} makes an argument mandatory"
        for name, spec in schema["properties"].items():
            assert spec.get("description"), f"{tool['name']}.{name} has no description"


def test_declared_arguments_match_what_the_skills_parse():
    """A declared argument the run function ignores is a promise nobody keeps."""
    assert set(SKILLS["coverage"].arguments) == {"baseline"}
    assert set(SKILLS["bundle"].arguments) == {"owner"}
    assert set(SKILLS["addresses"].arguments) == {"requirement"}
    assert set(SKILLS["history"].arguments) == {"tier", "name"}
    # Skills that take no arguments declare none, rather than declaring an
    # argument their run function would silently drop.
    for name in ("drift", "check", "frameworks", "roles", "hitrust"):
        assert SKILLS[name].arguments == {}, name


def test_routed_is_usable_without_arguments():
    assert Routed("coverage").as_args() == []
    assert not Routed(NO_SKILL).ran


# ---- invention is impossible, not merely discouraged --------------------


def test_a_value_the_question_never_named_is_dropped():
    """Measured, not hypothetical.

    On a live sweep glm-5.3-flash invented `baseline: moderate` once in ten
    and deepseek-v4-flash three times — always "moderate", always on a
    question naming no baseline. The prompt already asked them not to.
    """
    routed = route_with_arguments(
        "is any control claimed by two teams?", Router("coverage", {"baseline": "moderate"})
    )
    assert routed.arguments == {}
    assert routed.as_args() == []


def test_a_value_the_question_did_name_survives():
    routed = route_with_arguments(
        "which controls are orphaned in the moderate baseline?",
        Router("coverage", {"baseline": "moderate"}),
    )
    assert routed.arguments == {"baseline": "moderate"}


def test_matching_ignores_case_and_spacing():
    routed = route_with_arguments(
        "what is uncovered in the   Moderate  baseline?",
        Router("coverage", {"baseline": "moderate"}),
    )
    assert routed.arguments == {"baseline": "moderate"}


def test_a_value_embedded_in_a_longer_word_still_counts():
    """ "the highest impact level" legitimately yields "high"."""
    routed = route_with_arguments(
        "which controls are orphaned at the highest impact level?",
        Router("coverage", {"baseline": "high"}),
    )
    assert routed.arguments == {"baseline": "high"}


def test_a_team_name_the_model_knew_but_the_asker_did_not_type_is_dropped():
    """The deliberate loss, and the right trade.

    A bundle for the wrong team is worse than a prompt for the right one.
    """
    routed = route_with_arguments(
        "what does the IAM team own?", Router("bundle", {"owner": "IAM Engineering"})
    )
    assert routed.arguments == {}
