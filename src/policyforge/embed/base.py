"""Embedding vectors, for retrieval that is not looking for the same words.

`zardoz/retrieve.py` scores a passage on the words it shares with the
question, and gates hard on specificity so that "nothing in the synced
documents appears to bear on that" stays an honest answer. That design is
right and is why the shell refuses cleanly instead of returning noise. It
has one blind spot, and it is the one this module exists for: a passage
whose words differ from the question's never enters the candidate set at
all.

That is a **recall** failure, and it is what `the-users-own-words-survive`
measures — a rewrite turned "wipe" and "laptop" into "wiping of devices",
BM25 went looking for words the document does not contain, and the right
passage was never retrieved. Nothing downstream can fix that. A reranker
reorders what survived retrieval and cannot rescue what did not, which is
why this comes first and `rerank/` comes second.

`zardoz/paraphrase.py` is the existing workaround: ask a model to guess the
document's vocabulary, then search for the guesses. It works, it is honest
about being a guess, and it costs a model call per miss. Embeddings close
the same gap at the source.

Deliberately optional, like reranking. No `embed:` block means the lexical
retriever this project already had, unchanged.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod


class Embedder(ABC):
    """Turns text into vectors. One method, like LLMProvider and Reranker."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input, in the order given."""
        raise NotImplementedError

    @abstractmethod
    def check(self) -> bool:
        """Cheap round-trip, for `policyforge llm-check`."""
        raise NotImplementedError


def cosine(a: list[float], b: list[float]) -> float:
    """Similarity of two vectors, 0.0 when either has no magnitude.

    Not normalised away into a dot product: whether a given embedding model
    returns unit vectors is a property of that model, and assuming it would
    make this silently wrong for the first one that does not.
    """
    if len(a) != len(b):
        raise ValueError(f"vectors differ in length: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    magnitude = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / magnitude if magnitude else 0.0


def get_embedder(config: dict) -> Embedder | None:
    """Build the configured embedder, or None when there isn't one.

    None is the normal case and not an error: a project that has not asked
    for dense retrieval keeps the lexical retriever it already had.

    The endpoint is classified here, before anything is built, so a
    `classification` that is not a provider class fails at startup rather
    than on the first batch — and every batch after that is checked against
    the boundary and recorded in the ledger, like every model call.
    """
    block = config.get("embed")
    if not block:
        return None

    provider = block.get("provider", "ollama")
    if provider == "ollama":
        from ..llm.channel import Channel
        from .ollama_provider import DEFAULT_URL, OllamaEmbedder

        model = block.get("model", "bge-m3")
        return OllamaEmbedder(
            model=model,
            base_url=block.get("base_url", DEFAULT_URL),
            timeout=block.get("timeout", 300),
            channel=Channel.from_block(
                "embed",
                block,
                provider="ollama",
                model=model,
                default_url=DEFAULT_URL,
                config=config,
            ),
        )

    raise ValueError(f"Unknown embed.provider '{provider}'. Supported: ollama.")
