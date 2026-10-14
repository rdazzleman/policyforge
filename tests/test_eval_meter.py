"""The eval harness must measure the code path production runs.

`scripts/eval_zardoz.py` wraps the provider in `_Metered` to count calls and
cost. The wrapper exposed `generate` and `check` and nothing else. Every
capability the rest of the project asks for — `supports_schema`,
`generate_json`, `supports_effort` — was therefore absent under the harness,
and every caller took its fallback:

- routing used its prose path instead of the schema-constrained call;
- argument filling and chaining were skipped entirely;
- no effort level was ever sent.

So eval runs measured the fallbacks, not what a user of a schema-capable
model gets. It was found when a live chaining eval scored 0/6 on compound
questions that a direct probe of the same model and prompt got 12/12 on. The
wrapper predates schema routing, so this held for every epoch that measured it.

These tests pin that the wrapper is transparent about capabilities and meters
every call that costs money, not only `generate`.
"""

from __future__ import annotations

import json

import pytest

from policyforge.llm.base import LLMResponse
from scripts.eval_zardoz import _Metered


class Capable:
    """A provider with every optional capability, each call priced."""

    model = "capable"

    def __init__(self):
        self.calls: list[str] = []

    def supports_schema(self):
        return True

    def supports_effort(self):
        return True

    def supports_grounding(self):
        return True

    def generate(self, **kwargs):
        self.calls.append("generate")
        return LLMResponse(text="documents", model=self.model, cost_usd=0.01)

    def generate_json(self, **kwargs):
        self.calls.append("generate_json")
        return LLMResponse(
            text=json.dumps({"analysis": "coverage"}), model=self.model, cost_usd=0.02
        )

    def generate_grounded(self, **kwargs):
        self.calls.append("generate_grounded")
        return LLMResponse(text="answer [1]", model=self.model, cost_usd=0.03)

    def check(self):
        return True


@pytest.mark.parametrize("capability", ["supports_schema", "supports_effort", "supports_grounding"])
def test_the_meter_reports_what_the_provider_can_do(capability):
    """Absent, every caller silently took its fallback path under the harness."""
    assert getattr(_Metered(Capable()), capability)() is True


def test_a_provider_without_a_capability_still_reads_as_without_it():
    """Transparent both ways: the meter must not invent a capability either."""

    class Plain:
        def generate(self, **kwargs):
            return LLMResponse(text="x", model="plain")

        def check(self):
            return True

    metered = _Metered(Plain())
    assert not getattr(metered, "supports_schema", lambda: False)()


def test_structured_calls_are_metered_like_plain_ones():
    """A schema call costs money too; a meter that skipped it under-reports
    exactly the runs that use the production path."""
    inner = Capable()
    metered = _Metered(inner)

    metered.generate(system="s", prompt="p")
    metered.generate_json(system="s", prompt="p", schema={})
    metered.generate_grounded(system="s", prompt="p", documents=[])

    assert inner.calls == ["generate", "generate_json", "generate_grounded"]
    assert metered.calls == 3
    assert metered.cost == pytest.approx(0.06)
    assert metered.priced


def test_a_structured_call_that_raises_is_still_counted():
    """Same rule as `generate`: it reached the vendor, so it was billed."""

    class Failing(Capable):
        def generate_json(self, **kwargs):
            raise RuntimeError("upstream refused")

    metered = _Metered(Failing())
    with pytest.raises(RuntimeError):
        metered.generate_json(system="s", prompt="p", schema={})
    assert metered.calls == 1
    assert metered.unpriced == 1


def test_a_raised_call_carrying_a_cost_is_counted_as_priced():
    class PricedException(RuntimeError):
        def __init__(self, msg, cost):
            super().__init__(msg)
            self.cost_usd = cost

    class FailingPriced(Capable):
        def generate(self, **kwargs):
            raise PricedException("upstream refused after compute", 0.05)

    metered = _Metered(FailingPriced())
    with pytest.raises(PricedException):
        metered.generate(system="s", prompt="p")
    assert metered.calls == 1
    assert metered.unpriced == 0
    assert metered.priced_calls == 1
    assert metered.cost == pytest.approx(0.05)


def test_a_raised_call_carrying_only_its_last_cost_counts_it_and_stays_raised():
    class LastPricedException(RuntimeError):
        def __init__(self, msg, cost):
            super().__init__(msg)
            self.last_cost_usd = cost

    class FailingLastPriced(Capable):
        def generate(self, **kwargs):
            raise LastPricedException("schema failed", 0.07)

    metered = _Metered(FailingLastPriced())
    with pytest.raises(LastPricedException):
        metered.generate(system="s", prompt="p")
    assert metered.calls == 1
    # The total is still unknown, and the call RAISED: it counts as raised,
    # never as "returned no price", while its known last attempt counts.
    assert metered.unpriced == 1
    assert metered.returned_unpriced == 0
    assert metered.priced_calls == 0
    assert metered.cost == pytest.approx(0.07)
    assert metered.summary() == (
        "1 model call(s), $0.0700 known + 1 of unknown cost "
        "(1 raised, billed but unpriced; 0 returned no price)"
    )


class _Billed(RuntimeError):
    """An exception carrying what was billed, as `ReasoningBudgetExhausted`
    and a schema reply error do."""

    def __init__(self, cost_usd=None, last_cost_usd=None):
        super().__init__("billed, then failed")
        self.cost_usd = cost_usd
        self.last_cost_usd = last_cost_usd


class _Raises(Capable):
    def __init__(self, exc, provider_class=None):
        super().__init__()
        self._exc = exc
        if provider_class is not None:
            self._provider_class = provider_class

    def generate(self, **kwargs):
        raise self._exc


def _raise_once(exc, provider_class=None):
    metered = _Metered(_Raises(exc, provider_class))
    with pytest.raises(_Billed):
        metered.generate(system="s", prompt="p")
    return metered


def test_a_raised_call_with_both_costs_counts_its_total_once():
    """The ledger's order: a known total wins, and the last attempt is part
    of it, never an addend (`ledger.cost_split`)."""
    metered = _raise_once(_Billed(cost_usd=0.05, last_cost_usd=0.02))
    assert metered.cost == pytest.approx(0.05)
    assert (metered.priced_calls, metered.unpriced, metered.returned_unpriced) == (1, 0, 0)


def test_a_local_call_that_raises_without_a_cost_is_free():
    """As on the returned path: a local model is priced at 0.0 by its
    boundary class, so its raise is not a call of unknown cost."""
    metered = _raise_once(_Billed(), provider_class="local")
    assert (metered.priced_calls, metered.unpriced, metered.returned_unpriced) == (1, 0, 0)
    assert metered.cost == 0.0


def test_a_raised_call_with_no_cost_stays_raised_and_unpriced():
    metered = _raise_once(_Billed())
    assert (metered.priced_calls, metered.unpriced, metered.returned_unpriced) == (0, 1, 0)
    assert metered.cost == 0.0 and not metered.priced


def test_routing_under_the_meter_takes_the_schema_path():
    """The end-to-end symptom: routing through the harness must use the
    same call production does."""
    from policyforge.zardoz.skills import route

    inner = Capable()
    assert route("which controls does nobody own?", _Metered(inner)) == "coverage"
    assert "generate_json" in inner.calls
    assert "generate" not in inner.calls


# ---- #446: a reply that returns with no price ---------------------------------


class _Priced:
    """Replies costing what it is given, in order: None for no price."""

    def __init__(self, *costs, provider_class=None):
        self.costs = list(costs)
        if provider_class is not None:
            self._provider_class = provider_class

    def generate(self, **kwargs):
        return LLMResponse(text="x", model="m", cost_usd=self.costs.pop(0))


def _summary(*costs, provider_class=None) -> str:
    metered = _Metered(_Priced(*costs), provider_class=provider_class)
    for _ in costs:
        metered.generate(system="s", prompt="p")
    return metered.summary()


def test_a_known_part_is_not_the_total_when_a_reply_has_no_price():
    """ba's repro (#450, carried by #446): one priced $0.10 reply and one
    unpriced read "$0.1000 total", with "each" divided by both."""
    line = _summary(0.1, None)
    assert "total" not in line, line
    assert line.startswith("2 model call(s), $0.1000 known + 1 of unknown cost"), line
    assert "$0.10000 each over 1 priced" in line, line
    assert "1 returned no price" in line, line


def test_a_fully_priced_run_reads_exactly_as_before():
    assert _summary(0.2, 0.2) == "2 model call(s), $0.4000 total, $0.20000 each"


def test_a_local_models_unpriced_reply_is_free_not_unknown():
    assert _summary(0.2, None, provider_class="local") == (
        "2 model call(s), $0.2000 total, $0.10000 each"
    )


def test_the_class_is_read_off_the_ledger_wrapper_when_not_given():
    metered = _Metered(_Priced(0.2, None, provider_class="local"))
    for _ in range(2):
        metered.generate(system="s", prompt="p")
    assert metered.summary() == "2 model call(s), $0.2000 total, $0.10000 each"


def test_build_provider_tells_the_meter_the_configured_class(monkeypatch):
    import evals.provider as ep

    monkeypatch.setattr(ep, "get_provider", lambda config: _Priced())
    local = {"llm": {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "q"}}
    assert ep.build_provider(local)._provider_class == "local"
