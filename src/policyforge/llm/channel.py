"""The model channels that are not LLM providers, held to the same boundary.

`get_provider` wraps every language-model call in the ledger, and every
command that reads a file classifies it before a model sees it. Two other
channels send the organization's text to a model and did neither: the
embedder posts passages to `embed.base_url`, and the reranker posts a query
and passages to `rerank.base_url`. Both default to this machine and both are
off unless configured — and both would carry the policy corpus to a hosted
endpoint the moment someone pointed `base_url` at one, unclassified and
unrecorded. That is the one thing the ledger's guarantee says cannot happen.

A `Channel` is that guarantee for a URL-addressed service. It classifies the
endpoint the way `classify_provider` classifies an `llm:` block, admits a
batch only when the content in scope may reach it, and writes one ledger
record per batch: the count and a hash of the texts, never the texts.

What it does not do is add those records to the document's provenance. The
scope a document is drafted in lists the models that *wrote* it, and an
embedding model wrote nothing; counting it there would name a model in the
one answer that has to be exact.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .boundary import (
    BoundaryViolation,
    ContentClassification,
    ProviderClassification,
    check,
    classify_endpoint,
    refusal_advice,
)
from .ledger import (
    CallRecord,
    append,
    current_scope,
    ledger_enabled,
    ledger_path,
    scope_content,
)


def texts_digest(texts: list[str]) -> str:
    """A short fingerprint of one batch, in order.

    The same length as a prompt digest and for the same reason: enough to
    tell two batches apart, not enough to recover either.
    """
    payload = "\x00".join(texts).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


@dataclass(frozen=True)
class Channel:
    """One configured embed or rerank endpoint, classified."""

    #: The config key it was built from: "embed" or "rerank".
    name: str
    #: The service at the endpoint: "ollama", "llamacpp".
    provider: str
    model: str
    classification: ProviderClassification
    #: The whole config, for the ceilings and the ledger settings.
    config: dict = field(default_factory=dict, hash=False, compare=False)

    @classmethod
    def from_block(
        cls,
        name: str,
        block: dict,
        *,
        provider: str,
        model: str,
        default_url: str,
        config: dict | None = None,
    ) -> Channel:
        """Classify at construction, so a bad `classification` fails at startup."""
        return cls(
            name=name,
            provider=provider,
            model=model,
            classification=classify_endpoint(block, default_url=default_url, key=name),
            config=config or {},
        )

    def _content(self) -> ContentClassification:
        """What is being sent.

        The class the ledger scope names when a caller knew it; otherwise
        the organization's own material, which is what a policy corpus is
        and what `classify_path` calls anything that is not a declared
        catalog. A caller holding licensed-derived text says so with
        `ledger.about(..., content_class="licensed")`. The same reading as the
        model wrapper's (`ledger.scope_content`, #327).
        """
        return scope_content()

    def admit(self) -> None:
        """Refuse before any byte leaves, if the content may not go here."""
        decision = check(self._content(), self.classification, self.config)
        if not decision.allowed:
            raise BoundaryViolation(
                f"Refusing to send text to the {self.name} endpoint.\n"
                f"{decision.explain()}\n"
                + refusal_advice(
                    decision,
                    self.config,
                    channel=self.name,
                    otherwise=(
                        f"  Point `{self.name}.base_url` at a local server, or — if this endpoint "
                        "really is inside your boundary — declare that with "
                        f"`{self.name}.classification` in config.yaml."
                    ),
                )
            )

    def record(self, texts: list[str], *, error: str | None = None) -> None:
        """One ledger entry for one batch that reached the endpoint.

        A batch that raised is recorded too: it was sent, and the content in
        it was exposed whether or not a reply came back.
        """
        if not ledger_enabled(self.config):
            return
        scope = current_scope()
        append(
            CallRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                provider=self.provider,
                provider_class=self.classification.klass,
                model=self.model,
                subject=scope.subject if scope else None,
                site=scope.site if scope and scope.site else self.name,
                content_class=self._content().klass,
                prompt_sha=texts_digest(texts),
                error=error,
                items=len(texts),
            ),
            ledger_path(self.config),
        )
