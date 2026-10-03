"""Second-stage retrieval with a cross-encoder.

The behaviour worth protecting is the fallback. A reranker that is simply
not running leaves BM25's order untouched, which is a perfectly good
ordering and completely indistinguishable from a reranker that ran and
agreed — so anything that cannot tell those apart will happily report
retrieval it never performed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from policyforge.rerank import get_reranker, rerank_passages


@dataclass
class FakeChunk:
    text: str


@dataclass
class FakePassage:
    chunk: FakeChunk


def _passages(*texts):
    return [FakePassage(FakeChunk(t)) for t in texts]


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


def _reranker(session, **kwargs):
    from policyforge.rerank.llamacpp_provider import LlamaCppReranker

    return LlamaCppReranker(session=session, **kwargs)


def _results(*pairs):
    return FakeResponse({"results": [{"index": i, "relevance_score": s} for i, s in pairs]})


# --------------------------------------------------------------------------
# Talking to llama-server
# --------------------------------------------------------------------------


def test_it_sends_the_expected_request():
    session = FakeSession(_results((0, 1.0)))

    _reranker(session).score("how often?", ["Accounts are recertified quarterly."])

    sent = session.calls[0]
    assert sent["url"] == "http://127.0.0.1:8090/v1/rerank"
    assert sent["json"]["query"] == "how often?"
    assert sent["json"]["documents"] == ["Accounts are recertified quarterly."]


def test_results_come_back_best_first():
    """Sorted rather than trusted: the endpoint orders them today, and an
    ordering this depends on is cheap to guarantee and expensive to debug."""
    session = FakeSession(_results((0, -11.0), (1, 2.13), (2, -5.28)))

    scored = _reranker(session).score("q", ["a", "b", "c"])

    assert [s.index for s in scored] == [1, 2, 0]
    assert scored[0].score == pytest.approx(2.13)


def test_a_trailing_slash_does_not_double_up():
    session = FakeSession(_results((0, 1.0)))

    _reranker(session, base_url="http://127.0.0.1:8090/").score("q", ["a"])

    assert session.calls[0]["url"] == "http://127.0.0.1:8090/v1/rerank"


def test_a_503_says_the_model_is_still_loading():
    """/health returns OK before the model is ready, so this is the state
    anyone wiring it up hits first."""
    session = FakeSession(FakeResponse(status_code=503))

    with pytest.raises(RuntimeError, match="still loading"):
        _reranker(session).score("q", ["a"])


def test_an_unreachable_server_says_how_to_start_one():
    import requests

    session = FakeSession(raise_exc=requests.exceptions.ConnectionError("refused"))

    with pytest.raises(RuntimeError) as caught:
        _reranker(session).score("q", ["a"])

    assert "connection" in str(caught.value).lower()
    assert "--reranking" in str(caught.value)


def test_a_non_200_reports_the_server_response():
    session = FakeSession(FakeResponse(status_code=500, text="boom"))

    with pytest.raises(RuntimeError) as caught:
        _reranker(session).score("q", ["a"])

    assert "500" in str(caught.value) and "boom" in str(caught.value)


def test_check_scores_a_trivial_pair():
    assert _reranker(FakeSession(_results((0, 1.0)))).check() is True


# --------------------------------------------------------------------------
# Applying it to passages
# --------------------------------------------------------------------------


def test_passages_are_reordered_and_trimmed():
    passages = _passages("media sanitization", "recertified quarterly", "purpose of standard")
    reranker = _reranker(FakeSession(_results((0, -11.0), (1, 2.13), (2, -5.28))))

    result = rerank_passages(reranker, "how often?", passages, limit=2)

    assert [p.chunk.text for p in result.passages] == [
        "recertified quarterly",
        "purpose of standard",
    ]
    assert result.ran


def test_no_reranker_keeps_bm25_order_and_says_so():
    """The case that must never look like success."""
    passages = _passages("a", "b", "c")

    result = rerank_passages(None, "q", passages, limit=2)

    assert [p.chunk.text for p in result.passages] == ["a", "b"]
    assert not result.ran
    assert "no reranker configured" in result.fell_back


def test_a_dead_reranker_keeps_bm25_order_and_names_the_failure():
    """Degrading retrieval is acceptable; ending the session is not, and
    hiding the degradation is worse than either."""
    import requests

    passages = _passages("a", "b", "c")
    reranker = _reranker(FakeSession(raise_exc=requests.exceptions.ConnectionError("refused")))

    result = rerank_passages(reranker, "q", passages, limit=2)

    assert [p.chunk.text for p in result.passages] == ["a", "b"]
    assert not result.ran
    assert "connection" in result.fell_back.lower()


def test_a_short_scoring_falls_back_rather_than_serving_fewer_passages():
    """A reranker that returned an ordering of something other than what it
    was given would otherwise make retrieval look like it found less."""
    passages = _passages("a", "b", "c")
    reranker = _reranker(FakeSession(_results((0, 1.0), (1, 0.5))))

    result = rerank_passages(reranker, "q", passages, limit=3)

    assert len(result.passages) == 3
    assert not result.ran
    assert "2 of 3" in result.fell_back


def test_an_invented_index_is_not_followed():
    passages = _passages("a", "b")
    reranker = _reranker(FakeSession(_results((0, 1.0), (7, 0.9))))

    result = rerank_passages(reranker, "q", passages, limit=2)

    assert not result.ran
    assert [p.chunk.text for p in result.passages] == ["a", "b"]


def test_no_passages_is_not_a_failure():
    """Retrieval finding nothing is a real answer, not a broken reranker."""
    result = rerank_passages(_reranker(FakeSession(_results())), "q", [], limit=4)

    assert result.passages == []
    assert result.ran


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_reranking_is_off_unless_asked_for():
    """A project that has not asked for it should not be made to run a
    second server."""
    assert get_reranker({"llm": {"provider": "anthropic"}}) is None
    assert get_reranker({"rerank": {}}) is None


def test_a_configured_reranker_is_built():
    from policyforge.rerank.llamacpp_provider import LlamaCppReranker

    reranker = get_reranker({"rerank": {"provider": "llamacpp", "base_url": "http://x:1/"}})

    assert isinstance(reranker, LlamaCppReranker)
    assert reranker.base_url == "http://x:1"


def test_an_unknown_provider_names_what_is_supported():
    with pytest.raises(ValueError, match="llamacpp"):
        get_reranker({"rerank": {"provider": "nonsense"}})
