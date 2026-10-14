"""A spend ceiling: refuse a run that would cost more, stop one that does (#125).

`ssp` asked "Continue?" with a count of requests and no price, and nothing
else in the project estimated a cost, while `eval_zardoz.py` can issue
several hundred calls from one command. This is the one mechanism for all of
them (80's spec on #125), in three parts:

1. **A ceiling**, `budget.max_usd` in config, or `--max-cost USD` for one run
   on a command that can make more than one paid call. With neither, nothing
   here does anything.
2. **Refuse before starting**, where the number of calls is known up front:
   an estimate from the planned calls and the model's rates. It is an upper
   bound, because every output is priced at `max_tokens`. Above the ceiling
   the run is refused before any call, saying the estimate, the ceiling, the
   call count and the rates (`SpendRefused`, exit 2).
3. **Stop while running**, because an estimate can be wrong (the 1.6.1
   matrix was 29% off): the run keeps the cost of each response, and once the
   total has passed the ceiling it makes no further call (`SpendStopped`),
   and says REQUESTED beside COMPLETED, so a short run never reads as a
   cheap one.

**A model with no price is not free.** A local model (and a self-hosted one,
which has no per-token charge) costs $0. A hosted model whose price
`litellm_map` cannot give is refused under a set ceiling, naming the model:
it is never estimated as $0, which is the one reading under which a ceiling
would pass the run it exists to refuse.

Nothing here imports click; `cli/__init__.py` turns the two exceptions into
exit 2 and exit 1.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The config section and key: `budget: {max_usd: 5.00}`.
BUDGET = "budget"
MAX_USD = "max_usd"

#: Characters per input token, for an estimate. Smaller than English prose
#: gives (about four), so the input is over-counted rather than under: the
#: estimate is an upper bound and says so.
CHARS_PER_TOKEN = 3

#: Provider classes with no per-token charge: this machine's own model, and
#: one a person's organisation runs itself. Everything else is hosted.
_NO_PER_TOKEN_CHARGE = ("local", "self-hosted")


class SpendRefused(Exception):
    """A run was refused before its first call: it would cost more than the
    ceiling, or it names a hosted model that can't be priced under one."""


class SpendStopped(Exception):
    """The running total passed the ceiling, so no further call was made."""

    def __init__(self, message: str, *, spent: float, ceiling: float, completed: int):
        super().__init__(message)
        self.spent = spent
        self.ceiling = ceiling
        self.completed = completed


def usd(amount: float) -> str:
    """A dollar figure, to the cent, or to the hundredth of a cent below one:
    a ceiling of $0.0004 must not read as $0.00."""
    return f"${amount:.2f}" if amount >= 0.01 or amount == 0 else f"${amount:.4f}"


def ceiling_of(config: dict | None) -> float | None:
    """`budget.max_usd`, or None when it isn't set.

    A value that isn't a positive number is an error, not "no ceiling": a
    ceiling someone wrote and that silently didn't apply is how a run gets
    an unlimited bill.
    """
    from policyforge.config import ConfigError

    section = (config or {}).get(BUDGET)
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError(f"`{BUDGET}:` must be a mapping with `{MAX_USD}:` under it.")
    value = section.get(MAX_USD)
    if value is None:
        return None
    try:
        return checked_ceiling(value, where=f"`{BUDGET}.{MAX_USD}`")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def checked_ceiling(value, *, where: str) -> float:
    """`value` as a ceiling in dollars, or ValueError naming `where`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} must be a number of dollars, not {value!r}.")
    if not value > 0:
        raise ValueError(f"{where} must be above zero, not {value!r}.")
    return float(value)


def with_max_cost(config: dict, max_cost: float | None) -> dict:
    """`config` with `--max-cost` applied over `budget.max_usd`, or the same
    config when the flag wasn't given. The flag wins in both directions: it
    can tighten a configured ceiling, and it can set one where none is."""
    if max_cost is None:
        return config
    checked_ceiling(max_cost, where="`--max-cost`")
    budget = dict(config.get(BUDGET) or {}) if isinstance(config.get(BUDGET), dict) else {}
    budget[MAX_USD] = float(max_cost)
    return {**config, BUDGET: budget}


def input_tokens_of(*texts: str) -> int:
    """An upper-bound count of the tokens in `texts`."""
    return sum(-(-len(text) // CHARS_PER_TOKEN) for text in texts)


@dataclass(frozen=True)
class Rates:
    """What a model costs per million tokens, and where that was read."""

    model: str
    input_per_million: float | None
    output_per_million: float | None
    source: str | None
    free: bool = False

    @property
    def priced(self) -> bool:
        return self.free or (
            self.input_per_million is not None and self.output_per_million is not None
        )

    def describe(self) -> str:
        if self.free:
            return f"{self.model}: no per-token charge (local or self-hosted)"
        if not self.priced:
            return f"{self.model}: no price in the model map"
        return (
            f"{self.model}: ${self.input_per_million:g}/M input, "
            f"${self.output_per_million:g}/M output ({self.source})"
        )


def rates_for(model: str, provider_class: str | None) -> Rates:
    """The rates for `model`, from the same table a re-send's price comes from
    (`escalation._price`, LiteLLM's bundled map with our overrides)."""
    if provider_class in _NO_PER_TOKEN_CHARGE:
        return Rates(model, 0.0, 0.0, "no per-token charge", free=True)
    from .escalation import _price
    from .litellm_map import basis

    found = _price(model)
    if found is None:
        return Rates(model, None, None, None)
    per_in, per_out, key = found
    return Rates(model, per_in * 1_000_000, per_out * 1_000_000, basis(key))


@dataclass(frozen=True)
class Estimate:
    """What a planned run would cost at most."""

    rates: Rates
    calls: int
    input_tokens: int  # in all, across the calls
    max_tokens: int  # per call: every output is priced at this

    @property
    def usd(self) -> float | None:
        """The upper bound, or None when the model can't be priced."""
        if not self.rates.priced:
            return None
        spent_in = self.input_tokens * (self.rates.input_per_million or 0.0)
        spent_out = self.calls * self.max_tokens * (self.rates.output_per_million or 0.0)
        return (spent_in + spent_out) / 1_000_000


def cost_of_response(
    *,
    rates: Rates,
    input_tokens: int | None,
    output_tokens: int | None,
    prompt_tokens: int,
    max_tokens: int | None,
    replied: bool,
) -> float | None:
    """What one request cost, priced here, when the provider reported no cost
    (bedrock, gemini, openai-compat and the direct vendors return tokens or
    nothing: a running total that added $0 for them never stopped, 1d on
    PR 406).

    From the tokens the response reports, at `rates`. A figure the response
    lacks is the upper bound for it: the prompt's own length for the input,
    and `max_tokens` for an output that was returned but not counted. A
    request that raised returns no output, so only its input is counted.
    None when `rates` has no price, which the admission check has refused.
    """
    if rates.free:
        return 0.0
    if not rates.priced:
        return None
    tokens_in = input_tokens if input_tokens is not None else prompt_tokens
    tokens_out = (
        output_tokens if output_tokens is not None else ((max_tokens or 0) if replied else 0)
    )
    return (
        tokens_in * (rates.input_per_million or 0.0)
        + tokens_out * (rates.output_per_million or 0.0)
    ) / 1_000_000


def estimate(
    *, model: str, provider_class: str | None, calls: int, input_tokens: int, max_tokens: int
) -> Estimate:
    return Estimate(rates_for(model, provider_class), calls, input_tokens, max_tokens)


def refuse_unpriced(rates: Rates, ceiling: float) -> None:
    """Under a ceiling, a hosted model with no price is refused by name."""
    if not rates.priced:
        raise SpendRefused(
            f"A spend ceiling of {usd(ceiling)} is set, and {rates.model} has no price in the "
            "model map, so this run's cost can't be bounded; it is not assumed to be free. "
            "Use a model the map prices, a local model, or remove the ceiling "
            f"(`{BUDGET}.{MAX_USD}` in config, or `--max-cost`)."
        )


def refuse_if_over(planned: Estimate, ceiling: float) -> None:
    """Raise `SpendRefused` unless `planned` fits under `ceiling`."""
    refuse_unpriced(planned.rates, ceiling)
    bound = planned.usd
    if bound is None or bound <= ceiling:
        return
    raise SpendRefused(
        f"This run would cost up to {usd(bound)}, above the ceiling of {usd(ceiling)}, "
        "so no request was made.\n"
        f"  {planned.calls} request(s), about {planned.input_tokens:,} input token(s) in all, "
        f"{planned.max_tokens:,} output token(s) allowed each\n"
        f"  rates: {planned.rates.describe()}\n"
        "  The figure is an upper bound: every output is priced at its token limit, and a "
        "re-send at twice the room is not in it.\n"
        f"  Raise `{BUDGET}.{MAX_USD}` or `--max-cost`, or narrow the run."
    )


class Meter:
    """The running total of one run, kept from each response's cost."""

    def __init__(self, ceiling: float, planned: int | None = None):
        self.ceiling = ceiling
        self.planned = planned
        self.spent = 0.0
        self.completed = 0
        self.unknown = 0
        #: Requests whose cost the provider did not report, priced here from
        #: their tokens at the model map's rates (or, with no tokens, from the
        #: prompt and `max_tokens`): an upper bound, said as one.
        self.estimated = 0
        #: True once `check` has refused a call: from then on some request a
        #: caller made was not sent, and what it got in place of an answer
        #: is not a result (a harness that swallows provider failures must
        #: look at this, not at the exception).
        self.stopped = False

    def add(self, cost: float | None, *, whole: bool = True, estimated: bool = False) -> None:
        """Count one completed request. `whole` is False when `cost` is only
        the part of the request's cost that is known (its last attempt, where
        an earlier one has no price): it is added to the total and the request
        is still counted as of unknown cost, so the account never reads as a
        complete figure."""
        self.completed += 1
        if estimated:
            self.estimated += 1
        if cost is None:
            self.unknown += 1
            return
        self.spent += cost
        if not whole:
            self.unknown += 1

    def check(self) -> None:
        """Raise `SpendStopped` if the total is at or past the ceiling: the
        call that crossed it has completed; this one is not made."""
        if self.spent < self.ceiling:
            return
        self.stopped = True
        raise SpendStopped(
            f"Stopped: the run's cost reached {usd(self.spent)}, past the ceiling of "
            f"{usd(self.ceiling)}, so no further request was made. " + self.account(),
            spent=self.spent,
            ceiling=self.ceiling,
            completed=self.completed,
        )

    def account(self) -> str:
        """REQUESTED beside COMPLETED, so a short run never reads as a cheap
        one. REQUESTED is the planned count where the command knew it."""
        requested = f"{self.planned}" if self.planned is not None else "not known up front"
        notes = []
        if self.estimated:
            notes.append(f"{self.estimated} priced from their tokens at the map's rates")
        if self.unknown:
            notes.append(f"{self.unknown} of unknown cost")
        said = f" ({'; '.join(notes)})" if notes else ""
        return f"REQUESTED {requested}, COMPLETED {self.completed}{said}."
