"""The `generated_by` stamp, which travels in the document.

`generate` already built the right stamp and wrote it to `output/.history`,
which is gitignored. The question it exists to answer — after a model is
found to systematically weaken cited requirements, which documents did it
touch — has to be answerable from a checkout by someone who was not there.
"""

from __future__ import annotations

from pathlib import Path

from policyforge.content.provenance import (
    GENERATED_BY,
    attribution,
    attribution_summary,
    documents_by_model,
    read_generated_by,
    stamp_document,
)
from policyforge.content.tree import parse_document

STAMP = {
    "models": ["z-ai/glm-5.3-flash"],
    "calls": 3,
    "prompt_shas": ["abc123"],
    "provider": "litellm",
    "provider_class": "third-party",
    "cost_usd": 0.0123,
}

DOCUMENT = """# Access Control Standard

## Scope

Applies to production systems. [NIST AC-2]
"""


def parse(text: str):
    return parse_document(text, path=Path("docs/standards/access.md"), root=Path("docs"))


def test_a_stamped_document_parses_back_to_the_same_stamp():
    """Written with the library `content/tree.py` reads with, so it round-trips."""
    document = parse(stamp_document(DOCUMENT, STAMP))
    assert read_generated_by(document.metadata) == STAMP


def test_the_body_survives_stamping_unchanged():
    body = parse(stamp_document(DOCUMENT, STAMP)).body
    assert "# Access Control Standard" in body
    assert "[NIST AC-2]" in body
    assert GENERATED_BY not in body


def test_existing_frontmatter_is_preserved():
    """The synthesis-derived keys are not this function's to discard."""
    with_meta = "---\ntopic: Access Control\nowner: Security\ntier: standard\n---\n\n" + DOCUMENT
    document = parse(stamp_document(with_meta, STAMP))
    assert document.metadata["topic"] == "Access Control"
    assert document.metadata["owner"] == "Security"
    assert document.tier == "standard"
    assert read_generated_by(document.metadata)["calls"] == 3


def test_stamping_twice_does_not_nest_or_duplicate():
    once = stamp_document(DOCUMENT, STAMP)
    twice = stamp_document(once, STAMP)
    assert parse(twice).metadata[GENERATED_BY] == STAMP
    assert twice.count(GENERATED_BY) == 1


def test_an_unstamped_document_reads_as_unstamped_not_as_an_error():
    """A hand-written document, or one pulled back from Confluence."""
    document = parse(DOCUMENT)
    assert read_generated_by(document.metadata) == {}
    assert not attribution(document).stamped


def test_attribution_reads_the_model_and_provider_class():
    found = attribution(parse(stamp_document(DOCUMENT, STAMP)))
    assert found.models == ("z-ai/glm-5.3-flash",)
    assert found.provider_class == "third-party"
    assert found.cost_usd == 0.0123


def test_a_single_model_written_as_a_string_is_accepted():
    """Frontmatter is hand-editable, so a scalar where a list was expected
    is a thing that will happen."""
    found = attribution(parse(stamp_document(DOCUMENT, {"models": "sonnet-5"})))
    assert found.models == ("sonnet-5",)


# ---- the question the mechanism exists for -----------------------------


class Doc:
    def __init__(self, path, models):
        self.relative_path = path
        self.metadata = {GENERATED_BY: {"models": models}} if models else {}


def test_documents_by_model_answers_which_documents_a_model_touched():
    documents = [
        Doc("standards/access.md", ["glm-5.3-flash"]),
        Doc("standards/backup.md", ["glm-5.3-flash"]),
        Doc("policies/access.md", ["sonnet-5"]),
        Doc("procedures/handwritten.md", None),
    ]
    assert documents_by_model(documents) == {
        "glm-5.3-flash": ["standards/access.md", "standards/backup.md"],
        "sonnet-5": ["policies/access.md"],
    }


def test_a_document_written_by_two_models_is_listed_under_both():
    """A cascade answers with both, and both touched the text."""
    documents = [Doc("standards/access.md", ["primary-model", "escalated-model"])]
    grouped = documents_by_model(documents)
    assert grouped["primary-model"] == ["standards/access.md"]
    assert grouped["escalated-model"] == ["standards/access.md"]


def test_the_summary_says_absence_is_not_a_fault():
    summary = attribution_summary([Doc("a.md", ["glm-5.3-flash"]), Doc("b.md", None)])
    assert "1 of 2 document(s)" in summary
    assert "not a finding" in summary or "not a fault" in summary


def test_a_tree_with_no_stamps_explains_why_that_is_normal():
    summary = attribution_summary([Doc("a.md", None), Doc("b.md", None)])
    assert "hand-written" in summary
    assert "pulled back from Confluence" in summary


def test_an_empty_tree_does_not_crash():
    assert "0 document(s)" in attribution_summary([])


def test_a_stamped_document_passes_the_projects_own_markdown_gate(tmp_path):
    """The invariant that nearly did not hold.

    `generate` runs check_markdown_quality on what it writes. mdformat
    without the `frontmatter` extension reads a leading `---` as a thematic
    break and rewrites the YAML underneath into headings and list items —
    so every stamped document would have been reported as badly formatted,
    and anything that then applied mdformat would have destroyed the stamp.

    python-frontmatter and mdformat also disagree about YAML list
    indentation, which would have failed the same check over two spaces.
    """
    from policyforge.export.markdown_exporter import check_markdown_quality

    path = tmp_path / "standard.md"
    path.write_text(stamp_document(DOCUMENT, STAMP), encoding="utf-8")
    assert check_markdown_quality(path)


def test_the_gate_still_fails_a_badly_formatted_document(tmp_path):
    """The fix above must not have turned the check off."""
    from policyforge.export.markdown_exporter import check_markdown_quality

    path = tmp_path / "sloppy.md"
    path.write_text("#bad  heading\n\n*  sloppy list\n", encoding="utf-8")
    assert not check_markdown_quality(path)


def test_mdformat_does_not_destroy_the_stamp(tmp_path):
    """Directly, because this is the failure with the worst consequence."""
    import mdformat

    stamped = stamp_document(DOCUMENT, STAMP)
    formatted = mdformat.text(stamped, extensions={"gfm", "frontmatter"})
    assert read_generated_by(parse(formatted).metadata) == STAMP


def test_generate_writes_no_stamp_when_no_model_was_recorded(tmp_path):
    """`models: []` is noise claiming to be provenance.

    The ledger records nothing when a provider is injected directly rather
    than built through get_provider. Absence already means "not known",
    which is the honest record — a document asserting it was written by no
    models is worse than one asserting nothing.
    """
    empty = {"models": [], "calls": 0, "prompt_shas": []}
    assert not attribution(parse(stamp_document(DOCUMENT, empty))).stamped
