"""Calls a Claude model hosted on Google Cloud's Vertex AI Model Garden, via
Anthropic's own Vertex client. That client exposes the same request/
response shape as the direct Anthropic API — see `_anthropic_compat.py`,
shared with AnthropicProvider, including the same `temperature`-deprecation
retry.

Auth deliberately isn't an `api_key_env` like AnthropicProvider: Vertex AI
uses Google Cloud's Application Default Credentials (`gcloud auth
application-default login`, a service account key file, or a GCE/GKE-
attached identity) via `google-auth`, so there's no single key to name in
config.yaml. Configure credentials the normal `gcloud`/ADC way; this
provider only needs a GCP project ID, region, and model ID.
"""

from __future__ import annotations

from .base import LLMProvider, LLMResponse


class VertexProvider(LLMProvider):
    """Calls a Claude model on Google Cloud's Vertex AI Model Garden.

    Requires the `anthropic[vertex]` extra — install with
    `pip install "policyforge[vertex]"` — and Google Cloud credentials
    configured through Application Default Credentials, never a literal
    key in config.yaml.
    """

    def __init__(self, *, model: str, project_id: str, region: str = "us-central1", client=None):
        self.model = model
        self.project_id = project_id
        self.region = region
        if client is not None:
            # Dependency injection point for tests, so exercising the
            # request/response handling below doesn't require the
            # anthropic[vertex] extra (or live GCP credentials) to be
            # installed.
            self._client = client
            return
        try:
            from anthropic import AnthropicVertex
        except ImportError as exc:
            raise RuntimeError(
                "The 'anthropic[vertex]' extra is required for the vertex provider. "
                'Install it with: pip install "policyforge[vertex]"'
            ) from exc
        self._client = AnthropicVertex(project_id=project_id, region=region)

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

    def supports_caching(self) -> bool:
        """Same request shape as the direct API, so the same marker."""
        return True

    def supports_schema(self) -> bool:
        """Same request shape again, so the same constraint."""
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
        """Same request shape again, so the same document blocks."""
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

    def supports_effort(self) -> bool:
        """Same request shape as the direct API, so the same lever."""
        return True

    def check(self) -> bool:
        """Cheap round-trip to confirm credentials + model actually work."""
        result = self.generate(
            system="Reply with exactly one word.",
            prompt="Reply with the word: ok",
            max_tokens=8,
            temperature=0,
        )
        return "ok" in result.text.lower()
