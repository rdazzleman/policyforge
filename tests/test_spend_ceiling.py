"""A spend ceiling (#125): refuse a run that would cost more, stop one that does.

80's spec, one test per clause, with fake providers and fake rates (nothing
here reaches a network or a real price table):

1. the ceiling is `budget.max_usd`, or `--max-cost` for one run, which wins
   in both directions; with neither, nothing changes;
2. where the number of calls is known, a run whose upper-bound estimate is
   above the ceiling is refused before any request, saying so (exit 2);
3. the run also keeps a running total, and makes no further call once it has
   passed the ceiling, saying REQUESTED beside COMPLETED; the ledger records
   the stop;
4. a hosted model with no price is refused by name under a ceiling, and a
   local model passes at $0.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.config import ConfigError
from policyforge.llm import ledger, spend
from policyforge.llm.base import LLMResponse

IN_PER_TOKEN = 3e-6  # $3 per million input tokens
OUT_PER_TOKEN = 15e-6  # $15 per million output tokens
HOSTED = "fake-hosted"
HOSTED_CONFIG = {"provider": "anthropic", "model": HOSTED}


@pytest.fixture(autouse=True)
def fake_rates(monkeypatch):
    """The model map as this test defines it: `fake-hosted` is priced, and
    anything else is not in it."""

    def price(model):
        if model == HOSTED:
            return IN_PER_TOKEN, OUT_PER_TOKEN, "fake-table"
        return None

    monkeypatch.setattr("policyforge.llm.escalation._price", price)


class Priced:
    """A provider that answers every request at a fixed cost. `last_cost` is
    the known part of a request whose own total is unknown (an earlier attempt
    had no price)."""

    def __init__(
        self,
        cost: float | None = 0.03,
        model: str = HOSTED,
        last_cost: float | None = None,
        tokens: tuple[int, int] | None = None,
    ):
        self.cost = cost
        self.last_cost = last_cost
        self.model = model
        #: (input, output) tokens the response reports, or None: only
        #: litellm reports a cost, and the others return tokens or nothing.
        self.tokens = tokens
        self.calls = 0

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
        self.calls += 1
        return LLMResponse(
            text="- text",
            model=self.model,
            cost_usd=self.cost,
            last_cost_usd=self.last_cost,
            input_tokens=self.tokens[0] if self.tokens else None,
            output_tokens=self.tokens[1] if self.tokens else None,
        )

    def generate_json(self, *, system, prompt, schema, max_tokens=4096, **kwargs):
        return self.generate(system=system, prompt=prompt, max_tokens=max_tokens)

    def check(self):
        return True


def _wrapped(inner, config, tmp_path):
    config = {
        **config,
        "llm": {**config.get("llm", {}), "ledger": {"path": str(tmp_path / "l.jsonl")}},
    }
    return ledger.wrap(inner, config), tmp_path / "l.jsonl"


# ---- 1. the ceiling and the flag ------------------------------------------------


def test_with_no_ceiling_nothing_is_set():
    assert spend.ceiling_of({}) is None
    assert spend.ceiling_of({"budget": {}}) is None
    assert spend.with_max_cost({"a": 1}, None) == {"a": 1}


def test_the_ceiling_is_read_from_budget_max_usd():
    assert spend.ceiling_of({"budget": {"max_usd": 5}}) == 5.0
    assert spend.ceiling_of({"budget": {"max_usd": 0.25}}) == 0.25


@pytest.mark.parametrize("bad", [0, -1, "five", True, [1]])
def test_a_ceiling_that_is_not_a_positive_number_is_an_error_not_no_ceiling(bad):
    with pytest.raises(ConfigError, match=r"budget\.max_usd"):
        spend.ceiling_of({"budget": {"max_usd": bad}})


def test_max_cost_overrides_config_in_both_directions():
    configured = {"budget": {"max_usd": 5.0}, "keep": 1}
    tighter = spend.with_max_cost(configured, 0.5)
    assert spend.ceiling_of(tighter) == 0.5 and tighter["keep"] == 1
    looser = spend.with_max_cost(configured, 50)
    assert spend.ceiling_of(looser) == 50.0
    assert spend.ceiling_of(spend.with_max_cost({}, 2)) == 2.0
    assert configured["budget"]["max_usd"] == 5.0, "the config it was given is not changed"


# ---- 2. refused before it starts -----------------------------------------------


def test_the_estimate_is_calls_times_input_plus_max_tokens_at_the_models_rates():
    planned = spend.estimate(
        model=HOSTED, provider_class="third-party", calls=10, input_tokens=100_000, max_tokens=1000
    )
    # 100,000 input tokens at $3/M, and 10 x 1,000 output tokens at $15/M.
    assert planned.usd == pytest.approx(0.30 + 0.15)


def test_a_run_above_the_ceiling_is_refused_with_the_figures():
    planned = spend.estimate(
        model=HOSTED, provider_class="third-party", calls=10, input_tokens=100_000, max_tokens=1000
    )
    with pytest.raises(spend.SpendRefused) as raised:
        spend.refuse_if_over(planned, 0.40)
    said = str(raised.value)
    assert "$0.45" in said and "$0.40" in said
    assert "10 request(s)" in said and "$3/M input" in said and "$15/M output" in said
    assert "upper bound" in said


def test_a_run_at_or_below_the_ceiling_is_allowed():
    planned = spend.estimate(
        model=HOSTED, provider_class="third-party", calls=10, input_tokens=100_000, max_tokens=1000
    )
    spend.refuse_if_over(planned, 0.45 + 1e-9)
    spend.refuse_if_over(planned, 5.0)


# ---- 4. a model with no price is not free -----------------------------------------


def test_an_unpriced_hosted_model_is_refused_by_name_and_never_estimated_as_free():
    planned = spend.estimate(
        model="mystery-9", provider_class="third-party", calls=1, input_tokens=10, max_tokens=10
    )
    assert planned.usd is None, "an unpriced model has no estimate, not a zero"
    with pytest.raises(spend.SpendRefused, match="mystery-9"):
        spend.refuse_if_over(planned, 100.0)


@pytest.mark.parametrize("klass", ["local", "self-hosted"])
def test_a_model_with_no_per_token_charge_passes_at_zero(klass):
    planned = spend.estimate(
        model="qwen3:14b", provider_class=klass, calls=500, input_tokens=10**7, max_tokens=900
    )
    assert planned.usd == 0.0
    spend.refuse_if_over(planned, 0.01)


# ---- 3. stopped while running ---------------------------------------------------------


def test_the_run_makes_no_further_call_once_the_total_passes_the_ceiling(tmp_path):
    inner = Priced(cost=0.03)
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.05}}, tmp_path)

    with ledger.run(planned=5), pytest.raises(spend.SpendStopped) as stopped:
        for _ in range(5):
            provider.generate(system="s", prompt="p")

    # The call that crossed the ceiling completed; the next one was not made.
    assert inner.calls == 2
    assert stopped.value.completed == 2
    assert stopped.value.spent == pytest.approx(0.06)
    assert "REQUESTED 5, COMPLETED 2" in str(stopped.value)
    assert "ceiling of $0.05" in str(stopped.value)


def test_the_ledger_records_the_stop_and_keeps_it_out_of_the_totals(tmp_path):
    inner = Priced(cost=0.03)
    provider, path = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.05}}, tmp_path)
    with ledger.run(), pytest.raises(spend.SpendStopped):
        for _ in range(3):
            provider.generate(system="s", prompt="p")

    assert len(ledger.load(path)) == 2, "two requests, and the stop is not one of them"
    (stop,) = ledger.load_stops(path)
    assert stop.stop_reason == ledger.SPEND_STOP
    assert stop.cost_usd == pytest.approx(0.06)
    assert "REQUESTED" in (stop.error or "")


def test_without_a_ceiling_the_same_run_goes_on(tmp_path):
    inner = Priced(cost=0.03)
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG}, tmp_path)
    with ledger.run():
        for _ in range(5):
            provider.generate(system="s", prompt="p")
    assert inner.calls == 5


def test_the_ceiling_still_holds_with_the_ledger_switched_off(tmp_path):
    """A ceiling that stopped applying when recording was turned off would be
    a setting that quietly does nothing."""
    inner = Priced(cost=0.03)
    config = {
        "llm": {**HOSTED_CONFIG, "ledger": {"enabled": False}},
        "budget": {"max_usd": 0.05},
    }
    provider = ledger.wrap(inner, config)
    with pytest.raises(spend.SpendStopped):
        for _ in range(5):
            provider.generate(system="s", prompt="p")
    assert inner.calls == 2


def test_an_unpriced_hosted_model_is_refused_before_its_first_call_under_a_ceiling(tmp_path):
    inner = Priced(model="mystery-9")
    provider, _ = _wrapped(
        inner,
        {"llm": {"provider": "anthropic", "model": "mystery-9"}, "budget": {"max_usd": 1.0}},
        tmp_path,
    )
    with pytest.raises(spend.SpendRefused, match="mystery-9"):
        provider.generate(system="s", prompt="p")
    assert inner.calls == 0


def test_a_local_model_runs_under_a_ceiling_at_zero(tmp_path):
    inner = Priced(cost=0.0, model="qwen3:14b")
    provider, _ = _wrapped(
        inner,
        {
            "llm": {
                "provider": "local",
                "base_url": "http://localhost:11434/v1",
                "model": "qwen3:14b",
            },
            "budget": {"max_usd": 0.01},
        },
        tmp_path,
    )
    for _ in range(5):
        provider.generate(system="s", prompt="p")
    assert inner.calls == 5


# ---- the commands -------------------------------------------------------------------------


def _controls(tmp_path: Path, count: int) -> Path:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    entries = [
        {
            "control_id": f"AC-{n}",
            "title": f"Control {n}",
            "framework": "NIST 800-53",
            "framework_version": "Rev 5",
            "family": None,
            "family_abbr": None,
            "baseline": None,
            "control_statement": f"Manage thing number {n}.",
            "discussion": "",
            "enhancements": [],
            "related_controls": [],
            "source_crosswalk": {},
            "source_path": None,
        }
        for n in range(1, count + 1)
    ]
    path = catalog / "controls.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


class _Built(Exception):
    """A provider was built: a refusal that should come first did not."""


def _ssp(tmp_path, monkeypatch, *args, config=None, provider=None, controls=3):
    import policyforge.cli as cli_mod

    config = {"llm": HOSTED_CONFIG, **(config or {})}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)

    def build(cfg):
        if provider is None:
            raise _Built
        return ledger.wrap(provider, cfg)

    monkeypatch.setattr(cli_mod, "get_provider", build)
    out = tmp_path / "ssp.xlsx"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "ssp",
            "--controls",
            str(_controls(tmp_path, controls)),
            "--out",
            str(out),
            "--yes",
            *args,
        ],
    )
    return result, out


def test_ssp_refuses_a_run_above_the_ceiling_before_building_a_provider(tmp_path, monkeypatch):
    result, out = _ssp(tmp_path, monkeypatch, "--max-cost", "0.0001")

    assert result.exit_code == 2, result.output
    assert "would cost up to" in result.output and "$0.0001" in result.output
    assert "3 request(s)" in result.output
    assert not out.exists()


def test_ssp_goes_ahead_under_the_ceiling_and_says_what_it_estimated(tmp_path, monkeypatch):
    provider = Priced(cost=0.001)
    result, out = _ssp(tmp_path, monkeypatch, "--max-cost", "50", provider=provider)

    assert result.exit_code == 0, result.output
    assert "Spend ceiling $50.00: estimated at most" in result.output
    assert provider.calls == 3
    assert out.exists()


def test_ssp_says_nothing_new_with_no_ceiling(tmp_path, monkeypatch):
    provider = Priced(cost=0.001)
    result, _ = _ssp(tmp_path, monkeypatch, provider=provider)

    assert result.exit_code == 0, result.output
    assert "Spend ceiling" not in result.output
    assert provider.calls == 3


@pytest.mark.parametrize(
    ("configured", "flag", "exit_code"),
    [(0.0001, "50", 0), (50.0, "0.0001", 2)],
    ids=["flag loosens a configured ceiling", "flag tightens a configured ceiling"],
)
def test_ssp_max_cost_overrides_the_configured_ceiling_both_ways(
    tmp_path, monkeypatch, configured, flag, exit_code
):
    provider = Priced(cost=0.001)
    result, _ = _ssp(
        tmp_path,
        monkeypatch,
        "--max-cost",
        flag,
        config={"budget": {"max_usd": configured}},
        provider=provider,
    )
    assert result.exit_code == exit_code, result.output


def test_ssp_stops_loudly_when_the_running_cost_passes_the_ceiling(tmp_path, monkeypatch):
    """The estimate passes (an upper bound of a few cents), and each answer
    costs more than the estimate allowed for: the running total stops it."""
    provider = Priced(cost=0.03)
    result, out = _ssp(tmp_path, monkeypatch, "--max-cost", "0.05", provider=provider)

    assert result.exit_code == 1, result.output
    assert "REQUESTED 3, COMPLETED 2" in result.output
    assert provider.calls == 2
    assert not out.exists(), "a run that stopped does not write a workbook that looks whole"


def test_ssp_refuses_an_unpriced_hosted_model_under_a_ceiling(tmp_path, monkeypatch):
    result, _ = _ssp(
        tmp_path,
        monkeypatch,
        "--max-cost",
        "10",
        config={"llm": {"provider": "anthropic", "model": "mystery-9"}},
    )
    assert result.exit_code == 2, result.output
    assert "mystery-9" in result.output


def test_a_bad_max_cost_is_a_usage_error(tmp_path, monkeypatch):
    result, _ = _ssp(tmp_path, monkeypatch, "--max-cost", "0", provider=Priced())
    assert result.exit_code == 2, result.output
    assert "above zero" in result.output


# ---- the eval runner ----------------------------------------------------------------------


def test_a_harness_that_swallows_failures_can_still_see_that_a_request_was_refused(tmp_path):
    """The eval paths fall back on a provider failure on purpose, so a refused
    request reaches the runner as a graded fallback and not as an exception:
    it reads the meter instead, and discards the case (#125)."""
    from evals.provider import Metered

    inner = Priced(cost=0.03)
    wrapped, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.05}}, tmp_path)
    provider = Metered(wrapped, provider_class="third-party")

    seen = []
    for _ in range(4):
        with contextlib.suppress(spend.SpendStopped):  # what a path that falls back does
            provider.generate(system="s", prompt="p")
        meter = provider.spend_meter()
        seen.append(meter.stopped)

    assert seen == [False, False, True, True]
    assert inner.calls == 2


def test_a_provider_with_no_ceiling_has_no_meter(tmp_path):
    from evals.provider import Metered

    wrapped, _ = _wrapped(Priced(), {"llm": HOSTED_CONFIG}, tmp_path)
    assert Metered(wrapped, provider_class="third-party").spend_meter() is None


def test_reaching_the_ceiling_exactly_is_enough_to_stop():
    """A total equal to the ceiling leaves no room for the next request, so
    it is not made: the stop is at or past the ceiling, never one more call."""
    meter = spend.Meter(0.05)
    meter.add(0.025)
    meter.check()
    meter.add(0.025)
    with pytest.raises(spend.SpendStopped):
        meter.check()


# ---- what 9b's arms found on PR 406: the gaps, each with the input that tells ----------------


def test_the_input_estimate_errs_high_so_the_figure_is_an_upper_bound():
    """English prose runs about four characters a token. The estimate must
    count at least that many tokens for any text, or "an upper bound" is a
    sentence about nothing."""
    assert spend.input_tokens_of("x" * 300) >= 300 // 4
    assert spend.input_tokens_of("x" * 300, "y" * 300) == 2 * spend.input_tokens_of("x" * 300)


@pytest.mark.parametrize("bad", [5, [1], "5", True])
def test_a_budget_that_is_not_a_mapping_is_a_config_error(bad):
    with pytest.raises(ConfigError, match="budget"):
        spend.ceiling_of({"budget": bad})


def test_a_known_last_attempt_is_never_counted_as_less_than_it_cost(tmp_path):
    """The response has no total, its tokens price at a fraction of a cent,
    but its last attempt alone was priced at four cents (an earlier attempt
    is unknown): the larger known figure is what the ceiling sees."""
    inner = Priced(cost=None, last_cost=0.04, tokens=(10, 10))
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.05}}, tmp_path)

    with ledger.run(planned=4), pytest.raises(spend.SpendStopped) as stopped:
        for _ in range(4):
            provider.generate(system="s", prompt="p")

    assert inner.calls == 2
    assert stopped.value.spent == pytest.approx(0.08)
    assert "COMPLETED 2 (2 priced from their tokens" in str(stopped.value)


# ---- 1d's finding on PR 406: a provider that reports no cost --------------------------------


def test_a_provider_that_reports_tokens_and_no_cost_is_priced_from_them(tmp_path):
    """Only litellm sets a cost. bedrock, gemini and openai-compat return
    tokens: 20,000 in and 8,000 out at the map's rates is $0.18 a request,
    so a $0.50 ceiling stops before the fourth, not never (1d measured 40 of
    40 requests, about $7.20, with $0 added each time)."""
    inner = Priced(cost=None, tokens=(20_000, 8_000))
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.50}}, tmp_path)

    with ledger.run(planned=40), pytest.raises(spend.SpendStopped) as stopped:
        for _ in range(40):
            provider.generate(system="s", prompt="p")

    assert inner.calls == 3
    assert stopped.value.spent == pytest.approx(0.54)
    assert "REQUESTED 40, COMPLETED 3 (3 priced from their tokens" in str(stopped.value)


def test_a_response_with_no_tokens_either_is_counted_at_its_max_tokens(tmp_path):
    """Nothing reported: the output is counted at the limit the request was
    given, an upper bound like the estimate, and the prompt's own length is
    the input."""
    inner = Priced(cost=None, tokens=None)
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.05}}, tmp_path)

    with pytest.raises(spend.SpendStopped):
        for _ in range(20):
            # 1,000 output tokens at $15/M is a cent and a half a request.
            provider.generate(system="s", prompt="p", max_tokens=1000)

    assert inner.calls == 4  # four requests are $0.06, past $0.05


def test_a_local_model_that_reports_no_cost_is_still_free(tmp_path):
    inner = Priced(cost=None, model="qwen3:14b", tokens=(50_000, 20_000))
    provider, _ = _wrapped(
        inner,
        {
            "llm": {
                "provider": "local",
                "base_url": "http://localhost:11434/v1",
                "model": "qwen3:14b",
            },
            "budget": {"max_usd": 0.01},
        },
        tmp_path,
    )
    for _ in range(10):
        provider.generate(system="s", prompt="p")
    assert inner.calls == 10


def test_a_request_that_raised_counts_its_input_and_not_an_output_it_never_returned(tmp_path):
    class Failing:
        model = HOSTED

        def generate(self, **kwargs):
            raise RuntimeError("timeout")

    provider, _ = _wrapped(Failing(), {"llm": HOSTED_CONFIG, "budget": {"max_usd": 1.0}}, tmp_path)
    with pytest.raises(RuntimeError):
        provider.generate(system="s" * 3000, prompt="p" * 3000, max_tokens=4000)

    # 6,000 characters are 2,000 input tokens at $3/M; the 4,000 output tokens
    # it was allowed are not charged to a request that returned nothing.
    assert provider.spend_meter().spent == pytest.approx(2000 * 3 / 1_000_000)


def test_the_structured_path_is_priced_at_its_max_tokens_too(tmp_path):
    """`generate_json` is the path crosswalk propose and the entailment judge
    use; its limit travels in the keyword arguments."""
    inner = Priced(cost=None, tokens=None)
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.05}}, tmp_path)

    with pytest.raises(spend.SpendStopped):
        for _ in range(20):
            provider.generate_json(
                system="s", prompt="p", schema={"type": "object"}, max_tokens=1000
            )

    assert inner.calls == 4


def test_a_request_that_raised_after_billing_is_priced_from_the_tokens_it_carries(tmp_path):
    """1d's probe on PR 406: a native provider raised `ReasoningBudgetExhausted`
    with 20,000 input and 8,000 output tokens (two billed attempts) on every
    request, and a ceiling that priced a raise from its prompt alone let 40
    of 40 through, about $7.20 against $0.50."""
    from policyforge.llm._inline_thinking import exhausted

    class Exhausting:
        model = HOSTED
        calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            raise exhausted(HOSTED, 1000, 8000, input_tokens=20_000, output_tokens=8_000)

    inner = Exhausting()
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.50}}, tmp_path)

    with pytest.raises(spend.SpendStopped):
        for _ in range(40):
            try:
                provider.generate(system="s", prompt="p")
            except spend.SpendStopped:
                raise
            except Exception:  # what a run that goes on past a failure does
                continue

    assert inner.calls == 3  # $0.18 each: the third passes $0.50, the fourth is not made


def test_a_raise_that_says_its_output_was_spent_is_counted_at_that_limit(tmp_path):
    """No tokens on the exception, but it carries the limit the output was
    spent against: that is the upper bound for what was billed."""

    class Spent(Exception):
        max_tokens = 4000

    class Failing:
        model = HOSTED
        calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            raise Spent("exhausted")

    inner = Failing()
    provider, _ = _wrapped(inner, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.10}}, tmp_path)
    with pytest.raises(spend.SpendStopped):
        for _ in range(40):
            try:
                provider.generate(system="s", prompt="p")
            except spend.SpendStopped:
                raise
            except Spent:
                continue

    # 4,000 output tokens at $15/M is six cents; the second request passes $0.10.
    assert inner.calls == 2


# ---- the entailment judge is a model call like any other (#388) -----------------


def _judge(monkeypatch, tmp_path, config: dict) -> tuple:
    """`entailment_provider` as the shell builds it, over a priced fake."""
    import policyforge.llm.litellm_provider as litellm_provider
    from policyforge.entail.llm_entailer import entailment_provider

    inner = Priced(cost=0.03)
    monkeypatch.setattr(litellm_provider, "LiteLLMProvider", lambda **kwargs: inner)
    config = {
        **config,
        "llm": {**config.get("llm", {}), "ledger": {"path": str(tmp_path / "l.jsonl")}},
    }
    return entailment_provider({"model": HOSTED}, config), inner


def test_the_judge_stops_at_the_ceiling_like_the_answer_does(tmp_path, monkeypatch):
    """Its wrapper was built from `llm:` alone, so under `budget.max_usd` it
    had no meter, and its one call per cited sentence passed the ceiling the
    answer was stopped at (9b, PR 408)."""
    judge, inner = _judge(
        monkeypatch, tmp_path, {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.05}}
    )

    assert judge.spend_meter() is not None
    with ledger.run(planned=5), pytest.raises(spend.SpendStopped):
        for _ in range(5):
            judge.generate_json(system="s", prompt="p", schema={})

    assert inner.calls == 2


def test_without_a_ceiling_the_judge_has_no_meter(tmp_path, monkeypatch):
    judge, inner = _judge(monkeypatch, tmp_path, {"llm": HOSTED_CONFIG})

    assert judge.spend_meter() is None
    for _ in range(3):
        judge.generate_json(system="s", prompt="p", schema={})
    assert inner.calls == 3


# ---- one total for a session, across turns and providers (#388) ------------------


def test_a_sessions_providers_share_one_total_across_turns(tmp_path):
    """The answerer and the judge are two providers from one config. Each
    kept its own total, so a session spent the ceiling once per provider
    (1d, PR 408: 5 + 5 calls at $0.10 under $0.50). Held across turns, they
    draw on one. $0.125 a call keeps the sums exact: the fourth call reaches
    $0.50, and the meter stops at or past the ceiling, so it is the last."""
    config = {"llm": HOSTED_CONFIG, "budget": {"max_usd": 0.50}}
    answer_inner, judge_inner = Priced(cost=0.125), Priced(cost=0.125)
    answer, _ = _wrapped(answer_inner, config, tmp_path)
    judge, _ = _wrapped(judge_inner, config, tmp_path)
    session = ledger.new_spend()

    with pytest.raises(spend.SpendStopped):
        for _turn in range(10):
            with ledger.session_spend(session):
                answer.generate(system="s", prompt="p")
                judge.generate_json(system="s", prompt="p", schema={})

    assert answer_inner.calls + judge_inner.calls == 4
    assert judge_inner.calls > 0, "the judge drew on the same total"


def test_every_shell_turn_draws_on_the_sessions_total(monkeypatch):
    """`dispatch` is where the shell and the MCP server both send a line, so
    it is where the session's total is held."""
    from policyforge.llm.ledger import _run_spend
    from policyforge.zardoz import shell

    seen = []
    monkeypatch.setattr(shell, "_dispatch", lambda line, state: seen.append(_run_spend.get()) or "")
    state = shell.ShellState()

    shell.dispatch("a question", state)
    shell.dispatch("/help", state)

    assert seen == [state.spend, state.spend]
    assert seen[0] is state.spend
