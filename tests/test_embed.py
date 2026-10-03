"""Dense retrieval and hybrid fusion.

The property under protection is the one `zardoz/retrieve.py` is built
around: "nothing in the synced documents appears to bear on that" has to
stay possible. Cosine similarity never returns zero for unrelated policy
text — two random compliance sentences sit around 0.4 — so without a floor,
every question retrieves something and the shell loses the ability to say
it does not know.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from policyforge.embed import MIN_SIMILARITY, DenseIndex, cosine, fuse, get_embedder


@dataclass
class FakeChunk:
    doc_id: str
    index: int
    text: str = "text"
    section: str = "section"


@dataclass
class FakePassage:
    chunk: FakeChunk
    matched_terms: list = field(default_factory=list)


def _p(doc_id, index, **kw):
    return FakePassage(FakeChunk(doc_id=doc_id, index=index, **kw))


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None, raise_exc=None):
        self.response = response
        self.raise_exc = raise_exc
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _embedder(session, **kw):
    from policyforge.embed.ollama_provider import OllamaEmbedder

    return OllamaEmbedder(session=session, **kw)


# --------------------------------------------------------------------------
# cosine
# --------------------------------------------------------------------------


def test_identical_vectors_are_one():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_orthogonal_vectors_are_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_a_zero_vector_is_not_a_division_by_zero():
    assert cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_mismatched_lengths_are_refused():
    """Silently comparing a prefix would report a similarity nobody computed."""
    with pytest.raises(ValueError, match="differ in length"):
        cosine([1.0, 2.0], [1.0])


def test_magnitude_is_not_assumed_away():
    """Whether a model returns unit vectors is a property of that model."""
    assert cosine([3.0, 0.0], [10.0, 0.0]) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Talking to Ollama
# --------------------------------------------------------------------------


def test_it_sends_every_text_in_one_call():
    session = FakeSession(FakeResponse({"embeddings": [[1.0], [2.0]]}))

    _embedder(session).embed(["a", "b"])

    sent = session.calls[0]
    assert sent["url"] == "http://localhost:11434/api/embed"
    assert sent["json"] == {"model": "bge-m3", "input": ["a", "b"]}


def test_no_texts_is_no_call():
    session = FakeSession(FakeResponse({"embeddings": []}))

    assert _embedder(session).embed([]) == []
    assert session.calls == []


def test_a_501_explains_that_the_model_cannot_embed():
    """Easy to misread as "Ollama does not do embeddings". It does — the
    named model just is not an embedding model."""
    session = FakeSession(FakeResponse(status_code=501))

    with pytest.raises(RuntimeError) as caught:
        _embedder(session, model="llama3.2").embed(["a"])

    assert "bge-m3" in str(caught.value)


def test_an_unreachable_ollama_says_connection():
    import requests

    session = FakeSession(raise_exc=requests.exceptions.ConnectionError("refused"))

    with pytest.raises(RuntimeError) as caught:
        _embedder(session).embed(["a"])

    assert "connection" in str(caught.value).lower()


def test_a_short_reply_is_refused_rather_than_misaligned():
    """One missing vector would pair every later chunk with the wrong
    passage, and each similarity after it would be confidently wrong about
    which text it described."""
    session = FakeSession(FakeResponse({"embeddings": [[1.0]]}))

    with pytest.raises(RuntimeError, match="1 vector"):
        _embedder(session).embed(["a", "b"])


def test_a_trailing_slash_does_not_double_up():
    session = FakeSession(FakeResponse({"embeddings": [[1.0]]}))

    _embedder(session, base_url="http://localhost:11434/").embed(["a"])

    assert session.calls[0]["url"] == "http://localhost:11434/api/embed"


# --------------------------------------------------------------------------
# The dense index
# --------------------------------------------------------------------------


class StubEmbedder:
    """Returns a canned query vector; chunk vectors are supplied directly."""

    def __init__(self, query_vector):
        self.query_vector = query_vector

    def embed(self, texts):
        return [self.query_vector for _ in texts]

    def check(self):
        return True


def _index(query_vector, chunk_vectors, passages):
    return DenseIndex(embedder=StubEmbedder(query_vector), vectors=chunk_vectors, passages=passages)


def test_matches_below_the_floor_are_not_candidates():
    """The measured case: unrelated policy text scores ~0.46 against a
    question it has nothing to do with. Without a floor that is a match."""
    index = _index([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0]], [_p("d", 0), _p("d", 1)])

    hits = index.search("q", min_similarity=0.5)

    assert [p.chunk.index for p, _ in hits] == [0]


def test_nothing_above_the_floor_is_an_empty_result():
    """Which is what lets the shell still say it does not know."""
    index = _index([1.0, 0.0], [[0.0, 1.0]], [_p("d", 0)])

    assert index.search("q", min_similarity=0.5) == []


def test_an_empty_index_is_not_an_error():
    assert DenseIndex(embedder=StubEmbedder([1.0])).search("q") == []


def test_the_default_floor_is_the_measured_one():
    assert MIN_SIMILARITY == 0.50


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------


def test_a_passage_both_retrievers_found_outranks_one_only_seen_once():
    """The whole point of fusing: agreement is evidence."""
    both, lexical_only, dense_only = _p("d", 0), _p("d", 1), _p("d", 2)

    fused = fuse([lexical_only, both], [(both, 0.9), (dense_only, 0.8)], limit=3)

    assert fused[0].chunk.index == 0


def test_fusion_keeps_the_lexical_copy_of_a_shared_passage():
    """It carries the matched terms, which is what the shell shows a reader
    to explain why a passage surfaced. The dense copy has no such story."""
    lexical = _p("d", 0)
    lexical.matched_terms = ["retention"]
    dense = _p("d", 0)

    fused = fuse([lexical], [(dense, 0.9)], limit=1)

    assert fused[0].matched_terms == ["retention"]


def test_a_passage_only_dense_retrieval_found_is_still_returned():
    """The recall failure this exists for: BM25 returned nothing at all for
    two questions the document plainly answered."""
    dense_only = _p("d", 7)

    fused = fuse([], [(dense_only, 0.6)], limit=3)

    assert [p.chunk.index for p in fused] == [7]


def test_fusion_of_nothing_is_nothing():
    assert fuse([], [], limit=5) == []


def test_fusion_respects_the_limit():
    lexical = [_p("d", i) for i in range(10)]

    assert len(fuse(lexical, [], limit=4)) == 4


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_dense_retrieval_is_off_unless_asked_for():
    assert get_embedder({"llm": {"provider": "anthropic"}}) is None
    assert get_embedder({"embed": {}}) is None


def test_a_configured_embedder_is_built():
    from policyforge.embed.ollama_provider import OllamaEmbedder

    embedder = get_embedder({"embed": {"provider": "ollama", "model": "bge-m3"}})

    assert isinstance(embedder, OllamaEmbedder)
    assert embedder.model == "bge-m3"


def test_an_unknown_provider_names_what_is_supported():
    with pytest.raises(ValueError, match="ollama"):
        get_embedder({"embed": {"provider": "nonsense"}})
