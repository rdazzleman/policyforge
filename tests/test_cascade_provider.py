"""The cheap-first cascade.

What is worth testing here is mostly what it does *not* do. It escalates on
one narrow signal — the cheap model spent its whole budget reasoning and
never began an answer — and on nothing else, because nothing else is
visible from behind `LLMProvider.generate()`. A cascade that escalated on
any failure would pay twice to hit the same rate limit; one that escalated
on a bad answer would need the passages, which it never receives.
"""

from __future__ import annotations

import pytest

from policyforge.llm._inline_thinking import ReasoningBudgetExhausted
from policyforge.llm.base import LLMResponse
from policyforge.llm.cascade_provider import CascadeProvider


class FakeProvider:
    """Answers, or raises whatever it was told to raise."""

    def __init__(self, name, raises=None, text="answered", cost=0.001):
        self.model = name
        self.raises = raises
        self.text = text
        self.cost = cost
        self.calls = 0
        self.checked = False

    def generate(self, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return LLMResponse(text=self.text, model=self.model, cost_usd=self.cost)

    def check(self):
        self.checked = True
        return self.raises is None


def _cascade(primary, escalate_to):
    from policyforge.llm.cascade_provider import CascadeProvider

    return CascadeProvider(primary=primary, escalate_to=escalate_to)


def test_the_cheap_model_answers_and_the_strong_one_is_never_called():
    flash = FakeProvider("flash")
    pro = FakeProvider("pro")

    result = _cascade(flash, pro).generate(system="s", prompt="p")

    assert result.model == "flash"
    assert (flash.calls, pro.calls) == (1, 0)


def test_exhaustion_escalates_to_the_stronger_model():
    flash = FakeProvider("flash", raises=ReasoningBudgetExhausted("no room"))
    pro = FakeProvider("pro")

    result = _cascade(flash, pro).generate(system="s", prompt="p")

    assert result.model == "pro"
    assert (flash.calls, pro.calls) == (1, 1)


def test_the_answer_names_the_model_that_actually_answered():
    """Nothing is hidden: a caller can always tell which half served it."""
    flash = FakeProvider("flash", raises=ReasoningBudgetExhausted("no room"))
    pro = FakeProvider("pro", cost=0.06)

    result = _cascade(flash, pro).generate(system="s", prompt="p")

    assert result.model == "pro"
    assert result.cost_usd == 0.06


def test_an_unrelated_failure_does_not_escalate():
    """Escalating a rate limit or a dead key would spend twice to fail
    twice — the stronger model hits the same wall."""
    flash = FakeProvider("flash", raises=RuntimeError("Rate limit exceeded"))
    pro = FakeProvider("pro")

    with pytest.raises(RuntimeError, match="Rate limit"):
        _cascade(flash, pro).generate(system="s", prompt="p")

    assert pro.calls == 0


def test_a_failure_of_the_stronger_model_is_reported_on_its_own_terms():
    """Not chained to the first. "Pro also ran out of room" is the useful
    message; "Pro failed while handling Flash failing" buries it."""
    flash = FakeProvider("flash", raises=ReasoningBudgetExhausted("flash had no room"))
    pro = FakeProvider("pro", raises=ReasoningBudgetExhausted("pro had no room either"))

    with pytest.raises(ReasoningBudgetExhausted) as caught:
        _cascade(flash, pro).generate(system="s", prompt="p")

    assert "pro had no room either" in str(caught.value)
    assert caught.value.__context__ is None, "should not be raised inside the except block"


def test_it_counts_how_often_the_cheap_model_sufficed():
    """The economic case rests entirely on that ratio, and an unmeasured
    cascade is one nobody can tell is working."""
    flash = FakeProvider("flash")
    pro = FakeProvider("pro")
    cascade = _cascade(flash, pro)

    cascade.generate(system="s", prompt="p")
    cascade.generate(system="s", prompt="p")
    flash.raises = ReasoningBudgetExhausted("no room")
    cascade.generate(system="s", prompt="p")

    assert (cascade.primary_calls, cascade.escalations) == (3, 1)


def test_check_requires_both_halves():
    """Checking only the primary would report a healthy cascade whose
    fallback has a dead key."""
    flash = FakeProvider("flash")
    pro = FakeProvider("pro", raises=RuntimeError("bad key"))

    assert _cascade(flash, pro).check() is False
    assert flash.checked and pro.checked


def test_check_passes_when_both_work():
    flash, pro = FakeProvider("flash"), FakeProvider("pro")

    assert _cascade(flash, pro).check() is True


def test_the_model_name_shows_both_halves():
    assert _cascade(FakeProvider("flash"), FakeProvider("pro")).model == "flash -> pro"


def test_get_provider_builds_a_cascade_from_nested_blocks(monkeypatch):
    from policyforge.llm.base import get_provider

    built = []

    class FakeLiteLLMProvider:
        def __init__(self, **kwargs):
            self.model = kwargs["model"]
            built.append(kwargs["model"])

    monkeypatch.setattr("policyforge.llm.litellm_provider.LiteLLMProvider", FakeLiteLLMProvider)

    provider = get_provider(
        {
            "llm": {
                "provider": "cascade",
                "primary": {"provider": "litellm", "model": "flash-model"},
                "escalate_to": {"provider": "litellm", "model": "pro-model"},
            }
        }
    )

    assert built == ["flash-model", "pro-model"]
    assert provider.model == "flash-model -> pro-model"


def test_a_cascade_without_both_halves_says_which_is_missing():
    from policyforge.llm.base import get_provider

    with pytest.raises(ValueError) as caught:
        get_provider(
            {"llm": {"provider": "cascade", "primary": {"provider": "litellm", "model": "m"}}}
        )

    assert "escalate_to" in str(caught.value)


def test_an_unknown_provider_names_cascade_among_the_options():
    from policyforge.llm.base import get_provider

    with pytest.raises(ValueError) as caught:
        get_provider({"llm": {"provider": "nonsense", "model": "m"}})

    assert "cascade" in str(caught.value)


def test_llm_check_can_name_a_cascade(monkeypatch, tmp_path):
    """A cascade config has no top-level `model` — only two nested blocks —
    so anything reading config["llm"]["model"] to report what ran raises
    KeyError. `llm-check` did, and only a real run found it."""
    from click.testing import CliRunner

    from policyforge.cli import cli

    config = tmp_path / "cascade.yaml"
    config.write_text(
        "llm:\n"
        "  provider: cascade\n"
        "  primary:\n"
        "    provider: litellm\n"
        "    model: flash-model\n"
        "  escalate_to:\n"
        "    provider: litellm\n"
        "    model: pro-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POLICYFORGE_CONFIG", str(config))

    class FakeLiteLLMProvider:
        def __init__(self, **kwargs):
            self.model = kwargs["model"]

        def check(self):
            return True

    monkeypatch.setattr("policyforge.llm.litellm_provider.LiteLLMProvider", FakeLiteLLMProvider)

    result = CliRunner().invoke(cli, ["llm-check"])

    assert result.exit_code == 0, result.output
    assert "flash-model -> pro-model" in result.output


def test_a_run_reports_how_often_it_escalated():
    """The counter existed and nothing printed it, so a working cascade and
    a dead one produced identical reports."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path.cwd()))
    from scripts.eval_zardoz import _Metered

    flash = FakeProvider("flash", raises=ReasoningBudgetExhausted("no room"))
    pro = FakeProvider("pro")
    metered = _Metered(_cascade(flash, pro))

    metered.generate(system="s", prompt="p")

    assert "1/1 escalated to the stronger model (100%)" in metered.summary()


def test_a_plain_provider_reports_no_escalation_line():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path.cwd()))
    from scripts.eval_zardoz import _Metered

    metered = _Metered(FakeProvider("flash"))
    metered.generate(system="s", prompt="p")

    assert "escalated" not in metered.summary()


class SchemaProvider(FakeProvider):
    """A provider that can be held to a schema."""

    def __init__(self, name, can=True, **kw):
        super().__init__(name, **kw)
        self.can = can
        self.json_calls = 0

    def supports_schema(self):
        return self.can

    def generate_json(self, **kwargs):
        self.json_calls += 1
        if self.raises is not None:
            raise self.raises
        return LLMResponse(text='{"analysis": "history"}', model=self.model)


def test_a_cascade_constrains_output_only_when_both_halves_can():
    """One that constrained the cheap model and fell back to unconstrained
    prose would hand the caller JSON most of the time, which is worse than
    never promising it."""
    assert _cascade(SchemaProvider("f"), SchemaProvider("p")).supports_schema() is True
    assert _cascade(SchemaProvider("f"), SchemaProvider("p", can=False)).supports_schema() is False
    assert _cascade(SchemaProvider("f", can=False), SchemaProvider("p")).supports_schema() is False


def test_a_structured_call_escalates_the_same_way_a_plain_one_does():
    flash = SchemaProvider("flash", raises=ReasoningBudgetExhausted("no room"))
    pro = SchemaProvider("pro")
    cascade = _cascade(flash, pro)

    result = cascade.generate_json(system="s", prompt="p", schema={})

    assert result.model == "pro"
    assert (cascade.primary_calls, cascade.escalations) == (1, 1)


# ---- every capability flag, by the stated rule ------------------------------
#
# Until this was written the cascade forwarded `supports_schema` and let the
# other four fall to the base class's False, so a flash -> pro cascade of two
# capable models sent no effort, marked no cache prefix and asked for no
# citations. The rule is in the cascade's own comment: a flag is True only
# when both halves honour it, except batch, which is never. The flags are
# discovered from LLMProvider so a new one is held to the rule unasked.


def _cascade_flags():
    from policyforge.llm.base import LLMProvider

    return sorted(
        name
        for name in dir(LLMProvider)
        if name.startswith("supports_") and callable(getattr(LLMProvider, name))
    )


class _Half:
    """A provider that says yes to exactly the flags it is given."""

    def __init__(self, name, *flags):
        self.model = name
        self.seen = []
        for flag in flags:
            setattr(self, flag, lambda: True)

    def generate(self, **kwargs):
        self.seen.append(kwargs)
        return LLMResponse(text="ok", model=self.model)

    generate_json = generate
    generate_grounded = generate

    def check(self):
        return True


@pytest.mark.parametrize("flag", _cascade_flags())
def test_a_flag_is_true_only_when_both_halves_honour_it(flag):
    both = CascadeProvider(primary=_Half("a", flag), escalate_to=_Half("b", flag))
    only_primary = CascadeProvider(primary=_Half("a", flag), escalate_to=_Half("b"))
    only_escalation = CascadeProvider(primary=_Half("a"), escalate_to=_Half("b", flag))
    neither = CascadeProvider(primary=_Half("a"), escalate_to=_Half("b"))

    expected_when_both = flag != "supports_batch"
    assert getattr(both, flag)() is expected_when_both, flag
    assert getattr(only_primary, flag)() is False, flag
    assert getattr(only_escalation, flag)() is False, flag
    assert getattr(neither, flag)() is False, flag


def test_request_hints_reach_whichever_half_answers():
    """Effort and the cache prefix travel to the primary, and to the
    escalation when the primary could not answer."""
    from policyforge.llm import effort
    from policyforge.llm._inline_thinking import ReasoningBudgetExhausted

    class Exhausted(_Half):
        def generate(self, **kwargs):
            self.seen.append(kwargs)
            raise ReasoningBudgetExhausted("spent it all")

    flags = ("supports_effort", "supports_caching")
    primary = Exhausted("flash", *flags)
    strong = _Half("pro", *flags)
    cascade = CascadeProvider(primary=primary, escalate_to=strong)

    response = effort.call(
        cascade, effort="high", cache=True, cache_prefix="ORG ", system="S", prompt="P"
    )

    assert response.model == "pro"
    for half in (primary, strong):
        (request,) = half.seen
        assert request["effort"] == "high"
        assert request["cache"] is True
        assert request["cache_prefix"] == "ORG "
        assert request["prompt"] == "P"


def test_a_grounded_call_escalates_the_same_way_a_plain_one_does():
    from policyforge.llm._inline_thinking import ReasoningBudgetExhausted

    class Exhausted(_Half):
        def generate_grounded(self, **kwargs):
            self.seen.append(kwargs)
            raise ReasoningBudgetExhausted("spent it all")

    primary = Exhausted("flash", "supports_grounding")
    strong = _Half("pro", "supports_grounding")
    cascade = CascadeProvider(primary=primary, escalate_to=strong)

    response = cascade.generate_grounded(system="S", prompt="P", documents=["d"])

    assert response.model == "pro"
    assert cascade.escalations == 1
    assert strong.seen[0]["documents"] == ["d"]


def test_get_provider_builds_a_cascade_whose_flags_follow_the_rule(monkeypatch, tmp_path):
    """Through the factory and the ledger wrapper, with real halves behind
    injected fakes: a local primary that advertises nothing beyond generate,
    and an Anthropic escalation that advertises everything. Every flag on
    the built cascade must be the conjunction, and batch False."""
    import sys
    import types

    from policyforge.llm.base import capabilities, get_provider
    from policyforge.llm.ledger import RecordingProvider

    class _Messages:
        def create(self, **kwargs):
            class Block:
                type = "text"
                text = "ok"

            class Usage:
                input_tokens = 1
                output_tokens = 1

            class Message:
                content = [Block()]
                usage = Usage()
                stop_reason = "end_turn"

            return Message()

    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = lambda *, api_key: types.SimpleNamespace(messages=_Messages())
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)

    class _Session:
        def post(self, url, *, json, headers, timeout):
            class Response:
                status_code = 200
                text = ""

                @staticmethod
                def json():
                    return {
                        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                        "usage": {},
                    }

            return Response()

    import requests

    monkeypatch.setattr(requests, "Session", _Session)
    monkeypatch.setenv("POLICYFORGE_CASCADE_KEY", "not-a-real-key")

    config = {
        "llm": {
            "provider": "cascade",
            "primary": {
                "provider": "local",
                "model": "qwen3:14b",
                "base_url": "http://localhost:11434/v1",
            },
            "escalate_to": {
                "provider": "anthropic",
                "model": "claude-sonnet-5",
                "api_key_env": "POLICYFORGE_CASCADE_KEY",
            },
            "ledger": {"path": str(tmp_path / "calls.jsonl")},
        }
    }
    built = get_provider(config)
    assert isinstance(built, RecordingProvider)
    cascade = built.inner
    assert isinstance(cascade, CascadeProvider)

    primary, strong = cascade._primary, cascade._escalate_to
    for flag in _cascade_flags():
        p = getattr(primary, flag, None)
        e = getattr(strong, flag, None)
        expected = bool(p and p()) and bool(e and e()) and flag != "supports_batch"
        assert getattr(built, flag)() is expected, flag
    # The escalation advertises everything, so the cascade advertises
    # exactly what the local half can do. That is schema and nothing else:
    # `generate_json` on the local provider was implemented once a live
    # call proved a served model honours `response_format`, which is the
    # only route on which licensed text never leaves the machine. Pinned as
    # the exact set rather than "all False", so the next capability the
    # local half gains has to be asserted here deliberately.
    assert capabilities(built) == {
        "batch": False,
        "caching": False,
        "effort": False,
        "grounding": False,
        "schema": True,
    }
    assert all(capabilities(strong).values())
