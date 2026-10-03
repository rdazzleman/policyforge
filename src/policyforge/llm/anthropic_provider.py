from __future__ import annotations

import os

from .base import LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    """Calls Claude directly via the Anthropic API.

    Expects an API key in the environment variable named by `api_key_env`
    (configured in config.yaml, default ANTHROPIC_API_KEY) — never read a
    literal key out of a config file.
    """

    def __init__(self, *, model: str, api_key_env: str = "ANTHROPIC_API_KEY"):
        self.model = model
        self.api_key_env = api_key_env
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Environment variable {api_key_env} is not set. "
                f"Export your Anthropic API key before running policyforge, e.g.:\n"
                f"  export {api_key_env}=sk-ant-..."
            )
        # Imported lazily so `policyforge` doesn't hard-fail at import time
        # for commands that never touch the LLM (e.g. pure ETL/ingest).
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

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
        from ._anthropic_compat import call_messages_api

        return call_messages_api(
            self._client,
            model=self.model,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
            cache=cache,
            cache_prefix=cache_prefix,
        )

    def supports_schema(self) -> bool:
        """The Messages API constrains a reply with `output_config.format`.

        Until this returned True, an Anthropic user — the project's own
        default provider — got no schema guarantee anywhere, while the
        routing prompt asked for one and quietly fell back to parsing prose.
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
        from ._anthropic_compat import call_messages_api

        return call_messages_api(
            self._client,
            model=self.model,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
            schema=schema,
        )

    def supports_grounding(self) -> bool:
        """The Messages API cites document blocks by character range."""
        return True

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
        from ._anthropic_compat import call_messages_api

        return call_messages_api(
            self._client,
            model=self.model,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            effort=effort,
            documents=documents,
        )

    def supports_batch(self) -> bool:
        """The Messages API has a batch endpoint, at half the price."""
        return True

    def generate_batch(self, requests, **kwargs) -> dict:
        from ._anthropic_compat import submit_batch

        return submit_batch(self._client, requests, model=self.model, **kwargs)

    def supports_caching(self) -> bool:
        """The Messages API caches a marked prefix on every current model.

        Whether a given prefix is *long enough* to cache is decided by the
        model, silently: below its minimum the marker is ignored and the
        call is billed in full. `cached_input_tokens` on the response is how
        a caller finds out, which is why C-03 put it there.
        """
        return True

    def supports_effort(self) -> bool:
        """Current Claude models take `output_config.effort`.

        A model old enough to reject it refuses the request rather than
        ignoring the field, which is the honest failure: the call site finds
        out, instead of paying for deliberation it asked not to have.
        """
        return True

    def check(self) -> bool:
        """Cheap round-trip to confirm the key + model actually work."""
        result = self.generate(
            system="Reply with exactly one word.",
            prompt="Reply with the word: ok",
            max_tokens=8,
            temperature=0,
        )
        return "ok" in result.text.lower()
