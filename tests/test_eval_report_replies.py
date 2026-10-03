"""The eval report says, per run, why each reply stopped and how long it was.

The truncation re-measure had one `length` stop in 200 calls and no way to
tell whether it was the run that failed: the harness recorded pass or fail
and the ledger recorded stop reasons, and nothing joined them. Now the meter
keeps every reply's stop reason and output tokens, `run_case` attaches this
run's slice to its outcome, and the report prints them on a failure and
counts the runs that had a cut-off reply.
"""

from __future__ import annotations

from evals import runner
from evals.provider import Metered
from policyforge.llm.base import LLMResponse


class Scripted:
    """Replies in order, each with its own stop reason and length."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def generate(self, **kwargs):
        stop, tokens, text = self.replies.pop(0)
        return LLMResponse(text=text, model="m", stop_reason=stop, output_tokens=tokens)

    def check(self):
        return True


def _probe(case, provider, corpora):
    from policyforge.llm import effort

    response = effort.call(provider, system="S", prompt=case["question"], max_tokens=64)
    return runner.Outcome(response.text == "right", f"got {response.text!r}", response.text)


def test_the_meter_keeps_every_replys_stop_reason_and_length():
    metered = Metered(Scripted(("stop", 44, "a"), ("length", 1019, "b")))

    metered.generate(system="S", prompt="P")
    metered.generate(system="S", prompt="P")

    assert metered.replies == [("stop", 44, "m"), ("length", 1019, "m")]


def test_each_run_gets_only_its_own_replies(monkeypatch):
    monkeypatch.setitem(runner.SUITES, "probe", _probe)
    metered = Metered(
        Scripted(("stop", 40, "right"), ("length", 64, "wro"), ("stop", 128, "right"))
    )

    result = runner.run_case("probe", {"name": "c", "question": "q?"}, metered, repeat=2)

    # The second run's reply was cut off and retried at a larger budget by
    # effort.call; that run therefore holds two replies, and passed.
    assert [len(o.replies) for o in result.outcomes] == [1, 2]
    assert [o.passed for o in result.outcomes] == [True, True]
    assert result.outcomes[0].replies == [("stop", 40, "m")]
    assert result.outcomes[1].replies[0] == ("length", 64, "m")
    assert result.outcomes[1].cut_off is True
    assert result.outcomes[0].cut_off is False


def test_a_run_without_a_meter_is_graded_the_same(monkeypatch):
    """A bare provider has no `replies`; nothing breaks and nothing is attached."""
    monkeypatch.setitem(runner.SUITES, "probe", _probe)

    bare = Scripted(("stop", 1, "right"))
    result = runner.run_case("probe", {"name": "c", "question": "q?"}, bare)

    assert result.passes == 1
    assert result.outcomes[0].replies == []


def test_the_report_shows_a_failed_runs_replies_and_counts_cut_off_runs():
    cut = [("length", 1019, "m")]
    failed = runner.Outcome(False, "missing ['quarterly']", "half an", replies=cut)
    passed = runner.Outcome(True, "", "right", replies=[("stop", 40, "m")])
    results = [
        runner.CaseResult(suite="answering", name="a-cut-off-case", outcomes=[failed, passed]),
        runner.CaseResult(suite="answering", name="a-clean-case", outcomes=[passed, passed]),
    ]

    report = runner.format_report(results, repeat=2, requested=["answering"])

    assert "replies: stop=length out=1019" in report
    assert "1 run(s) had a reply cut off at its budget" in report


def test_a_clean_report_says_nothing_about_cut_off_runs():
    passed = runner.Outcome(True, "", "right", replies=[("stop", 40, "m")])
    results = [runner.CaseResult(suite="answering", name="a-clean-case", outcomes=[passed])]

    report = runner.format_report(results, repeat=1, requested=["answering"])

    assert "cut off" not in report
    assert "replies:" not in report
