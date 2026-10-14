"""Calls a model behind an OpenAI-compatible `/v1/chat/completions`
endpoint — a local runtime (Ollama, LM Studio, llama.cpp's server, vLLM)
or a hosted one (Vertex AI's Model-as-a-Service open models, and most
third-party inference vendors). They all speak the same request/response
shape, so one provider covers the lot; only `base_url` changes.

Deliberately built on `requests`, which this project already depends on,
rather than the `openai` SDK. The surface used here is one POST and three
fields of the reply, and an extra dependency (plus an extra install extra
to document) buys nothing for that.

Auth is optional, unlike every other provider here: a local server on
localhost normally has none, so `api_key_env` is only consulted if
config.yaml names it. That is the one place this differs from
AnthropicProvider, where a missing key is always an error.

Running locally is not only about token cost. This project keeps licensed
BYOC exports (HITRUST, GovRAMP) out of git precisely because redistributing
them is a licence question, and sending one to a third-party API processor
is the same question wearing a different hat — see the README's "What you
may redistribute" table and `generate-parser`'s warning. A model on
localhost removes that question rather than answering it.
"""

from __future__ import annotations

import os

from ..config import ProviderSetupError
from ._inline_thinking import answer_and_stripped, exhausted, needs_more_room, retry_budget
from .base import (
    LLMProvider,
    LLMResponse,
    ProviderHttpError,
    ProviderRejected,
    SchemaReplyError,
    recordable_endpoint,
)

#: The statuses that mean "the request, as written, was refused": bad
#: request, unknown model or route, payload too large, unprocessable.
#: Deliberately not every 4xx — see `_post`.
REJECTED_AS_WRITTEN = frozenset({400, 404, 413, 422})


def _separated_reasoning(message: dict) -> str:
    """Reasoning the server returned beside the answer rather than inside it.

    Ollama and vLLM put a reasoning model's chain of thought in
    `message.reasoning`, leaving `content` as the answer alone — so
    `answer_and_stripped` finds no inline `<think>` block, strips nothing,
    and would record a zero for reasoning it never looked at. `llm/base.py`
    is explicit that a zero asserted by a provider that never looked is a
    measurement nobody made, which is the distinction this exists to keep.

    `reasoning_content` is the spelling vLLM uses; both are read because a
    local endpoint is whichever of the two the user happens to run.
    """
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


class OpenAICompatProvider(LLMProvider):
    """Calls any OpenAI-compatible chat-completions endpoint.

    `base_url` is the server root (e.g. http://localhost:11434/v1); the
    provider appends /chat/completions. `api_key_env` is optional and only
    needed by endpoints that authenticate.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key_env: str | None = None,
        timeout: int = 600,
        session=None,
    ):
        self.model = model
        # Tolerate a trailing slash in config rather than producing a URL
        # with a doubled one, which some servers 404 on.
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        # Generous by default: a 14B on a consumer GPU can take minutes over
        # a long synthesis prompt, and the failure mode of a short timeout
        # is a run that dies most of the way through a document.
        self.timeout = timeout

        self._headers = {"Content-Type": "application/json"}
        if api_key_env:
            api_key = os.environ.get(api_key_env)
            if not api_key:
                raise ProviderSetupError(
                    f"Environment variable {api_key_env} is not set. "
                    f"Either export it, or drop llm.api_key_env from your config "
                    f"if this endpoint needs no key (a local server usually does not)."
                )
            self._headers["Authorization"] = f"Bearer {api_key}"

        if session is not None:
            # Dependency injection point for tests, matching BedrockProvider
            # and VertexProvider, so the request/response handling below is
            # exercisable without a server running.
            self._session = session
            return
        import requests

        from .boundary import is_loopback

        self._session = requests.Session()
        # A loopback model is reached directly, never through a proxy the
        # environment names: requests sent it through HTTP_PROXY, the text in
        # cleartext (#69, measured). A hosted endpoint keeps the environment,
        # so corporate egress still works. trust_env=False also stops
        # REQUESTS_CA_BUNDLE and .netrc applying to this loopback session.
        self._session.trust_env = not is_loopback(self.base_url)

    def _post(self, payload: dict) -> dict:
        import requests

        url = f"{self.base_url}/chat/completions"
        try:
            response = self._session.post(
                url, json=payload, headers=self._headers, timeout=self.timeout
            )
        except requests.exceptions.RequestException as exc:
            # The word "connection" is load-bearing: evals/runner.py grades a
            # matching message as infrastructure rather than as a failed
            # case, so a server that is simply not running does not get
            # reported as the prompt getting the answer wrong.
            raise RuntimeError(
                f"Connection to {url} failed: {exc}. Is the server running? "
                f"For Ollama, `ollama serve`; check with `ollama ps`."
            ) from exc

        if response.status_code in REJECTED_AS_WRITTEN:
            # The server understood the request and refused it as written — a
            # budget above the model's cap, an unknown model, an oversized
            # body. That is a rejection the user has to act on, and
            # `llm/effort.py` names the call and the budget on it.
            raise ProviderRejected(
                response.text[:400] or f"{url} returned HTTP {response.status_code}",
                status=response.status_code,
                model=self.model,
            )
        if response.status_code != 200:
            # Everything else is not about the request as written: a 429 or
            # 408 clears by waiting, a 401 or 403 is the key, a 5xx is the
            # server. Budget advice on any of those would send the user to
            # change the wrong thing, so they stay the plain error they were.
            raise ProviderHttpError(
                f"{url} returned HTTP {response.status_code}: {response.text[:400]}",
                status=response.status_code,
                url=url,
                reason=str(getattr(response, "reason", "") or ""),
            )
        return response.json()

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        _response_format: dict | None = None,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if _response_format is not None:
            # Private, and not on the LLMProvider interface: callers ask for
            # a schema through `generate_json`, which is also the method
            # that checks the reply actually parsed.
            payload["response_format"] = _response_format

        data = self._post(payload)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text, stripped = answer_and_stripped(message.get("content", ""))
        stripped += len(_separated_reasoning(message))

        # A reasoning model spends `max_tokens` thinking before it writes
        # anything, so a tight budget can return a stop-on-length reply whose
        # every token was preamble. Returning "" for that is the dangerous
        # outcome, because it is indistinguishable from the model choosing to
        # say nothing — and callers read that as a decision. The identical
        # bug on the Anthropic path was measured at roughly one call in
        # eight on a 12-token budget; see `_anthropic_compat` for the full
        # account. Retry with room to speak.
        if needs_more_room(text, choice.get("finish_reason")):
            from . import escalation

            second = retry_budget(max_tokens)
            first_usage = data.get("usage") or {}
            # Said before it is sent, and kept for this call's ledger row (#361).
            escalation.announce(
                model=self.model,
                first_max_tokens=max_tokens,
                max_tokens=second,
                input_tokens=first_usage.get("prompt_tokens"),
                first_request_id=data.get("id"),
                first_output_tokens=first_usage.get("completion_tokens"),
                first_stop_reason=choice.get("finish_reason"),
            )
            data = self._post({**payload, "max_tokens": second})
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text, stripped = answer_and_stripped(message.get("content", ""))
            stripped += len(_separated_reasoning(message))
            if needs_more_room(text, choice.get("finish_reason")):
                # Both attempts reached the endpoint; the last one's id and
                # tokens go to the ledger on the exception (#343). This API
                # reports no cost, so none is carried.
                usage = data.get("usage") or {}
                raise exhausted(
                    self.model,
                    max_tokens,
                    second,
                    request_id=data.get("id"),
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                )

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model") or self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            # `finish_reason` was read above only to retry an empty reply,
            # then dropped — so a non-empty reply Ollama or vLLM cut off at
            # `max_tokens` reached the caller with no stop reason, and
            # `LLMResponse.truncated` was False on exactly the local setup
            # the docs recommend for licensed content. It travels now, as it
            # does from every other provider.
            stop_reason=choice.get("finish_reason"),
            # What the server says it billed for thinking and did not put in
            # the answer. Reported by servers that separate reasoning; left
            # None by those that do not, so "not reported" stays apart from
            # "reported as none".
            hidden_output_tokens=(usage.get("completion_tokens_details") or {}).get(
                "reasoning_tokens"
            ),
            stripped_reasoning_chars=stripped,
            # The server's id, where it sends one: kept on the exhaustion
            # path since #343, and dropped here until #426, so every local
            # row we measured had none.
            request_id=data.get("id"),
            # The server root this provider posts to, from config (#328).
            endpoint=recordable_endpoint(self.base_url),
        )

    def supports_schema(self) -> bool:
        """Whether a schema-constrained reply will actually be constrained.

        True because a live call proved it, not because the endpoint says
        so: `qwen3:14b` through Ollama's OpenAI-compatible endpoint
        returned JSON matching a two-field schema on the first attempt.
        That is the same standard `litellm_provider` holds — its own
        docstring notes a capability table that was already wrong about
        `temperature` on claude-sonnet-5 — and it is why `generate_json`
        verifies the parse rather than trusting this flag.

        The honest scope: True says *this provider sends the constraint and
        checks the answer*, not that every server behind `base_url` honours
        it. An endpoint that ignores `response_format` answers with prose
        and a 200, and `generate_json` raises `SchemaReplyError` on it
        rather than returning unconstrained text as though it were
        constrained.
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
    ) -> LLMResponse:
        """A reply constrained to `schema`, parsed and checked.

        The reason this exists is containment rather than convenience: a
        schema-constrained call is what the crosswalk and edit paths need,
        and until the local provider had one, those paths could only run
        through a vendor. This is the only route on which a licensed
        catalog's text never leaves the machine.

        The constraint is asked for and the result is still verified.
        `llama.cpp` grammars and Ollama's structured outputs both hold a
        model to a shape, but an endpoint that ignores `response_format`
        answers with prose and a 200 — so the parse below is what turns a
        server's silence about a capability into an error the caller can
        act on.
        """
        import json

        response = self.generate(
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
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
        """Cheap round-trip to confirm the endpoint + model actually work."""
        result = self.generate(
            system="Reply with exactly one word.",
            prompt="Reply with the word: ok",
            max_tokens=8,
            temperature=0,
        )
        return "ok" in result.text.lower()
