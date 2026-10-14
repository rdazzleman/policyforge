"""A stamped content_class that can't be read counts as licensed (#336).

b5 found it reading PR 335: a document's stamped `content_class` was kept
only if it was a known class, so `licenced` (a typo) or `bogus` read as no
class at all, which every reader treats as the organization's own. With
PR 335 that reading decides model calls, so a misspelt licensed stamp
failed open. 80's ruling: the stamp-field twin of 320, fail closed. A value
that is present and not a known class counts as licensed, with one warning
per document; an absent field is unchanged; known classes read as
themselves. One reading, `provenance.stamped_class`, for every reader.
"""

from __future__ import annotations

import pytest

from policyforge.content.provenance import carried_content_class, stamped_class, unknown_stamp
from tests.test_one_boundary_decision import (
    LICENSED,
    Fake,
    _ask,
    _hosted,
    _write,
)


@pytest.mark.parametrize(
    ("value", "read", "warned"),
    [
        ("licenced", "licensed", "licenced"),
        ("bogus", "licensed", "bogus"),
        ("Licenced ", "licensed", "Licenced"),
        ("LICENSED", "licensed", ""),
        (" licensed\t", "licensed", ""),
        ("organization-internal", "organization-internal", ""),
        ("public-domain", "public-domain", ""),
    ],
)
def test_a_stamp_reads_as_a_known_class_or_licensed(value, read, warned):
    assert stamped_class(value) == read
    assert unknown_stamp(value) == warned


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_an_absent_stamp_is_unchanged(absent):
    assert stamped_class(absent) is None
    assert unknown_stamp(absent) == ""
    assert carried_content_class([absent]) is None


def test_the_most_guarded_is_carried_and_an_unknown_value_is_the_most_guarded():
    assert carried_content_class(["public-domain", "licenced"]) == "licensed"
    assert carried_content_class(["public-domain", "organization-internal"]) == (
        "organization-internal"
    )


def test_a_misspelt_licensed_stamp_never_reaches_a_hosted_model(tmp_path, monkeypatch):
    """Through the real corpus and answer path: the stamp says `licenced`,
    the sync report says so once, and the answer is refused for a hosted
    model, naming the passage."""
    import policyforge.llm.base as base
    from policyforge.mcp.server import build_state
    from policyforge.zardoz.corpus import sync_corpus
    from tests.test_edit import _registry

    content = tmp_path / "docs"
    _write(
        content / "standards" / "access-review-standard.md",
        "---\ntitle: Access Review Standard\ntier: standard\nowner: IAM\n"
        "content_class: licenced\n---\n\n"
        f"## 1. Reviews\n\n{LICENSED}\n",
    )
    report = sync_corpus([], content_dir=content, corpus_dir=tmp_path / "corpus")
    fake = Fake()
    monkeypatch.setattr(base, "_build_provider", lambda config: fake)
    state = build_state(
        _hosted(tmp_path),
        corpus_dir=tmp_path / "corpus",
        topics_path=_registry(tmp_path),
        provider_factory=base.get_provider,
    )

    out = _ask(state, "How often are accounts reviewed by the system owner?")

    assert report.format_report().count("which isn't a known class") == 1
    assert "standards/access-review-standard.md says content_class: 'licenced'" in (
        report.format_report()
    )
    assert "Not sent to the model" in out
    assert not fake.saw(LICENSED)


def test_edit_topic_warns_once_and_reads_a_misspelt_stamp_as_licensed(tmp_path, monkeypatch):
    from tests.test_one_boundary_decision import _calls, _edit

    result, fake = _edit(tmp_path, monkeypatch, _hosted(tmp_path), stamp="licenced")

    assert result.exit_code != 0
    assert result.output.count("which isn't a known class") == 1
    assert "'licenced' (not a known class, read as licensed)" in result.output
    assert _calls(fake) == 0


@pytest.mark.parametrize(
    "stamp",
    [
        "content_class: no\n",  # YAML False
        "content_class: 0\n",
        "content_class: {}\n",
        "generated_by:\n  content_class: false\n",
    ],
)
def test_a_present_but_falsy_yaml_stamp_reads_as_licensed_in_the_corpus_and_the_wiki(
    tmp_path, stamp
):
    """9b on PR 338: `declared_content_class` decided presence by truthiness,
    so YAML's `no`, `0` and `{}` read as absent in the corpus (the reader
    that decides model calls) while the wiki exporter read the same stamp as
    licensed. Both readers, one file, one answer, and one warning."""
    from policyforge.content.tree import load_content_tree
    from policyforge.export.github_wiki import _licensed
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    content = tmp_path / "docs"
    _write(
        content / "standards" / "s.md",
        f"---\ntitle: S\ntier: standard\nowner: IAM\n{stamp}---\n\nBody.\n",
    )

    report = sync_corpus([], content_dir=content, corpus_dir=tmp_path / "corpus")
    (doc,) = load_content_tree(content)[0]

    assert [d.content_class for d in load_corpus(tmp_path / "corpus").documents] == ["licensed"]
    assert _licensed(doc) is True, "the wiki exporter agrees"
    assert report.format_report().count("which isn't a known class") == 1
