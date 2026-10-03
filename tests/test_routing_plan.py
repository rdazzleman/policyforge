"""A question that asks for two analyses gets both, and no question gets a spurious one.

The risk this is built around is not a missed second report — that is a
follow-up question. It is an unwanted one: an extra report attached to an
ordinary question, burying the answer somebody asked for. So most of these
pin the conservative edges, and the routing decision itself is checked to be
exactly what it was.
"""

from __future__ import annotations

import json

from policyforge.llm.base import LLMResponse, SchemaReplyError
from policyforge.zardoz.skills import (
    MAX_ANALYSES,
    NO_SKILL,
    Routed,
    route_plan,
    route_with_arguments,
)


class Planner:
    """Answers each routing call by the schema it was asked with."""

    def __init__(self, analysis, also="none", arguments=None, *, schema=True, fail_also=False):
        self.analysis = analysis
        self.also = also
        self.arguments = arguments or {}
        self._schema = schema
        self.fail_also = fail_also
        self.asked: list[str] = []

    def supports_schema(self):
        return self._schema

    def generate_json(self, **kwargs):
        name = kwargs["schema"]["json_schema"]["name"]
        self.asked.append(name)
        if name == "routing":
            payload = {"analysis": self.analysis}
        elif name == "also":
            if self.fail_also:
                raise RuntimeError("upstream refused")
            payload = {"also": self.also}
        else:
            payload = self.arguments
        return LLMResponse(text=json.dumps(payload), model="test")

    def generate(self, **kwargs):
        self.asked.append("prose")
        return LLMResponse(text=self.analysis, model="test")


def names(plan):
    return [routed.skill for routed in plan]


def test_a_compound_question_runs_both_analyses():
    plan = route_plan(
        "which controls does nobody own, and how many parameters are undecided?",
        Planner("coverage", also="parameters"),
    )
    assert names(plan) == ["coverage", "parameters"]


def test_a_single_question_runs_one():
    plan = route_plan("which controls does nobody own?", Planner("coverage", also="none"))
    assert names(plan) == ["coverage"]


def test_the_first_analysis_is_exactly_what_routing_chose_alone():
    """Chaining adds; it never changes the decision that measures 100%."""
    question = "which controls does nobody own, and did the catalog change?"
    alone = route_with_arguments(question, Planner("coverage"))
    planned = route_plan(question, Planner("coverage", also="drift"))
    assert planned[0] == alone


def test_a_document_question_is_never_chained():
    """Passages are not a report to set another beside."""
    planner = Planner(NO_SKILL, also="coverage")
    plan = route_plan("what is our access review cadence?", planner)
    assert names(plan) == [NO_SKILL]
    assert "also" not in planner.asked


def test_without_a_schema_nothing_is_chained():
    """The second choice would be a word parsed from prose."""
    planner = Planner("coverage", also="parameters", schema=False)
    assert names(route_plan("orphans and parameters?", planner)) == ["coverage"]
    assert "also" not in planner.asked


def test_without_a_model_nothing_is_chained():
    assert names(route_plan("which controls does nobody own?")) == ["coverage"]


def test_a_failed_second_call_costs_the_second_report_not_the_first():
    plan = route_plan(
        "orphans and parameters?", Planner("coverage", also="parameters", fail_also=True)
    )
    assert names(plan) == ["coverage"]


def test_a_failure_with_no_reply_is_not_asked_again():
    """A timeout or refusal has nothing to recover; a prose retry only doubles the wait."""
    planner = Planner("coverage", also="parameters", fail_also=True)
    route_plan("orphans and parameters?", planner)
    assert "prose" not in planner.asked


def test_an_answer_off_the_list_is_none():
    """`strict` should prevent it; nothing inside can tell whether it did."""
    assert names(route_plan("orphans?", Planner("coverage", also="invented"))) == ["coverage"]


def test_the_first_analysis_cannot_be_chosen_again():
    assert names(route_plan("orphans?", Planner("coverage", also="coverage"))) == ["coverage"]


def test_the_second_analysis_gets_its_own_grounded_arguments():
    """Same rule as the first: a value must appear in the question."""
    plan = route_plan(
        "which controls are orphaned, and where do we address AC-2?",
        Planner("coverage", also="addresses", arguments={"requirement": "AC-2"}),
    )
    assert plan[1] == Routed("addresses", {"requirement": "AC-2"})


def test_an_invented_argument_on_the_second_analysis_is_dropped():
    plan = route_plan(
        "which controls are orphaned, and what does a team own?",
        Planner("coverage", also="bundle", arguments={"owner": "IAM Engineering"}),
    )
    assert plan[1] == Routed("bundle", {})


def test_never_more_than_the_cap():
    assert MAX_ANALYSES == 2
    plan = route_plan("a and b and c?", Planner("coverage", also="parameters"))
    assert len(plan) <= MAX_ANALYSES


def test_the_shell_prints_each_report_under_its_own_command(monkeypatch):
    """Two untouched reports, never merged into one account."""
    from policyforge.zardoz import skills
    from policyforge.zardoz.shell import ShellState, dispatch

    monkeypatch.setitem(
        skills.SKILLS,
        "coverage",
        skills.SKILLS["coverage"].__class__(
            **{**skills.SKILLS["coverage"].__dict__, "run": lambda state, args: "COVERAGE REPORT"}
        ),
    )
    monkeypatch.setitem(
        skills.SKILLS,
        "parameters",
        skills.SKILLS["parameters"].__class__(
            **{
                **skills.SKILLS["parameters"].__dict__,
                "run": lambda state, args: "PARAMETERS REPORT",
            }
        ),
    )
    state = ShellState(provider=Planner("coverage", also="parameters"))
    output = dispatch(
        "which controls does nobody own, and how many parameters are undecided?", state
    )

    assert "(ran /coverage)" in output
    assert "(ran /parameters)" in output
    assert output.index("COVERAGE REPORT") < output.index("(ran /parameters)")
    assert output.index("(ran /parameters)") < output.index("PARAMETERS REPORT")


class BareWordPlanner(Planner):
    """A schema-capable model that sometimes drops the JSON wrapper.

    Measured on glm-5.3-flash through OpenRouter: asked for {"also": ...} it
    occasionally replied with the bare word. The provider raises on that, and
    the second analysis used to vanish with it.
    """

    def __init__(self, analysis, also="none", *, reply=None):
        super().__init__(analysis, also=also)
        self.reply = also if reply is None else reply

    def generate_json(self, **kwargs):
        if kwargs["schema"]["json_schema"]["name"] == "also":
            self.asked.append("also")
            raise SchemaReplyError(f"returned something else: {self.reply!r}", text=self.reply)
        return super().generate_json(**kwargs)

    def generate(self, **kwargs):
        self.asked.append("prose")
        return LLMResponse(text=f"{self.also}.", model="test")


def test_a_bare_word_reply_to_the_schema_call_is_read_without_asking_again():
    planner = BareWordPlanner("coverage", also="parameters")
    plan = route_plan(
        "which controls does nobody own, and how many parameters are undecided?", planner
    )
    assert names(plan) == ["coverage", "parameters"]
    assert "prose" not in planner.asked


def test_an_unreadable_schema_reply_is_asked_once_in_prose():
    planner = BareWordPlanner("coverage", also="parameters", reply='{"also": "param')
    plan = route_plan(
        "which controls does nobody own, and how many parameters are undecided?", planner
    )
    assert names(plan) == ["coverage", "parameters"]
    assert planner.asked.count("prose") == 1


def test_the_bare_word_still_cannot_invent_an_analysis():
    planner = BareWordPlanner("coverage", also="delete_everything")
    assert names(route_plan("orphans?", planner)) == ["coverage"]


def test_the_prose_fallback_still_cannot_invent_an_analysis():
    planner = BareWordPlanner("coverage", also="delete_everything", reply="{garbled")
    assert names(route_plan("orphans?", planner)) == ["coverage"]
    assert planner.asked.count("prose") == 1


def test_a_bare_none_is_none():
    planner = BareWordPlanner("coverage", also="none")
    assert names(route_plan("which controls does nobody own?", planner)) == ["coverage"]
    assert "prose" not in planner.asked
