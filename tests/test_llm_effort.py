"""A-05 and C-03 — effort per call site, and what the response carries back.

Two halves of one change. The providers knew things they threw away: why
the model stopped, whether the prompt cache was hit, which request it was.
And the token budgets in `zardoz/budgets.py` exist because a reasoning model
spends its ceiling deliberating before a one-word answer — which is a lever
set in the wrong place.

The tests that matter here are the negative ones. A provider that cannot act
on an effort level must not be handed one, because a call site that believes
it asked for less deliberation and paid for the same is worse off than one
that never asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from policyforge.llm import effort
from policyforge.llm.base import LLMResponse


class Recording:
    """A provider that records its keyword arguments."""

    def __init__(self, *, effort_supported: bool):
        self._effort_supported = effort_supported
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(text="ok", model="fake")

    def supports_effort(self) -> bool:
        return self._effort_supported


class Minimal:
    """A provider of the shape every fake in this suite has: one method."""

    def __init__(self):
        self.calls: list[dict] = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(text="ok", model="fake")


# --------------------------------------------------------------------------
# Asking before passing
# --------------------------------------------------------------------------


def test_a_provider_that_acts_on_effort_is_given_it():
    provider = Recording(effort_supported=True)

    effort.call(provider, effort=effort.ROUTING, system="s", prompt="p")

    assert provider.calls[0]["effort"] == "low"


def test_a_provider_that_does_not_is_called_exactly_as_before():
    """Not handed a parameter it would drop. A provider that accepted and
    ignored it would let the call site believe it had set something."""
    provider = Minimal()

    effort.call(provider, effort=effort.DRAFTING, system="s", prompt="p")

    assert provider.calls == [{"system": "s", "prompt": "p"}]


def test_no_effort_asked_for_means_no_effort_passed():
    provider = Recording(effort_supported=True)

    effort.call(provider, effort=None, system="s", prompt="p")

    assert "effort" not in provider.calls[0]


def test_the_tiers_are_the_ones_the_budgets_already_expressed():
    """One word from a closed list is the call that least wants thinking;
    a document somebody is audited against is the one that most does."""
    assert effort.ROUTING == effort.RESOLUTION == effort.EXPANSION == "low"
    assert effort.DRAFTING == effort.SYNTHESIS == effort.EDITING == "high"
    assert effort.ANSWERING == "medium"


# --------------------------------------------------------------------------
# The Anthropic request shape
# --------------------------------------------------------------------------


@dataclass
class FakeBlock:
    text: str
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int | None = None


@dataclass
class FakeMessage:
    content: list = field(default_factory=list)
    usage: FakeUsage = field(default_factory=FakeUsage)
    stop_reason: str = "end_turn"
    _request_id: str = "req_abc123"


class FakeMessages:
    def __init__(self, message):
        self.message = message
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.message


class FakeClient:
    def __init__(self, message=None):
        self.messages = FakeMessages(message or FakeMessage(content=[FakeBlock("hello")]))


def _call(client, **kwargs):
    from policyforge.llm._anthropic_compat import call_messages_api

    return call_messages_api(
        client, model="claude-x", system="s", prompt="p", max_tokens=100, temperature=0.0, **kwargs
    )


def test_effort_is_sent_inside_output_config():
    """Top-level `effort` is accepted by the SDK and changes nothing, which
    is the worst outcome: the call site believes it asked."""
    client = FakeClient()

    _call(client, effort="low")

    sent = client.messages.calls[0]
    assert sent["output_config"] == {"effort": "low"}
    assert "effort" not in sent


def test_no_output_config_when_no_effort_is_asked_for():
    client = FakeClient()

    _call(client)

    assert "output_config" not in client.messages.calls[0]


# --------------------------------------------------------------------------
# Prompt caching (A-04)
# --------------------------------------------------------------------------


def test_caching_marks_the_system_prompt_when_only_it_repeats():
    client = FakeClient()

    _call(client, cache=True)

    assert client.messages.calls[0]["system"] == [
        {"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}
    ]


def test_a_stable_prefix_moves_the_breakpoint_past_it():
    """The organization block in front of every control: unchanging, and
    sizeable enough to be the part worth not paying full price for."""
    client = FakeClient()

    _call(client, cache=True, cache_prefix="ORG CONTEXT\n\n")

    blocks = client.messages.calls[0]["messages"][0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == "ORG CONTEXT\n\n"
    assert blocks[1] == {"type": "text", "text": "p"}
    # Two blocks are the same content as their concatenation: this changes
    # what is billed, not what the model reads.
    assert "".join(block["text"] for block in blocks) == "ORG CONTEXT\n\np"


def test_a_provider_that_cannot_cache_still_receives_the_whole_prompt():
    """The prefix is part of the request, not a hint about it. Dropping it
    for a provider without caching would send a different question."""
    provider = Minimal()

    effort.call(
        provider, cache=True, cache_prefix="ORG CONTEXT\n\n", system="s", prompt="the control"
    )

    assert provider.calls[0]["prompt"] == "ORG CONTEXT\n\nthe control"


def test_a_caching_provider_is_told_which_part_repeats():
    provider = Recording(effort_supported=False)
    provider.supports_caching = lambda: True

    effort.call(provider, cache=True, cache_prefix="ORG\n", system="s", prompt="p")

    assert provider.calls[0]["cache"] is True
    assert provider.calls[0]["cache_prefix"] == "ORG\n"


# --------------------------------------------------------------------------
# What comes back (C-03)
# --------------------------------------------------------------------------


def test_the_response_carries_why_the_model_stopped():
    """Read inside the provider to decide on a retry, then discarded — so a
    reply truncated after some text reached the caller looking finished."""
    message = FakeMessage(
        content=[FakeBlock("half a sentence")],
        stop_reason="max_tokens",
        usage=FakeUsage(cache_read_input_tokens=2048),
    )

    result = _call(FakeClient(message))

    assert result.stop_reason == "max_tokens"
    assert result.cached_input_tokens == 2048
    assert result.request_id == "req_abc123"


def test_a_cache_that_could_have_hit_and_did_not_reads_as_zero():
    """Zero and None are different answers: zero is what a silently
    too-short cacheable prefix looks like from here."""
    message = FakeMessage(content=[FakeBlock("hi")], usage=FakeUsage(cache_read_input_tokens=0))

    assert _call(FakeClient(message)).cached_input_tokens == 0


def test_bedrock_reports_the_same_three_facts_under_its_own_names():
    from policyforge.llm.bedrock_provider import BedrockProvider

    class FakeBedrock:
        def converse(self, **kwargs):
            return {
                "output": {"message": {"content": [{"text": "hi"}]}},
                "usage": {"inputTokens": 7, "outputTokens": 3, "cacheReadInputTokens": 512},
                "stopReason": "max_tokens",
                "ResponseMetadata": {"RequestId": "aws-req-1"},
            }

    result = BedrockProvider(model="m", client=FakeBedrock()).generate(system="s", prompt="p")

    assert (result.stop_reason, result.cached_input_tokens) == ("max_tokens", 512)
    assert result.request_id == "aws-req-1"


def test_the_ledger_records_them(tmp_path):
    """The point of carrying them: a truncated document is a fact somebody
    can find afterwards rather than a short file nobody questions."""
    from policyforge.llm import ledger

    class Truncated:
        def generate(self, **kwargs):
            return LLMResponse(
                text="half",
                model="m",
                stop_reason="max_tokens",
                cached_input_tokens=1024,
                request_id="req_xyz",
            )

        def check(self):
            return True

    wrapped = ledger.wrap(Truncated(), {"llm": {"ledger": {"path": str(tmp_path / "c.jsonl")}}})
    wrapped.generate(system="s", prompt="p")

    (record,) = ledger.load(tmp_path / "c.jsonl")
    assert record.stop_reason == "max_tokens"
    assert record.cached_input_tokens == 1024
    assert record.request_id == "req_xyz"
