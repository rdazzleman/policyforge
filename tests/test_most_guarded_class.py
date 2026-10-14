"""A document's class is the most guarded of everything it states (#339).

9b found it reading PR 338: a document can state its class twice, as a
top-level `content_class` and in its `generate` stamp
(`generated_by.content_class`). The Zardoz corpus took the top-level value
alone whenever there was one, so a hand-added `content_class: public-domain`
lowered a licensed stamp, while the wiki exporter refused if either said
licensed. 80's ruling: the most guarded class wins, everywhere, through the
same reading as 320 and 336.
"""

from __future__ import annotations

import pytest

from policyforge.content.provenance import declared_content_class
from tests.test_one_boundary_decision import LICENSED, Fake, _ask, _hosted, _write


def _both(top: str, stamp: str) -> dict:
    return {"content_class": top, "generated_by": {"content_class": stamp}}


@pytest.mark.parametrize(
    ("top", "stamp"),
    [
        ("public-domain", "licensed"),
        ("licensed", "public-domain"),
        ("organization-internal", "licensed"),
        ("public-domain", "organization-internal"),
    ],
)
def test_the_more_guarded_of_two_classes_wins_in_either_order(top, stamp):
    expected = "licensed" if "licensed" in (top, stamp) else "organization-internal"
    assert declared_content_class(_both(top, stamp)) == expected


@pytest.mark.parametrize("klass", ["public-domain", "organization-internal", "licensed"])
def test_two_agreeing_classes_read_as_themselves(klass):
    assert declared_content_class(_both(klass, klass)) == klass


def test_a_missing_top_level_class_falls_back_to_the_stamp():
    assert declared_content_class({"generated_by": {"content_class": "licensed"}}) == "licensed"
    assert declared_content_class({"content_class": "licensed"}) == "licensed"
    assert declared_content_class({}) == ""


@pytest.mark.parametrize(
    ("top", "stamp"), [("public-domain", "licensed"), ("organization-internal", "licensed")]
)
def test_a_lower_top_level_label_never_sends_a_licensed_stamp_to_a_hosted_model(
    tmp_path, monkeypatch, top, stamp
):
    """9b's shapes, through the real corpus and MCP answer path, with the
    wiki exporter read on the same file: both say licensed."""
    import policyforge.llm.base as base
    from policyforge.content.tree import load_content_tree
    from policyforge.export.github_wiki import _licensed
    from policyforge.mcp.server import build_state
    from policyforge.zardoz.corpus import load_corpus, sync_corpus
    from tests.test_edit import _registry

    content = tmp_path / "docs"
    _write(
        content / "standards" / "access-review-standard.md",
        "---\ntitle: Access Review Standard\ntier: standard\nowner: IAM\n"
        f"content_class: {top}\ngenerated_by:\n  content_class: {stamp}\n---\n\n"
        f"## 1. Reviews\n\n{LICENSED}\n",
    )
    sync_corpus([], content_dir=content, corpus_dir=tmp_path / "corpus")
    fake = Fake()
    monkeypatch.setattr(base, "_build_provider", lambda config: fake)
    state = build_state(
        _hosted(tmp_path),
        corpus_dir=tmp_path / "corpus",
        topics_path=_registry(tmp_path),
        provider_factory=base.get_provider,
    )
    (doc,) = load_content_tree(content)[0]

    out = _ask(state, "How often are accounts reviewed by the system owner?")

    assert [d.content_class for d in load_corpus(tmp_path / "corpus").documents] == ["licensed"]
    assert _licensed(doc) is True, "the wiki exporter agrees"
    assert "Not sent to the model" in out
    assert not fake.saw(LICENSED)
