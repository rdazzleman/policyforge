"""No silent escalation (#361, 80's ruling half 1).

A reasoning model can spend its whole budget thinking and return an empty,
billed reply. Measured on #301: sonnet's Evaluation Standard came back empty
at 16,384 tokens, and the provider's re-send at 131,072 had a worst case of
$1.41, with nothing telling the user. Every bigger-budget re-send is now
announced before it is sent (the topic, the model, the new max_tokens and
the worst case at the model's list price), and the billed attempt before it
is kept in that call's ledger row.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from policyforge.llm import escalation, ledger
from policyforge.llm.litellm_provider import LiteLLMProvider


def _reply(text, finish, *, cost, prompt=1000, completion=16384, rid="gen-1", reported=True):
    """A LiteLLM reply. `reported` adds the header OpenRouter's reply carries
    when the vendor itself priced the call (#87); #301 was OpenRouter."""
    choice = SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)
    hidden = {"response_cost": cost}
    if reported and cost is not None:
        hidden["additional_headers"] = {"llm_provider-x-litellm-response-cost": cost}
    return SimpleNamespace(choices=[choice], usage=usage, id=rid, model="m", _hidden_params=hidden)


class _Completion:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, **payload):
        self.calls.append(payload)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


#: A known price, so these tests hold with or without `litellm` installed
#: (CI installs it only as an extra). Per token: $2/M in, $10/M out.
PRICE = (2e-06, 1e-05, "test-price")


@pytest.fixture(autouse=True)
def _known_price(monkeypatch, request):
    """Every test prices at PRICE, except the ones that read the real table."""
    if request.node.name != "test_the_real_price_table_names_its_entry":
        monkeypatch.setattr(escalation, "_price", lambda model: None if "local" in model else PRICE)


def _wrapped(model, completion, path):
    # The provider's own injection point: no `litellm` import is needed.
    inner = LiteLLMProvider(model=model, completion=completion)
    return ledger.RecordingProvider(
        inner, provider_name="litellm", provider_class="cloud", path=path
    )


def test_a_re_send_is_announced_and_both_attempts_are_in_the_ledger(tmp_path, capsys):
    """The #301 shape: empty and cut off at 16,384, then re-sent at 8x."""
    completion = _Completion(
        _reply("", "length", cost=0.1985, rid="gen-first"),
        _reply("# Standard\n\nText.", "stop", cost=0.30, completion=26338, rid="gen-second"),
    )
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "calls.jsonl")

    with ledger.about("standard/ai-evaluation-measurement", site="generate"):
        provider.generate(system="s", prompt="p", max_tokens=16384)

    said = capsys.readouterr().err
    assert "standard/ai-evaluation-measurement" in said, "the topic"
    # 8x of 16,384 is 131,072, but claude-sonnet-5 publishes 128,000 as its
    # ceiling (#3, measured in two sources, comment 1485), so the re-send is
    # clamped to it rather than refused by the vendor as a 400.
    assert "max_tokens 128,000" in said and "16,384" in said, "the clamped budget and the old"
    # The worst case moves with the clamp (80's condition 2 on #3).
    worst = 1000 * PRICE[0] + 128000 * PRICE[1]
    assert f"worst case ${worst:.4f}, estimated at test-price's price in LiteLLM " in said, (
        "the clamped budget, priced, and named as an estimate (#79)"
    )
    assert "'s bundled table" in said, "the price's source: the pinned map, with its version"
    assert "$0.1985, billed" in said, "the empty first attempt was billed"
    assert [c["max_tokens"] for c in completion.calls] == [16384, 128000]

    (row,) = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert row["request_id"] == "gen-second"
    (first,) = row["escalations"]
    assert first["first_request_id"] == "gen-first", "the billed first attempt is on the record"
    assert first["first_cost_usd"] == 0.1985
    assert first["max_tokens"] == 128000 and first["first_max_tokens"] == 16384
    assert first["subject"] == "standard/ai-evaluation-measurement"


def test_the_real_price_table_names_its_entry():
    """With `litellm` installed, a routed model string is priced from LiteLLM's
    own table, with the entry named. Skipped where `litellm` is not installed:
    there the warning says "unpriced", which the unknown-model test holds."""
    pytest.importorskip("litellm")
    price = escalation._price("openrouter/anthropic/claude-sonnet-5")
    assert price is not None, "the premise: the model is in LiteLLM's table under some name"
    assert price[2] in {
        "openrouter/anthropic/claude-sonnet-5",
        "anthropic/claude-sonnet-5",
        "claude-sonnet-5",
    }


def test_the_worst_case_is_the_new_budget_at_the_list_price():
    """The arithmetic, shown: input at the input price plus the NEW max_tokens
    at the output price, from the model's price entry, which is named."""
    price = escalation._price("openrouter/anthropic/claude-sonnet-5")
    assert price == PRICE
    out = io.StringIO()
    e = escalation.announce(
        model="openrouter/anthropic/claude-sonnet-5",
        first_max_tokens=16384,
        max_tokens=131072,
        input_tokens=17448,
        out=out,
    )
    assert e.worst_case_usd == pytest.approx(17448 * price[0] + 131072 * price[1], rel=1e-6)
    assert e.price_source == price[2]
    escalation.take()


def test_an_unknown_model_is_unpriced_not_guessed():
    out = io.StringIO()
    e = escalation.announce(
        model="local/some-model", first_max_tokens=64, max_tokens=512, input_tokens=10, out=out
    )
    assert e.worst_case_usd is None
    assert "worst case unpriced: up to 512 output tokens" in out.getvalue()
    escalation.take()


def test_a_call_with_no_re_send_announces_nothing(tmp_path, capsys):
    completion = _Completion(_reply("fine", "stop", cost=0.01, completion=10))
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "calls.jsonl")
    with ledger.about("standard/x", site="generate"):
        provider.generate(system="s", prompt="p", max_tokens=100)
    assert capsys.readouterr().err == ""
    (row,) = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert row["escalations"] == []


def test_an_escalation_that_then_fails_is_still_recorded(tmp_path, capsys):
    """Empty again after the re-send: the call raises, and its row still
    carries the escalation and the billed first attempt."""
    completion = _Completion(
        _reply("", "length", cost=0.2, rid="gen-a"), _reply("", "length", cost=1.3, rid="gen-b")
    )
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "calls.jsonl")
    from policyforge.llm._inline_thinking import ReasoningBudgetExhausted

    with ledger.about("standard/x", site="generate"), pytest.raises(ReasoningBudgetExhausted):
        provider.generate(system="s", prompt="p", max_tokens=100)
    (row,) = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert row["error"] and row["escalations"][0]["first_request_id"] == "gen-a"


def test_effort_announces_its_own_2x_retry(capsys):
    """`effort` retries a cut-off reply at 2x the budget: a budget
    escalation too, announced the same way."""
    from policyforge.llm import effort
    from policyforge.llm.base import LLMResponse

    replies = [
        LLMResponse(text="partial", model="m", stop_reason="length", output_tokens=100),
        LLMResponse(text="whole", model="m", stop_reason="stop"),
    ]

    class Fake:
        def generate(self, **kwargs):
            return replies.pop(0)

    with ledger.about("standard/y", site="generate"):
        effort.call(Fake(), system="s", prompt="p", max_tokens=1000)
    said = capsys.readouterr().err
    assert "standard/y" in said and "max_tokens 2,000" in said and "cut off" in said
    # Unwrapped, so no row took it: the scope closed it on exit and said so.
    assert "no ledger row recorded the re-send of standard/y" in said
    assert escalation.take() == ()


def test_the_anthropic_shim_announces_its_re_send(capsys):
    """`_anthropic_compat`, shared by the Anthropic and Vertex providers:
    a thinking block and no text at max_tokens is re-sent at 8x."""
    from policyforge.llm._anthropic_compat import call_messages_api

    def message(blocks, stop, rid, usage):
        return SimpleNamespace(
            content=blocks,
            stop_reason=stop,
            _request_id=rid,
            model="claude-sonnet-5",
            usage=SimpleNamespace(
                input_tokens=usage[0],
                output_tokens=usage[1],
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )

    thinking = SimpleNamespace(type="thinking", thinking="...")
    text = SimpleNamespace(type="text", text="ok")
    replies = [
        message([thinking], "max_tokens", "req_1", (900, 64)),
        message([text], "end_turn", "req_2", (900, 20)),
    ]
    calls = []

    def create(**payload):
        calls.append(payload["max_tokens"])
        return replies.pop(0)

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    with ledger.about("standard/w", site="generate"):
        call_messages_api(
            client, model="claude-sonnet-5", system="s", prompt="p", max_tokens=64, temperature=0.2
        )
        # Taken inside the block: on exit, an unrecorded escalation is dropped.
        (e,) = escalation.take()
    said = capsys.readouterr().err
    assert calls == [64, 512]
    assert "standard/w" in said and "max_tokens 512" in said and "worst case $" in said
    assert e["first_request_id"] == "req_1" and e["input_tokens"] == 900


def test_the_openai_compatible_provider_announces_its_re_send(capsys, monkeypatch):
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    provider = OpenAICompatProvider(model="local/q", base_url="http://localhost:1/v1")
    posts = [
        {
            "id": "r1",
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 64},
        },
        {
            "id": "r2",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        },
    ]
    monkeypatch.setattr(provider, "_post", lambda payload: posts.pop(0))
    with ledger.about("synthesis/z", site="synthesize"):
        provider.generate(system="s", prompt="p", max_tokens=64)
    said = capsys.readouterr().err
    assert "synthesis/z" in said and "max_tokens 512" in said and "unpriced" in said
    assert escalation.take() == ()


# ---- #372: what a ledger total may add, and where a pending escalation goes ----


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_first_cost_is_already_in_a_row_that_has_a_cost(tmp_path, capsys):
    """LiteLLM's 8x path sums both attempts into the row, and `effort`'s 2x
    path gives the first attempt its own row. Either way the rows alone add
    up to what was billed, so adding `first_cost_usd` would count it twice."""
    completion = _Completion(
        _reply("", "length", cost=0.20, rid="a"), _reply("ok", "stop", cost=1.00, rid="b")
    )
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "l.jsonl")
    with ledger.about("t", site="generate"):
        provider.generate(system="s", prompt="p", max_tokens=100)
    (row,) = _rows(tmp_path / "l.jsonl")
    assert row["cost_usd"] == pytest.approx(1.20), "billed 0.20 + 1.00"
    assert row["escalations"][0]["first_cost_usd"] == 0.20

    from policyforge.llm import effort
    from policyforge.llm.base import LLMResponse

    replies = [
        LLMResponse(text="part", model="m", stop_reason="length", output_tokens=9, cost_usd=0.20),
        LLMResponse(text="whole", model="m", stop_reason="stop", cost_usd=0.40),
    ]

    class Fake:
        model = "m"

        def generate(self, **kwargs):
            return replies.pop(0)

    path = tmp_path / "e.jsonl"
    wrapped = ledger.RecordingProvider(
        Fake(), provider_name="fake", provider_class="cloud", path=path
    )
    with ledger.about("t", site="generate"):
        effort.call(wrapped, system="s", prompt="p", max_tokens=1000)
    rows = _rows(path)
    assert sum(r["cost_usd"] for r in rows) == pytest.approx(0.60), "billed 0.20 + 0.40"
    assert [len(r["escalations"]) for r in rows] == [0, 1]
    assert rows[1]["escalations"][0]["first_cost_usd"] == 0.20, "the first row, repeated"


def test_on_an_error_row_first_cost_is_already_in_the_row(tmp_path, capsys):
    """The re-send fails too. Until #343 the row had no cost and the
    re-send's $1.30 was in no row; now the row carries both charges, so
    `first_cost_usd` is an addend on no row, this one included."""
    completion = _Completion(
        _reply("", "length", cost=0.20, rid="a"), _reply("", "length", cost=1.30, rid="b")
    )
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "l.jsonl")
    from policyforge.llm._inline_thinking import ReasoningBudgetExhausted

    with ledger.about("t", site="generate"), pytest.raises(ReasoningBudgetExhausted):
        provider.generate(system="s", prompt="p", max_tokens=100)
    (row,) = _rows(tmp_path / "l.jsonl")
    assert row["error"] and row["cost_usd"] == pytest.approx(1.50), "billed 0.20 + 1.30"
    assert row["escalations"][0]["first_cost_usd"] == 0.20, "already in the 1.50"
    assert row["request_id"] == "b", "the re-send's own id is on the row"


def test_an_unrecorded_calls_escalation_does_not_reach_the_next_row(tmp_path, capsys):
    """1d on #366: `_pending` was drained only by a recorded call, so an
    escalation from an unwrapped call landed in the next, unrelated row."""
    with ledger.about("unrecorded", site="generate"):
        escalation.announce(
            model="local/q",
            first_max_tokens=64,
            max_tokens=512,
            first_request_id="gen-lost",
            first_cost_usd=0.2,
        )
    closed = capsys.readouterr().err
    completion = _Completion(_reply("fine", "stop", cost=0.01, completion=10))
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "l.jsonl")
    with ledger.about("recorded", site="generate"):
        provider.generate(system="s", prompt="p", max_tokens=100)
    (row,) = _rows(tmp_path / "l.jsonl")
    assert row["subject"] == "recorded" and row["escalations"] == []
    # Not dropped silently (80 on #373): the closing scope names what no row took.
    assert "no ledger row recorded the re-send of unrecorded" in closed
    assert "gen-lost" in closed and "$0.2000" in closed
    assert "no ledger row" not in capsys.readouterr().err, "a recorded call warns nothing"


def test_a_taken_escalation_is_not_reported_as_lost(tmp_path, capsys):
    """The passing case: the row took it, so the scope has nothing to say."""
    completion = _Completion(
        _reply("", "length", cost=0.2, rid="a"), _reply("ok", "stop", cost=1.0, rid="b")
    )
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "l.jsonl")
    with ledger.about("t", site="generate"):
        provider.generate(system="s", prompt="p", max_tokens=100)
    said = capsys.readouterr().err
    assert "re-sending t" in said and "no ledger row" not in said


def test_a_nested_scope_keeps_its_own_escalations(tmp_path, capsys):
    """The inner block's unrecorded escalation neither reaches the outer
    block's row nor survives the inner block."""
    completion = _Completion(_reply("fine", "stop", cost=0.01, completion=10))
    provider = _wrapped("anthropic/claude-sonnet-5", completion, tmp_path / "l.jsonl")
    with ledger.about("outer", site="generate"):
        with ledger.about("inner", site="generate"):
            escalation.announce(model="local/q", first_max_tokens=64, max_tokens=512)
        provider.generate(system="s", prompt="p", max_tokens=100)
    (row,) = _rows(tmp_path / "l.jsonl")
    assert row["subject"] == "outer" and row["escalations"] == []
