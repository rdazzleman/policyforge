"""How hard a model should think, chosen per call site.

The token budgets in `zardoz/budgets.py` exist because of a shape this
project measured rather than guessed: a reasoning model spends its ceiling
thinking before a one-word answer, so a routing call asking for a single
word needed 800 tokens of headroom and still came back empty one time in
eight. `_anthropic_compat` carries a retry for the same reason, at eight
times the budget.

Both are workarounds for the wrong lever. On current Claude models thinking
is adaptive and `output_config.effort` is the dial: how much the model
spends deliberating is the thing to set, not how much room its deliberation
is allowed to consume. Effort is per request, so it belongs at the call
site — the same request that knows whether it is routing a question or
drafting a Standard.

The tiers below are the ones the budgets already expressed. A call that
picks one word from a closed list, rewrites a question, or lists search
terms is `LOW`. A call that writes a document somebody will be audited
against is `HIGH`.

Nothing here lowers a budget. The budgets were measured, and changing them
is a measured change: `docs/measurements/epochs.md` epoch 2 records what happened when
they were set too tight, and the same evidence is owed before setting them
back down. Effort goes in first, and the budgets come down when a sweep says
they can.
"""

from __future__ import annotations

LOW = "low"
MEDIUM = "medium"
HIGH = "high"

#: One word from a closed list. Deliberation here is the failure mode, not
#: the goal — the router that thinks its way past "documents" has made the
#: shell worse.
ROUTING = LOW
#: A question rewritten into the question it obviously means.
RESOLUTION = LOW
#: The document's own vocabulary, guessed. Wrong guesses cost a retrieval,
#: not a document.
EXPANSION = LOW
#: Reading passages and writing a grounded answer with citations. Mid: the
#: claims have to be checked against the passages, and that is reasoning,
#: but an answer is not a document.
ANSWERING = MEDIUM
#: Judging whether a passage carries a claim. The whole point of the call is
#: the judgement.
ENTAILMENT = MEDIUM
#: Grouping page titles nobody's naming convention reached. A judgement, but
#: over titles rather than requirements, and a wrong group is a proposal a
#: person declines rather than a document they publish.
CLUSTERING = MEDIUM
#: Reading one requirement against candidate controls and quoting the basis
#: for each mapping. A judgement over requirement text, and the output is a
#: proposal a person reviews, never a mapping the pipeline reads unreviewed.
MAPPING = MEDIUM
#: Merging framework requirements into statements a policy is built from.
SYNTHESIS = HIGH
#: Drafting a Standard, Policy or Procedure, and planning or applying an
#: edit to a published one. These are the documents an assessor reads.
DRAFTING = HIGH
EDITING = HIGH
#: One control narrative for a System Security Plan.
NARRATIVE = HIGH


def accepts_effort(provider) -> bool:
    """Whether this provider will actually act on an effort level.

    Asked rather than assumed, for the reason `supports_schema` is asked:
    a provider that cannot honour the parameter would have to accept it and
    ignore it, and the call site would then believe it had set something.
    Read through `getattr` because providers here are duck-typed — the fakes
    in the test suite implement `generate` and nothing else.
    """
    ask = getattr(provider, "supports_effort", None)
    return bool(ask and ask())


def output_ceiling(provider) -> int | None:
    """The provider's published output ceiling, or None if it names none.

    Duck-typed like the flags above, and NOT a method on `LLMProvider`: a
    method the base class defines is answered by the base through the
    ledger's `RecordingProvider`, whatever the provider inside would say.
    """
    ask = getattr(provider, "max_output_tokens", None)
    try:
        value = ask() if ask else None
    except Exception:  # noqa: BLE001 - an unanswerable question is no ceiling
        return None
    return int(value) if value else None


def accepts_caching(provider) -> bool:
    """Whether this provider will actually mark a cacheable prefix."""
    ask = getattr(provider, "supports_caching", None)
    return bool(ask and ask())


#: What `generate` assumes when a call site names no budget. Kept equal to
#: the providers' own default so the retry arithmetic below starts from the
#: number the first request actually carried.
DEFAULT_MAX_TOKENS = 4096
#: A cut-off reply is retried once at this multiple of its budget ...
RETRY_FACTOR = 2
#: ... and never above this, whatever the site asked for, nor above the
#: model's published ceiling (`llm/ceiling.py`). Twice the largest budget
#: any site names (a long document at 32768, #3), so the largest document
#: still gets its one retry; a reply that needs more than this is a document
#: that should have been two documents. Was 32768 while the largest site
#: budget was 16384.
RETRY_CEILING = 65536


def truncated(response) -> bool:
    """Whether a reply stopped at its output budget, read leniently.

    Through `getattr`, like every capability question here: a response from
    a duck-typed fake without the property is a finished reply.
    """
    return bool(getattr(response, "truncated", False))


def _complete(send, kwargs: dict, ceiling: int | None = None):
    """`send(**kwargs)`, retried once with a larger budget if it was cut off.

    Every reply this module hands back has been checked here, so a call site
    cannot forget to. A reply that stopped at its budget is retried once at
    `RETRY_FACTOR` times that budget, capped at `RETRY_CEILING`; one that is
    still cut off raises `TruncatedResponse`, naming the subject the ledger
    scope knows, rather than returning text that looks finished and is not.
    Both calls were billed and both are in the ledger, which is what the
    ledger is for.

    A provider may retry on its own first: the Anthropic shim and the
    LiteLLM provider each re-send an *empty* length-cut reply at eight
    times the budget (`_inline_thinking.needs_more_room`), and hand back
    whatever that second attempt produced. If that is a non-empty cut-off
    reply, this retry follows at twice the *original* budget — smaller than
    the provider's own retry, because the provider does not expose the
    budget it last used. Up to four billed calls in the worst case, all in
    the ledger. Accepted rather than plumbed through: the empty-then-cut
    sequence needs a reasoning model to spend 8x its budget thinking and
    then overrun on the answer, and the fix for that is a larger budget at
    the site, which the refusal names.
    """
    from .base import ProviderRejected, TruncatedResponse
    from .ledger import current_scope

    first = int(kwargs.get("max_tokens") or DEFAULT_MAX_TOKENS)
    # What the provider will actually have sent: it clamps to the model's
    # ceiling (#3). The retry is sized from that, and capped by it, so a
    # retry the ceiling would clamp back to the same budget isn't sent.
    if ceiling:
        first = min(first, ceiling)

    def _send(request: dict, budget: int):
        # A rejection carries the vendor's words; what it cannot know is
        # which call this was and how much it asked for. Added here, the one
        # place both are in hand, so the message that reaches the user
        # names the document, the command and the number.
        try:
            return send(**request)
        except ProviderRejected as exc:
            scope = current_scope()
            exc.annotate(
                subject=scope.subject if scope else None,
                site=scope.site if scope else None,
                budget=budget,
            )
            raise

    # The first request goes out exactly as the site wrote it — a site that
    # names no budget keeps sending none — so the request shape is unchanged
    # by this function; only the retry sets a budget of its own.
    response = _send(kwargs, first)
    if not truncated(response):
        return response
    larger = min(first * RETRY_FACTOR, RETRY_CEILING, ceiling or RETRY_CEILING)
    if larger > first:
        from . import escalation

        # Said before it is sent (#361). The cut-off first attempt already
        # has its own ledger row here, since each send is a separate call.
        escalation.announce(
            model=getattr(response, "model", None) or "the model",
            first_max_tokens=first,
            max_tokens=larger,
            input_tokens=getattr(response, "input_tokens", None),
            first_request_id=getattr(response, "request_id", None),
            first_cost_usd=getattr(response, "cost_usd", None),
            first_cost_source=getattr(response, "cost_source", None),
            first_output_tokens=getattr(response, "output_tokens", None),
            first_stop_reason=getattr(response, "stop_reason", None),
            empty=not (getattr(response, "text", "") or "").strip(),
        )
        response = _send({**kwargs, "max_tokens": larger}, larger)
        if not truncated(response):
            return response
    else:
        larger = first
    scope = current_scope()
    raise TruncatedResponse(
        subject=scope.subject if scope else None,
        site=scope.site if scope else None,
        first_budget=first,
        budget=larger,
        stop_reason=getattr(response, "stop_reason", None),
        model=getattr(response, "model", None),
        text=getattr(response, "text", "") or "",
    )


def document_text(response, *, what: str) -> str:
    """The reply's text, for a call whose reply is a document.

    Refuses an empty one by name rather than returning "" for a caller to
    write. Asked for explicitly by the document producers — synthesis,
    drafting, editing — and not applied inside `_complete`, because a
    routing or expansion call may answer nothing and mean it.
    """
    text = (getattr(response, "text", "") or "").strip()
    if text:
        return text
    from .base import EmptyReply
    from .ledger import current_scope

    scope = current_scope()
    raise EmptyReply(
        what=what,
        subject=scope.subject if scope else None,
        site=scope.site if scope else None,
        response=response,
    )


def call(
    provider,
    *,
    effort: str | None = None,
    cache: bool = False,
    cache_prefix: str | None = None,
    **kwargs,
):
    """`provider.generate(**kwargs)`, with the parts a provider can honour.

    The single place that knows how to ask. A provider that advertises
    neither is called exactly as before — not handed parameters it would
    drop, which would let the call site believe it had asked for less
    deliberation, or for a tenth of the price on the half of the request
    that never changes.

    `cache` marks the stable region so the API can serve it from the prompt
    cache; `cache_prefix` says how much of the user prompt belongs to that
    region, for a call site whose leading text repeats every time.
    """
    extra = {}
    if effort is not None and accepts_effort(provider):
        extra["effort"] = effort
    if cache and accepts_caching(provider):
        extra["cache"] = True
        if cache_prefix is not None:
            extra["cache_prefix"] = cache_prefix
    elif cache_prefix is not None:
        # The prefix is part of the prompt, not a hint about it. A provider
        # that cannot mark it still has to be *sent* it, or the call site
        # would silently drop the organization block it split off — which
        # is a different request, not a slower one.
        kwargs["prompt"] = cache_prefix + kwargs["prompt"]
    return _complete(provider.generate, {**extra, **kwargs}, output_ceiling(provider))


def call_json(provider, *, effort: str | None = None, **kwargs):
    """`provider.generate_json(**kwargs)`, at `effort` where it means something.

    The schema half of `call`. Kept separate rather than folded in, because
    asking for a schema is a different question from asking for less
    deliberation: `generate_json` refuses outright on a provider that cannot
    constrain a reply, and that refusal is the point — a caller parsing JSON
    that was never guaranteed to be JSON is the failure it exists to remove.
    """
    if effort is not None and accepts_effort(provider):
        return _complete(
            provider.generate_json, {"effort": effort, **kwargs}, output_ceiling(provider)
        )
    return _complete(provider.generate_json, kwargs, output_ceiling(provider))


def accepts_grounding(provider) -> bool:
    """Whether this provider will send passages as citable documents."""
    ask = getattr(provider, "supports_grounding", None)
    return bool(ask and ask())


def call_grounded(provider, *, documents, effort: str | None = None, **kwargs):
    """`provider.generate_grounded(**kwargs)`, at `effort` where it lands."""
    if effort is not None and accepts_effort(provider):
        return _complete(
            provider.generate_grounded,
            {"documents": documents, "effort": effort, **kwargs},
            output_ceiling(provider),
        )
    return _complete(
        provider.generate_grounded, {"documents": documents, **kwargs}, output_ceiling(provider)
    )


def accepts_schema(provider) -> bool:
    """Whether this provider will actually hold a reply to a schema."""
    ask = getattr(provider, "supports_schema", None)
    return bool(ask and ask())


def call_shaped(provider, *, schema: dict, effort: str | None = None, **kwargs):
    """Ask for a shape, and settle for asking nicely where that is all there is.

    `call_json` refuses on a provider that cannot constrain a reply. That is
    right for the entailer, whose whole output is a label from a closed set —
    an unconstrained verdict is not worth having, so not getting one is the
    correct outcome.

    It is wrong for the two callers here. The edit planner and the topic
    clusterer both describe their shape in the system prompt and both parse
    the reply leniently, and they have run that way against local models
    since before any provider here could enforce a schema. Refusing would
    take a working feature away from an Ollama user to gain a guarantee they
    were never relying on. So the schema goes where it can be honoured, the
    prompt keeps asking everywhere, and the lenient parser stays — it is
    still the only thing standing behind a model that answers in prose.
    """
    if accepts_schema(provider):
        return call_json(provider, schema=schema, effort=effort, **kwargs)
    return call(provider, effort=effort, **kwargs)
