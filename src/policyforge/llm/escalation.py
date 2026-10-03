"""No silent escalation: every bigger-budget re-send is announced and recorded (#361).

A reasoning model can spend its whole output budget thinking and return an
empty reply, which is still billed. The providers recover by re-sending at
8x the budget (`_inline_thinking.retry_budget`, `_anthropic_compat`), and
`effort` retries a cut-off reply at 2x. Measured on #301: sonnet's
Evaluation Standard came back empty at 16,384 tokens ($0.1985), and the 8x
re-send's worst case was $1.4109. **Nothing told the user either had
happened**, and the ledger kept only the second attempt's request id (#343).

80's ruling (#361, half 1): the user is told before the re-send goes out,
naming the topic, the model, the new `max_tokens` and the worst-case cost at
that model's list price, and the billed first attempt is recorded in the
ledger. Half 2, sizing the first budget, waits for a user-approved
measurement and is not done here.

**How it reaches the ledger without double counting.** `announce` keeps each
escalation for the current call; `RecordingProvider` takes them into the
call's one row (`CallRecord.escalations`). That row's cost is already the
summed cost where the provider reports one (LiteLLM), so a separate row for
the first attempt would count it twice. A call that fails keeps the same
shape (#343): every billed request's id is on the row or in `escalations`,
and, when every attempt reports a cost, its cost is in the row's total. If
any attempt's cost is unknown the row's cost is None, never the known part
presented as the whole (9b and 1d on #378). One row per request was
considered and dropped (80 on #343), because that conservation is what it
was for.

**`first_cost_usd` is never added to a ledger total** (#372, measured with
fakes billing known amounts; #343 closed the error-row exception):

- **On a row that has a cost.** On LiteLLM's 8x path the row's `cost_usd`
  already sums both attempts. On `effort`'s 2x path the first attempt is
  its own row, and the escalation on the second row repeats it. The sum of
  row costs equals what was billed on both paths.
- **On an error row too.** When the re-send fails, the exception carries
  what both attempts billed, so the row's `cost_usd` is the sum: $0.20 +
  $1.30 billed, $1.50 in the row, and the $0.20 here is already in it. A
  provider that reports no cost leaves the row's cost None, as on success.

**Scoped to `ledger.about`.** Each `about` block starts a fresh pending list
and closes it on exit, so an escalation from a call that recorded no row
(an unwrapped provider) cannot land in a later, unrelated row. One still
pending at exit is not dropped silently: `end` names its billed first
attempt on stderr (80 on #373). Outside any `about` block there is one
list, and the next recorded call takes it.
"""

from __future__ import annotations

import sys
from contextvars import ContextVar
from dataclasses import asdict, dataclass

from . import litellm_map
from .base import COST_ESTIMATE, COST_PROVIDER

_pending: ContextVar[list | None] = ContextVar("policyforge_escalations", default=None)


@dataclass(frozen=True)
class Escalation:
    """One re-send with a larger budget, and the billed attempt before it."""

    subject: str | None
    site: str | None
    model: str
    first_max_tokens: int
    max_tokens: int
    input_tokens: int | None
    #: The worst case of the re-send at the model's list price, or None when
    #: no price is known for the model. Never a guess.
    worst_case_usd: float | None
    price_source: str | None
    #: The billed attempt that came back empty or cut off.
    first_request_id: str | None
    #: Already counted in its row's cost, error rows included (#343): never
    #: add it to one (see the module doc).
    first_cost_usd: float | None
    first_output_tokens: int | None
    first_stop_reason: str | None
    #: The model whose reply came back empty, when it is not `model`: a
    #: cascade's hop to a stronger model (#343). None for a re-send to the
    #: same model.
    first_model: str | None = None
    #: Where `worst_case_usd`'s price came from, in words a user can check:
    #: LiteLLM's bundled table (with its version) or PolicyForge's override
    #: table (with its source and date). An estimate, never a bill (#79).
    price_basis: str | None = None
    #: Where `first_cost_usd` came from: `COST_PROVIDER` or `COST_ESTIMATE`
    #: (#87). None on a record written before #87, which reads as "not
    #: recorded", never as billed.
    first_cost_source: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _price(model: str) -> tuple[float, float, str] | None:
    """(input $/token, output $/token, the LiteLLM entry) for `model`, or None.

    Tried as written, without a route prefix ("openrouter/"), then as the bare
    model name. A price found under another name is still the model's list
    price, and the entry is named in the warning so a reader can check it.
    """
    try:
        from .litellm_map import load

        litellm = load()
    except ImportError:
        return None
    table = getattr(litellm, "model_cost", {}) or {}
    parts = model.split("/")
    for key in dict.fromkeys([model, "/".join(parts[1:]), parts[-1]]):
        entry = table.get(key) if key else None
        if (
            entry
            and entry.get("input_cost_per_token") is not None
            and entry.get("output_cost_per_token") is not None
        ):
            return float(entry["input_cost_per_token"]), float(entry["output_cost_per_token"]), key
    return None


def figure(cost: float, source: str | None) -> str:
    """A dollar figure, said as what it is (#87): the provider's report, an
    estimate from LiteLLM's model map, or, on a row written before #87 kept
    the difference, a figure whose source was not recorded. Never "billed"
    for anything but the first."""
    if source == COST_PROVIDER:
        return f"${cost:.4f}"
    if source == COST_ESTIMATE:
        return f"${cost:.4f} estimated"
    return f"${cost:.4f}, source not recorded"


def announce(
    *,
    model: str,
    first_max_tokens: int,
    max_tokens: int,
    input_tokens: int | None = None,
    first_request_id: str | None = None,
    first_cost_usd: float | None = None,
    first_cost_source: str | None = None,
    first_output_tokens: int | None = None,
    first_stop_reason: str | None = None,
    first_model: str | None = None,
    empty: bool = True,
    out=None,
) -> Escalation:
    """Say, before it is sent, that a request is being re-sent with more room,
    and keep it for this call's ledger row."""
    from .ledger import current_scope

    scope = current_scope()
    price = _price(model)
    worst = None
    if price is not None and input_tokens is not None:
        worst = input_tokens * price[0] + max_tokens * price[1]
    escalation = Escalation(
        subject=scope.subject if scope else None,
        site=scope.site if scope else None,
        model=model,
        first_max_tokens=first_max_tokens,
        max_tokens=max_tokens,
        input_tokens=input_tokens,
        worst_case_usd=round(worst, 6) if worst is not None else None,
        price_source=price[2] if price else None,
        first_request_id=first_request_id,
        first_cost_usd=first_cost_usd,
        first_output_tokens=first_output_tokens,
        first_stop_reason=first_stop_reason,
        first_model=first_model if first_model and first_model != model else None,
        price_basis=litellm_map.basis(price[2]) if price else None,
        first_cost_source=first_cost_source if first_cost_usd is not None else None,
    )
    what = escalation.subject or "this request"
    whose = f"{escalation.first_model}'s reply" if escalation.first_model else "the reply"
    if escalation.worst_case_usd is not None:
        cost = (
            f"worst case ${escalation.worst_case_usd:.4f}, estimated at "
            f"{escalation.price_source}'s price in {escalation.price_basis}"
        )
    else:
        cost = f"worst case unpriced: up to {max_tokens:,} output tokens"
    # "billed" only beside a figure the provider reported, or with no figure
    # at all (the attempt was charged, amount unknown). An estimated or
    # unrecorded figure is a "cost", as the run summary says it (#97, #93),
    # and the attempt is still said to have been paid for (80's ruling).
    if first_cost_usd is None:
        spent = " (billed)"
    elif escalation.first_cost_source == COST_PROVIDER:
        spent = f" (${first_cost_usd:.4f}, billed)"
    else:
        spent = f" (paid for; cost {figure(first_cost_usd, escalation.first_cost_source)})"
    (out or sys.stderr).write(
        f"Warning: re-sending {what} to {model} with max_tokens {max_tokens:,}: {whose} at "
        f"{first_max_tokens:,} came back {'empty' if empty else 'cut off'}{spent}; "
        f"{cost}.\n"
    )
    pending = _pending.get()
    if pending is None:
        pending = []
        _pending.set(pending)
    pending.append(escalation)
    return escalation


def begin():
    """Start a fresh pending list; pass the token to `end`. For `ledger.about`."""
    return _pending.set([])


def end(token, out=None) -> None:
    """Close what `begin` started, restoring the enclosing list.

    An escalation still pending here was announced by a call that recorded
    no row, so this is its last chance to be seen. It is written to stderr
    with its billed first attempt rather than dropped silently (80 on #373):
    losing it without a word would be #343's failure again.
    """
    for e in _pending.get() or ():
        # "billed" as in `announce`: never beside an estimated figure (#97).
        if e.first_cost_usd is None:
            attempt, spent = "billed first attempt", "cost unknown"
        elif e.first_cost_source == COST_PROVIDER:
            attempt, spent = "billed first attempt", figure(e.first_cost_usd, COST_PROVIDER)
        else:
            attempt = "paid-for first attempt"
            spent = f"cost {figure(e.first_cost_usd, e.first_cost_source)}"
        (out or sys.stderr).write(
            f"Warning: no ledger row recorded the re-send of {e.subject or 'a request'} "
            f"to {e.model}; its {attempt} was request {e.first_request_id or 'unknown'}, "
            f"{spent}.\n"
        )
    _pending.reset(token)


def take() -> tuple[dict, ...]:
    """The escalations announced since the last take, for one ledger row."""
    pending = _pending.get()
    if not pending:
        return ()
    taken = tuple(e.as_dict() for e in pending)
    pending.clear()
    return taken
