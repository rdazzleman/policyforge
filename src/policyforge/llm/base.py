"""Provider-agnostic LLM interface.

Ships six concrete providers: Anthropic's API directly, Amazon Bedrock,
Google Cloud's Vertex AI Model Garden, any OpenAI-compatible
chat-completions endpoint (a local Ollama/LM Studio/vLLM server, or a
hosted open model), LiteLLM, which reaches most of the rest behind one
model string, and a cascade that pairs two of the above. Everything that
calls an LLM in
this codebase should depend on this interface, not on `anthropic` or a
cloud SDK directly — that's what makes it possible to add another provider
later without touching mapping/, synthesis/, or generate/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

#: The two answers to "where did this cost come from?" (#87).
COST_PROVIDER = "provider"
COST_ESTIMATE = "estimate"


def combined_source(*sources: str | None) -> str | None:
    """The source of a sum of costs: a bill only when every part was.

    None parts (no cost) are skipped; a sum with any estimated part is an
    estimate, since presenting it as billed is the failure #87 exists for.
    """
    known = [s for s in sources if s is not None]
    if not known:
        return None
    return COST_PROVIDER if all(s == COST_PROVIDER for s in known) else COST_ESTIMATE


def recordable_endpoint(url) -> str | None:
    """`url` as the ledger may keep it (#328): scheme, host, port and path.

    Dropped: the user name and password before the host, the query string,
    the fragment, and each path segment's `;parameters` (ba on PR 353), the
    places a URL conventionally carries a credential. The path is otherwise
    kept as given, so a secret that IS a path segment (`/k/<key>/v1`) is
    recorded: nothing can tell it from an ordinary segment (1d and ba on
    PR 353). None when there is no URL, or nothing with both a scheme and a
    host to keep.
    """
    if not url:
        return None
    from urllib.parse import urlsplit

    parts = urlsplit(str(url))
    host = parts.netloc.rpartition("@")[2]
    if not parts.scheme or not host:
        return None
    # `urlsplit` leaves `;params` in the path, on any segment, not only the
    # last, so each segment is cut at its first `;`.
    path = "/".join(segment.partition(";")[0] for segment in parts.path.split("/"))
    return f"{parts.scheme}://{host}{path}"


class SchemaReplyError(RuntimeError):
    """A schema-constrained call came back as something other than JSON.

    A `RuntimeError` so every existing handler still catches it, and its own
    type so a caller can tell "the model answered, just not in the shape
    asked" from a timeout or an auth failure. The first carries a reply worth
    reading (`text`); the second has nothing to recover, and a caller that
    retries on it only doubles the wait.
    """

    def __init__(self, message: str, text: str, response: LLMResponse | None = None):
        super().__init__(message)
        self.text = text
        # The reply was billed (#426). Its id, cost and tokens travel on the
        # exception, as a re-send's do (#343), since this call reaches the
        # ledger with no response: 22 real rows had lost both id and cost.
        self.cost_usd = response.cost_usd if response else None
        self.last_cost_usd = response.last_cost_usd if response else None
        self.cost_source = response.cost_source if response else None
        self.request_id = response.request_id if response else None
        self.input_tokens = response.input_tokens if response else None
        self.output_tokens = response.output_tokens if response else None


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: What the call cost, in USD, when the provider can say. Only
    #: LiteLLMProvider populates it today — it prices the call from
    #: LiteLLM's own tables — and a local model correctly reports 0.0
    #: rather than None. Left None by every other provider, so a caller
    #: must treat "unknown" and "free" as different answers.
    cost_usd: float | None = None
    #: Where `cost_usd` (or `last_cost_usd`, when that is the known part)
    #: came from (#87): `COST_PROVIDER` when the vendor reported what it
    #: billed, `COST_ESTIMATE` when LiteLLM priced the tokens from its model
    #: map instead. On a chat route only OpenRouter reports its own cost, in
    #: LiteLLM 1.103.0; every other route's cost is an estimate. None when
    #: there is no cost. A sum of attempts is `COST_PROVIDER` only when
    #: every attempt was: part of it estimated is not a bill.
    cost_source: str | None = None
    #: Why the model stopped. `max_tokens` means the reply was cut off, and
    #: the providers already knew: they read it to decide whether to retry
    #: and then threw it away, so a truncated document reached the caller
    #: looking like a finished one. Now it travels, and the ledger records it.
    stop_reason: str | None = None
    #: Input tokens served from the prompt cache. Zero and None are different
    #: answers: zero means the call could have hit the cache and did not —
    #: which is what a silently-too-short cacheable prefix looks like — and
    #: None means the provider does not report caching at all.
    cached_input_tokens: int | None = None
    #: Tokens billed as output that are not in `text`. Today that means a
    #: model's own reasoning — Gemini calls it `thoughtsTokenCount`, other
    #: vendors call it thinking or reasoning — but the field is about the
    #: billing contract rather than the cause: the operator paid for these
    #: and cannot see them.
    #:
    #: **None and zero are different answers, and the difference is the
    #: point.** None means the provider does not report such tokens, which
    #: is every provider here except Gemini today. Zero means it reports
    #: them and there were none on this call. Defaulting to zero would say
    #: "this call had no hidden output" about providers that simply cannot
    #: tell, and a cost total built on that reads as complete when it is a
    #: floor.
    #:
    #: This is not a Gemini property. It is the first provider in this
    #: project to *surface* the count; a thinking model reached through
    #: LiteLLM or an OpenAI-compatible endpoint bills the same way and
    #: reports nothing here, so its ledger entries stay honestly unknown
    #: rather than falsely zero.
    #:
    #: Measured, not assumed: on a live call, 842 of these sat beside 813
    #: returned tokens, so roughly half of what was billed was invisible to
    #: the ledger before this existed.
    hidden_output_tokens: int | None = None
    #: Characters removed from the reply before it was recorded — inline
    #: reasoning that `_inline_thinking.answer_of` cuts. The pair to
    #: `hidden_output_tokens` from the other side: that field is what the
    #: vendor billed and did not return, this is what arrived and was not
    #: kept. Both were unrecoverable after the fact until they existed.
    #:
    #: None means this provider does not strip, which is every provider
    #: reaching a vendor that separates reasoning for us. Zero means the
    #: strip ran and found nothing, which is the common case on a model
    #: that does not think inline. The difference matters for the same
    #: reason it does on the token counts: a zero asserted on behalf of a
    #: provider that never looked is a measurement nobody made.
    #:
    #: **Read this when a reply came back empty.** An empty `text` has two
    #: causes that were indistinguishable until this field existed: the
    #: model said nothing, or it said only unclosed reasoning that was cut.
    #: A non-zero count beside an empty answer means the whole reply was
    #: thinking the budget never escaped — the case `needs_more_room` and
    #: `ReasoningBudgetExhausted` exist for — and a zero means the model
    #: genuinely returned nothing. The ledger now records which, so the
    #: question can be answered about a run that has already finished
    #: rather than only by making the call again. That is the difference
    #: between recording a symptom and recording a cause, and it is the
    #: reason to reach for this field from the `EmptyReply` path, where
    #: nothing else would point you at it.
    stripped_reasoning_chars: int | None = None
    #: The provider's own id for the request, which is the first thing
    #: Anthropic support asks for. Free on every response and impossible to
    #: reconstruct afterwards.
    request_id: str | None = None
    #: Where this call went (#328): the base URL the provider resolved for
    #: it, environment overrides such as `ANTHROPIC_BASE_URL` included,
    #: through `recordable_endpoint`, which drops userinfo, the query, the
    #: fragment and path parameters, and keeps the rest of the path.
    #: None where the provider can't say (a fake client in a test).
    endpoint: str | None = None
    #: The LAST attempt's own cost, when known: the request `request_id`
    #: names. Equal to `cost_usd` for a call sent once. After a re-send
    #: `cost_usd` is every attempt summed, or None if any attempt's cost is
    #: unknown, and this is what keeps the last attempt's known figure in
    #: that case (#382). The ledger records it only on a row whose total is
    #: None; it is never an addend to a total that is known. Only
    #: LiteLLMProvider reports a cost today: a provider that starts to must
    #: set this too, or a re-send's known cost is lost again (1d on #431).
    last_cost_usd: float | None = None
    #: Spans the API says were quoted, when the request sent its passages as
    #: document blocks. `llm/grounded.py` says what makes these different
    #: from the `[2]` markers a model writes: the text is extracted from the
    #: document rather than generated, so it is verbatim by construction.
    #: None means the request did not ask for citations; an empty list means
    #: it did and the model cited nothing.
    citations: list | None = None

    @property
    def truncated(self) -> bool:
        """Whether the model stopped because it ran out of output budget.

        Each vendor spells it differently — `length` (OpenAI and everything
        LiteLLM maps to it), `max_tokens` (Anthropic, Vertex, Bedrock),
        `MAX_TOKENS` (Gemini's enum) — and the difference between them is
        nothing a caller should have to know. A reply that stopped this way
        is not a document, however finished its last sentence looks: fifteen
        of eighty calls in one 20-topic run ended here, and every one was
        written to disk with exit 0.
        """
        return (self.stop_reason or "").strip().lower() in TRUNCATION_STOP_REASONS


#: The stop reasons that mean "cut off", lower-cased. Kept as data so a new
#: provider's spelling is one line here rather than a check somewhere else.
TRUNCATION_STOP_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


class TruncatedResponse(RuntimeError):
    """A reply was cut off at its output budget, twice.

    Raised by `llm/effort.py` after one retry with a larger budget, so a
    caller never sees text that the model did not finish. The partial text
    travels on the exception for diagnosis and is never written anywhere:
    a Standard missing its last four requirements reads as a Standard.
    """

    def __init__(
        self,
        *,
        subject: str | None,
        site: str | None,
        first_budget: int,
        budget: int,
        stop_reason: str | None,
        model: str | None,
        text: str,
    ):
        self.subject = subject
        self.site = site
        self.first_budget = first_budget
        self.budget = budget
        self.stop_reason = stop_reason
        self.model = model
        self.text = text
        what = subject or "the reply"
        where = f" ({site})" if site else ""
        retried = (
            f" after a retry from {first_budget}"
            if budget != first_budget
            else " and could not be retried any larger"
        )
        super().__init__(
            f"{what}{where}: the model's reply was cut off at {budget} output tokens "
            f"(stop_reason {stop_reason!r}{', ' + model if model else ''}){retried}. "
            f"Nothing was written. Give this call site a larger budget or split the "
            f"work; the {len(text)} characters that came back are not kept."
        )


class EmptyReply(RuntimeError):
    """A document call came back with no text at all, and stopped normally.

    Different from a truncation, and found by the live acceptance run for
    the truncation fix: glm-5.3-flash answered a synthesis request with
    4,869 output tokens, stop_reason `stop`, and an empty content field — the
    tokens went into its reasoning channel and never into an answer — and
    the synthesis was written as a file with frontmatter and no body, exit 0.
    The next command then failed on an empty synthesis. A routing call may
    legitimately answer nothing; a document never may.
    """

    def __init__(self, *, what: str, subject: str | None, site: str | None, response):
        self.what = what
        self.subject = subject
        self.site = site
        self.stop_reason = getattr(response, "stop_reason", None)
        self.model = getattr(response, "model", None)
        self.output_tokens = getattr(response, "output_tokens", None)
        where = f" ({site})" if site else ""
        spent = (
            f" after {self.output_tokens} output tokens" if self.output_tokens is not None else ""
        )
        super().__init__(
            f"{subject or what}{where}: the model returned no text for the {what}"
            f"{spent} (stop_reason {self.stop_reason!r}{', ' + self.model if self.model else ''}). "
            f"Nothing was written. A reasoning model may have spent the whole reply "
            f"thinking; try again, or choose a model that answers in its content."
        )


class ProviderHttpError(RuntimeError):
    """A hosted model endpoint answered with a status that is not about the
    request as written (#413): 401 and 403 are the key, 429 clears by waiting,
    a 5xx is the server. A `RuntimeError` with the message these sites always
    raised (`<url> returned HTTP <status>: <body>`), so anything that matched
    or caught the old one still does; it also carries the status and the
    address, so the CLI can say which service refused and what to check
    (`cli._http_failure`) rather than print a traceback."""

    def __init__(self, message: str, *, status: int, url: str, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url
        self.reason = reason


class ProviderRejected(RuntimeError):
    """The provider refused the request before answering it.

    A 4xx from the vendor — a `max_tokens` above the model's cap, an
    unknown model, a parameter the model does not take. Raised by the
    provider with the vendor's own message, then annotated by
    `llm/effort.py` with the ledger subject, the site and the budget the
    request carried, so what reaches the user says which document, which
    command and which number, rather than a traceback ending in the SDK.
    The budgets sized in #74 made this the likeliest 4xx: a model whose
    output cap is below 16384 rejects every draft, and "HTTP 400" tells
    nobody why.
    """

    def __init__(self, vendor_message: str, *, status: int | None = None, model: str | None = None):
        self.vendor_message = vendor_message.strip()
        self.status = status
        self.model = model
        self.subject: str | None = None
        self.site: str | None = None
        self.budget: int | None = None
        super().__init__(self._render())

    def annotate(self, *, subject: str | None, site: str | None, budget: int | None) -> None:
        """Add what the provider could not know: which call, on whose behalf."""
        self.subject = subject
        self.site = site
        self.budget = budget
        self.args = (self._render(),)

    def _render(self) -> str:
        where = f"{self.subject}" if self.subject else "the request"
        if self.site:
            where += f" ({self.site})"
        status = f" (HTTP {self.status})" if self.status else ""
        model = f" by {self.model}" if self.model else ""
        budget = f" at {self.budget} output tokens" if self.budget else ""
        return (
            f"{where}: the request{budget} was rejected{model}{status}: {self.vendor_message}. "
            f"Nothing was written. If the message names max_tokens, this model's output cap "
            f"is below the budget for this call site; lower the budget or choose a model "
            f"that accepts it."
        )


class LLMProvider(ABC):
    """Minimal surface every provider must implement."""

    @abstractmethod
    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> LLMResponse:
        """Send a single-turn request and return the completion."""
        raise NotImplementedError

    @abstractmethod
    def check(self) -> bool:
        """Cheap connectivity/auth check. Used by `policyforge llm-check`."""
        raise NotImplementedError

    # ---- structured output, opt-in ------------------------------------
    #
    # Deliberately not part of `generate`'s signature. A provider that
    # cannot enforce a schema would have to accept the argument and ignore
    # it, and a caller would then parse JSON that was never guaranteed to
    # be JSON — the silent failure this is supposed to remove. Asking first
    # is the honest shape, and it keeps every existing provider working
    # untouched.
    #
    # Worth having because three unrelated models have now failed the
    # `INSUFFICIENT_CONTEXT` sentinel while being substantively correct,
    # and `parse_expansion` discards a reply of the wrong shape, which is
    # what made one model score 11% on expansion. A schema turns "did the
    # model obey the format" from a graded risk into a guarantee.

    def supports_schema(self) -> bool:
        """Whether `generate_json` will actually constrain the reply."""
        return False

    # ---- effort, opt-in ------------------------------------------------
    #
    # Asked rather than passed, for the same reason as the schema above. A
    # provider that cannot act on an effort level would have to accept the
    # argument and drop it, and the call site would believe it had set
    # something. `llm/effort.py` is where a caller asks.

    def supports_effort(self) -> bool:
        """Whether `generate` will act on an `effort` argument."""
        return False

    def supports_caching(self) -> bool:
        """Whether `generate` will act on `cache` / `cache_prefix`.

        Asked for the same reason again. A provider that cannot mark a
        cacheable prefix would drop the hint, and the high-volume call
        sites would believe they were paying a tenth for the part of the
        request that never changes.
        """
        return False

    # ---- batching, opt-in ----------------------------------------------

    def supports_batch(self) -> bool:
        """Whether `generate_batch` will submit a real batch."""
        return False

    def generate_batch(self, requests, **kwargs) -> dict:
        """Answer many requests together, keyed by each one's `custom_id`.

        Only call this when `supports_batch()` is True. The default refuses
        rather than quietly running the requests one at a time: a caller
        that asked for a batch asked for its price, and a silent fallback
        would bill interactive rates for a run somebody chose to wait on.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot submit a batch. "
            f"Check supports_batch() before calling generate_batch()."
        )

    # ---- native citations, opt-in --------------------------------------
    #
    # Asked before use, like everything else here. A provider that cannot
    # send document blocks would have to flatten them into the prompt, and
    # the caller would then believe its citations were verified spans when
    # they were `[n]` markers a model wrote — the exact substitution the
    # mechanism exists to remove.

    def supports_grounding(self) -> bool:
        """Whether `generate_grounded` will send real document blocks."""
        return False

    def generate_grounded(
        self,
        *,
        system: str,
        prompt: str,
        documents: list,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        effort: str | None = None,
    ) -> LLMResponse:
        """Like `generate`, with the passages sent as citable documents.

        Only call this when `supports_grounding()` is True. The default
        refuses rather than quietly inlining the documents into the prompt:
        a caller that asked for citations would get uncited prose back and
        no way to tell, which is worse than the error.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot send citable documents. "
            f"Check supports_grounding() before calling generate_grounded()."
        )

    def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        effort: str | None = None,
    ) -> LLMResponse:
        """Like `generate`, but the reply is constrained to `schema`.

        Only call this when `supports_schema()` is True; the default
        refuses rather than quietly returning unconstrained prose.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot constrain output to a schema. "
            f"Check supports_schema() before calling generate_json()."
        )


def capability_flags() -> list[str]:
    """Every `supports_*` question a caller can ask a provider, by name.

    Discovered from the class rather than listed, so that adding a flag
    above is enough for `llm-check` to report it and for the ledger tests
    to require the wrapper to forward it.
    """
    return sorted(
        name
        for name in dir(LLMProvider)
        if name.startswith("supports_") and callable(getattr(LLMProvider, name))
    )


def capabilities(provider) -> dict[str, bool]:
    """What this provider says it will honour, flag by flag.

    Read through `getattr`, as `llm/effort.py` does, because providers are
    duck-typed: one that never heard of a flag does not support it.
    """
    report = {}
    for name in capability_flags():
        ask = getattr(provider, name, None)
        try:
            report[name.removeprefix("supports_")] = bool(ask and ask())
        except AttributeError:
            # A cascade answers by asking its halves, and a half that never
            # heard of the flag is a half that cannot honour it.
            report[name.removeprefix("supports_")] = False
    return report


def get_provider(config: dict) -> LLMProvider:
    """The configured provider, recording every call it makes.

    The ledger wraps here rather than at each call site because there are
    fourteen call sites across nine modules and the fifteenth is the one
    that would be missed. `llm/ledger.py` says what is recorded and what
    deliberately is not; `llm.ledger.enabled: false` turns it off, which is
    a decision visible in a file rather than a gap nobody notices.
    """
    from .ledger import wrap

    return wrap(_build_provider(config), config)


def _build_provider(config: dict) -> LLMProvider:
    """Factory: build the configured provider from config['llm'].

    Adding a new provider later means: write a new class implementing
    LLMProvider, register it in this dict, and nothing else in the codebase
    changes.

    Separate from `get_provider` so that the cascade branch can recurse
    without wrapping each half in its own ledger — which would write three
    records for one call and make a cascade look like it cost triple.
    """
    llm = config.get("llm")
    if not isinstance(llm, dict) or not llm.get("provider"):
        from policyforge.config import MissingLlmError

        raise MissingLlmError(
            "config/config.yaml has no `llm:` block naming a provider and model, so there is "
            "no model to call. Run `policyforge init` for a starter config, or copy one from "
            "config/config.example.yaml."
        )
    provider_name = config["llm"]["provider"]

    if provider_name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=config["llm"]["model"],
            api_key_env=config["llm"]["api_key_env"],
        )

    if provider_name == "bedrock":
        from .bedrock_provider import BedrockProvider

        return BedrockProvider(
            model=config["llm"]["model"],
            region=config["llm"].get("region", "us-east-1"),
        )

    if provider_name == "vertex":
        from .vertex_provider import VertexProvider

        if "project_id" not in config["llm"]:
            raise ValueError(
                "llm.project_id is required for the vertex provider — set it to your "
                "GCP project ID in config.yaml."
            )
        return VertexProvider(
            model=config["llm"]["model"],
            project_id=config["llm"]["project_id"],
            region=config["llm"].get("region", "us-central1"),
        )

    if provider_name in ("openai-compat", "local"):
        from .openai_compat_provider import OpenAICompatProvider

        if "base_url" not in config["llm"]:
            raise ValueError(
                f"llm.base_url is required for the {provider_name} provider — set it to your "
                "endpoint root, e.g. http://localhost:11434/v1 for Ollama."
            )
        return OpenAICompatProvider(
            model=config["llm"]["model"],
            base_url=config["llm"]["base_url"],
            # Optional here, unlike the anthropic provider: a local server
            # normally authenticates nothing, so absence is not an error.
            api_key_env=config["llm"].get("api_key_env"),
            timeout=config["llm"].get("timeout", 600),
        )

    if provider_name == "gemini":
        from .gemini_provider import GeminiProvider

        return GeminiProvider(
            model=config["llm"]["model"],
            api_key_env=config["llm"].get("api_key_env", "GEMINI_API_KEY"),
            timeout=config["llm"].get("timeout", 600),
        )

    if provider_name == "litellm":
        from .litellm_provider import LiteLLMProvider

        return LiteLLMProvider(
            model=config["llm"]["model"],
            # Optional: LiteLLM resolves each provider's credentials from
            # its own environment conventions, so most model strings need
            # neither of these.
            api_base=config["llm"].get("api_base"),
            api_key_env=config["llm"].get("api_key_env"),
            timeout=config["llm"].get("timeout", 600),
            num_retries=config["llm"].get("num_retries", 3),
            min_interval_seconds=config["llm"].get("min_interval_seconds", 0.0),
        )

    if provider_name == "cascade":
        from .cascade_provider import CascadeProvider

        # Two nested provider blocks, each in the same shape as a top-level
        # `llm:`. Recursing rather than inventing a second syntax means a
        # cascade can pair anything this factory can already build, and a
        # model string that works standalone works here unchanged.
        for key in ("primary", "escalate_to"):
            if key not in config["llm"]:
                raise ValueError(
                    f"llm.{key} is required for the cascade provider — give it a provider "
                    "block of its own, e.g.\n"
                    "  primary:\n"
                    "    provider: litellm\n"
                    "    model: openrouter/deepseek/deepseek-v4-flash"
                )

        return CascadeProvider(
            primary=_build_provider({"llm": config["llm"]["primary"]}),
            escalate_to=_build_provider({"llm": config["llm"]["escalate_to"]}),
        )

    raise ValueError(
        f"Unknown llm.provider '{provider_name}'. Supported: anthropic, bedrock, "
        "vertex, gemini, openai-compat (alias: local), litellm, cascade."
    )
