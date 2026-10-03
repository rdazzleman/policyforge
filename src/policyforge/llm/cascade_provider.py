"""Runs a cheap model and falls back to a stronger one when the cheap model
demonstrably could not do the job.

The measurement this is built on: `deepseek-v4-flash` scores 92% routing,
100% resolution, 100% expansion at $0.0013 a sweep; `deepseek-v4-pro`
scores 97/100/100 at $0.0607, forty-seven times the price for one extra
routing case. Paying Pro rates for every call to cover the rare one is the
wrong trade, and so is never having Pro available.

**What counts as "could not do the job" is narrow on purpose.** Only
`ReasoningBudgetExhausted` escalates by default: the model spent its whole
budget deliberating, twice, and never began an answer. That is a fact the
provider layer can see for itself, unambiguously, without knowing anything
about the task.

Everything more interesting than that — a wrong routing decision, an answer
citing a passage it should not, an unfilled placeholder reproduced as fact
— is invisible here, and deliberately so. `LLMProvider.generate()` receives
a system prompt, a user prompt and a budget; it never sees the passages,
so it cannot run `zardoz/answer.py`'s integrity checks. A cascade gated on
*those* belongs in `answer_question`, which holds the question, the
passages, the provider and the verdict at once. It is a separate piece of
work and is on the roadmap. This one is the floor, not the ceiling.

Nothing is hidden. The `LLMResponse.model` of an escalated call names the
model that actually answered, so a caller can always tell which one it got.
"""

from __future__ import annotations

from ._inline_thinking import ReasoningBudgetExhausted
from .base import LLMProvider, LLMResponse, combined_source

#: Failures that mean "this model could not produce an answer", as opposed
#: to "this model produced a bad answer" (invisible here) or "the request
#: never ran" (a rate limit or a dead key, which the stronger model would
#: hit too — escalating those would double the bill to fail twice).
ESCALATE_ON: tuple[type[Exception], ...] = (ReasoningBudgetExhausted,)


class CascadeProvider(LLMProvider):
    """Tries `primary`, falls back to `escalate_to` on a recoverable failure.

    Both are ordinary providers, so a cascade can pair any two models —
    local and hosted, cheap and strong, or two vendors for redundancy.
    """

    def __init__(
        self,
        *,
        primary: LLMProvider,
        escalate_to: LLMProvider,
        escalate_on: tuple[type[Exception], ...] = ESCALATE_ON,
    ):
        self._primary = primary
        self._escalate_to = escalate_to
        self._escalate_on = escalate_on
        #: Counters, so a run can report how often the cheap model sufficed.
        #: The whole economic case rests on that ratio being high, and an
        #: unmeasured cascade is one nobody can tell is working.
        self.primary_calls = 0
        self.escalations = 0

    @property
    def model(self) -> str:
        """What this provider is, for anything that logs a model name."""
        return (
            f"{getattr(self._primary, 'model', '?')} -> {getattr(self._escalate_to, 'model', '?')}"
        )

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        **kwargs,
    ) -> LLMResponse:
        # `**kwargs` carries `effort`, `cache` and `cache_prefix`, which a
        # caller passes only after asking the flags below — and the flags say
        # yes only when both halves take them, so both halves can be handed
        # the request unchanged.
        request = {
            "system": system,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs,
        }
        return self._run(lambda half: half.generate(**request), max_tokens=max_tokens)

    def _run(self, call, *, max_tokens: int | None = None):
        """`call(primary)`, then `call(escalate_to)` on a recoverable failure."""
        self.primary_calls += 1
        try:
            return call(self._primary)
        except self._escalate_on as exc:
            self.escalations += 1
            failed = exc

        # **The primary's attempts were billed** (#343). Its exhaustion is
        # caught here, so it never reaches the ledger as an exception: the
        # hop to the stronger model is announced before it is sent, like any
        # re-send (#361), which keeps the primary's last billed attempt in
        # this call's `escalations`; and its cost is added to what the call
        # returns or raises, so the row's cost is everything the call billed.
        billed = getattr(failed, "cost_usd", None)
        if getattr(failed, "request_id", None) is not None or billed is not None:
            from . import escalation

            escalation.announce(
                model=getattr(self._escalate_to, "model", "?"),
                first_max_tokens=getattr(failed, "max_tokens", None) or 0,
                max_tokens=max_tokens or getattr(failed, "max_tokens", None) or 0,
                input_tokens=getattr(failed, "input_tokens", None),
                first_request_id=getattr(failed, "request_id", None),
                first_cost_usd=getattr(failed, "last_cost_usd", None),
                first_cost_source=getattr(failed, "cost_source", None),
                first_output_tokens=getattr(failed, "output_tokens", None),
                first_stop_reason="length",
                first_model=getattr(self._primary, "model", None),
            )

        # Outside the `except` so that a failure of the stronger model is
        # reported on its own terms rather than chained to the first one.
        # "Pro also ran out of room" is the useful message; "Pro failed
        # while handling Flash failing" buries it.
        #
        # A cost of None means "billed, amount unknown" (a provider that
        # reports no cost), so the sum stays None rather than becoming the
        # known part presented as the whole. The known parts are not lost:
        # each billed attempt is in the row's `escalations` (1d on #378). An
        # exception with no `cost_usd` at all never reached a bill we can see.
        #
        # The other order too (#382): a primary that billed an UNKNOWN amount
        # (a request id and no cost) leaves the total unknown, so the
        # stronger model's known cost is not the row's cost. It is kept as
        # `last_cost_usd`, which the row records when its total is None.
        # "Billed" is read as the announcement above reads it, by an id or a
        # cost. **Residual, named:** an OpenAI-compatible endpoint that sends
        # no `id` raises an exhaustion carrying neither, so it shows no bill:
        # the hop is not announced and the total stays the stronger model's,
        # although both of its attempts reached the endpoint.
        unknown = billed is None and getattr(failed, "request_id", None) is not None
        # A summed cost is a bill only if every part was (#87).
        billed_source = getattr(failed, "cost_source", None)
        try:
            response = call(self._escalate_to)
        except Exception as second:
            if billed is not None:
                if not hasattr(second, "cost_usd"):
                    second.cost_usd = billed
                    second.cost_source = billed_source
                elif second.cost_usd is not None:
                    second.cost_usd += billed
                    second.cost_source = combined_source(
                        getattr(second, "cost_source", None), billed_source
                    )
            elif unknown and getattr(second, "cost_usd", None) is not None:
                second.cost_usd = None
            raise
        if billed is not None and response.cost_usd is not None:
            response.cost_usd += billed
            response.cost_source = combined_source(response.cost_source, billed_source)
        elif unknown:
            response.cost_usd = None
        return response

    # ---- capabilities: a flag is true only when both halves honour it ----
    #
    # The rule, stated once because "the cascade supports X" is ambiguous
    # when the halves differ: **the cascade advertises a capability only
    # when both halves do.** Which half answers a given request is decided
    # by a runtime failure, after the request was built, so a caller that
    # was promised a capability has to get it from whichever half answers.
    # For a shape-changing capability (schema, grounding) the alternative is
    # a JSON reply most of the time and prose the rest, or verified citation
    # spans until the day an escalation returns bare markers. For a request
    # hint (effort, caching) the alternative is a `TypeError` from a half
    # whose `generate` does not take the argument, or a hint silently dropped
    # on escalation. Both are the kind of difference nobody notices until it
    # matters, which is what the flags exist to prevent.
    #
    # The cost of the rule is that a local + hosted cascade advertises the
    # local half's capabilities, and a user who wants effort or citations on
    # the hosted half configures it directly. `policyforge llm-check` prints
    # the table for the cascade as configured, so that trade is visible.
    #
    # Until this was written, only `supports_schema` was forwarded and the
    # other four fell to the base class's False — so a flash -> pro cascade
    # of two capable models sent no effort, marked no cache prefix and asked
    # for no citations, the same class of silent loss as the ledger wrapper
    # bug that cost a release. `tests/test_cascade_provider.py` now discovers
    # every flag on `LLMProvider` and holds the cascade to this rule.

    def _both(self, flag: str) -> bool:
        """Read through `getattr` as `llm/effort.py` reads them: a half that
        never heard of a flag does not support it."""

        def ask(half) -> bool:
            method = getattr(half, flag, None)
            return bool(method and method())

        return ask(self._primary) and ask(self._escalate_to)

    def max_output_tokens(self) -> int | None:
        """The smaller of the halves' published ceilings (#3).

        Either half may be the one that answers, so a budget sized for the
        cascade must fit both. A half that publishes none adds no bound: it
        clamps nothing itself either. None when neither half publishes one.
        """
        known = []
        for half in (self._primary, self._escalate_to):
            ask = getattr(half, "max_output_tokens", None)
            value = ask() if ask else None
            if value:
                known.append(int(value))
        return min(known) if known else None

    def supports_schema(self) -> bool:
        return self._both("supports_schema")

    def supports_effort(self) -> bool:
        return self._both("supports_effort")

    def supports_caching(self) -> bool:
        return self._both("supports_caching")

    def supports_grounding(self) -> bool:
        return self._both("supports_grounding")

    def supports_batch(self) -> bool:
        """Never, whatever the halves say.

        A batch is one submission answered hours later; there is no per-item
        failure to escalate on and no second submission that would be
        cheaper than the first. A cascade asked for a batch would either
        submit everything to the primary and hand back whatever it returned,
        or submit everything twice. Neither is what a cascade is for, so the
        flag is False and `ssp --batch` says to configure the batching
        provider directly.
        """
        return False

    def generate_json(self, **kwargs) -> LLMResponse:
        return self._run(lambda half: half.generate_json(**kwargs))

    def generate_grounded(self, **kwargs) -> LLMResponse:
        return self._run(lambda half: half.generate_grounded(**kwargs))

    def check(self) -> bool:
        """Both halves must work.

        Checking only the primary would report a healthy cascade whose
        fallback has a dead key, and the first thing that discovers it
        would be a hard question in front of a user.
        """
        return self._primary.check() and self._escalate_to.check()
