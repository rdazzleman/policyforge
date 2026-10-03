"""Shared internals for the two providers that talk to arbitrary models —
OpenAICompatProvider and LiteLLMProvider. Both face the same problem, which
`_anthropic_compat.py` solves for the Anthropic SDK: a reasoning model
spends tokens thinking before it writes an answer, and neither the thinking
nor a budget exhausted by it is an answer.

Where the model puts its reasoning varies by transport, which is exactly
why this is shared rather than assumed:

- A raw OpenAI-compatible server (Ollama, LM Studio, llama.cpp) inlines it
  in `content` as a <think> block, so it has to be cut out.
- LiteLLM normalises it into a separate `reasoning_content` field, so
  `content` arrives clean — but arrives *empty* when the budget ran out
  mid-thought, which is the same failure wearing different clothes.

Handling both means neither provider has to care which transport it got.
"""

from __future__ import annotations

import re

#: Floor for the retry after a reasoning preamble ate the whole budget.
#: Same value and same reasoning as `_anthropic_compat.MIN_RETRY_TOKENS`:
#: enough for a preamble plus a short answer, which is the shape of every
#: small call this project makes — a routing word, a rewritten question, a
#: list of terms.
MIN_RETRY_TOKENS = 256

#: Local reasoning models (Qwen3, R1 distills, QwQ) emit their chain of
#: thought inline in `content`. Stripped for the reason
#: `_anthropic_compat._text_of` drops thinking blocks: a thinking block is
#: not an answer, and a router that reads one as an answer routes on it.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def answer_of(content: str | None) -> str:
    """The answer with any inline reasoning preamble removed.

    The unclosed case is the one that matters. A small `max_tokens` can be
    spent entirely inside an unterminated <think>, and what comes back then
    is pure reasoning with no closing tag — which, left alone, reads as a
    confident one-word answer to whatever asked. Cutting from the opening
    tag to the end leaves "", which `needs_more_room` can then recognise.
    """
    without_closed = _THINK_BLOCK.sub("", content or "")
    return _UNCLOSED_THINK.sub("", without_closed).strip()


def answer_and_stripped(content: str | None) -> tuple[str, int]:
    """The answer, and how many characters were removed to get it.

    The pair to `LLMResponse.hidden_output_tokens`, from the other side.
    That field records what the vendor says it billed and did not return;
    this records what *we* removed before writing. Today neither is
    recoverable afterwards: a reply arrives, its reasoning is cut, and the
    ledger stores the remainder with nothing to say a cut happened. So
    "did this model spend most of its reply thinking?" is a question that
    needs a fresh run to answer, and a run is not the same run.

    The count is characters rather than tokens, because characters are
    exact here and tokens would need a tokenizer this project does not
    carry for every model. It answers "how much was removed" and not "what
    it cost", which is the honest scope for a subtraction.

    Zero and None stay apart at the call site: this function always returns
    a number, and a provider that never strips leaves the response field
    None. Zero means the strip ran and found nothing to cut.
    """
    answer = answer_of(content)
    return answer, len(content or "") - len(answer)


def needs_more_room(text: str, finish_reason: str | None) -> bool:
    """Whether an empty answer was truncation rather than a decision.

    Only a stop-on-length reply qualifies. A model that stopped normally
    with nothing to say has made a decision, and retrying it spends eight
    times the budget to hear the same silence — while a truncation returned
    as "" is indistinguishable from that decision to every caller, which is
    the bug this exists to prevent. Zardoz's router reads an empty reply as
    "not an analysis" and silently routes to the documents instead;
    measured on the Anthropic path at roughly one call in eight on a
    12-token budget.
    """
    return not text and finish_reason == "length"


class ReasoningBudgetExhausted(RuntimeError):
    """A model spent both attempts reasoning and never reached an answer.

    Raised rather than returning "" because those are different facts and
    only one of them is true. An empty string says the model considered the
    question and chose to say nothing, and callers act on that: Zardoz's
    router reads it as "not an analysis" and sends a coverage question to
    the documents instead. A model that was cut off mid-thought said
    nothing of the kind.

    The first truncation is recoverable and is retried. A *second* one, at
    eight times the budget, is not bad luck — it is a model whose reasoning
    does not fit the job, and the honest report is a loud failure rather
    than a quiet wrong answer. Measured: qwen3.7-flash needed 926 output
    tokens to answer a 150-token query-expansion prompt, and returned empty
    content at both 150 and 1200.

    Deliberately worded to avoid the substrings `evals/runner.py` treats as
    infrastructure — this is a fact about the model, and grading it as "the
    request never ran" would hide exactly what it demonstrates.

    **It carries what was billed** (#343). Both attempts reached the vendor
    and were charged, and the call ends here with no response, so these are
    the only place that cost can travel to the ledger: the summed cost where
    the provider reports one, and the LAST attempt's request id and tokens,
    as a successful call's row carries them. The first attempt is in the
    row's `escalations` (#366).
    """

    #: Both attempts, summed, where the provider reports cost.
    cost_usd: float | None = None
    #: The re-send's own cost: the part `escalations` does not already hold.
    last_cost_usd: float | None = None
    #: Where those costs came from: `base.COST_PROVIDER` or `COST_ESTIMATE` (#87).
    cost_source: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: The re-send's budget.
    max_tokens: int | None = None


def exhausted(
    model: str,
    first: int,
    second: int,
    *,
    cost_usd: float | None = None,
    last_cost_usd: float | None = None,
    request_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    ceiling: int | None = None,
    cost_source: str | None = None,
) -> ReasoningBudgetExhausted:
    if ceiling is not None and second <= first:
        # Not re-sent: the model's published ceiling left no more room than
        # the first attempt had (#3). Saying "and again" would claim a
        # second billed call that never happened.
        exc = ReasoningBudgetExhausted(
            f"{model} used its whole output budget on reasoning and never began an "
            f"answer, at {first} tokens. It was not re-sent with more room: "
            f"{ceiling} is its output ceiling in LiteLLM's model map. Use a model "
            f"that reasons less to reach a short answer."
        )
    else:
        exc = ReasoningBudgetExhausted(
            f"{model} used its whole output budget on reasoning and never began an "
            f"answer, at {first} tokens and again at {second}. Either raise max_tokens "
            f"for this call site, or use a model that reasons less to reach a short answer."
        )
    exc.cost_usd, exc.last_cost_usd, exc.request_id = cost_usd, last_cost_usd, request_id
    exc.cost_source = cost_source
    exc.input_tokens, exc.output_tokens, exc.max_tokens = input_tokens, output_tokens, second
    return exc


def retry_budget(max_tokens: int) -> int:
    """Room to think *and* speak, for the second attempt."""
    return max(max_tokens * 8, MIN_RETRY_TOKENS)
