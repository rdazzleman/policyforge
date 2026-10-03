"""Output tokens the operator paid for and cannot read are recorded.

A reasoning model bills for tokens it never returns. Measured on a live
Gemini call: 842 such tokens beside 813 returned ones, and on a
schema-constrained call, 292 beside 12. Before this field existed the
ledger recorded only the returned half, so every cost figure produced
through such a provider was a floor presented as a total — silently, and
by a ratio that varied per call.

The distinction these tests exist to protect is None versus zero. Zero
means a provider that reports hidden tokens saw none on this call. None
means the provider cannot tell, which is every provider here but Gemini.
Collapsing them would make a ledger built from providers that cannot
report read as though nothing were hidden, which is the false-completeness
this project refuses everywhere else.
"""

from __future__ import annotations

from policyforge.llm.base import LLMResponse
from policyforge.llm.ledger import CallRecord, Totals, format_summary


def _record(**kwargs) -> CallRecord:
    base = {
        "timestamp": "2026-09-18T12:00:00Z",
        "provider": "gemini",
        "provider_class": "third-party",
        "model": "m",
        "subject": "s",
        "site": "generate",
        "input_tokens": 10,
        "output_tokens": 20,
    }
    return CallRecord(**{**base, **kwargs})


# ---- the field ----------------------------------------------------------


def test_the_default_is_unknown_not_none_of_them():
    """A provider that says nothing must not be read as saying zero."""
    assert LLMResponse(text="x", model="m").hidden_output_tokens is None
    assert _record().hidden_output_tokens is None


def test_zero_survives_as_zero():
    """A provider that reports the count and saw none is a different fact
    from one that cannot report, and both must reach the ledger intact."""
    assert LLMResponse(text="x", model="m", hidden_output_tokens=0).hidden_output_tokens == 0
    assert _record(hidden_output_tokens=0).hidden_output_tokens == 0


def test_the_measured_shape_round_trips():
    """The live numbers, so a future change that quietly caps or rounds the
    field fails against the case it was built from."""
    response = LLMResponse(
        text="A security control is...",
        model="gemini-3.6-flash",
        input_tokens=7,
        output_tokens=813,
        hidden_output_tokens=842,
    )

    assert response.hidden_output_tokens == 842
    assert response.output_tokens == 813


# ---- the totals ---------------------------------------------------------


def test_totals_sum_hidden_tokens_and_say_they_were_reported():
    totals = Totals()
    totals.add(_record(hidden_output_tokens=842))
    totals.add(_record(hidden_output_tokens=292))

    assert totals.hidden_output_tokens == 1134
    assert totals.hidden_reported is True


def test_totals_over_providers_that_cannot_report_stay_unreported():
    """Three calls, none of them able to say. The total is zero and the flag
    is False, so a reader is told "unknown" rather than shown a zero."""
    totals = Totals()
    for _ in range(3):
        totals.add(_record())

    assert totals.hidden_output_tokens == 0
    assert totals.hidden_reported is False


def test_a_mixed_group_reports_what_it_knows():
    """One Gemini call among three others: the total is real but partial,
    and the flag says a number exists rather than that it is complete."""
    totals = Totals()
    totals.add(_record(provider="anthropic"))
    totals.add(_record(provider="gemini", hidden_output_tokens=60))
    totals.add(_record(provider="litellm"))

    assert totals.hidden_output_tokens == 60
    assert totals.hidden_reported is True


def test_a_zero_report_counts_as_reported():
    """The case that separates this from a truthiness check: a provider
    that reported zero has told us something, and `or 0` would lose it."""
    totals = Totals()
    totals.add(_record(hidden_output_tokens=0))

    assert totals.hidden_output_tokens == 0
    assert totals.hidden_reported is True


# ---- the summary a person reads -----------------------------------------


def test_the_summary_shows_hidden_tokens_only_where_some_were_reported():
    with_hidden = format_summary([_record(hidden_output_tokens=842)], by="site")
    without = format_summary([_record()], by="site")

    assert "hidden" in with_hidden
    assert "842" in with_hidden
    assert "hidden" not in without, (
        "a column of zeros against providers that cannot report is the "
        "false zero this field exists to avoid"
    )
