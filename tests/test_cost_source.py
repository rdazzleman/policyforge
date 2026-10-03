"""Whether a recorded cost was billed by the provider or estimated (#87).

80's ruling on #87: every ledger row records `cost_source`, `provider` or
`estimate`; an old row without it reads as unknown, never `provider`; no
display says "billed" for an estimated row; and a total that mixes the two
shows both parts. 1d's read list (#87) is the spec for these tests.

The measurement behind it (b5, #79, 1732): on OpenRouter, a row's `cost_usd`
is the vendor's own figure (1,883 epoch-25 rows match neither LiteLLM map).
In LiteLLM 1.103.0 only OpenRouter sets the provider-cost header on a chat
reply; on every other route LiteLLM fills the cost from its model map.
"""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.llm import escalation, ledger
from policyforge.llm.base import COST_ESTIMATE, COST_PROVIDER, combined_source
from policyforge.llm.litellm_provider import LiteLLMProvider

ROOT = Path(__file__).resolve().parents[1]
HEADER = "llm_provider-x-litellm-response-cost"


def _reply(text, finish="stop", *, cost, reported, rid="gen-1", completion=10):
    """A LiteLLM reply shaped as the provider reads one. `reported` puts the
    cost where OpenRouter's reply carries it (the hidden header LiteLLM's cost
    calculator reads first); without it, LiteLLM priced the call itself."""
    hidden = {"response_cost": cost}
    if reported:
        hidden["additional_headers"] = {HEADER: cost}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=completion),
        id=rid,
        model="m",
        _hidden_params=hidden,
    )


class _Completion:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, **payload):
        self.calls.append(payload)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


@pytest.fixture(autouse=True)
def _known_price(monkeypatch):
    monkeypatch.setattr(escalation, "_price", lambda model: (2e-06, 1e-05, "test-price"))


def _provider(*replies):
    return LiteLLMProvider(model="openrouter/some/model", completion=_Completion(*replies))


# ---- where the source is decided -------------------------------------------


def test_a_reply_carrying_the_vendors_cost_is_provider():
    response = _provider(_reply("ok", cost=0.012, reported=True)).generate(system="s", prompt="p")
    assert (response.cost_usd, response.cost_source) == (0.012, COST_PROVIDER)


def test_a_reply_priced_by_litellm_alone_is_an_estimate():
    """The shape every non-OpenRouter LiteLLM route returns: a `response_cost`
    LiteLLM computed from its map, and no provider header."""
    response = _provider(_reply("ok", cost=0.012, reported=False)).generate(system="s", prompt="p")
    assert (response.cost_usd, response.cost_source) == (0.012, COST_ESTIMATE)


def test_a_reply_with_no_cost_has_no_source():
    response = _provider(_reply("ok", cost=None, reported=False)).generate(system="s", prompt="p")
    assert (response.cost_usd, response.cost_source) == (None, None)


def test_a_real_litellm_response_is_read_the_same_way():
    """The same decision on LiteLLM's own `ModelResponse`, not a stand-in."""
    litellm = pytest.importorskip("litellm")
    for reported, want in ((True, COST_PROVIDER), (False, COST_ESTIMATE)):
        response = litellm.ModelResponse(
            model="m", choices=[{"message": {"role": "assistant", "content": "ok"}}]
        )
        response._hidden_params = {"response_cost": 0.02}
        if reported:
            response._hidden_params["additional_headers"] = {HEADER: 0.02}
        got = _provider(response).generate(system="s", prompt="p")
        assert got.cost_source == want, reported


#: What LiteLLM puts in `additional_headers` on an ordinary reply: the
#: vendor's response headers, prefixed. Present on every route, OpenRouter or
#: not, so their presence says nothing about who priced the call (9b on #93).
ORDINARY_HEADERS = {
    "llm_provider-x-ratelimit-remaining-requests": "99",
    "llm_provider-content-type": "application/json",
}


def test_ordinary_response_headers_do_not_make_a_cost_billed():
    """An estimate with other headers present stays an estimate: only the
    provider-cost header decides. A stand-in first, then LiteLLM's own type."""
    reply = _reply("ok", cost=0.012, reported=False)
    reply._hidden_params["additional_headers"] = dict(ORDINARY_HEADERS)
    got = _provider(reply).generate(system="s", prompt="p")
    assert got.cost_source == COST_ESTIMATE

    litellm = pytest.importorskip("litellm")
    response = litellm.ModelResponse(
        model="m", choices=[{"message": {"role": "assistant", "content": "ok"}}]
    )
    response._hidden_params = {"response_cost": 0.02, "additional_headers": dict(ORDINARY_HEADERS)}
    assert _provider(response).generate(system="s", prompt="p").cost_source == COST_ESTIMATE


def test_a_vendor_reported_zero_is_still_billed():
    """A header of 0.0 is the vendor saying the call cost nothing, which is a
    bill of $0, not an absence: `is not None`, never truthiness."""
    reply = _reply("ok", cost=0.0, reported=True)
    got = _provider(reply).generate(system="s", prompt="p")
    assert (got.cost_usd, got.cost_source) == (0.0, COST_PROVIDER)


def test_a_re_send_billed_by_the_vendor_once_and_estimated_once_is_an_estimate():
    """The 8x path sums both attempts into one row: a bill only if both were."""
    provider = _provider(
        _reply("", "length", cost=0.2, reported=True, rid="a"),
        _reply("# Doc", "stop", cost=0.3, reported=False, rid="b"),
    )
    response = provider.generate(system="s", prompt="p", max_tokens=1000)
    assert response.cost_usd == pytest.approx(0.5)
    assert response.cost_source == COST_ESTIMATE
    escalation.take()


def test_combined_source_is_a_bill_only_when_every_part_is():
    assert combined_source(COST_PROVIDER, COST_PROVIDER) == COST_PROVIDER
    assert combined_source(COST_PROVIDER, COST_ESTIMATE) == COST_ESTIMATE
    assert combined_source(COST_PROVIDER, None) == COST_PROVIDER
    assert combined_source(None, None) is None


def test_no_other_provider_claims_a_source():
    """1d's read list: what each non-LiteLLM provider writes, from the code.
    Only the LiteLLM provider reports a cost at all (base.py says so); this
    reads every module under llm/ and fails if another one starts setting a
    `cost_source`, so a new reporting provider has to be decided, not
    inherited."""
    allowed = {
        "litellm_provider.py",  # decides it
        "base.py",  # carries it
        "_inline_thinking.py",  # carries it on the exhausted exception
        "cascade_provider.py",  # merges the two halves'
        "ledger.py",  # records it
        "escalation.py",  # labels a re-send's figure
        "effort.py",  # passes a first attempt's on
    }
    setters = []
    for path in sorted((ROOT / "src" / "policyforge" / "llm").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            passed = isinstance(node, ast.keyword) and (node.arg or "").endswith("cost_source")
            assigned = (
                isinstance(node, ast.Attribute)
                and node.attr == "cost_source"
                and isinstance(node.ctx, ast.Store)
            )
            if passed or assigned:
                setters.append(path.name)
    assert set(setters) <= allowed, sorted(set(setters) - allowed)
    assert "litellm_provider.py" in setters, "the premise: the decision is found"


# ---- the ledger ---------------------------------------------------------------


def _wrapped(tmp_path, *replies):
    return ledger.RecordingProvider(
        _provider(*replies),
        provider_name="litellm",
        provider_class="cloud",
        path=tmp_path / "c.jsonl",
    )


def test_the_row_records_the_source_and_reads_it_back(tmp_path):
    _wrapped(tmp_path, _reply("ok", cost=0.012, reported=False)).generate(system="s", prompt="p")
    (row,) = [json.loads(line) for line in (tmp_path / "c.jsonl").read_text().splitlines()]
    assert row["cost_source"] == COST_ESTIMATE
    (record,) = ledger.load(tmp_path / "c.jsonl")
    assert record.cost_source == COST_ESTIMATE


def test_the_committed_ledgers_read_as_unrecorded_never_as_billed():
    """Every row written before #87 has no `cost_source`. Loaded from the real
    epoch-25 ledgers, they read as unknown, and a total of them is never a
    bare figure that looks like a bill."""
    folder = ROOT / "docs" / "measurements" / "ledgers"
    # The pipeline and eval ledgers; the run record beside them is not a ledger.
    paths = sorted(
        p
        for p in folder.glob("2026-09-28-epoch25-*.jsonl")
        if p.stem.endswith(("-pipeline", "-evals"))
    )
    assert paths, "the premise: the committed ledgers are there"
    records = [r for p in paths for r in ledger.load(p)]
    assert records and all(r.cost_source is None for r in records)
    split = ledger.cost_split(records)
    assert split.unrecorded > 0 and split.billed == 0 and split.estimated == 0
    said = ledger.describe_cost(split)
    assert said.endswith(" of unrecorded source") and "billed" not in said


# ---- every display of recorded spend ------------------------------------------
#
# Enumerated from the code (#87): the run report (`ledger.run_summary`, printed
# by `synthesize` and `generate`), `model-log` (`ledger.Totals.cost`), the
# re-send notice's first-attempt figure (`escalation.announce`), the warning
# for a re-send no row recorded (`escalation.end`), and the provenance stamp in
# every generated document (`ledger.Scope.provenance`).


def _row(cost, source, **kw):
    return ledger.CallRecord(
        "t", "litellm", "cloud", "m", None, None, cost_usd=cost, cost_source=source, **kw
    )


BILLED, ESTIMATED, OLD = _row(0.25, COST_PROVIDER), _row(0.10, COST_ESTIMATE), _row(0.05, None)


def _totals(*rows):
    totals = ledger.Totals()
    for row in rows:
        totals.add(row)
    return totals.cost


def test_an_openrouter_row_stays_billed_with_its_figure_unchanged():
    """80's passing arm."""
    assert ledger.run_summary([BILLED]) == "Run: 1 request(s), $0.2500; 0 re-send(s)."
    assert _totals(BILLED) == "$0.2500"
    assert ledger.cost_parts([BILLED]) == (pytest.approx(0.25), 0)


def test_an_estimated_row_is_never_said_billed():
    """80's first arm, at every display."""
    assert "$0.1000 estimated" in ledger.run_summary([ESTIMATED])
    assert _totals(ESTIMATED) == "$0.1000 estimated"
    out = io.StringIO()
    escalation.announce(
        model="m", first_max_tokens=1000, max_tokens=8000, input_tokens=10,
        first_cost_usd=0.1, first_cost_source=COST_ESTIMATE, out=out,
    )  # fmt: skip
    said = out.getvalue()
    assert "$0.1000 estimated" in said and "$0.1000, billed" not in said
    escalation.take()


def test_an_old_row_is_never_read_as_billed():
    """80's second arm."""
    assert _totals(OLD) == "$0.0500 of unrecorded source"
    assert ledger.run_summary([OLD]) == (
        "Run: 1 request(s), $0.0500 of unrecorded source; 0 re-send(s)."
    )


def test_a_mixed_total_shows_both_parts():
    """80's third arm: never the sum said as though it were all billed."""
    assert _totals(BILLED, ESTIMATED) == "$0.2500 billed + $0.1000 estimated"
    assert ledger.run_summary([BILLED, ESTIMATED, OLD]) == (
        "Run: 3 request(s), $0.2500 billed + $0.1000 estimated + $0.0500 of unrecorded "
        "source; 0 re-send(s)."
    )
    unpriced = ledger.CallRecord("t", "litellm", "cloud", "m", None, None)
    assert _totals(BILLED, ESTIMATED, unpriced) == (
        "($0.2500 billed + $0.1000 estimated) + 1 unpriced"
    )


def test_a_re_sends_replaced_attempts_are_labelled_by_source():
    row = _row(
        0.5, COST_ESTIMATE, request_id="b",
        escalations=(
            {"first_request_id": "a", "first_cost_usd": 0.2, "first_cost_source": COST_ESTIMATE},
        ),
    )  # fmt: skip
    said = ledger.run_summary([row])
    assert "replaced attempts cost $0.2000 estimated of that" in said
    assert "billed $0.2000 estimated" not in said, "no 'billed' beside an estimate (1d)"


def test_a_row_of_unknown_total_splits_its_known_parts_by_source():
    """`cost_split` reaches a re-send's first attempt only on a row whose
    total is unknown (#382): there each known part keeps its own source."""
    row = ledger.CallRecord(
        "t", "litellm", "cloud", "m", None, None,
        request_id="b", last_cost_usd=0.3, cost_source=COST_PROVIDER,
        escalations=(
            {"first_request_id": "a", "first_cost_usd": 0.2, "first_cost_source": COST_ESTIMATE},
        ),
    )  # fmt: skip
    split = ledger.cost_split([row])
    assert (split.billed, split.estimated, split.unpriced) == (
        pytest.approx(0.3),
        pytest.approx(0.2),
        0,
    )


def test_a_re_send_no_row_recorded_labels_its_figure(capsys):
    token = escalation.begin()
    escalation.announce(
        model="m", first_max_tokens=1000, max_tokens=8000,
        first_cost_usd=0.2, first_cost_source=COST_ESTIMATE, out=io.StringIO(),
    )  # fmt: skip
    out = io.StringIO()
    escalation.end(token, out=out)
    assert "$0.2000 estimated" in out.getvalue()


def test_the_stamp_always_says_its_estimated_part(tmp_path):
    """Written 0.0 when nothing was estimated, so a stamp WITHOUT the key is
    one from before #87, not one that was all billed."""
    provider = _wrapped(tmp_path, _reply("ok", cost=0.012, reported=True))
    with ledger.about("policy/x", site="generate") as scope:
        provider.generate(system="s", prompt="p")
    assert scope.provenance()["cost_estimated_usd"] == 0.0

    provider = _wrapped(tmp_path, _reply("ok", cost=0.012, reported=False))
    with ledger.about("policy/y", site="generate") as scope:
        provider.generate(system="s", prompt="p")
    stamp = scope.provenance()
    assert stamp["cost_estimated_usd"] == pytest.approx(0.012)
    assert stamp["cost_usd"] == pytest.approx(0.012), "the total is still the total"


# ---- #97: "billed" only beside a figure the vendor reported ------------------


def _notice(cost, source):
    out = io.StringIO()
    escalation.announce(
        model="m", first_max_tokens=1000, max_tokens=8000, input_tokens=10,
        first_request_id="a", first_cost_usd=cost, first_cost_source=source, out=out,
    )  # fmt: skip
    escalation.take()
    return out.getvalue()


def _unrecorded_resend(cost, source):
    token = escalation.begin()
    escalation.announce(
        model="m", first_max_tokens=1000, max_tokens=8000,
        first_request_id="a", first_cost_usd=cost, first_cost_source=source, out=io.StringIO(),
    )  # fmt: skip
    out = io.StringIO()
    escalation.end(token, out=out)
    return out.getvalue()


@pytest.mark.parametrize("source", [COST_ESTIMATE, None])
def test_no_billed_beside_an_estimated_or_unrecorded_figure(source):
    """80's ruling on #97, 1d's arm: neither message says "billed" anywhere
    when the figure it gives is an estimate, or has no recorded source. The
    whole message is checked, not just the parenthesis."""
    for said in (_notice(0.0123, source), _unrecorded_resend(0.0123, source)):
        assert "$0.0123" in said, "the premise: the figure is shown"
        assert "billed" not in said, said


def test_the_notice_and_the_run_summary_say_it_the_same_way():
    """#93 made the run summary say "cost $X estimated"; the notice agrees."""
    assert "(paid for; cost $0.0123 estimated)" in _notice(0.0123, COST_ESTIMATE)
    assert "cost $0.0123 estimated." in _unrecorded_resend(0.0123, COST_ESTIMATE)


@pytest.mark.parametrize("cost, source", [
    (0.0123, COST_PROVIDER), (0.0123, COST_ESTIMATE), (0.0123, None), (None, None),
])  # fmt: skip
def test_every_message_says_the_first_attempt_was_paid_for(cost, source):
    """80's ruling on #97: the attempt was charged whatever its figure is, so
    every branch says so, "billed" beside a reported or unknown amount and
    "paid for" beside an estimated or unrecorded one (ba on #98)."""
    for said in (_notice(cost, source), _unrecorded_resend(cost, source)):
        assert "billed" in said or "paid for" in said or "paid-for" in said, said


def test_a_vendor_reported_figure_keeps_billed_unchanged():
    """The passing arm: provider-reported figures read exactly as before."""
    assert "($0.0123, billed)" in _notice(0.0123, COST_PROVIDER)
    assert "its billed first attempt was request a, $0.0123." in _unrecorded_resend(
        0.0123, COST_PROVIDER
    )


def test_an_attempt_of_unknown_cost_is_still_said_billed():
    """No figure at all: the attempt was charged, amount unknown. "billed"
    stands there, since nothing estimated sits beside it."""
    assert "(billed)" in _notice(None, None)
    assert "its billed first attempt was request a, cost unknown." in _unrecorded_resend(None, None)
