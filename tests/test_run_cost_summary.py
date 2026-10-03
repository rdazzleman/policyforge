"""A run ends with what it cost, said once (#379), by one rule (80's ruling).

The rule, for this summary, the provenance stamp and `policyforge model-log`'s
totals: a total is a total only when every call is priced; otherwise the
known part and the count of calls of unknown cost are both said, and never
merged. A local model's call is priced at 0.0 by its boundary class.
Measured before this: 6 of 27 session ledgers mixed priced and unpriced calls,
and every total over them presented the known part as the whole.
"""

from __future__ import annotations

import contextlib
import json
import re

import pytest

from policyforge.content.provenance import attribution
from policyforge.llm import escalation, ledger
from policyforge.llm.cascade_provider import CascadeProvider
from tests.test_escalation import PRICE
from tests.test_ledger_billed_failures import (
    ANSWER_C,
    SCENARIOS,
    _litellm,
    _reply,
    _unpriced_exhausting,
)


@pytest.fixture(autouse=True)
def _known_price(monkeypatch):
    monkeypatch.setattr(escalation, "_price", lambda model: PRICE)
    escalation.take()


def _run(provider, path):
    """One call through the recorder, inside a run; the summary it reports."""
    said = []
    wrapped = ledger.RecordingProvider(
        provider, provider_name="t", provider_class="cloud", path=path
    )
    # Some scenarios end in an exhaustion; it is swallowed inside the run.
    with (
        ledger.run(report=said.append) as calls,
        ledger.about("standard/x", site="generate"),
        contextlib.suppress(Exception),
    ):
        wrapped.generate(system="s", prompt="p", max_tokens=100)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    (summary,) = said
    return summary, calls, rows


def _dollars(summary: str) -> float:
    return float(re.search(r"request\(s\), \$([0-9.]+)", summary).group(1))


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_summary_total_is_the_ledgers_and_every_billed_request_is_counted(name, tmp_path):
    """#378's conservation, one level up: over the #366 fakes, the summary's
    total is the sum of the run's row costs, and its request count is every
    billed request id, on the row or in its escalations."""
    factory, billed, ids, _ = SCENARIOS[name]
    summary, calls, rows = _run(factory(), tmp_path / "calls.jsonl")
    assert len(calls) == len(rows)
    assert _dollars(summary) == pytest.approx(sum(r["cost_usd"] for r in rows))
    assert _dollars(summary) == pytest.approx(billed)
    assert f"Run: {len(ids)} request(s)" in summary, summary
    resends = sum(len(r["escalations"]) for r in rows)
    assert f"; {resends} re-send(s)" in summary, summary


def test_a_run_that_fails_still_says_what_it_cost(tmp_path):
    """The exception leaves the run: its bill is still reported, since a run
    that failed after a re-send is the one whose cost most needs saying."""
    from policyforge.llm._inline_thinking import ReasoningBudgetExhausted
    from tests.test_ledger_billed_failures import EMPTY_A, EMPTY_B

    said = []
    wrapped = ledger.RecordingProvider(
        _litellm("m", EMPTY_A, EMPTY_B), provider_name="t", provider_class="cloud",
        path=tmp_path / "c.jsonl",
    )  # fmt: skip
    with pytest.raises(ReasoningBudgetExhausted), ledger.run(report=said.append):
        wrapped.generate(system="s", prompt="p", max_tokens=100)
    assert said == [
        "Run: 2 request(s), $1.5000; 1 re-send(s), whose replaced attempts cost $0.2000 of that."
    ]


def test_a_run_with_no_re_send_says_zero_rather_than_nothing(tmp_path):
    summary, _, _ = _run(
        _litellm("m", _reply("ok", "stop", cost=0.3, rid="g1")), tmp_path / "c.jsonl"
    )
    assert summary == "Run: 1 request(s), $0.3000; 0 re-send(s)."


def test_a_re_send_names_what_the_attempt_it_replaced_billed(tmp_path):
    first = _reply("", "length", cost=0.2, rid="gen-a")
    summary, _, _ = _run(
        _litellm("m", first, _reply("ok", "stop", cost=0.3, rid="gen-b")), tmp_path / "c.jsonl"
    )
    assert summary == (
        "Run: 2 request(s), $0.5000; 1 re-send(s), whose replaced attempts cost $0.2000 of that."
    )


def test_an_unpriced_call_is_counted_not_summed_as_zero(tmp_path, monkeypatch):
    """The cascade #382 fixed: an unpriced primary, then a priced stronger
    model. Its known part is said, and so is the call it cannot price."""
    provider = CascadeProvider(
        primary=_unpriced_exhausting(monkeypatch), escalate_to=_litellm("pro", ANSWER_C)
    )
    summary, _, _ = _run(provider, tmp_path / "c.jsonl")
    assert "$0.5000 known + 2 request(s) of unknown cost" in summary, summary


def test_the_stamp_says_both_parts_and_gives_a_total_only_when_every_call_is_priced():
    priced = ledger.Scope(
        "s", records=[ledger.CallRecord("t", "p", "cloud", "m", None, None, cost_usd=0.2)]
    )
    stamp = priced.provenance()
    assert (stamp["cost_usd"], stamp["cost_known_usd"], stamp["calls_unpriced"]) == (0.2, 0.2, 0)

    mixed = ledger.Scope(
        "s",
        records=[
            ledger.CallRecord("t", "p", "cloud", "m", None, None, cost_usd=0.2),
            ledger.CallRecord("t", "p", "cloud", "m", None, None),
        ],
    )
    stamp = mixed.provenance()
    assert "cost_usd" not in stamp
    # The scope's own total too: not the known 0.2 standing as the whole.
    assert (mixed.cost_usd, mixed.cost_known_usd, mixed.calls_unpriced) == (None, 0.2, 1)
    assert (stamp["cost_known_usd"], stamp["calls_unpriced"]) == (0.2, 1)

    local = ledger.Scope("s", records=[ledger.CallRecord("t", "p", "local", "qwen", None, None)])
    assert (local.provenance()["cost_usd"], local.calls_unpriced) == (0.0, 0)


class _Doc:
    def __init__(self, block):
        self.metadata = {"generated_by": block}
        self.relative_path = "standards/x.md"


def test_an_old_stamp_reads_as_not_recorded_rather_than_zero():
    old = attribution(_Doc({"models": ["m"], "cost_usd": 0.2}))
    assert (old.cost_usd, old.cost_known_usd, old.calls_unpriced) == (0.2, None, None)
    new = attribution(_Doc({"models": ["m"], "cost_known_usd": 0.2, "calls_unpriced": 1}))
    assert (new.cost_usd, new.cost_known_usd, new.calls_unpriced) == (None, 0.2, 1)


def test_an_attempt_that_is_its_own_row_is_not_counted_again_from_the_escalation():
    """`effort`'s 2x path records its cut-off first attempt as a row AND as
    the next row's escalation. Counted once, whether priced or not."""
    first = ledger.CallRecord("t", "p", "cloud", "m", None, None, request_id="a")
    second = ledger.CallRecord(
        "t", "p", "cloud", "m", None, None, request_id="b", last_cost_usd=0.3,
        cost_source="provider",
        escalations=({"first_request_id": "a", "first_cost_usd": None},),
    )  # fmt: skip
    assert ledger.cost_parts([first, second]) == (pytest.approx(0.3), 1)
    totals = ledger.Totals()
    totals.add(first)
    totals.add(second)
    assert (totals.cost_known_usd, totals.calls_unpriced) == (pytest.approx(0.3), 1)
    assert ledger.run_summary([first, second]).startswith("Run: 2 request(s), $0.3000 known + 1")


def test_generate_ends_with_the_run_summary(tmp_path, monkeypatch):
    """Through the command a user runs: the line is printed at the end."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod
    from policyforge.llm.base import LLMResponse

    class _Writer:
        def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
            return LLMResponse(text="# Access Review\n\nStaff must review access.\n", model="fake",
                               stop_reason="stop", cost_usd=0.01, cost_source="provider",
                               request_id="r1")  # fmt: skip

        def check(self):
            return True

    monkeypatch.chdir(tmp_path)
    synthesis = tmp_path / "access-review.md"
    synthesis.write_text("- Access must be reviewed. [NIST AC-2]\n", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"org": {"name": "Acme"}})
    # Wrapped as `get_provider` wraps it when the ledger is on (`ledger.wrap`).
    recorded = ledger.RecordingProvider(
        _Writer(), provider_name="t", provider_class="cloud", path=tmp_path / "calls.jsonl"
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: recorded)
    result = CliRunner().invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis),
         "--history-dir", str(tmp_path / "history")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "Run: 1 request(s), $0.0100; 0 re-send(s)." in result.output, result.output


def test_with_nothing_recorded_it_does_not_claim_no_calls_were_made():
    """The ledger can be off: then calls are made and none is recorded, and
    "no model calls" would be a false zero."""
    summary = ledger.run_summary([])
    assert "no calls recorded in the ledger" in summary
    assert "no model calls" not in summary
