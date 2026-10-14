"""Calls any model LiteLLM can reach, which is most of them: Anthropic,
OpenAI, Gemini, Bedrock, Vertex, Azure, Groq, Together, a local Ollama, and
a long tail besides. One config string picks the provider *and* the model —
`anthropic/claude-opus-5`, `ollama_chat/qwen3:14b`, `gemini/gemini-2.0-flash` —
so comparing two models across two vendors is a one-line edit rather than a
second provider class.

This exists alongside OpenAICompatProvider rather than replacing it,
because they answer different questions. OpenAICompatProvider needs no
dependency beyond `requests` and talks to anything exposing
`/v1/chat/completions` — including a LiteLLM *proxy*, which means the proxy
route already worked before this file existed. This provider is the SDK
route: no gateway process to run, and per-call cost comes back from
LiteLLM's own pricing tables, which is what makes a quality comparison
across models also a cost comparison.

Auth is deliberately not an `api_key_env` by default. LiteLLM resolves
credentials per provider from its own conventions (ANTHROPIC_API_KEY,
GEMINI_API_KEY, AWS's chain, GCP ADC), so naming one variable here would be
wrong for every model string but one. `api_key_env` stays available for the
endpoints that need an explicit key, such as a proxy.
"""

from __future__ import annotations

import os
import time

from ..config import ProviderSetupError
from . import ceiling, escalation
from ._inline_thinking import answer_and_stripped, exhausted, needs_more_room, retry_budget
from .base import (
    COST_ESTIMATE,
    COST_PROVIDER,
    LLMProvider,
    LLMResponse,
    ProviderRejected,
    SchemaReplyError,
    combined_source,
    recordable_endpoint,
)

#: Where LiteLLM carries a cost the VENDOR reported, as opposed to one it
#: priced from its model map (#87). Its cost calculator reads this first
#: (`get_response_cost_from_hidden_params`); on a chat route, only
#: OpenRouter sets it, in LiteLLM 1.103.0 (b5 read every setter on #87).
_PROVIDER_COST = "llm_provider-x-litellm-response-cost"


class _NeverRaised(Exception):
    """Stands in for litellm's error type when litellm is not installed.

    Nothing raises it, so the `temperature` retry simply cannot fire — which
    is correct: without litellm there is no real call to be rejected, and an
    injected fake client raises whatever the test tells it to.
    """


def _bad_request_error() -> type[BaseException]:
    try:
        from .litellm_map import load

        return load().BadRequestError
    except ImportError:
        return _NeverRaised


class LiteLLMProvider(LLMProvider):
    """Calls a model through LiteLLM's unified completion interface.

    Requires the `litellm` package — install with
    `pip install "policyforge[litellm]"`. `model` uses LiteLLM's
    `<provider>/<model>` form; `api_base` overrides the endpoint for a
    local server or a proxy.
    """

    def __init__(
        self,
        *,
        model: str,
        api_base: str | None = None,
        api_key_env: str | None = None,
        timeout: int = 600,
        num_retries: int = 3,
        min_interval_seconds: float = 0.0,
        completion=None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.model = model
        self.api_base = api_base
        self.api_key_env = api_key_env
        #: Handed to LiteLLM, which retries 429s and 5xx with exponential
        #: backoff. Worth having because a refused request is not a result:
        #: `evals/runner.py` correctly grades a rate limit as "this says
        #: nothing about the prompt", so without retries a busy vendor
        #: silently shrinks the sample a comparison is drawn from. A new
        #: account's per-minute cap did exactly that to three models.
        self.num_retries = num_retries

        #: Shortest gap between requests, in seconds. Zero means no
        #: throttling, which is right for almost every endpoint.
        #:
        #: Retries alone are not always enough. A per-model cap can be low
        #: enough that backing off three times still lands inside the same
        #: window: `cohere/command-a` refused 16 of 23 cases that way while
        #: DeepSeek and GLM ran unthrottled all day. Probed empirically it
        #: serves 30 requests a minute without complaint, and the eval was
        #: firing nearer 150. Spacing requests is the difference between a
        #: measurement and a void run; two seconds a call costs about two
        #: minutes across a suite.
        self.min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        # Generous for the same reason as OpenAICompatProvider: a local
        # model on a consumer GPU can take minutes over a long synthesis
        # prompt, and a short timeout kills a run mid-document.
        self.timeout = timeout

        self._api_key = None
        if api_key_env:
            self._api_key = os.environ.get(api_key_env)
            if not self._api_key:
                raise ProviderSetupError(
                    f"Environment variable {api_key_env} is not set. Either export it, "
                    f"or drop llm.api_key_env — LiteLLM resolves most providers' "
                    f"credentials from their own environment variables without it."
                )

        #: Cleared the first time a model rejects `temperature`, so the rest
        #: of the run stops sending it. See `_create`.
        self._send_temperature = True

        #: The exception `_create` retries on, resolved once here rather
        #: than imported per call.
        #:
        #: Importing litellm inside `_create` made the *injected* client
        #: path require the extra too, which defeats the point of injecting
        #: one: every test that called `generate()` failed wherever litellm
        #: was absent, and CI installs only `.[dev]`. BedrockProvider and
        #: VertexProvider reach for their SDK only when building a real
        #: client, and this now matches them.
        self._bad_request = _bad_request_error()

        if completion is not None:
            # Dependency injection point for tests, matching the other
            # providers, so the request/response handling below is
            # exercisable without litellm installed or any model reachable.
            self._completion = completion
            return
        try:
            from .litellm_map import load

            litellm = load()
        except ImportError as exc:
            raise ProviderSetupError(
                "The 'litellm' package is required for the litellm provider. "
                'Install it with: pip install "policyforge[litellm]"'
            ) from exc
        # LiteLLM prints a vendor banner and update notices on first call,
        # which corrupts the eval harness's one-character-per-case progress
        # output and any piped command.
        litellm.suppress_debug_info = True
        self._completion = litellm.completion

    def _wait_turn(self) -> None:
        """Hold off until `min_interval_seconds` has passed since the last send.

        Measured from the moment the previous request *started*, so time the
        request itself spent counts towards the gap. Sleeping a full interval
        on top of a slow call would halve the throughput of exactly the
        endpoint that can least afford it.
        """
        if not self.min_interval_seconds:
            return
        if self._last_request_at is not None:
            overdue = self.min_interval_seconds - (self._clock() - self._last_request_at)
            if overdue > 0:
                self._sleep(overdue)
        self._last_request_at = self._clock()

    def _create(self, payload: dict, temperature: float):
        """One request, remembering whether this model tolerates temperature."""
        # Before the request, and inside `_create` rather than `generate`, so
        # that a truncation retry is spaced too — both halves go to the same
        # capped endpoint.
        self._wait_turn()

        try:
            if not self._send_temperature:
                return self._completion(**payload)
            try:
                return self._completion(temperature=temperature, **payload)
            except self._bad_request as exc:
                # Belt to `drop_params`' braces. LiteLLM refuses a parameter it
                # knows the model rejects before sending anything, and
                # `drop_params` in the payload handles that case. This catches
                # the other one: a model whose restriction LiteLLM does not know
                # about yet, where the rejection comes back from the API. New
                # models ship faster than metadata about them.
                #
                # Both spellings are matched because the two paths word it
                # differently — "is deprecated" from Anthropic's API,
                # "does not support temperature=0.2" from LiteLLM itself.
                message = str(exc)
                if "temperature" in message and (
                    "deprecated" in message or "does not support" in message
                ):
                    self._send_temperature = False
                    return self._completion(**payload)
                raise
        except self._bad_request as exc:
            # Every other 400 — a max_tokens above the model's cap being the
            # usual one — becomes the project's own rejection type, so the
            # caller can say which call and which budget and the CLI can
            # print that rather than a traceback.
            raise ProviderRejected(
                str(exc), status=getattr(exc, "status_code", None), model=self.model
            ) from exc

    @staticmethod
    def _cost_source(response, cost: float | None) -> str | None:
        """Whether `cost` is what the vendor reported or LiteLLM's estimate (#87).

        LiteLLM's `response_cost` is the vendor's figure when the vendor sent
        one, and its own map's estimate otherwise, with nothing on the cost
        itself to tell them apart. The hidden header is what tells them apart.
        """
        if cost is None:
            return None
        hidden = getattr(response, "_hidden_params", None) or {}
        reported = (hidden.get("additional_headers") or {}).get(_PROVIDER_COST)
        return COST_PROVIDER if reported is not None else COST_ESTIMATE

    @staticmethod
    def _read(response) -> tuple[str, str | None, float | None, int]:
        """Text, finish reason, and cost from a LiteLLM ModelResponse.

        `answer_of` still runs even though LiteLLM normalises reasoning into
        a separate `reasoning_content` field: normalisation depends on
        LiteLLM recognising the model, and an unrecognised one falls back to
        whatever the endpoint sent — which for a local reasoning model is an
        inline <think> block.
        """
        choice = response.choices[0]
        text, stripped = answer_and_stripped(getattr(choice.message, "content", ""))
        cost = (getattr(response, "_hidden_params", None) or {}).get("response_cost")
        return text, getattr(choice, "finish_reason", None), cost, stripped

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        effort: str | None = None,
        _response_format: dict | None = None,
    ) -> LLMResponse:
        # Private, and not on the LLMProvider interface: callers ask for a
        # schema through `generate_json`, which is the method that also
        # checks the reply. This only carries it to LiteLLM.
        #
        # Never more than the model's published ceiling (#3, 80's condition
        # 1): a larger request is refused as a 400, which reads nothing like
        # the truncation the budget was sized to prevent.
        max_tokens = ceiling.clamp(max_tokens, self.max_output_tokens())
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "timeout": self.timeout,
            # Let LiteLLM strip parameters the target model does not accept,
            # rather than hard-failing the call. Reaching many vendors means
            # meeting many restrictions — claude-sonnet-5 permits only
            # temperature=1, other models refuse top_p, and the list moves —
            # and catching each one by hand is a losing game.
            #
            # Worth knowing what this costs: on a model that refuses
            # temperature, this project's `temperature=0.0` is dropped and
            # the model runs at its own default. `answer_question` sets zero
            # so that the same question over the same documents cannot give
            # two accounts of what the organization requires, and on those
            # models that guarantee comes from the model, not from us. The
            # Anthropic provider has always behaved this way for the same
            # reason; this only makes it true across more vendors.
            "drop_params": True,
            "num_retries": self.num_retries,
        }
        if effort is not None:
            # LiteLLM's own spelling of the same lever, mapped per provider.
            # `drop_params` above removes it for a model with no such
            # control, which is why `supports_effort` is a belief rather
            # than a guarantee — see its docstring.
            payload["reasoning_effort"] = effort
        if _response_format is not None:
            payload["response_format"] = _response_format
        if self.api_base:
            payload["api_base"] = self.api_base
        if self._api_key:
            payload["api_key"] = self._api_key

        response = self._create(payload, temperature)
        text, finish_reason, cost, stripped = self._read(response)
        source = self._cost_source(response, cost)
        last_cost = cost

        if needs_more_room(text, finish_reason):
            # Eight times the first budget, clamped to the model's ceiling
            # (#3). If the clamp leaves no more room than the first attempt
            # had, re-sending would bill the same empty reply again, so it
            # is refused now, with the one attempt it cost.
            cap = self.max_output_tokens()
            second = ceiling.clamp(retry_budget(max_tokens), cap)
            if second <= max_tokens:
                usage = getattr(response, "usage", None)
                raise exhausted(
                    self.model,
                    max_tokens,
                    second,
                    cost_usd=cost,
                    last_cost_usd=cost,
                    request_id=getattr(response, "id", None),
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                    ceiling=cap,
                    cost_source=source,
                )
            first_usage = getattr(response, "usage", None)
            # Said before it is sent, and kept for this call's ledger row (#361).
            escalation.announce(
                model=self.model,
                first_max_tokens=max_tokens,
                max_tokens=second,
                input_tokens=getattr(first_usage, "prompt_tokens", None),
                first_request_id=getattr(response, "id", None),
                first_cost_usd=cost,
                first_cost_source=source,
                first_output_tokens=getattr(first_usage, "completion_tokens", None),
                first_stop_reason=finish_reason,
            )
            response = self._create({**payload, "max_tokens": second}, temperature)
            text, finish_reason, retry_cost, stripped = self._read(response)
            retry_source = self._cost_source(response, retry_cost)
            last_cost = retry_cost
            # Both calls are billed, so both are reported. Charging a
            # comparison only for the successful attempt would make a model
            # that needs the retry look cheaper than one that does not,
            # which inverts the thing being measured.
            #
            # Summed over what was actually *reported* rather than over what
            # is truthy: a local model prices both attempts at 0.0, and
            # `0.0 or None` is None — which would turn "this was free" into
            # "nobody knows", the one distinction cost_usd exists to keep.
            #
            # And None if either attempt's cost is unknown: the sum of the
            # known part alone would read as the whole bill (9b on #378, the
            # rule the cascade follows). The first attempt's cost is kept in
            # the row's `escalations`, and the re-send's own cost travels as
            # `last_cost_usd`, which the row keeps when its total is None
            # (#382). LiteLLM prices by model, so both attempts are normally
            # known or both unknown.
            known = cost is not None and retry_cost is not None
            cost = cost + retry_cost if known else None
            # The sum is a bill only if both attempts were (#87); with the
            # total unknown, the known part is the re-send's own cost.
            source = combined_source(source, retry_source) if known else retry_source
            if needs_more_room(text, finish_reason):
                # Empty again: both attempts were still billed, and this
                # exception is the only way that reaches the ledger (#343).
                usage = getattr(response, "usage", None)
                raise exhausted(
                    self.model,
                    max_tokens,
                    second,
                    cost_usd=cost,
                    last_cost_usd=retry_cost,
                    request_id=getattr(response, "id", None),
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                    cost_source=source,
                )

        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            model=getattr(response, "model", None) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            cost_usd=cost,
            cost_source=source,
            stripped_reasoning_chars=stripped,
            # LiteLLM's own spelling of the three facts the SDK providers
            # now carry. `finish_reason` is already read above to decide
            # whether to retry a truncation; it travels now instead of
            # being spent on that decision and discarded.
            stop_reason=finish_reason,
            cached_input_tokens=getattr(
                getattr(usage, "prompt_tokens_details", None), "cached_tokens", None
            ),
            request_id=getattr(response, "id", None),
            last_cost_usd=last_cost,
            # The configured `api_base`, which LiteLLM sends to as given, or
            # None (#328). Not LiteLLM's own `_hidden_params["api_base"]`:
            # with OPENAI_BASE_URL set and no api_base, LiteLLM 1.103.0 sent
            # the request to the override and reported https://api.openai.com
            # there (b5 measured it), so that field can name a host the text
            # never went to. With no api_base, where it went is the route's
            # own environment's to say, and the row says nothing.
            endpoint=recordable_endpoint(self.api_base),
        )

    def max_output_tokens(self) -> int | None:
        """The model's published output ceiling, or None if none is published.

        Deliberately NOT on `LLMProvider`: a method the base class defines is
        answered by the base class through the ledger's `RecordingProvider`,
        whatever the provider inside would say (the trap its `__getattr__`
        docstring records). Left off the base, the wrapper forwards it.
        """
        return ceiling.published_output_ceiling(self.model)

    def supports_effort(self) -> bool:
        """Whether an effort level will reach the model.

        True means "it will be sent", not "the model will obey": LiteLLM
        maps `reasoning_effort` per provider and `drop_params` removes it
        for a model with no such control. That is the same belief
        `supports_schema` reports, and it is the honest answer available —
        the alternative is a capability table that was already wrong about
        `temperature` on claude-sonnet-5.
        """
        return True

    def supports_schema(self) -> bool:
        """Whether LiteLLM believes this model can be held to a schema.

        A belief, not a guarantee — LiteLLM's metadata claimed
        claude-sonnet-5 accepted `temperature`, which it does not. So
        `generate_json` still verifies that what came back parses, rather
        than trusting the capability table that said it would.
        """
        try:
            from .litellm_map import load

            return bool(load().supports_response_schema(model=self.model))
        except Exception:  # noqa: BLE001 - an unmapped model is simply unknown
            return False

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
        """A reply constrained to `schema`, parsed and checked.

        `max_tokens` matters more here than anywhere else. A reasoning
        model spends the budget deliberating and emits its JSON last, so a
        tight ceiling returns truncated prose that is not JSON at all —
        measured three separate times while building this. The same
        truncation retry as `generate` applies, and the parse failure below
        says which of the two went wrong.
        """
        import json

        response = self.generate(
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
            _response_format=schema,
        )
        try:
            json.loads(response.text)
        except (TypeError, ValueError) as exc:
            raise SchemaReplyError(
                f"{self.model} was asked for JSON matching a schema and returned "
                f"something else: {response.text[:160]!r}",
                text=response.text,
                response=response,
            ) from exc
        return response

    def check(self) -> bool:
        """Cheap round-trip to confirm credentials + model actually work."""
        result = self.generate(
            system="Reply with exactly one word.",
            prompt="Reply with the word: ok",
            max_tokens=8,
            temperature=0,
        )
        return "ok" in result.text.lower()
