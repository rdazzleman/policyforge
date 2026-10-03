"""A billed request is in the ledger even when its call then fails (#343).

After #366 the success path kept every billed attempt: the row's cost is the
summed cost and the first attempt is in `escalations`. The failure paths lost
money:
- a re-send that came back empty again raised `ReasoningBudgetExhausted`,
  and the row had `cost_usd` None and no request id: the re-send's cost
  (b5 measured the shape at up to $1.30) and its id were recorded nowhere;
- a cascade caught that exhaustion and escalated, so the row carried only
  the stronger model's cost, and the primary's re-send vanished again.

**The conservation check below is the point**: in every scenario, where
every attempt reports a cost, the row's cost is the total the fakes billed,
and every billed request id is on the row or in its escalations. A path
that loses a request fails it. Where an attempt's cost is unknown, the
row's cost is None (the tests after it).
"""

from __future__ import annotations

import json

import pytest

from policyforge.llm import escalation, ledger
from policyforge.llm._inline_thinking import ReasoningBudgetExhausted
from policyforge.llm.base import SchemaReplyError
from policyforge.llm.cascade_provider import CascadeProvider
from policyforge.llm.litellm_provider import LiteLLMProvider
from tests.test_escalation import PRICE, _Completion, _reply


@pytest.fixture(autouse=True)
def _known_price(monkeypatch):
    monkeypatch.setattr(escalation, "_price", lambda model: PRICE)
    # Another test may announce a re-send with no ledger wrapper to collect
    # it; that entry would be taken into the first row recorded here (seen
    # in the full suite, not alone: an id of None among these ids).
    escalation.take()


def _litellm(model, *replies):
    return LiteLLMProvider(model=model, completion=_Completion(*replies))


def _row(provider, path):
    wrapped = ledger.RecordingProvider(
        provider, provider_name="t", provider_class="cloud", path=path
    )
    raised = None
    try:
        with ledger.about("standard/x", site="generate"):
            wrapped.generate(system="s", prompt="p", max_tokens=100)
    except Exception as exc:
        raised = type(exc).__name__
    (row,) = [json.loads(line) for line in path.read_text().splitlines()]
    return row, raised


def _ids(row):
    return {row["request_id"]} | {e["first_request_id"] for e in row["escalations"]}


EMPTY_A = _reply("", "length", cost=0.2, rid="gen-a")
EMPTY_B = _reply("", "length", cost=1.3, completion=800, rid="gen-b")
ANSWER_C = _reply("Answer.", "stop", cost=0.5, completion=40, rid="gen-c")
EMPTY_C = _reply("", "length", cost=0.4, rid="gen-c")
EMPTY_D = _reply("", "length", cost=0.6, completion=800, rid="gen-d")

SCENARIOS = {
    # name: (provider factory, billed total, billed ids, error expected)
    "re-send answers (the #366 path)": (
        lambda: _litellm("m", EMPTY_A, _reply("ok", "stop", cost=0.3, rid="gen-b")),
        0.5, {"gen-a", "gen-b"}, None,
    ),
    "re-send empty again": (
        lambda: _litellm("m", EMPTY_A, EMPTY_B),
        1.5, {"gen-a", "gen-b"}, "ReasoningBudgetExhausted",
    ),
    "cascade: primary exhausts, stronger answers": (
        lambda: CascadeProvider(
            primary=_litellm("flash", EMPTY_A, EMPTY_B), escalate_to=_litellm("pro", ANSWER_C)
        ),
        2.0, {"gen-a", "gen-b", "gen-c"}, None,
    ),
    "cascade: both exhaust": (
        lambda: CascadeProvider(
            primary=_litellm("flash", EMPTY_A, EMPTY_B),
            escalate_to=_litellm("pro", EMPTY_C, EMPTY_D),
        ),
        2.5, {"gen-a", "gen-b", "gen-c", "gen-d"}, "ReasoningBudgetExhausted",
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_billed_request_is_on_the_row_and_its_cost_in_the_total(name, tmp_path):
    factory, billed, ids, error = SCENARIOS[name]
    row, raised = _row(factory(), tmp_path / "calls.jsonl")
    assert raised == error
    assert row["cost_usd"] == pytest.approx(billed), row
    assert _ids(row) == ids, row
    # A known total holds every attempt: nothing beside it to add (#382).
    assert row["last_cost_usd"] is None, row


def _known(row):
    """Every known cost the row keeps apart from its total (#382)."""
    parts = [e["first_cost_usd"] for e in row["escalations"] if e["first_cost_usd"] is not None]
    return sorted(parts + ([row["last_cost_usd"]] if row["last_cost_usd"] is not None else []))


def _unpriced_exhausting(monkeypatch, model="local/q"):
    """A provider that reports no cost and runs out of room twice: billed,
    amount unknown (r1, r2)."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    weak = OpenAICompatProvider(model=model, base_url="http://localhost:1/v1")
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    usage = {"prompt_tokens": 5, "completion_tokens": 64}
    posts = [{"id": "r1", **empty, "usage": usage}, {"id": "r2", **empty, "usage": usage}]
    monkeypatch.setattr(weak, "_post", lambda payload: posts.pop(0))
    return weak


def test_the_failed_re_send_carries_its_id_and_tokens(tmp_path):
    row, _ = _row(_litellm("m", EMPTY_A, EMPTY_B), tmp_path / "calls.jsonl")
    assert row["error"] == "ReasoningBudgetExhausted"
    assert (row["request_id"], row["output_tokens"]) == ("gen-b", 800)
    assert row["escalations"][0]["first_cost_usd"] == 0.2


def test_the_cascade_hop_is_announced_naming_whose_reply_was_empty(tmp_path, capsys):
    """A hop to the stronger model is a re-send at the user's expense, and is
    said before it is sent (#361). The reply that came back empty was the
    primary's, so the message names it."""
    provider = CascadeProvider(
        primary=_litellm("flash", EMPTY_A, EMPTY_B), escalate_to=_litellm("pro", ANSWER_C)
    )
    row, _ = _row(provider, tmp_path / "calls.jsonl")
    said = capsys.readouterr().err
    assert "re-sending standard/x to pro" in said and "flash's reply at 800" in said
    hop = row["escalations"][-1]
    assert (hop["model"], hop["first_model"], hop["first_cost_usd"]) == ("pro", "flash", 1.3)


def test_a_failure_that_billed_nothing_records_no_cost(tmp_path):
    """An exception that carries no billing stays as it was: unknown, not zero."""

    class _Down:
        model = "m"

        def generate(self, **kwargs):
            raise ConnectionError("endpoint down")

    row, raised = _row(_Down(), tmp_path / "calls.jsonl")
    assert raised == "ConnectionError"
    assert row["cost_usd"] is None and row["request_id"] is None


def test_the_openai_compatible_re_send_records_its_id(tmp_path, monkeypatch):
    """That API reports no cost, so none is invented; the id and tokens are kept."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    provider = OpenAICompatProvider(model="local/q", base_url="http://localhost:1/v1")
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    posts = [
        {"id": "r1", **empty, "usage": {"prompt_tokens": 5, "completion_tokens": 64}},
        {"id": "r2", **empty, "usage": {"prompt_tokens": 5, "completion_tokens": 512}},
    ]
    monkeypatch.setattr(provider, "_post", lambda payload: posts.pop(0))
    row, raised = _row(provider, tmp_path / "calls.jsonl")
    assert raised == "ReasoningBudgetExhausted"
    assert (row["request_id"], row["output_tokens"], row["cost_usd"]) == ("r2", 512, None)
    assert _ids(row) == {"r1", "r2"}


@pytest.mark.parametrize("answers", [True, False], ids=["answers", "exhausts"])
def test_a_stronger_model_of_unknown_cost_leaves_the_row_unknown(answers, tmp_path, monkeypatch):
    """1d on #378: a stronger half that reports no cost billed an unknown
    amount, so the row's cost is None whether it answers or runs out too,
    never the primary's $1.50 standing in for the whole bill. The known
    parts are in the escalations."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    strong = OpenAICompatProvider(model="local/q", base_url="http://localhost:1/v1")
    usage = {"prompt_tokens": 5, "completion_tokens": 64}
    empty = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    good = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    posts = [{"id": "r1", **(good if answers else empty), "usage": usage}]
    posts += [] if answers else [{"id": "r2", **empty, "usage": usage}]
    monkeypatch.setattr(strong, "_post", lambda payload: posts.pop(0))
    provider = CascadeProvider(primary=_litellm("flash", EMPTY_A, EMPTY_B), escalate_to=strong)
    row, raised = _row(provider, tmp_path / "calls.jsonl")
    assert raised == (None if answers else "ReasoningBudgetExhausted")
    assert row["cost_usd"] is None, row
    known = [e["first_cost_usd"] for e in row["escalations"] if e["first_cost_usd"] is not None]
    assert known == [0.2, 1.3]


def test_a_stronger_model_that_never_ran_keeps_the_primary_cost(tmp_path):
    """The stronger half fails before any bill (an endpoint down): the row's
    cost is what the primary billed, not unknown."""

    class _Down:
        model = "pro"

        def generate(self, **kwargs):
            raise ConnectionError("endpoint down")

    provider = CascadeProvider(primary=_litellm("flash", EMPTY_A, EMPTY_B), escalate_to=_Down())
    row, raised = _row(provider, tmp_path / "calls.jsonl")
    assert raised == "ConnectionError"
    assert row["cost_usd"] == pytest.approx(1.5), row
    assert {e["first_request_id"] for e in row["escalations"]} == {"gen-a", "gen-b"}


@pytest.mark.parametrize("first_known", [True, False], ids=["retry-unknown", "first-unknown"])
@pytest.mark.parametrize("answers", [True, False], ids=["answers", "exhausts"])
def test_one_attempt_of_unknown_cost_leaves_the_row_unknown(first_known, answers, tmp_path):
    """9b on #378: the same-model re-send summed only the known attempt and
    wrote it as the total. Now the row says unknown, as the cascade does."""
    first = _reply("", "length", cost=0.2 if first_known else None, rid="gen-a")
    last = _reply("ok" if answers else "", "stop" if answers else "length",
                  cost=None if first_known else 1.3, rid="gen-b")  # fmt: skip
    row, raised = _row(_litellm("m", first, last), tmp_path / "calls.jsonl")
    assert raised == (None if answers else "ReasoningBudgetExhausted")
    assert row["cost_usd"] is None, row
    assert _ids(row) == {"gen-a", "gen-b"}
    # #382: whichever attempt's cost is known has one place on the row. The
    # re-send's $1.30 after an unknown first attempt was recorded nowhere.
    assert _known(row) == ([0.2] if first_known else [1.3]), row


@pytest.mark.parametrize("answers", [True, False], ids=["answers", "exhausts"])
def test_an_unpriced_primary_leaves_the_row_unknown(answers, tmp_path, monkeypatch):
    """#382, the order #378's test did not pin: a primary that billed an
    unknown amount, then a priced stronger model. The row's cost was the
    stronger model's alone ($0.50, or $1.00), presented as the whole bill.
    Now it is None, and the stronger model's known costs are kept."""
    strong = _litellm("pro", ANSWER_C) if answers else _litellm("pro", EMPTY_C, EMPTY_D)
    provider = CascadeProvider(primary=_unpriced_exhausting(monkeypatch), escalate_to=strong)
    row, raised = _row(provider, tmp_path / "calls.jsonl")
    assert raised == (None if answers else "ReasoningBudgetExhausted")
    assert row["cost_usd"] is None, row
    assert _known(row) == ([0.5] if answers else [0.4, 0.6]), row
    assert {"r1", "r2"} <= _ids(row), row


def test_the_exception_type_is_unchanged():
    assert issubclass(ReasoningBudgetExhausted, RuntimeError)


# ---- #426: a reply that is not JSON was billed too -----------------------------


def _json_row(provider, path):
    wrapped = ledger.RecordingProvider(
        provider, provider_name="t", provider_class="cloud", path=path
    )
    with pytest.raises(SchemaReplyError), ledger.about("crosswalk/x", site="crosswalk-propose"):
        wrapped.generate_json(system="s", prompt="p", schema={"type": "object"}, max_tokens=100)
    (row,) = [json.loads(line) for line in path.read_text().splitlines()]
    assert row["error"] == "SchemaReplyError"
    return row


def test_a_reply_that_is_not_json_keeps_its_id_cost_and_tokens(tmp_path):
    """80's ruling on #426: 22 real rows had cost None and no request id for
    a reply that was priced and billed. The exception carries them now."""
    prose = _reply(
        "Here is the mapping you asked for.", "stop", cost=0.3, completion=40, rid="gen-p"
    )
    row = _json_row(_litellm("m", prose), tmp_path / "calls.jsonl")
    assert (row["request_id"], row["cost_usd"], row["output_tokens"]) == ("gen-p", 0.3, 40)


def test_a_re_send_then_a_reply_that_is_not_json_keeps_both(tmp_path):
    """The conservation check, on this path: both billed requests are on the
    row and the total is both costs."""
    prose = _reply("Not JSON.", "stop", cost=0.3, completion=40, rid="gen-b")
    row = _json_row(_litellm("m", EMPTY_A, prose), tmp_path / "calls.jsonl")
    assert row["cost_usd"] == pytest.approx(0.5), row
    assert _ids(row) == {"gen-a", "gen-b"}
    assert row["last_cost_usd"] is None


def test_an_unpriced_reply_that_is_not_json_keeps_its_id(tmp_path, monkeypatch):
    """That API reports no cost, so none is invented; the id and tokens are kept."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    provider = OpenAICompatProvider(model="local/q", base_url="http://localhost:1/v1")
    post = {
        "id": "r1",
        "choices": [{"message": {"content": "prose"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
    }
    monkeypatch.setattr(provider, "_post", lambda payload: post)
    row = _json_row(provider, tmp_path / "calls.jsonl")
    assert (row["request_id"], row["output_tokens"], row["cost_usd"]) == ("r1", 7, None)
