"""Calls a Gemini model through Google AI Studio's API, with an API key.

This is **not** the Vertex path. `vertex_provider.py` calls *Claude* models
hosted in Google Cloud's Model Garden, through Anthropic's own client,
authenticated by a GCP project and application-default credentials. It has
nothing to do with Gemini, and the two are easy to confuse because both say
"Google". This provider is the other thing: Google's own models, reached at
`generativelanguage.googleapis.com` with a key you create in AI Studio, no
cloud project and no `gcloud` anywhere.

Built on `requests`, which this project already depends on, rather than the
`google-genai` SDK — the same reasoning as `openai_compat_provider.py`. The
surface used here is one POST and a handful of reply fields, and a
dependency would add a hashed-lock entry and an install extra to document
for no gain.

**The key is a subprocessor question the operator has to answer, not one
this project can answer for them.** Google's published terms treat the free
tier of the Gemini API differently from the paid tier where content use is
concerned, and those terms are theirs to change. `docs/subprocessors.md`
says what this project can say — that content goes to Google when you
configure this provider — and names the rest as something you confirm. Do
not read the absence of a warning here as an assurance.

Capability flags are deliberately sparse, and the reason is `bedrock`: that
provider declares none at all, so a Claude model served through it silently
got no effort level, no cache hints and no schema. The lesson taken here is
not "declare more" but "declare what this file actually sends". Structured
output is implemented and sent; effort, caching, grounding and batch are
not implemented, and say so rather than being absent by accident.
"""

from __future__ import annotations

import os

from .base import LLMProvider, LLMResponse, ProviderRejected

#: Where AI Studio keys are honoured. Not a Vertex endpoint: Vertex lives on
#: `*-aiplatform.googleapis.com` behind cloud credentials.
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

#: The statuses meaning "the request, as written, was refused" — bad
#: request, unknown model or route, payload too large, unprocessable. The
#: same set `openai_compat_provider.py` uses, and deliberately not every
#: 4xx: a 429 clears by waiting and a 403 is the key, so budget advice on
#: either would send the operator to change the wrong thing.
REJECTED_AS_WRITTEN = frozenset({400, 404, 413, 422})

#: Gemini's `finishReason` for a reply cut off at the token cap. Named here
#: because `LLMResponse.truncated` matches on the vocabulary of whichever
#: provider answered, and this one spells it differently from the others.
MAX_TOKENS_REASON = "MAX_TOKENS"


class GeminiProvider(LLMProvider):
    """Calls a Gemini model via the AI Studio API key flow.

    `api_key_env` names the variable holding the key, never the key itself —
    the same rule every other provider here follows, and the reason
    `tests/test_credential_containment.py` can assert where credentials are
    read.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "GEMINI_API_KEY",
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 600,
        session=None,
    ):
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Environment variable {api_key_env} is not set. Create a key at "
                f"Google AI Studio and export it before running policyforge, e.g.:\n"
                f"  export {api_key_env}=..."
            )
        # The key travels in a header rather than in the query string, which
        # the API also accepts: a URL ends up in access logs, proxy logs and
        # error messages, and a header does not.
        self._headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

        if session is not None:
            # Injection point for tests, matching the other HTTP providers,
            # so request and response handling is exercisable with no key
            # and no network.
            self._session = session
            return
        import requests

        self._session = requests.Session()

    # -- transport ---------------------------------------------------------

    def _post(self, payload: dict) -> dict:
        import requests

        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        try:
            response = self._session.post(
                url, json=payload, headers=self._headers, timeout=self.timeout
            )
        except requests.exceptions.RequestException as exc:
            # "connection" is load-bearing: evals/runner.py grades a matching
            # message as infrastructure rather than as a failed case.
            raise RuntimeError(f"Connection to {url} failed: {exc}.") from exc

        if response.status_code in REJECTED_AS_WRITTEN:
            raise ProviderRejected(
                response.text[:400] or f"{url} returned HTTP {response.status_code}",
                status=response.status_code,
                model=self.model,
            )
        if response.status_code != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status_code}: {response.text[:400]}")
        return response.json()

    def _body(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        schema: dict | None = None,
    ) -> dict:
        generation: dict = {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            # A caller asking for `max_tokens=64` means sixty-four tokens of
            # answer. Gemini means sixty-four tokens of anything, thinking
            # included, and thinking is not returned — so on a live call a
            # 64-token budget spent 60 on reasoning and came back with no
            # content at all and `finishReason: MAX_TOKENS`. The truncation
            # guard fired correctly on a call that was never going to
            # produce anything, which reads as a guard defect and would be
            # "fixed" by removing the guard.
            #
            # Translating the caller's contract into the vendor's is what an
            # adapter is for: if effort.py had to learn that one vendor
            # thinks, every future caller and every future budget would have
            # to learn it too. Measured: with this set, the same 64-token
            # budget returned 60 tokens of prose and no thoughts count.
            #
            # The cost is that reasoning is off. That is consistent with
            # `supports_effort()` being False here: this provider does not
            # offer the caller a way to ask for thinking, so it does not
            # silently spend the caller's budget on it either. Wiring effort
            # levels to a non-zero thinking budget is a sensible next step
            # and is not this change.
            "thinkingConfig": {"thinkingBudget": 0},
        }
        if schema is not None:
            generation["responseMimeType"] = "application/json"
            generation["responseSchema"] = schema
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }
        if system:
            # A separate field rather than a first user turn: the API keeps
            # system instructions out of the conversation, and folding them
            # into the prompt would change what the model is shown.
            body["system_instruction"] = {"parts": [{"text": system}]}
        return body

    @staticmethod
    def _response(data: dict, *, model: str) -> LLMResponse:
        """One `generateContent` reply, in this project's terms.

        Every field is read defensively because a refusal comes back as a
        200 with no candidate text — a safety block, or a reply cut off
        before its first token — and the caller must be able to tell an
        empty reply from a missing field. `effort.document_text` raises
        `EmptyReply` on the empty string, which is the behaviour wanted
        here; returning None instead would look like a provider that does
        not report text at all.
        """
        candidates = data.get("candidates") or []
        first = candidates[0] if candidates else {}
        parts = ((first.get("content") or {}).get("parts")) or []
        text = "".join(part.get("text", "") for part in parts)

        usage = data.get("usageMetadata") or {}
        # `.get()` throughout, never `.get(..., 0)`. Both of these fields go
        # *missing* rather than arriving as zero: `candidatesTokenCount` is
        # absent on a reply cut off before its first content token, and
        # `thoughtsTokenCount` is absent whenever no thinking happened. Zero
        # would assert "none of these were billed", which is a different
        # claim from "the API did not say", and the ledger's whole design
        # rests on keeping those apart.
        return LLMResponse(
            text=text,
            model=data.get("modelVersion") or model,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            # `finishReason` is the field `llm/base.py` reads to decide a
            # reply was truncated. Passed through as the API spells it.
            hidden_output_tokens=usage.get("thoughtsTokenCount"),
            stop_reason=first.get("finishReason"),
            request_id=data.get("responseId"),
        )

    # -- the calls ---------------------------------------------------------

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        effort: str | None = None,
        cache: bool = False,
        cache_prefix: str | None = None,
    ) -> LLMResponse:
        """`effort`, `cache` and `cache_prefix` are accepted and ignored.

        Accepted because the signature is the interface's, ignored because
        this provider does not implement them and says so through
        `supports_effort()` and `supports_caching()`. A caller that honours
        those flags never passes them; one that does not, gets a plain call
        rather than a crash.
        """
        data = self._post(
            self._body(system=system, prompt=prompt, max_tokens=max_tokens, temperature=temperature)
        )
        return self._response(data, model=self.model)

    def supports_schema(self) -> bool:
        """`generationConfig.responseSchema` constrains the reply.

        Honest about what is proven: `tests/test_gemini_provider.py` pins
        that a schema call sends `responseMimeType` and `responseSchema` in
        the request, so this project's half of the contract is enforced.
        Whether the API honours it is Google's half and is documented
        rather than measured here — no live call is made by the test suite.
        """
        return True

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
        data = self._post(
            self._body(
                system=system,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                schema=schema,
            )
        )
        return self._response(data, model=self.model)

    # -- what this provider does not do ------------------------------------
    #
    # Each of these is a deliberate False rather than an inherited one. The
    # API may well support the capability; this file does not send it, and a
    # flag describes what the provider does, not what the vendor offers.

    def supports_effort(self) -> bool:
        """Not sent. Gemini's thinking budget is a numeric
        `generationConfig.thinkingConfig` field on some models only, which
        is a different shape from the effort levels this project passes, and
        mapping one to the other unmeasured would be guesswork."""
        return False

    def supports_caching(self) -> bool:
        """Not sent. Gemini's context caching is an explicit resource you
        create and then reference, not a marker on a prefix like Anthropic's
        `cache_control`, so the caching hints this interface passes have
        nowhere to go."""
        return False

    def supports_grounding(self) -> bool:
        """Not sent. In this project `grounding` means the API returns the
        document spans it quoted, which `llm/grounded.py` relies on being
        verbatim by construction. Gemini's search grounding answers a
        different question."""
        return False

    def check(self) -> bool:
        """Cheap round trip: one token, so a wrong key or model fails here
        rather than part-way through a document."""
        self.generate(system="", prompt="ping", max_tokens=1, temperature=0.0)
        return True
