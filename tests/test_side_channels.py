"""S-03 — the model channels that are not LLM providers.

The entailer built its own provider instead of going through `get_provider`,
and the embedder and reranker posted passage text to a configurable
`base_url` with no classification. All three were invisible to the ledger
and to the boundary — the two guarantees this project makes about where the
organization's text goes and what record it leaves.

What is tested here is the guarantee rather than the plumbing: text a
ceiling forbids never reaches the endpoint (the fake session records every
post, and the refusal cases assert it made none), every batch that does
leave writes exactly one record that says how much went and never what, and
none of it can be skipped by constructing a provider directly.

No test touches the network.
"""

from __future__ import annotations

import json

import pytest

from policyforge.llm import ledger
from policyforge.llm.boundary import (
    LICENSED,
    LOCAL,
    SELF_HOSTED,
    THIRD_PARTY,
    BoundaryViolation,
)
from policyforge.llm.channel import Channel


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Answers embed and rerank requests, sized to what was sent."""

    def __init__(self, status_code=200):
        self.status_code = status_code
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json})
        if "input" in json:
            payload = {"embeddings": [[1.0, 0.0] for _ in json["input"]]}
        else:
            payload = {
                "results": [
                    {"index": i, "relevance_score": -float(i)}
                    for i in range(len(json["documents"]))
                ]
            }
        return FakeResponse(self.status_code, payload)


def _config(tmp_path, **llm):
    return {"llm": {"ledger": {"path": str(tmp_path / "calls.jsonl")}, **llm}}


def _records(tmp_path):
    return ledger.load(tmp_path / "calls.jsonl")


def _embedder(
    tmp_path, *, base_url="http://localhost:11434", block=None, config=None, session=None
):
    from policyforge.embed.ollama_provider import OllamaEmbedder

    channel = Channel.from_block(
        "embed",
        {"base_url": base_url, **(block or {})},
        provider="ollama",
        model="bge-m3",
        default_url="http://localhost:11434",
        config=config if config is not None else _config(tmp_path),
    )
    return OllamaEmbedder(base_url=base_url, session=session or FakeSession(), channel=channel)


def _reranker(tmp_path, *, base_url="http://127.0.0.1:8090", config=None, session=None):
    from policyforge.rerank.llamacpp_provider import LlamaCppReranker

    channel = Channel.from_block(
        "rerank",
        {"base_url": base_url},
        provider="llamacpp",
        model="rerank",
        default_url="http://127.0.0.1:8090",
        config=config if config is not None else _config(tmp_path),
    )
    return LlamaCppReranker(base_url=base_url, session=session or FakeSession(), channel=channel)


# --------------------------------------------------------------------------
# Classifying the endpoint
# --------------------------------------------------------------------------


def test_the_default_endpoints_are_this_machine():
    from policyforge.embed import get_embedder
    from policyforge.rerank import get_reranker

    assert get_embedder({"embed": {"model": "bge-m3"}})._channel.classification.klass == LOCAL
    assert get_reranker({"rerank": {"provider": "llamacpp"}})._channel.classification.klass == LOCAL


def test_a_hosted_embed_endpoint_is_third_party():
    from policyforge.embed import get_embedder

    embedder = get_embedder({"embed": {"base_url": "https://embed.example.com"}})
    assert embedder._channel.classification.klass == THIRD_PARTY


def test_a_declared_classification_wins_over_the_host():
    """The operator who knows their network, the same as llm.classification."""
    from policyforge.embed import get_embedder

    embedder = get_embedder(
        {"embed": {"base_url": "https://embed.corp.example", "classification": "self-hosted"}}
    )
    assert embedder._channel.classification.klass == SELF_HOSTED
    assert embedder._channel.classification.declared


def test_a_classification_that_is_not_a_class_fails_at_startup():
    from policyforge.rerank import get_reranker

    with pytest.raises(ValueError, match=r"rerank.classification"):
        get_reranker({"rerank": {"classification": "trusted"}})


# --------------------------------------------------------------------------
# The boundary: what may not leave, does not
# --------------------------------------------------------------------------


def test_licensed_text_never_reaches_even_a_self_hosted_embedder(tmp_path):
    session = FakeSession()
    embedder = _embedder(tmp_path, base_url="http://10.0.0.5:11434", session=session)

    with ledger.about("hitrust-01.a", content_class=LICENSED), pytest.raises(BoundaryViolation):
        embedder.embed(["01.a Invented Control Title — requirement text"])

    assert session.posts == [], "the refusal must come before the request"
    assert _records(tmp_path) == [], "nothing was sent, so nothing is recorded"


def test_licensed_text_may_reach_an_embedder_on_this_machine(tmp_path):
    session = FakeSession()
    embedder = _embedder(tmp_path, session=session)

    with ledger.about("hitrust-01.a", content_class=LICENSED):
        embedder.embed(["01.a Invented Control Title"])

    assert len(session.posts) == 1


def test_organization_text_may_reach_a_hosted_endpoint_by_default(tmp_path):
    """The default ceiling for the organization's own documents is the same
    as for a model call. This channel was never meant to be stricter than
    `llm:` — only as visible."""
    embedder = _embedder(tmp_path, base_url="https://embed.example.com")
    embedder.embed(["Accounts are recertified quarterly."])

    (record,) = _records(tmp_path)
    assert record.provider_class == THIRD_PARTY


def test_a_tightened_ceiling_keeps_organization_text_off_a_hosted_endpoint(tmp_path):
    session = FakeSession()
    config = _config(tmp_path, boundary={"organization-internal": "self-hosted"})
    embedder = _embedder(
        tmp_path, base_url="https://embed.example.com", config=config, session=session
    )

    with pytest.raises(BoundaryViolation, match=r"embed.base_url"):
        embedder.embed(["Accounts are recertified quarterly."])
    assert session.posts == []


def test_licensed_text_never_reaches_a_hosted_reranker(tmp_path):
    session = FakeSession()
    reranker = _reranker(tmp_path, base_url="https://rerank.example.com", session=session)

    with ledger.about("govramp-ac-2", content_class=LICENSED), pytest.raises(BoundaryViolation):
        reranker.score("who approves access?", ["AC-2 (a) [at least annually]"])
    assert session.posts == []


def test_a_directly_built_embedder_is_guarded_too(tmp_path):
    """The factory is not the only way in, so it cannot be the only guard."""
    from policyforge.embed.ollama_provider import OllamaEmbedder

    embedder = OllamaEmbedder(base_url="https://embed.example.com")
    assert embedder._channel.classification.klass == THIRD_PARTY
    with ledger.about("hitrust", content_class=LICENSED), pytest.raises(BoundaryViolation):
        embedder.embed(["licensed text"])


def test_a_directly_built_reranker_is_guarded_too():
    from policyforge.rerank.llamacpp_provider import LlamaCppReranker

    reranker = LlamaCppReranker(base_url="https://rerank.example.com")
    assert reranker._channel.classification.klass == THIRD_PARTY


# --------------------------------------------------------------------------
# The ledger: one record per batch, counting and never quoting
# --------------------------------------------------------------------------


def test_each_batch_is_one_record_with_a_count_and_no_text(tmp_path):
    embedder = _embedder(tmp_path)
    embedder.embed(["SECRET-PASSAGE-ONE", "SECRET-PASSAGE-TWO", "SECRET-PASSAGE-THREE"])

    (record,) = _records(tmp_path)
    assert record.items == 3
    assert record.provider == "ollama"
    assert record.model == "bge-m3"
    assert record.site == "embed"
    assert len(record.prompt_sha) == 16
    assert "SECRET-PASSAGE" not in (tmp_path / "calls.jsonl").read_text(encoding="utf-8")


def test_a_rerank_batch_counts_the_question_as_well_as_the_passages(tmp_path):
    """The question is part of what was sent, and often the most sensitive part."""
    reranker = _reranker(tmp_path)
    reranker.score("which controls do we fail?", ["passage a", "passage b"])

    (record,) = _records(tmp_path)
    assert record.items == 3
    assert record.provider == "llamacpp"


def test_a_batch_that_failed_is_recorded_because_it_was_sent(tmp_path):
    embedder = _embedder(tmp_path, session=FakeSession(status_code=500))

    with pytest.raises(RuntimeError):
        embedder.embed(["text"])

    (record,) = _records(tmp_path)
    assert record.error == "RuntimeError"


def test_a_batch_takes_its_subject_from_the_scope_but_stays_out_of_provenance(tmp_path):
    """The scope's record list answers "which models wrote this document".
    An embedding model wrote nothing, and naming it there would be false."""
    embedder = _embedder(tmp_path)

    with ledger.about("access-control-standard", site="zardoz.sync") as scope:
        embedder.embed(["Accounts are recertified quarterly."])

    (record,) = _records(tmp_path)
    assert record.subject == "access-control-standard"
    assert record.site == "zardoz.sync"
    assert scope.records == []


def test_the_health_probe_is_not_recorded(tmp_path):
    _embedder(tmp_path).check()
    _reranker(tmp_path).check()

    assert _records(tmp_path) == []


def test_turning_the_ledger_off_turns_it_off_here_too(tmp_path):
    config = {"llm": {"ledger": {"enabled": False, "path": str(tmp_path / "calls.jsonl")}}}
    _embedder(tmp_path, config=config).embed(["text"])

    assert not (tmp_path / "calls.jsonl").exists()


def test_records_written_before_the_count_existed_still_load(tmp_path):
    path = tmp_path / "calls.jsonl"
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-01T00:00:00+00:00",
                "provider": "anthropic",
                "provider_class": "third-party",
                "model": "claude-sonnet-5",
                "subject": None,
                "site": "generate",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    (record,) = ledger.load(path)
    assert record.items is None


# --------------------------------------------------------------------------
# The entailer, through the same wrap as every other model call
# --------------------------------------------------------------------------


class FakeLiteLLM:
    """Stands in for LiteLLMProvider, answering one schema-constrained call."""

    def __init__(self, model, api_base=None, **kwargs):
        self.model = model
        self.api_base = api_base

    def supports_schema(self):
        return True

    def generate_json(self, *, system, prompt, schema, **kwargs):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(
            text=json.dumps({"label": "entailed", "reason": "it says so"}), model=self.model
        )

    def check(self):
        return True


@pytest.fixture
def fake_litellm(monkeypatch):
    monkeypatch.setattr("policyforge.llm.litellm_provider.LiteLLMProvider", FakeLiteLLM)


def test_the_entailer_is_recorded_like_every_other_model_call(tmp_path, fake_litellm):
    from policyforge.entail import get_entailer

    entailer = get_entailer({"entail": {"model": "judge-model"}, **_config(tmp_path)})
    entailer.entails("Records are kept six years.", "Records are kept six years.")

    (record,) = _records(tmp_path)
    assert record.provider == "litellm"
    assert record.model == "judge-model"
    # A bare model string resolves to a hosted vendor, and says so.
    assert record.provider_class == THIRD_PARTY


def test_an_entailer_on_a_local_endpoint_is_classified_as_one(tmp_path, fake_litellm):
    from policyforge.entail import get_entailer

    entailer = get_entailer(
        {
            "entail": {"model": "ollama/qwen3", "api_base": "http://localhost:11434"},
            **_config(tmp_path),
        }
    )
    entailer.entails("p", "c")

    (record,) = _records(tmp_path)
    assert record.provider_class == LOCAL


def test_a_directly_built_entailer_is_recorded_too(fake_litellm):
    from policyforge.entail.llm_entailer import LLMEntailer
    from policyforge.llm.ledger import RecordingProvider

    assert isinstance(LLMEntailer(model="judge-model")._provider, RecordingProvider)


def test_an_entailer_with_no_model_is_still_refused_with_the_reason():
    from policyforge.entail import get_entailer

    with pytest.raises(ValueError, match=r"entail.model is required"):
        get_entailer({"entail": {"provider": "llm"}})
