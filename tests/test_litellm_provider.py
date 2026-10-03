"""The LiteLLM provider, driven through an injected fake completion so none
of this reaches a vendor or needs a key.

Two behaviours here are not shared with the other providers and are the
reason this file exists. LiteLLM reports what a call cost, which is what
turns a quality comparison across models into a cost comparison — so the
figure has to survive a retry, or a model that needs one looks cheaper than
a model that does not. And LiteLLM's own `drop_params` cannot rescue the
`temperature` deprecation, because its metadata lists temperature as
supported for the very models that reject it; the retry is hand-rolled and
has to stay.
"""

from __future__ import annotations

import pytest


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason):
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, prompt_tokens=11, completion_tokens=5):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeResponse:
    """Duck-typed stand-in for litellm's ModelResponse — the provider reads
    `.choices[0].message.content`, `.choices[0].finish_reason`, `.model`,
    `.usage` and `._hidden_params` off it and nothing else."""

    def __init__(self, content, finish_reason="stop", model="anthropic/claude-opus-5", cost=0.0012):
        self.choices = [_Choice(content, finish_reason)]
        self.model = model
        self.usage = _Usage()
        self._hidden_params = {"response_cost": cost}


class FakeCompletion:
    """Records each call; returns the next scripted reply, or raises."""

    def __init__(self, replies=None, raise_on_temperature=None):
        self.replies = list(replies or [])
        self.raise_on_temperature = raise_on_temperature
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_on_temperature is not None and "temperature" in kwargs:
            raise self.raise_on_temperature
        return self.replies.pop(0) if self.replies else FakeResponse("ok")


def _temperature_deprecated_error():
    # These three tests assert against litellm's own exception types, so
    # they need the extra. Everything else in this file drives an injected
    # fake and must run without it — CI installs only `.[dev]`.
    litellm = pytest.importorskip("litellm")

    return litellm.BadRequestError(
        message="`temperature` is deprecated for this model.",
        model="claude-sonnet-5",
        llm_provider="anthropic",
    )


def _provider(completion, **kwargs):
    from policyforge.llm.litellm_provider import LiteLLMProvider

    return LiteLLMProvider(
        model=kwargs.pop("model", "anthropic/claude-opus-5"),
        completion=completion,
        **kwargs,
    )


def test_it_sends_the_expected_request_and_parses_the_response():
    completion = FakeCompletion([FakeResponse("hello from litellm")])

    result = _provider(completion).generate(system="sys", prompt="hi", max_tokens=64)

    sent = completion.calls[0]
    assert sent["model"] == "anthropic/claude-opus-5"
    assert sent["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert sent["max_tokens"] == 64
    assert result.text == "hello from litellm"
    assert (result.input_tokens, result.output_tokens) == (11, 5)


def test_the_cost_of_the_call_is_reported():
    """The point of routing through LiteLLM: comparing models on quality is
    only half an answer without what each one charged."""
    completion = FakeCompletion([FakeResponse("ok", cost=0.0042)])

    assert _provider(completion).generate(system="s", prompt="p").cost_usd == 0.0042


def test_a_local_model_reports_zero_cost_not_unknown():
    """0.0 and None are different answers — free, versus the provider
    cannot say. A caller summing a run must be able to tell them apart."""
    completion = FakeCompletion([FakeResponse("ok", cost=0.0)])

    assert _provider(completion).generate(system="s", prompt="p").cost_usd == 0.0


def test_api_base_is_sent_only_when_configured():
    completion = FakeCompletion([FakeResponse("ok"), FakeResponse("ok")])

    _provider(completion).generate(system="s", prompt="p")
    assert "api_base" not in completion.calls[0]

    _provider(completion, api_base="http://localhost:11434").generate(system="s", prompt="p")
    assert completion.calls[1]["api_base"] == "http://localhost:11434"


def test_a_model_that_rejects_temperature_is_retried_without_it():
    """LiteLLM's drop_params does not cover this: its metadata lists
    temperature as supported for claude-sonnet-5, which rejects it."""
    completion = FakeCompletion(
        [FakeResponse("ok")], raise_on_temperature=_temperature_deprecated_error()
    )

    result = _provider(completion, model="anthropic/claude-sonnet-5").generate(
        system="s", prompt="p"
    )

    assert result.text == "ok"
    assert "temperature" in completion.calls[0]
    assert "temperature" not in completion.calls[1]


def test_drop_params_is_requested_so_one_model_s_limits_do_not_fail_the_call():
    """LiteLLM refuses a parameter it knows the model rejects before
    sending. Asking it to drop instead is what lets one config reach many
    vendors without a hand-written exception per restriction."""
    completion = FakeCompletion([FakeResponse("ok")])

    _provider(completion).generate(system="s", prompt="p")

    assert completion.calls[0]["drop_params"] is True


def test_a_client_side_unsupported_param_is_also_retried_without_temperature():
    """The second wording. LiteLLM raises UnsupportedParamsError (a
    BadRequestError subclass) saying "does not support temperature=0.2",
    where Anthropic's own API says "is deprecated"."""
    litellm = pytest.importorskip("litellm")

    completion = FakeCompletion(
        [FakeResponse("ok")],
        raise_on_temperature=litellm.UnsupportedParamsError(
            message="claude-sonnet-5 does not support temperature=0.2. "
            "Only temperature=1 is supported.",
            model="claude-sonnet-5",
            llm_provider="anthropic",
        ),
    )

    result = _provider(completion, model="anthropic/claude-sonnet-5").generate(
        system="s", prompt="p"
    )

    assert result.text == "ok"
    assert "temperature" not in completion.calls[1]


def test_temperature_is_dropped_for_the_rest_of_the_run():
    """Learned once, not rediscovered on every call — the second request
    should not pay for the same rejection again."""
    completion = FakeCompletion(
        [FakeResponse("one"), FakeResponse("two")],
        raise_on_temperature=_temperature_deprecated_error(),
    )
    provider = _provider(completion, model="anthropic/claude-sonnet-5")

    provider.generate(system="s", prompt="p")
    provider.generate(system="s", prompt="p")

    # call 0 raised, 1 succeeded, 2 never sends temperature again.
    assert len(completion.calls) == 3
    assert "temperature" not in completion.calls[2]


def test_an_unrelated_bad_request_is_not_swallowed():
    litellm = pytest.importorskip("litellm")

    completion = FakeCompletion(
        raise_on_temperature=litellm.BadRequestError(
            message="context length exceeded", model="m", llm_provider="anthropic"
        )
    )

    # Not swallowed, and not raw either: it surfaces as the project's own
    # rejection type, carrying the vendor's words and chained from the SDK
    # error, so the CLI can print a sentence instead of a traceback.
    from policyforge.llm.base import ProviderRejected

    with pytest.raises(ProviderRejected) as caught:
        _provider(completion).generate(system="s", prompt="p")
    assert "context length exceeded" in caught.value.vendor_message
    assert isinstance(caught.value.__cause__, litellm.BadRequestError)


def test_a_budget_spent_entirely_on_reasoning_is_retried_with_room_to_speak():
    """LiteLLM moves reasoning into `reasoning_content`, so a truncated
    reasoning model returns empty `content` with finish_reason "length" —
    the same failure the inline <think> case produces, wearing different
    clothes."""
    completion = FakeCompletion(
        [FakeResponse("", finish_reason="length"), FakeResponse("coverage")]
    )

    result = _provider(completion).generate(system="s", prompt="p", max_tokens=12)

    assert result.text == "coverage"
    assert len(completion.calls) == 2
    assert completion.calls[1]["max_tokens"] >= 256


def test_both_attempts_are_billed_when_a_retry_happens():
    """Charging only the successful attempt would make a model that needs
    the retry look cheaper than one that never does, inverting the
    comparison this provider exists to support."""
    completion = FakeCompletion(
        [
            FakeResponse("", finish_reason="length", cost=0.001),
            FakeResponse("coverage", cost=0.004),
        ]
    )

    result = _provider(completion).generate(system="s", prompt="p", max_tokens=12)

    assert result.cost_usd == pytest.approx(0.005)


def test_a_free_retry_still_reports_free_not_unknown():
    """The regression real usage found and the suite did not. Summing on
    truthiness makes `0.0 or None` collapse to None, so two free local
    calls reported "nobody knows" instead of "this cost nothing" — the one
    distinction cost_usd exists to keep. The retry test used priced calls
    and the zero-cost test never retried, so the gap sat between them."""
    completion = FakeCompletion(
        [
            FakeResponse("", finish_reason="length", cost=0.0),
            FakeResponse("coverage", cost=0.0),
        ]
    )

    result = _provider(completion).generate(system="s", prompt="p", max_tokens=12)

    assert result.cost_usd == 0.0


def test_a_provider_that_reports_no_cost_at_all_stays_unknown():
    completion = FakeCompletion(
        [
            FakeResponse("", finish_reason="length", cost=None),
            FakeResponse("coverage", cost=None),
        ]
    )

    result = _provider(completion).generate(system="s", prompt="p", max_tokens=12)

    assert result.cost_usd is None


def test_a_deliberate_empty_answer_is_not_retried():
    completion = FakeCompletion([FakeResponse("", finish_reason="stop")])

    result = _provider(completion).generate(system="s", prompt="p", max_tokens=12)

    assert result.text == ""
    assert len(completion.calls) == 1


def test_inline_thinking_is_still_stripped():
    """Normalisation depends on LiteLLM recognising the model. One it does
    not recognise falls through to whatever the endpoint sent, which for a
    local reasoning model is an inline <think> block."""
    completion = FakeCompletion([FakeResponse("<think>weighing it up</think>ok")])

    assert _provider(completion).generate(system="s", prompt="p").text == "ok"


def test_a_named_but_unset_api_key_env_is_an_error(monkeypatch):
    monkeypatch.delenv("SOME_PROXY_KEY", raising=False)

    with pytest.raises(RuntimeError) as caught:
        _provider(FakeCompletion(), api_key_env="SOME_PROXY_KEY")

    assert "SOME_PROXY_KEY" in str(caught.value)


def test_an_api_key_is_sent_when_config_names_one(monkeypatch):
    monkeypatch.setenv("SOME_PROXY_KEY", "sk-proxy-1")
    completion = FakeCompletion([FakeResponse("ok")])

    _provider(completion, api_key_env="SOME_PROXY_KEY").generate(system="s", prompt="p")

    assert completion.calls[0]["api_key"] == "sk-proxy-1"


def test_check_is_true_on_an_ok_reply():
    assert _provider(FakeCompletion([FakeResponse("ok")])).check() is True


def test_check_is_false_on_an_unexpected_reply():
    assert _provider(FakeCompletion([FakeResponse("certainly!")])).check() is False


def test_get_provider_dispatches_to_litellm(monkeypatch):
    from policyforge.llm.base import get_provider

    captured = {}

    class FakeLiteLLMProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("policyforge.llm.litellm_provider.LiteLLMProvider", FakeLiteLLMProvider)

    provider = get_provider({"llm": {"provider": "litellm", "model": "gemini/gemini-2.0-flash"}})

    # `get_provider` wraps what it builds in the model-call ledger, so
    # the dispatch is asserted on the factory's result underneath.
    assert isinstance(provider.inner, FakeLiteLLMProvider)
    assert captured["model"] == "gemini/gemini-2.0-flash"
    # Neither is required: LiteLLM resolves most providers' credentials
    # from their own environment variables.
    assert captured["api_base"] is None
    assert captured["api_key_env"] is None


def test_an_unknown_provider_names_litellm_among_the_options():
    from policyforge.llm.base import get_provider

    with pytest.raises(ValueError) as caught:
        get_provider({"llm": {"provider": "nonsense", "model": "m"}})

    assert "litellm" in str(caught.value)


def test_a_second_truncation_is_raised_not_returned_as_silence():
    """One truncation is bad luck and is retried. Two is a model whose
    reasoning does not fit the job, and returning "" for it would claim the
    model considered the question and chose to say nothing."""
    from policyforge.llm._inline_thinking import ReasoningBudgetExhausted

    completion = FakeCompletion(
        [
            FakeResponse("", finish_reason="length"),
            FakeResponse("", finish_reason="length"),
        ]
    )

    with pytest.raises(ReasoningBudgetExhausted) as caught:
        _provider(completion).generate(system="s", prompt="p", max_tokens=150)

    assert len(completion.calls) == 2
    assert "150" in str(caught.value) and "1200" in str(caught.value)


def test_the_exhaustion_message_is_not_graded_as_infrastructure():
    """evals/runner.py buckets a message naming a connection or a rate limit
    as "this never ran". A model that reasons past its budget did run, and
    hiding that would erase the finding."""
    from evals.runner import _INFRASTRUCTURE
    from policyforge.llm._inline_thinking import exhausted

    message = str(exhausted("qwen/qwen3.7-flash", 150, 1200)).lower()

    assert not [hint for hint in _INFRASTRUCTURE if hint in message]


def test_retries_are_requested_so_a_rate_limit_does_not_shrink_the_sample():
    """A refused request is not a result. evals/runner.py correctly grades a
    rate limit as saying nothing about the prompt, so without retries a busy
    vendor quietly shrinks the sample a comparison is drawn from."""
    completion = FakeCompletion([FakeResponse("ok")])

    _provider(completion).generate(system="s", prompt="p")

    assert completion.calls[0]["num_retries"] == 3


def test_the_retry_count_is_configurable():
    completion = FakeCompletion([FakeResponse("ok")])

    _provider(completion, num_retries=7).generate(system="s", prompt="p")

    assert completion.calls[0]["num_retries"] == 7


class FakeClock:
    """A clock that only moves when something sleeps."""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


def test_no_throttling_by_default():
    """Right for almost every endpoint; only a low per-model cap needs it."""
    clock = FakeClock()
    completion = FakeCompletion([FakeResponse("ok"), FakeResponse("ok")])
    provider = _provider(completion, sleep=clock.sleep, clock=clock.monotonic)

    provider.generate(system="s", prompt="p")
    provider.generate(system="s", prompt="p")

    assert clock.slept == []


def test_requests_are_spaced_when_an_interval_is_set():
    """cohere/command-a refused 16 of 23 cases because retries alone still
    landed inside the same one-minute window."""
    clock = FakeClock()
    completion = FakeCompletion([FakeResponse("ok"), FakeResponse("ok"), FakeResponse("ok")])
    provider = _provider(
        completion, min_interval_seconds=2.0, sleep=clock.sleep, clock=clock.monotonic
    )

    provider.generate(system="s", prompt="p")
    provider.generate(system="s", prompt="p")
    provider.generate(system="s", prompt="p")

    # Nothing before the first; a full gap before each one after it.
    assert clock.slept == [2.0, 2.0]


def test_time_already_spent_counts_towards_the_gap():
    """A slow call has already waited. Sleeping the full interval on top
    would halve the throughput of exactly the endpoint that can least
    afford it."""
    clock = FakeClock()

    def slow_completion(**kwargs):
        clock.now += 1.5  # the request itself took 1.5s
        return FakeResponse("ok")

    provider = _provider(
        slow_completion, min_interval_seconds=2.0, sleep=clock.sleep, clock=clock.monotonic
    )

    provider.generate(system="s", prompt="p")
    provider.generate(system="s", prompt="p")

    assert clock.slept == [0.5]


def test_the_retry_is_throttled_too():
    """Both requests of a retry go to the same capped endpoint."""
    clock = FakeClock()
    completion = FakeCompletion(
        [FakeResponse("", finish_reason="length"), FakeResponse("coverage")]
    )
    provider = _provider(
        completion, min_interval_seconds=2.0, sleep=clock.sleep, clock=clock.monotonic
    )

    provider.generate(system="s", prompt="p", max_tokens=12)

    assert clock.slept == [2.0]


# --------------------------------------------------------------------------
# Structured output, as an opt-in capability
#
# Kept off `generate`'s signature on purpose: a provider that cannot enforce
# a schema would have to accept the argument and ignore it, and the caller
# would then parse JSON that was never guaranteed to be JSON — the silent
# failure the whole feature exists to remove.
# --------------------------------------------------------------------------

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "routing",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"analysis": {"type": "string"}},
            "required": ["analysis"],
            "additionalProperties": False,
        },
    },
}


def test_generate_json_sends_the_schema_as_response_format():
    completion = FakeCompletion([FakeResponse('{"analysis": "history"}')])

    result = _provider(completion).generate_json(system="s", prompt="p", schema=SCHEMA)

    assert completion.calls[0]["response_format"] == SCHEMA
    assert result.text == '{"analysis": "history"}'


def test_generate_does_not_send_a_response_format_by_default():
    completion = FakeCompletion([FakeResponse("ok")])

    _provider(completion).generate(system="s", prompt="p")

    assert "response_format" not in completion.calls[0]


def test_a_reply_that_is_not_json_is_reported_as_such():
    """LiteLLM's capability table claimed claude-sonnet-5 accepted
    `temperature`, which it does not. A table that says a model can be held
    to a schema is a belief, so the reply is still checked."""
    completion = FakeCompletion([FakeResponse("I think the answer is history.")])

    with pytest.raises(RuntimeError) as caught:
        _provider(completion).generate_json(system="s", prompt="p", schema=SCHEMA)

    assert "returned something else" in str(caught.value)
    # Its own type, carrying the whole reply, so a caller can read an answer
    # that arrived in the wrong shape instead of asking again.
    from policyforge.llm.base import SchemaReplyError

    assert isinstance(caught.value, SchemaReplyError)
    assert caught.value.text == "I think the answer is history."


def test_a_truncated_reply_is_retried_before_being_called_bad_json():
    """Measured three separate times while building this: a reasoning model
    emits its JSON last, so a tight ceiling returns prose that is not JSON
    at all. That is truncation, not disobedience."""
    completion = FakeCompletion(
        [FakeResponse("", finish_reason="length"), FakeResponse('{"analysis": "drift"}')]
    )

    result = _provider(completion).generate_json(
        system="s", prompt="p", schema=SCHEMA, max_tokens=32
    )

    assert result.text == '{"analysis": "drift"}'
    assert len(completion.calls) == 2
