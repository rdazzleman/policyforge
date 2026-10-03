"""Reordering retrieved passages by how well they answer the question.

BM25 scores a passage on the words it shares with the query. That is fast,
explainable, and blind to the thing that matters most here: a Standard
writes "privileged access" where a user types "who has admin", and lexical
overlap cannot see that those are the same idea. `zardoz/paraphrase.py`
exists entirely to paper over that gap by guessing the document's
vocabulary — a workaround with its own failure mode, since a guessed term
retrieves whatever happens to contain it.

A cross-encoder reads the query and the passage *together* and scores the
pair, so it judges relevance rather than word overlap. Used as a second
stage — retrieve widely with BM25, rerank, keep a few — it is the standard
fix for exactly this.

Deliberately optional. No `rerank:` block in config means no reranking and
the behaviour this project already had, because a second model is a second
thing to run and a second thing to be down.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scored:
    """One document's place in the reranked order.

    `score` is a logit, not a probability: unbounded, and negative for most
    passages even when the ranking is confident. Measured on a real query,
    the right passage scored +2.1 while the rest sat between -5 and -11. Use
    it to order, and derive any cutoff from your own cases rather than
    assuming zero means anything.
    """

    index: int
    score: float


@dataclass
class Reranked:
    """Reordered passages, and whether the reranker actually ran.

    The flag is not decoration. A reranker that is simply down leaves the
    BM25 order in place, which is a perfectly good answer and completely
    indistinguishable from a reranker that ran and agreed — so a caller
    that cannot tell the two apart will report improved retrieval it never
    performed. `Answer.warnings` makes the same argument about repairs a
    caller hides.
    """

    passages: list = field(default_factory=list)
    #: None when the reranker ran. A sentence naming what went wrong
    #: otherwise, with the original order preserved.
    fell_back: str | None = None

    @property
    def ran(self) -> bool:
        return self.fell_back is None


class Reranker(ABC):
    """Scores (query, document) pairs. One method, like LLMProvider."""

    @abstractmethod
    def score(self, query: str, documents: list[str]) -> list[Scored]:
        """Every document scored against `query`, best first."""
        raise NotImplementedError

    @abstractmethod
    def check(self) -> bool:
        """Cheap round-trip, for `policyforge llm-check`."""
        raise NotImplementedError


def rerank_passages(reranker: Reranker | None, query: str, passages: list, *, limit: int):
    """The `limit` passages a cross-encoder judges most relevant.

    Falls back to the order it was given — BM25's — rather than raising,
    because a missing reranker should degrade retrieval quality and not end
    a session. What it will not do is hide that: the caller gets a reason,
    and `Reranked.ran` answers the question that matters.
    """
    if reranker is None:
        return Reranked(passages=passages[:limit], fell_back="no reranker configured")
    if not passages:
        return Reranked(passages=[], fell_back=None)

    try:
        scored = reranker.score(query, [p.chunk.text for p in passages])
    except Exception as exc:  # noqa: BLE001 - any failure means keep BM25's order
        return Reranked(
            passages=passages[:limit],
            fell_back=f"{type(exc).__name__}: {exc}",
        )

    ordered = [passages[s.index] for s in scored if 0 <= s.index < len(passages)]
    # A reranker that dropped or invented an index has not returned an
    # ordering of what it was given, and quietly serving a short list would
    # look like retrieval finding less than it did.
    if len(ordered) != len(passages):
        return Reranked(
            passages=passages[:limit],
            fell_back=f"scored {len(ordered)} of {len(passages)} passages",
        )
    return Reranked(passages=ordered[:limit], fell_back=None)


def get_reranker(config: dict) -> Reranker | None:
    """Build the configured reranker, or None when there isn't one.

    None is the normal case and not an error: reranking is an addition, and
    a project that has not asked for it should not be made to run a server.

    Classified here, like the embedder: a reranker is sent the question and
    every candidate passage, so it is held to the same boundary and recorded
    in the same ledger as a model call.
    """
    block = config.get("rerank")
    if not block:
        return None

    provider = block.get("provider", "llamacpp")
    if provider == "llamacpp":
        from ..llm.channel import Channel
        from .llamacpp_provider import DEFAULT_URL, LlamaCppReranker

        return LlamaCppReranker(
            base_url=block.get("base_url", DEFAULT_URL),
            timeout=block.get("timeout", 60),
            channel=Channel.from_block(
                "rerank",
                block,
                provider="llamacpp",
                model=block.get("model", "rerank"),
                default_url=DEFAULT_URL,
                config=config,
            ),
        )

    raise ValueError(f"Unknown rerank.provider '{provider}'. Supported: llamacpp.")
