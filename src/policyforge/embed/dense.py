"""A dense index over the same chunks the lexical one reads, and the fusion
of the two.

The fusion is Reciprocal Rank Fusion: each retriever contributes `1 / (k +
rank)` for every passage it ranked, and the sums decide the final order.
Chosen over a weighted sum of scores because BM25 scores and cosine
similarities are not on the same scale and never will be — one is unbounded
and corpus-relative, the other is bounded and absolute — so any weighting
between them is a constant somebody tuned on one corpus. Ranks are
comparable by construction.

**The thing this must not break.** `zardoz/retrieve.py` gates hard on
specificity so that "nothing in the synced documents appears to bear on
that" stays a real answer rather than a pile of weak matches. Cosine
similarity has no such gate: every passage is *somewhat* similar to every
question, and a naive fusion would mean every question retrieves something
and the shell stops being able to say it does not know. So dense results
are floored — below `min_similarity` a passage is not a candidate at all —
and when neither retriever has anything above its bar, the result is empty,
exactly as it was before.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import Embedder, cosine

#: Rank-fusion constant. 60 is the value from the paper RRF comes from and
#: the one every implementation uses; it damps the difference between the
#: first few ranks so a single retriever cannot dominate on confidence
#: alone.
RRF_K = 60

#: Floor for a dense match, as cosine similarity.
#:
#: Embedding models do not return ~0 for unrelated text — two random policy
#: sentences sit far above zero — so this is not "any positive similarity".
#: It is the bar below which a passage is noise, and it is what keeps an
#: honest refusal possible.
#:
#: Measured with bge-m3 against a real generated Standard, rather than
#: guessed. Questions the document answers in words it does not use scored
#: 0.646, 0.557 and 0.521; questions it does not answer at all scored 0.458
#: and 0.410. An earlier guess of 0.55 would have discarded the 0.521 —
#: a passage BM25 missed entirely, which is the exact case dense retrieval
#: is here to catch.
#:
#: The margin between noise at 0.458 and signal at 0.521 is thin, and it is
#: a property of this model and this corpus. Re-measure before trusting it
#: on another: a floor set too high silently reinstates the recall failure,
#: and one set too low costs the honest refusal.
MIN_SIMILARITY = 0.50


@dataclass
class DenseIndex:
    """Chunk vectors, and the passages they came from.

    Built once per corpus. Embedding is the expensive part and it happens
    here rather than per query, which is also why `sync` is the natural
    place to build one.
    """

    embedder: Embedder
    vectors: list[list[float]] = field(default_factory=list)
    passages: list = field(default_factory=list)

    def search(self, query: str, *, limit: int = 20, min_similarity: float = MIN_SIMILARITY):
        """(passage, similarity) pairs above the floor, best first."""
        if not self.vectors:
            return []
        query_vector = self.embedder.embed([query])[0]
        scored = [
            (passage, cosine(query_vector, vector))
            for passage, vector in zip(self.passages, self.vectors, strict=True)
        ]
        above = [pair for pair in scored if pair[1] >= min_similarity]
        above.sort(key=lambda pair: pair[1], reverse=True)
        return above[:limit]


def build_dense_index(embedder: Embedder, corpus) -> DenseIndex:
    """Embed every chunk of every document in `corpus`.

    Chunked identically to the lexical index — same `chunk_document`, so a
    passage means the same thing to both retrievers and a citation points
    at the same span whichever one surfaced it.
    """
    from ..zardoz.retrieve import Passage, chunk_document

    passages = [
        Passage(chunk=chunk, document=document, score=0.0)
        for document in corpus.documents
        for chunk in chunk_document(document)
    ]
    if not passages:
        return DenseIndex(embedder=embedder)

    # The section heading is embedded with the text for the reason the
    # lexical index weights it: "4.1 Account Review" is often what tells a
    # reader — and a model — what the paragraph underneath is about.
    vectors = embedder.embed([f"{p.chunk.section}\n\n{p.chunk.text}" for p in passages])
    return DenseIndex(embedder=embedder, vectors=vectors, passages=passages)


def _key(passage) -> tuple[str, int]:
    """Identifies a passage across the two retrievers."""
    return (passage.chunk.doc_id, passage.chunk.index)


def fuse(lexical: list, dense: list, *, limit: int = 5, k: int = RRF_K) -> list:
    """Reciprocal Rank Fusion of lexical passages and dense (passage, score) pairs.

    Returns the lexical `Passage` object wherever both retrievers found the
    same chunk, so the match explanation a reader is shown — which terms
    hit, and which were guessed — survives fusion. A passage only dense
    retrieval found has no such explanation to offer, and saying nothing is
    better than implying it matched words it did not.
    """
    contributions: dict[tuple[str, int], float] = {}
    best: dict[tuple[str, int], object] = {}

    for rank, passage in enumerate(lexical):
        key = _key(passage)
        contributions[key] = contributions.get(key, 0.0) + 1.0 / (k + rank + 1)
        best[key] = passage

    for rank, (passage, _similarity) in enumerate(dense):
        key = _key(passage)
        contributions[key] = contributions.get(key, 0.0) + 1.0 / (k + rank + 1)
        # Only if the lexical retriever did not already supply this chunk:
        # its copy carries the matched terms.
        best.setdefault(key, passage)

    ordered = sorted(contributions.items(), key=lambda item: item[1], reverse=True)
    return [best[key] for key, _ in ordered[:limit]]
