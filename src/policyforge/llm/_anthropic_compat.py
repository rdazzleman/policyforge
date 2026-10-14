"""Shared internals for AnthropicProvider and VertexProvider: both wrap the
`anthropic` SDK's `messages.create` with the identical request shape (only
how the client itself is constructed/authenticated differs). Not part of
the public LLMProvider interface — provider-specific auth/client setup
stays in each provider's own module.

**No `temperature` is sent (#352, 80's ruling).** anthropic 1.7.0, the
locked SDK, removed the keyword from `messages.create`, so passing it raised
`TypeError` before any request left, on every Anthropic and Vertex call.
Newer models refused it anyway, which is what the old "temperature is
deprecated" retry was for. The callers' `temperature` is still accepted, so
one interface serves every provider, and it is not sent; the providers that
take it (Bedrock, Gemini, LiteLLM, openai-compat) still send it.
"""

from __future__ import annotations

from .base import LLMResponse

#: Floor for the retry after a thinking block ate the whole budget. Enough
#: for a reasoning preamble plus a short answer, which is the shape of every
#: small call this project makes: a routing word, a rewritten question, a
#: list of terms.
MIN_RETRY_TOKENS = 256


def _text_of(response) -> str:
    """The text blocks only. A thinking block is not an answer."""
    return "".join(block.text for block in response.content if block.type == "text")


def _output_format(schema: dict) -> dict:
    """`output_config.format`, from either spelling of a schema.

    This project's schema constants are written in the envelope LiteLLM
    wants — `{"type": "json_schema", "json_schema": {"name": ..., "schema":
    {...}}}` — and the Messages API wants the schema itself under
    `format.schema`. Accepting both means one constant per prompt rather
    than one per provider, which is the point of having an interface.
    """
    inner = schema.get("json_schema", schema)
    return {"type": "json_schema", "schema": inner.get("schema", inner)}


def _message_params(request, *, model: str) -> dict:
    """One batch entry's request body, in the same shape as a live call."""
    params: dict = {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": request.system,
        "messages": [{"role": "user", "content": request.prompt}],
    }
    if request.effort is not None:
        params["output_config"] = {"effort": request.effort}
    if request.cache_prefix:
        params["messages"] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": request.cache_prefix,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": request.prompt},
                ],
            }
        ]
    return params


def submit_batch(
    client,
    requests,
    *,
    model: str,
    poll_seconds: float = 30.0,
    timeout_seconds: float = 24 * 60 * 60,
    sleep=None,
    clock=None,
) -> dict:
    """Submit `requests` as one batch and wait for it, keyed by `custom_id`.

    Polls rather than streams because a batch has no progress to stream:
    it is queued, it ends, and the results arrive together. The wait is
    bounded — a batch that never ends would otherwise hang a run forever —
    and the bound is a day, which is the window the API itself promises.

    Results come back in any order. They are read into a dict by the id the
    caller chose, and a caller that matched them by position would silently
    attribute one control's narrative to another.
    """
    import time

    from .base import LLMResponse
    from .batch import BatchError

    sleep = sleep or time.sleep
    clock = clock or time.monotonic

    payload = [
        {"custom_id": request.custom_id, "params": _message_params(request, model=model)}
        for request in requests
    ]
    if not payload:
        return {}

    batch = client.messages.batches.create(requests=payload)
    started = clock()
    while True:
        state = client.messages.batches.retrieve(batch.id)
        if getattr(state, "processing_status", None) == "ended":
            break
        if clock() - started > timeout_seconds:
            raise BatchError(
                f"Batch {batch.id} was still {getattr(state, 'processing_status', '?')} after "
                f"{timeout_seconds / 3600:.0f}h. Nothing was written; the batch may still "
                f"finish, and its results can be fetched with its id."
            )
        sleep(poll_seconds)

    answers: dict[str, LLMResponse] = {}
    failures: list[str] = []
    for entry in client.messages.batches.results(batch.id):
        result = entry.result
        if getattr(result, "type", None) != "succeeded":
            failures.append(f"{entry.custom_id}: {getattr(result, 'type', 'unknown')}")
            continue
        message = result.message
        usage = message.usage
        answers[entry.custom_id] = LLMResponse(
            text=_text_of(message),
            model=getattr(message, "model", model),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            stop_reason=getattr(message, "stop_reason", None),
            cached_input_tokens=getattr(usage, "cache_read_input_tokens", None),
            request_id=getattr(message, "id", None),
            endpoint=sent_to(client),
        )

    if failures:
        raise BatchError(
            f"{len(failures)} of {len(payload)} batch request(s) did not succeed: "
            + "; ".join(failures[:5])
            + ("..." if len(failures) > 5 else "")
        )
    return answers


def sent_to(client) -> str | None:
    """Where an Anthropic SDK client sends its requests (#328): the base URL
    the SDK resolved when it was built, so `ANTHROPIC_BASE_URL` (or the
    Vertex client's own override) is what's recorded, not the default."""
    from .base import recordable_endpoint

    return recordable_endpoint(getattr(client, "base_url", None))


def call_messages_api(
    client,
    *,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    effort: str | None = None,
    cache: bool = False,
    cache_prefix: str | None = None,
    schema: dict | None = None,
    documents=None,
) -> LLMResponse:
    import anthropic

    from .ceiling import clamp, published_output_ceiling
    from .grounded import documents_block, read_citations

    # Never more than the model's published ceiling (#3), where one is
    # published for this model id; a larger request is a 400.
    cap = published_output_ceiling(model)
    max_tokens = clamp(max_tokens, cap)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }

    if documents:
        if schema is not None:
            # The API refuses this combination, and it refuses it with a
            # message about content blocks that does not mention citations.
            # Saying so here costs one branch and saves reading that.
            raise ValueError(
                "A reply cannot be both cited and constrained to a schema — "
                "`output_config.format` and document citations are mutually "
                "exclusive. Ask for one or the other."
            )
        # Documents first, question last. The same ordering the prose path
        # uses, and for the same measured reason: the text nearest the
        # question is what a model weighs hardest, so the instruction goes
        # after the material it is about.
        kwargs["messages"] = [
            {
                "role": "user",
                "content": [*documents_block(documents), {"type": "text", "text": prompt}],
            }
        ]

    if cache:
        # A cache is a prefix match, so the breakpoint goes at the end of
        # the region that repeats — and the render order is system, then
        # messages. Marking the system prompt alone caches the system
        # prompt; a call site whose user prompt also opens with unchanging
        # text (the organization block in front of each control) passes it
        # as `cache_prefix`, and the breakpoint moves past both.
        #
        # The text sent is identical either way: two text blocks are the
        # same content as their concatenation, so this changes what is
        # billed and not what the model reads.
        if documents:
            # The documents are the bulk of the request and they are already
            # built, so the breakpoint goes on the last of them rather than
            # rebuilding the turn from a prefix string — which would drop the
            # document blocks and turn a cited answer into an uncited one.
            kwargs["messages"][0]["content"][len(documents) - 1]["cache_control"] = {
                "type": "ephemeral"
            }
        elif cache_prefix:
            kwargs["messages"] = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": cache_prefix,
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
        else:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

    # Both of these live under `output_config`, so they are built together:
    # writing one and then the other would drop whichever came first. And
    # `effort` belongs inside it rather than at the top level, where the
    # SDK's kwargs accept it and the API changes nothing — the worst of both,
    # since the call site believes it asked for less deliberation and pays
    # for the same.
    output_config: dict = {}
    if effort is not None:
        output_config["effort"] = effort
    if schema is not None:
        output_config["format"] = _output_format(schema)
    if output_config:
        kwargs["output_config"] = output_config

    def _create(**overrides):
        """One request. `temperature` is never sent (#352): see the module
        docstring."""
        try:
            return client.messages.create(**{**kwargs, **overrides})
        except anthropic.BadRequestError as exc:
            # Any other 400 is the API refusing the request as sent — a
            # max_tokens above the model's cap is the usual one now that
            # document budgets are 16384. Raised as the project's own type so
            # `llm/effort.py` can say which call and which budget, and the
            # CLI can print that instead of a traceback ending in the SDK.
            from .base import ProviderRejected

            raise ProviderRejected(
                str(exc), status=getattr(exc, "status_code", None), model=model
            ) from exc

    response = _create()
    text = _text_of(response)

    # A reasoning model emits a thinking block before any text, and those
    # tokens come out of `max_tokens`. On a tight budget the whole
    # allowance can be spent thinking, and the reply comes back with
    # `stop_reason="max_tokens"` and no text block at all.
    #
    # Returning "" for that is the dangerous outcome, because it is
    # indistinguishable from the model deliberately saying nothing — and
    # callers read that as a decision. Zardoz's router treats an empty reply
    # as "not an analysis", so a truncated routing call silently sends a
    # coverage question to the documents instead. Measured at roughly one
    # call in eight on a 12-token budget: intermittent, invisible, and
    # wrong. So a truncation before any text is retried with room to speak.
    # Clamped to the ceiling too (#3); a clamp that leaves no more room than
    # the first attempt had is not re-sent, since it would bill the same
    # empty reply again. The empty reply then reaches the caller, which
    # refuses it as before.
    second = clamp(max(max_tokens * 8, MIN_RETRY_TOKENS), cap)
    if not text and getattr(response, "stop_reason", "") == "max_tokens" and second > max_tokens:
        from . import escalation

        first_usage = getattr(response, "usage", None)
        # Said before it is sent, and kept for this call's ledger row (#361).
        escalation.announce(
            model=model,
            first_max_tokens=max_tokens,
            max_tokens=second,
            input_tokens=getattr(first_usage, "input_tokens", None),
            first_request_id=getattr(response, "_request_id", None),
            first_output_tokens=getattr(first_usage, "output_tokens", None),
            first_stop_reason=getattr(response, "stop_reason", None),
        )
        response = _create(max_tokens=second)
        text = _text_of(response)

    usage = response.usage
    return LLMResponse(
        text=text,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        # All three were on the response already and thrown away here. The
        # retry above is the only thing that ever read `stop_reason`, so a
        # reply truncated *after* some text reached the caller looking
        # finished; a cache that silently never hit was invisible; and the
        # request id, which is the first thing Anthropic support asks for,
        # cannot be reconstructed once this function returns.
        stop_reason=getattr(response, "stop_reason", None),
        cached_input_tokens=getattr(usage, "cache_read_input_tokens", None),
        request_id=getattr(response, "_request_id", None),
        citations=read_citations(response, documents) if documents else None,
        endpoint=sent_to(client),
    )
