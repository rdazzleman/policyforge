"""The long-document budget and the model's published output ceiling (#3).

The first budget for a Standard or Procedure is 32,768 (measured uncensored,
docs/measurements/epochs.md epoch 25). Two conditions came with it (80 on #3, 1485):
never ask a model for more than its published ceiling, and re-derive every
budget built on `max_tokens`. Every ceiling in these tests is WRITTEN BY
HAND (20,000, under 32k), never read from the table the code reads, so a test
can't agree with the code by sharing its source (1d on #3, 1486).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from policyforge.generate import policy_writer
from policyforge.llm import base, ceiling, effort, escalation, ledger
from policyforge.llm._inline_thinking import ReasoningBudgetExhausted
from policyforge.llm.base import TruncatedResponse
from policyforge.llm.cascade_provider import CascadeProvider
from policyforge.llm.litellm_provider import LiteLLMProvider

#: A ceiling under the 32k budget, by hand.
CEILING = 20_000


def _reply(text, finish, *, cost=0.001, rid="gen-1"):
    choice = SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500)
    return SimpleNamespace(
        choices=[choice], usage=usage, id=rid, model="m", _hidden_params={"response_cost": cost}
    )


class _Completion:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, **payload):
        self.calls.append(payload)
        return self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]


@pytest.fixture(autouse=True)
def _quiet_price(monkeypatch):
    monkeypatch.setattr(escalation, "_price", lambda model: None)


def _capped(completion, cap=CEILING):
    provider = LiteLLMProvider(model="vendor/some-model", completion=completion)
    provider.max_output_tokens = lambda: cap
    return provider


# ---- the numbers, as literals on purpose ----------------------------------


def test_the_long_document_budget_is_32k_and_its_retry_ceiling_is_twice_that():
    """Literals, so a change to either shows up here as a decision to make."""
    assert policy_writer.LONG_DOCUMENT_TOKENS == 32768
    assert effort.RETRY_CEILING == 65536, "twice the largest site budget keeps its one retry"


def test_clamp():
    assert ceiling.clamp(32768, 20000) == 20000
    assert ceiling.clamp(16384, 20000) == 16384
    assert ceiling.clamp(32768, None) == 32768, "unknown is no clamp, not zero"


# ---- the litellm provider -------------------------------------------------


def test_the_first_request_never_asks_for_more_than_the_ceiling():
    completion = _Completion(_reply("# Standard\n\nText.", "stop"))
    _capped(completion).generate(system="s", prompt="p", max_tokens=32768)
    assert [c["max_tokens"] for c in completion.calls] == [CEILING]


def test_a_budget_under_the_ceiling_goes_as_written():
    completion = _Completion(_reply("ok", "stop"))
    _capped(completion).generate(system="s", prompt="p", max_tokens=4096)
    assert [c["max_tokens"] for c in completion.calls] == [4096]


def test_with_no_published_ceiling_the_request_goes_as_written():
    completion = _Completion(_reply("ok", "stop"))
    _capped(completion, cap=None).generate(system="s", prompt="p", max_tokens=32768)
    assert [c["max_tokens"] for c in completion.calls] == [32768]


def test_the_8x_re_send_is_clamped_to_the_ceiling():
    """Empty at 2,048: 8x would be 16,384; a 10,000 ceiling makes it 10,000."""
    completion = _Completion(_reply("", "length"), _reply("# Standard\n\nText.", "stop"))
    _capped(completion, cap=10_000).generate(system="s", prompt="p", max_tokens=2048)
    assert [c["max_tokens"] for c in completion.calls] == [2048, 10_000]


def test_a_re_send_the_ceiling_leaves_no_room_for_is_not_sent():
    """Empty at the ceiling itself: 8x clamps back to the same budget, so a
    re-send would bill the same empty reply again. Refused after one call,
    and the message does not claim a second attempt."""
    completion = _Completion(_reply("", "length"), _reply("# Standard\n\nText.", "stop"))
    with pytest.raises(ReasoningBudgetExhausted) as caught:
        _capped(completion).generate(system="s", prompt="p", max_tokens=32768)
    assert [c["max_tokens"] for c in completion.calls] == [CEILING], "one billed call, not two"
    message = str(caught.value)
    assert "again" not in message and "not re-sent" in message and str(CEILING) in message


# ---- effort's 2x retry ------------------------------------------------------


class _Cut:
    """A provider whose every reply is cut off, recording each budget asked."""

    def __init__(self, cap):
        self.cap, self.asked = cap, []

    def max_output_tokens(self):
        return self.cap

    def generate(self, *, system, prompt, max_tokens=4096, **_):
        self.asked.append(max_tokens)
        return SimpleNamespace(text="partial", truncated=True, stop_reason="length", model="m")


def test_the_2x_retry_goes_to_twice_the_budget_when_no_ceiling_binds():
    provider = _Cut(cap=None)
    with pytest.raises(TruncatedResponse):
        effort.call(provider, system="s", prompt="p", max_tokens=32768)
    assert provider.asked == [32768, 65536], "a 32k document keeps its one retry"


def test_the_2x_retry_is_not_sent_when_the_ceiling_leaves_no_room():
    provider = _Cut(cap=CEILING)
    with pytest.raises(TruncatedResponse) as caught:
        effort.call(provider, system="s", prompt="p", max_tokens=32768)
    assert provider.asked == [32768], "the retry would be clamped to what was already sent"
    assert caught.value.first_budget == CEILING, "the budget the provider actually sent"


def test_the_2x_retry_stops_at_the_ceiling():
    provider = _Cut(cap=30_000)
    with pytest.raises(TruncatedResponse):
        effort.call(provider, system="s", prompt="p", max_tokens=8192)
    assert provider.asked == [8192, 16384]
    provider = _Cut(cap=CEILING)
    with pytest.raises(TruncatedResponse):
        effort.call(provider, system="s", prompt="p", max_tokens=16384)
    assert provider.asked == [16384, CEILING]


# ---- the Anthropic shim (the anthropic, bedrock and vertex providers) --------


def _message(blocks, stop, rid):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop,
        _request_id=rid,
        model="m",
        usage=SimpleNamespace(
            input_tokens=900,
            output_tokens=64,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


def _shim(monkeypatch, replies, *, max_tokens, cap=CEILING):
    from policyforge.llm._anthropic_compat import call_messages_api

    monkeypatch.setattr(ceiling, "published_output_ceiling", lambda model: cap)
    calls = []

    def create(**kwargs):
        calls.append(kwargs["max_tokens"])
        return replies[min(len(calls) - 1, len(replies) - 1)]

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    response = call_messages_api(
        client, model="m", system="s", prompt="p", max_tokens=max_tokens, temperature=0.2
    )
    return calls, response


THINKING = SimpleNamespace(type="thinking", thinking="...")
TEXT = SimpleNamespace(type="text", text="ok")


def test_the_shim_never_asks_for_more_than_the_ceiling(monkeypatch):
    calls, _ = _shim(monkeypatch, [_message([TEXT], "end_turn", "r1")], max_tokens=32768)
    assert calls == [CEILING]


def test_the_shims_8x_re_send_is_clamped(monkeypatch):
    replies = [_message([THINKING], "max_tokens", "r1"), _message([TEXT], "end_turn", "r2")]
    calls, response = _shim(monkeypatch, replies, max_tokens=2048, cap=10_000)
    assert calls == [2048, 10_000] and response.text == "ok"


def test_the_shim_does_not_re_send_into_the_same_budget(monkeypatch):
    """Empty at the ceiling: no re-send (it would bill the same empty reply).
    The empty reply goes back, and the caller refuses it, as for any empty."""
    replies = [_message([THINKING], "max_tokens", "r1"), _message([TEXT], "end_turn", "r2")]
    calls, response = _shim(monkeypatch, replies, max_tokens=32768)
    assert calls == [CEILING] and response.text == ""


# ---- the wrappers pass it through ------------------------------------------


def test_the_ceiling_is_not_on_the_base_class():
    """If it were, the ledger's wrapper would answer the base's default for
    every provider, whatever the one inside says: the trap that silenced
    effort, caching and citations for two days (RecordingProvider.__getattr__)."""
    assert not hasattr(base.LLMProvider, "max_output_tokens")


def test_the_ledger_wrapper_reports_the_inner_providers_ceiling(tmp_path):
    inner = _capped(_Completion(_reply("ok", "stop")))
    wrapped = ledger.RecordingProvider(
        inner, provider_name="litellm", provider_class="cloud", path=tmp_path / "calls.jsonl"
    )
    assert effort.output_ceiling(wrapped) == CEILING


def test_a_cascade_reports_the_smaller_published_ceiling():
    small, large, unknown = _Cut(cap=CEILING), _Cut(cap=128_000), _Cut(cap=None)
    assert CascadeProvider(primary=large, escalate_to=small).max_output_tokens() == CEILING
    assert CascadeProvider(primary=unknown, escalate_to=large).max_output_tokens() == 128_000
    assert CascadeProvider(primary=unknown, escalate_to=_Cut(cap=None)).max_output_tokens() is None


def test_a_provider_that_names_no_ceiling_has_none():
    assert effort.output_ceiling(SimpleNamespace(generate=lambda **_: None)) is None


# ---- the real table, where it is installed ---------------------------------


def test_the_published_ceiling_for_a_model_we_name():
    """claude-sonnet-5 via OpenRouter publishes 128,000, measured in two
    sources on #3 (1485): OpenRouter's model list and LiteLLM's map. Written
    here by hand from that measurement, not read from the map."""
    pytest.importorskip("litellm")
    assert ceiling.published_output_ceiling("openrouter/anthropic/claude-sonnet-5") == 128_000
    assert ceiling.published_output_ceiling("vendor/no-such-model-b5") is None


def test_a_local_model_is_never_looked_up(monkeypatch):
    """#78 (1d): for `ollama/` and `ollama_chat/` LiteLLM asks a running
    server (5 s a lookup) and answers with the context window, which is not an
    output ceiling. So a local model is None without asking anything. The
    names are written here by hand, not read from LITELLM_LOCAL_VENDORS."""
    litellm = pytest.importorskip("litellm")
    asked = []

    def info(model):
        # Answers like the live lookup did (the context window), and records
        # the call. It must not raise: the lookup swallows exceptions as
        # "unknown", so a raising fake would pass whether or not it was asked.
        asked.append(model)
        return {"max_output_tokens": 40_960}

    monkeypatch.setattr(litellm, "get_model_info", info)
    ceiling._looked_up.cache_clear()
    try:
        for model in (
            "ollama/qwen3:14b-pf",
            "ollama_chat/qwen3:14b",
            "Ollama/qwen3:14b-pf",
            "OLLAMA_CHAT/qwen3:14b",
        ):
            assert ceiling.published_output_ceiling(model) is None, model
        assert asked == [], f"a local model was looked up: {asked}"
    finally:
        ceiling._looked_up.cache_clear()


def test_a_hosted_model_is_looked_up_once_per_process(monkeypatch):
    """The map doesn't change while the process runs, and a request asks for
    the ceiling two or three times (1d on #78), so it is cached per model."""
    litellm = pytest.importorskip("litellm")
    asked = []

    def info(model):
        asked.append(model)
        return {"max_output_tokens": CEILING}

    monkeypatch.setattr(litellm, "get_model_info", info)
    ceiling._looked_up.cache_clear()
    try:
        for _ in range(3):
            assert ceiling.published_output_ceiling("vendor/b5-cache-probe") == CEILING
        assert asked == ["vendor/b5-cache-probe"]
    finally:
        ceiling._looked_up.cache_clear()
