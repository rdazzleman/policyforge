"""Every synthesis case still describes the topic the model would be given (#8).

The synthesis eval refuses a case whose anchors no longer supply the controls
and frameworks it states, before calling any model. That check lived only
inside the eval, which calls a model, so it never ran in `check.py`: GH#332
carried NIST's own 800-171 links, two of three cases went from 6 controls to
8, and nothing went red for three days. This runs the same function, with no
model, for every case the production loader reads.
"""

from __future__ import annotations

import pytest

from evals.runner import load_cases, synthesis_input

CASES = load_cases()["synthesis"]


def test_the_suite_has_cases_to_check():
    # A loader returning nothing would make the parametrised test below
    # vanish and this file pass. Three cases today.
    assert len(CASES) >= 3


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_each_case_supplies_what_it_states(case):
    """A failure here is a catalog, anchor or crosswalk change: decide
    whether it was intended, then rewrite the case from the source, not
    from this test's output."""
    _, refusal = synthesis_input(case)
    assert refusal is None, f"{case['name']}: {refusal}"


def test_a_stale_count_is_refused_by_the_same_function():
    """The check can fail: one case with its count off by one."""
    case = {**CASES[0], "expect_controls": CASES[0]["expect_controls"] - 1}
    _, refusal = synthesis_input(case)
    assert refusal and "an anchor or crosswalk change" in refusal


def _reply(topic, drop: str = "") -> str:
    """A model reply citing each supplied control as its catalog names it,
    optionally leaving out one framework's controls."""
    from policyforge.mapping.crosswalk import normalize_framework

    return "\n".join(
        f"- The organization does the synthetic thing. [{c.framework} {c.control_id}]"
        for c in topic.controls
        if normalize_framework(c.framework) != drop
    )


def test_run_synthesis_grades_past_the_input_checks(monkeypatch):
    """Through the whole eval with the model replaced: the checks moved out
    of `run_synthesis` left names it still grades with (`loaded`,
    `expected`), and only a case that PASSES the input checks reaches them.
    Both directions: a reply citing all four frameworks passes, and one
    that drops 800-171 is failed for exactly that."""
    from evals.runner import run_synthesis
    from policyforge.synthesis import merge

    case = CASES[0]
    monkeypatch.setattr(merge, "synthesize_topic", lambda t, provider: _reply(t))
    outcome = run_synthesis(case, provider=None)
    assert outcome.passed, outcome.detail

    monkeypatch.setattr(
        merge, "synthesize_topic", lambda t, provider: _reply(t, drop="nist-800-171")
    )
    outcome = run_synthesis(case, provider=None)
    assert not outcome.passed
    assert "no requirement cites ['nist-800-171']" in outcome.detail


def test_a_missing_framework_is_refused_by_the_same_function():
    """And the framework half: the count right, a framework the topic does
    not supply expected in place of one it does."""
    frameworks = [f for f in CASES[0]["expect_frameworks"] if f != "hipaa"] + ["hitrust-csf"]
    case = {**CASES[0], "expect_frameworks": frameworks}
    _, refusal = synthesis_input(case)
    assert refusal and "the case expects" in refusal
