"""What was sent to which model, when, and at what cost.

Three properties carry the whole value of this record, and each is a way it
could quietly stop being worth anything.

It must record the model that **answered**, not the one configured, or a
cascade that escalated writes a document and the record names the wrong
model. It must record **no content**, or the ledger copies a licensed
export into a file under `output/` and becomes the leak it exists to
disprove. And it must **raise rather than drop** a write it cannot make, or
it reports a clean history of a run it never observed.

The rest is arithmetic, and the arithmetic has one trap of its own: a local
model costs 0.0 and a provider that does not price its calls costs None, and
a summary that folds those together reads an unpriced run as a free one.
"""

from __future__ import annotations

import json

import pytest

from policyforge.llm import ledger
from policyforge.llm.base import LLMResponse
from policyforge.llm.ledger import CallRecord, RecordingProvider, Totals


class FakeProvider:
    """A provider that answers with whatever it was handed."""

    def __init__(self, responses=None, raises=None):
        self._responses = list(responses or [LLMResponse(text="ok", model="fake-1")])
        self._raises = raises
        self.model = "configured-model"
        self.calls = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        self.calls.append({"system": system, "prompt": prompt})
        if self._raises:
            raise self._raises
        return self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]

    def supports_schema(self):
        return True

    def generate_json(self, *, system, prompt, schema, **kwargs):
        return self.generate(system=system, prompt=prompt)

    def check(self):
        return True


def _recorder(tmp_path, inner=None, **kwargs):
    return RecordingProvider(
        inner or FakeProvider(),
        provider_name=kwargs.pop("provider_name", "litellm"),
        provider_class=kwargs.pop("provider_class", "third-party"),
        path=tmp_path / "calls.jsonl",
    )


# ---- what gets recorded ---------------------------------------------------


def test_a_call_is_recorded_with_its_model_and_cost(tmp_path):
    inner = FakeProvider(
        [
            LLMResponse(
                text="drafted",
                model="deepseek-v4-flash",
                input_tokens=120,
                output_tokens=40,
                cost_usd=0.0013,
            )
        ]
    )
    provider = _recorder(tmp_path, inner)

    provider.generate(system="S", prompt="P")

    records = ledger.load(tmp_path / "calls.jsonl")
    assert len(records) == 1
    assert records[0].model == "deepseek-v4-flash"
    assert records[0].provider == "litellm"
    assert records[0].provider_class == "third-party"
    assert records[0].input_tokens == 120
    assert records[0].cost_usd == pytest.approx(0.0013)


def test_the_model_recorded_is_the_one_that_answered(tmp_path):
    """Not the one in config.

    A cascade answers with its stronger half whenever the cheap one runs out
    of room, and `LLMResponse.model` is the only thing that knows which. A
    record naming the configured model would be false in precisely the case
    somebody later needs it to be true.
    """
    inner = FakeProvider([LLMResponse(text="x", model="deepseek-v4-pro")])
    inner.model = "deepseek-v4-flash -> deepseek-v4-pro"
    provider = _recorder(tmp_path, inner)

    provider.generate(system="S", prompt="P")

    assert ledger.load(tmp_path / "calls.jsonl")[0].model == "deepseek-v4-pro"


def test_no_prompt_or_reply_is_written_to_the_file(tmp_path):
    """The one property that makes this safe to keep.

    A ledger that quoted what it saw would take a licensed export that
    correctly went to a local model and copy it into a file under output/.
    """
    secret_system = "HITRUST CSF requirement 01.c verbatim"
    secret_prompt = "Category: Access Control, Requirement: unique user identification"
    provider = _recorder(tmp_path, FakeProvider([LLMResponse(text="a reply", model="m")]))

    provider.generate(system=secret_system, prompt=secret_prompt)

    raw = (tmp_path / "calls.jsonl").read_text(encoding="utf-8")
    assert secret_system not in raw
    assert secret_prompt not in raw
    assert "a reply" not in raw
    # A fingerprint is kept, so two runs can be told apart without either
    # being reconstructable from the file.
    assert ledger.load(tmp_path / "calls.jsonl")[0].prompt_sha


def test_the_prompt_hash_distinguishes_prompts_and_where_text_sits():
    """Moving a rule between the system and user prompt changes the digest.

    Which is right: for several of the models measured here, moving one rule
    is worth points in either direction, so the two prompts are not the same
    prompt.
    """
    a = ledger.prompt_digest("rule one", "question")
    b = ledger.prompt_digest("", "rule one question")
    c = ledger.prompt_digest("rule one", "question")

    assert a == c
    assert a != b


def test_a_failed_call_is_recorded_and_the_error_re_raised(tmp_path):
    """It reached the vendor, was billed, and carried its content there.

    A ledger that only recorded successes would undercount both the spend
    and the exposure, which are the two things it is for.
    """
    provider = _recorder(tmp_path, FakeProvider(raises=RuntimeError("rate limited")))

    with pytest.raises(RuntimeError):
        provider.generate(system="S", prompt="P")

    records = ledger.load(tmp_path / "calls.jsonl")
    assert len(records) == 1
    assert records[0].error == "RuntimeError"


def test_the_connectivity_probe_is_not_recorded(tmp_path):
    """`llm-check` sends a fixed two-word probe and sees no real content.

    Recording it would bury the calls that saw something under the ones that
    saw nothing.
    """
    provider = _recorder(tmp_path)

    assert provider.check()
    assert not (tmp_path / "calls.jsonl").exists()


def test_a_write_failure_raises_rather_than_being_swallowed(tmp_path, monkeypatch):
    """The difference between a control and a log.

    A ledger that reported a clean history of a run it did not observe is
    worse than no ledger, because somebody would rely on it.
    """
    provider = _recorder(tmp_path)

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "append", explode)

    with pytest.raises(OSError):
        provider.generate(system="S", prompt="P")


# ---- scopes ---------------------------------------------------------------


def test_calls_inside_a_scope_are_attributed_to_its_subject(tmp_path):
    provider = _recorder(tmp_path)

    with ledger.about("standard/authenticator-mgmt", site="generate") as scope:
        provider.generate(system="S", prompt="one")
        provider.generate(system="S", prompt="two")

    assert len(scope.records) == 2
    recorded = ledger.load(tmp_path / "calls.jsonl")
    assert {r.subject for r in recorded} == {"standard/authenticator-mgmt"}
    assert {r.site for r in recorded} == {"generate"}


def test_calls_outside_a_scope_are_recorded_with_no_subject(tmp_path):
    """Unattributed rather than guessed at.

    An entry saying "we do not know what this was about" is a true statement
    about a run. An invented one is not, and would be indistinguishable from
    a real attribution later.
    """
    provider = _recorder(tmp_path)

    provider.generate(system="S", prompt="P")

    assert ledger.load(tmp_path / "calls.jsonl")[0].subject is None


def test_scopes_nest_and_the_outer_one_resumes(tmp_path):
    provider = _recorder(tmp_path)

    with ledger.about("outer"):
        provider.generate(system="S", prompt="a")
        with ledger.about("inner"):
            provider.generate(system="S", prompt="b")
        provider.generate(system="S", prompt="c")

    subjects = [r.subject for r in ledger.load(tmp_path / "calls.jsonl")]
    assert subjects == ["outer", "inner", "outer"]


def test_a_scope_names_every_model_that_answered(tmp_path):
    """A cascade can write one document with two models, and both are true."""
    inner = FakeProvider(
        [
            LLMResponse(text="a", model="flash"),
            LLMResponse(text="b", model="pro"),
            LLMResponse(text="c", model="flash"),
        ]
    )
    provider = _recorder(tmp_path, inner)

    with ledger.about("standard/x") as scope:
        for _ in range(3):
            provider.generate(system="S", prompt="P")

    assert scope.models == ["flash", "pro"]


def test_provenance_is_small_and_names_what_wrote_the_document(tmp_path):
    inner = FakeProvider(
        [LLMResponse(text="a", model="claude-sonnet-5", cost_usd=0.02, cost_source="provider")]
    )
    provider = _recorder(tmp_path, inner, provider_name="anthropic")

    with ledger.about("policy/access-control", site="generate") as scope:
        provider.generate(system="S", prompt="P")

    stamp = scope.provenance()
    assert stamp["models"] == ["claude-sonnet-5"]
    assert stamp["calls"] == 1
    assert stamp["provider"] == "anthropic"
    assert stamp["cost_usd"] == pytest.approx(0.02)
    # Both parts, always (80's ruling on #379): here every call is priced.
    assert (stamp["cost_known_usd"], stamp["calls_unpriced"]) == (pytest.approx(0.02), 0)
    assert len(stamp["prompt_shas"]) == 1
    # It goes into every version-history entry, so it stays a stamp rather
    # than a copy of the ledger.
    assert set(stamp) <= {
        "models",
        "calls",
        "prompt_shas",
        "provider",
        "provider_class",
        "cost_usd",
        "cost_known_usd",
        "calls_unpriced",
        # #87: always written (0.0 here), so a stamp without it predates #87.
        "cost_estimated_usd",
        "cost_unrecorded_source_usd",
        "content_class",
    }


def test_an_unpriced_scope_reports_no_cost_rather_than_zero(tmp_path):
    """Free and unknown are different answers."""
    provider = _recorder(tmp_path, FakeProvider([LLMResponse(text="a", model="m")]))

    with ledger.about("standard/x") as scope:
        provider.generate(system="S", prompt="P")

    assert scope.cost_usd is None
    assert "cost_usd" not in scope.provenance()


def test_a_local_model_reports_zero_rather_than_unknown(tmp_path):
    provider = _recorder(
        tmp_path, FakeProvider([LLMResponse(text="a", model="qwen3", cost_usd=0.0)])
    )

    with ledger.about("standard/x") as scope:
        provider.generate(system="S", prompt="P")

    assert scope.cost_usd == 0.0
    assert scope.provenance()["cost_usd"] == 0.0


# ---- reading it back ------------------------------------------------------


def test_a_torn_line_does_not_make_the_history_unreadable(tmp_path):
    """A run killed mid-write should cost one record, not all of them."""
    path = tmp_path / "calls.jsonl"
    good = CallRecord(
        timestamp="2026-09-14T00:00:00+00:00",
        provider="litellm",
        provider_class="third-party",
        model="m",
        subject="standard/x",
        site="generate",
    )
    path.write_text(good.as_json() + "\n" + '{"timestamp": "2026-' + "\n", encoding="utf-8")

    records = ledger.load(path)
    assert len(records) == 1
    assert records[0].subject == "standard/x"


def test_an_unknown_field_from_a_future_version_is_ignored(tmp_path):
    """So a newer ledger stays readable by an older checkout."""
    path = tmp_path / "calls.jsonl"
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-14T00:00:00+00:00",
                "provider": "litellm",
                "provider_class": "local",
                "model": "m",
                "subject": None,
                "site": None,
                "effort": "high",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert ledger.load(path)[0].model == "m"


def test_totals_distinguish_unpriced_from_free():
    """A hosted call with no price is unknown. A local model's call is free,
    by its boundary class, even where its endpoint reports no price (80's
    ruling on #379; this test used a local call as its unknown case before)."""
    unpriced = Totals()
    unpriced.add(CallRecord("t", "p", "cloud", "m", None, None))
    assert (unpriced.cost, unpriced.cost_usd) == ("unpriced (1)", None)

    free = Totals()
    free.add(CallRecord("t", "p", "local", "m", None, None, cost_usd=0.0))
    free.add(CallRecord("t", "p", "local", "m", None, None))
    assert (free.cost, free.calls_unpriced) == ("$0.0000", 0)


def test_one_unpriced_call_does_not_leave_the_rest_standing_as_the_whole():
    """#379: the sum of the priced calls was the group's cost whenever any
    was priced. 6 of 27 session ledgers mixed the two (#379)."""
    mixed = Totals()
    mixed.add(CallRecord("t", "p", "cloud", "m", None, None, cost_usd=0.25, cost_source="provider"))
    mixed.add(CallRecord("t", "p", "cloud", "m", None, None))
    assert mixed.cost_usd is None
    assert mixed.cost == "$0.2500 + 1 unpriced"


def test_summaries_group_by_any_field():
    records = [
        CallRecord("t", "p", "local", "flash", "standard/a", "generate"),
        CallRecord("t", "p", "local", "pro", "standard/a", "generate"),
        CallRecord("t", "p", "local", "flash", "standard/b", "ssp"),
    ]

    by_model = ledger.summarize(records, "model")
    assert by_model["flash"].calls == 2
    assert by_model["pro"].calls == 1

    by_subject = ledger.summarize(records, "subject")
    assert by_subject["standard/a"].calls == 2

    rendered = ledger.format_summary(records, "site")
    assert "generate" in rendered and "ssp" in rendered


def test_unattributed_calls_are_grouped_under_a_visible_label():
    records = [CallRecord("t", "p", "local", "m", None, None)]
    assert "(unattributed)" in ledger.format_summary(records, "subject")


# ---- configuration --------------------------------------------------------


def test_the_ledger_is_on_unless_config_turns_it_off():
    assert ledger.ledger_enabled({})
    assert ledger.ledger_enabled({"llm": {}})
    assert not ledger.ledger_enabled({"llm": {"ledger": {"enabled": False}}})


def test_turned_off_the_ledger_records_nothing_but_the_boundary_still_decides(
    tmp_path, monkeypatch
):
    """#327. This test used to assert `wrap` returned the bare provider when
    the ledger was off. The boundary decision now lives in the wrapper, so
    the bare provider meant no check at all: turning off a RECORD turned off
    a CONTROL. Off now means: decided, not written down."""
    from policyforge.llm.boundary import BoundaryViolation

    monkeypatch.chdir(tmp_path)
    inner = FakeProvider()
    path = tmp_path / "calls.jsonl"
    config = {"llm": {"provider": "anthropic", "ledger": {"enabled": False, "path": str(path)}}}

    wrapped = ledger.wrap(inner, config)
    wrapped.generate(system="S", prompt="P")
    with (
        ledger.about("x", content_class="licensed"),
        pytest.raises(BoundaryViolation, match="licensed content -> third-party"),
    ):
        wrapped.generate(system="S", prompt="licensed text")

    assert not path.exists() and not (tmp_path / "output").exists(), "nothing recorded"
    assert len(inner.calls) == 1, "the licensed call never reached the provider"


def test_wrapping_carries_the_provider_class_from_the_boundary(tmp_path):
    inner = FakeProvider()
    config = {
        "llm": {
            "provider": "local",
            "base_url": "http://localhost:11434/v1",
            "ledger": {"path": str(tmp_path / "calls.jsonl")},
        }
    }

    wrapped = ledger.wrap(inner, config)
    wrapped.generate(system="S", prompt="P")

    assert ledger.load(tmp_path / "calls.jsonl")[0].provider_class == "local"


def test_the_wrapper_passes_through_attributes_of_the_provider_inside(tmp_path):
    """So a cascade's escalation counters stay visible to the eval meter.

    `_Metered.summary()` reaches for `escalations` and `primary_calls`; an
    opaque wrapper would make a working cascade indistinguishable from a
    dead one, which is the regression that counter was added to fix.
    """
    inner = FakeProvider()
    inner.escalations = 3
    inner.primary_calls = 40

    wrapped = _recorder(tmp_path, inner)

    assert wrapped.escalations == 3
    assert wrapped.primary_calls == 40
    assert wrapped.inner is inner


# ---- the flags the wrapper must forward -----------------------------------
#
# `__getattr__` never reaches a method the base class defines, and every
# `supports_*` flag is defined there as `return False`. So a flag the wrapper
# forgets to delegate answers False for every provider — which is how native
# citations, effort, caching and batching all went dark on Anthropic and
# Vertex while every test passed. The flags are discovered from the base
# class rather than listed here: a new one that the wrapper does not forward
# fails this test before it can fail a user.

from policyforge.llm.base import LLMProvider, capability_flags  # noqa: E402

CAPABILITY_FLAGS = sorted(
    name
    for name in dir(LLMProvider)
    if name.startswith("supports_") and callable(getattr(LLMProvider, name))
)


def _provider_answering(answer: bool) -> LLMProvider:
    """A real `LLMProvider` subclass, so the base-class defaults are in play."""

    class Flagged(LLMProvider):
        def generate(self, **kwargs):
            return LLMResponse(text="ok", model="flagged")

        def check(self):
            return True

    for name in CAPABILITY_FLAGS:
        setattr(Flagged, name, lambda self, _answer=answer: _answer)
    return Flagged()


def test_the_flags_are_discovered_not_listed():
    """An empty discovery would make the parametrized test below pass vacuously."""
    assert len(CAPABILITY_FLAGS) >= 5
    assert capability_flags() == CAPABILITY_FLAGS


@pytest.mark.parametrize("answer", [True, False])
@pytest.mark.parametrize("flag", CAPABILITY_FLAGS)
def test_every_capability_flag_is_answered_by_the_provider_inside(tmp_path, flag, answer):
    inner = _provider_answering(answer)
    wrapped = _recorder(tmp_path, inner)

    assert getattr(inner, flag)() is answer
    assert getattr(wrapped, flag)() is answer


def test_effort_and_cache_arguments_travel_through_the_wrapper(tmp_path):
    """The flags and the arguments go together.

    Once the wrapper says a provider honours effort and caching,
    `llm/effort.py` passes `effort`, `cache` and `cache_prefix` to
    `generate`. A wrapper with a fixed signature would turn every such call
    into a TypeError — and inline the prefix it was supposed to mark.
    """
    from policyforge.llm import effort

    class Honours(LLMProvider):
        seen: dict = {}

        def generate(
            self,
            *,
            system,
            prompt,
            max_tokens=4096,
            temperature=0.2,
            effort=None,
            cache=False,
            cache_prefix=None,
        ):
            self.seen = {
                "prompt": prompt,
                "effort": effort,
                "cache": cache,
                "cache_prefix": cache_prefix,
            }
            return LLMResponse(text="ok", model="honours", cached_input_tokens=2048)

        def check(self):
            return True

        def supports_effort(self):
            return True

        def supports_caching(self):
            return True

    inner = Honours()
    wrapped = _recorder(tmp_path, inner)

    effort.call(wrapped, effort="high", cache=True, cache_prefix="ORG ", system="S", prompt="P")

    assert inner.seen == {"prompt": "P", "effort": "high", "cache": True, "cache_prefix": "ORG "}
    (record,) = ledger.load(tmp_path / "calls.jsonl")
    assert record.cached_input_tokens == 2048


def test_a_grounded_call_is_recorded_and_its_documents_reach_the_provider(tmp_path):
    """`generate_grounded` passes through the way `generate_json` does.

    The passages leave the machine as document blocks rather than prompt
    text, so the call is exposure like any other and belongs in the ledger —
    and, like a prompt, the passages themselves must not be written to it.
    """

    class Grounding(LLMProvider):
        seen = None

        def generate(self, **kwargs):
            return LLMResponse(text="prose", model="grounding")

        def check(self):
            return True

        def supports_grounding(self):
            return True

        def generate_grounded(self, *, system, prompt, documents, **kwargs):
            self.seen = (documents, kwargs)
            return LLMResponse(text="cited [1]", model="grounding", citations=[])

    inner = Grounding()
    wrapped = _recorder(tmp_path, inner)
    passage = "HITRUST 01.c verbatim requirement text"

    response = wrapped.generate_grounded(
        system="S", prompt="P", documents=[passage, "another"], effort="low"
    )

    assert response.citations == []
    assert inner.seen == ([passage, "another"], {"effort": "low"})
    (record,) = ledger.load(tmp_path / "calls.jsonl")
    assert record.model == "grounding"
    assert passage not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")


def test_a_failed_grounded_call_is_recorded_and_re_raised(tmp_path):
    class Refusing(LLMProvider):
        def generate(self, **kwargs):
            return LLMResponse(text="ok", model="m")

        def check(self):
            return True

        def supports_grounding(self):
            return True

        def generate_grounded(self, **kwargs):
            raise RuntimeError("overloaded")

    wrapped = _recorder(tmp_path, Refusing())

    with pytest.raises(RuntimeError):
        wrapped.generate_grounded(system="S", prompt="P", documents=[])

    (record,) = ledger.load(tmp_path / "calls.jsonl")
    assert record.error == "RuntimeError"


# ---- the composition production actually runs -----------------------------
#
# The wrapper tests above use fakes. Every command builds its provider with
# `get_provider`, which is `_build_provider` inside `wrap`, and that is the
# object whose flags decide what a request contains. So the same question is
# asked of the real thing: for every provider the factory can build without
# a network, the wrapped object must answer each flag the way the provider
# inside it does. `scripts/eval_zardoz.py --model` builds a LiteLLMProvider
# directly and never saw this — which is why the measured request and the
# production request differed for two days.

_TEST_KEY = "POLICYFORGE_TEST_KEY"

OFFLINE_CONFIGS = {
    "anthropic": {"provider": "anthropic", "model": "claude-sonnet-5", "api_key_env": _TEST_KEY},
    "litellm": {
        "provider": "litellm",
        "model": "openrouter/deepseek/deepseek-v4-flash",
        "api_key_env": _TEST_KEY,
    },
    "local": {"provider": "local", "model": "qwen3:14b", "base_url": "http://localhost:11434/v1"},
    "bedrock": {"provider": "bedrock", "model": "anthropic.claude-sonnet-5", "region": "us-east-1"},
    "vertex": {"provider": "vertex", "model": "claude-sonnet-5", "project_id": "example-project"},
    "cascade": {
        "provider": "cascade",
        "primary": {
            "provider": "local",
            "model": "qwen3:14b",
            "base_url": "http://127.0.0.1:11434/v1",
        },
        "escalate_to": {
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "api_key_env": _TEST_KEY,
        },
    },
}

#: Built from the core dependencies alone. A skip on one of these would be
#: the test quietly stopping, which is the failure this file exists to catch.
ALWAYS_CONSTRUCTIBLE = {"anthropic", "local", "cascade"}


@pytest.mark.parametrize("name", sorted(OFFLINE_CONFIGS))
def test_a_provider_built_from_config_keeps_every_flag_of_the_one_inside(
    tmp_path, monkeypatch, name
):
    from policyforge.llm.base import get_provider

    monkeypatch.setenv(_TEST_KEY, "not-a-real-key")
    config = {"llm": {**OFFLINE_CONFIGS[name], "ledger": {"path": str(tmp_path / "calls.jsonl")}}}
    try:
        provider = get_provider(config)
    except (ImportError, RuntimeError) as exc:
        if name in ALWAYS_CONSTRUCTIBLE:
            raise
        pytest.skip(f"{name} needs an extra this environment lacks: {exc}")

    assert isinstance(provider, RecordingProvider)
    for flag in CAPABILITY_FLAGS:
        assert getattr(provider, flag)() == getattr(provider.inner, flag)(), flag


def test_the_litellm_provider_keeps_every_flag_through_the_wrapper_without_litellm(tmp_path):
    """The default provider, held to the rule where CI can run it.

    The `get_provider` case above skips when the `litellm` extra is absent,
    and CI installs only `.[dev]` — so the provider this bug hit hardest
    would be the one CI never checked. `LiteLLMProvider` takes an injected
    `completion` callable precisely so its request handling runs without
    the package, and the flags are read the same way. This never skips.
    """
    from policyforge.llm.base import capabilities
    from policyforge.llm.litellm_provider import LiteLLMProvider

    inner = LiteLLMProvider(
        model="openrouter/deepseek/deepseek-v4-flash",
        completion=lambda **kwargs: None,
    )
    wrapped = ledger.wrap(
        inner,
        {"llm": {"provider": "litellm", "ledger": {"path": str(tmp_path / "calls.jsonl")}}},
    )

    assert isinstance(wrapped, RecordingProvider)
    for flag in CAPABILITY_FLAGS:
        assert getattr(wrapped, flag)() == getattr(inner, flag)(), flag
    assert inner.supports_effort() is True
    assert wrapped.supports_effort() is True
    assert capabilities(wrapped) == capabilities(inner)
