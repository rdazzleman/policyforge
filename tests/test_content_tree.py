"""content/tree.py tests — markdown documents as the source of truth.

The behaviour worth pinning here is the tolerance. A tree of documents this
project generated has no frontmatter at all, and a tree somebody has curated
by hand has frontmatter on some files and not others. Both have to resolve,
and where they disagree the explicit declaration has to win — otherwise the
inferred answer quietly overrides the one somebody wrote down on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.content.tree import (
    ContentError,
    first_heading,
    load_content_tree,
    parse_document,
)


def _doc(tmp_path, relative, text):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Resolving one file
# --------------------------------------------------------------------------


def test_a_file_with_no_frontmatter_still_resolves(tmp_path):
    """The documents `generate` has already written carry none, and a content
    model that only works for files created after it existed is a migration
    nobody performs."""
    path = _doc(tmp_path, "standards/access-control.md", "# Access Control Standard\n\nBody.\n")

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.title == "Access Control Standard"  # from the H1
    assert doc.tier == "standard"  # from the directory
    assert doc.slug == "access-control"  # from the filename
    assert doc.relative_path == "standards/access-control.md"
    assert doc.body.startswith("# Access Control Standard")


def test_frontmatter_beats_what_the_filesystem_implies(tmp_path):
    path = _doc(
        tmp_path,
        "standards/ac.md",
        "---\ntitle: Logical Access Control\ntier: policy\nslug: logical-access\n---\n\n"
        "# Something Else\n",
    )

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.title == "Logical Access Control"
    assert doc.tier == "policy"
    assert doc.slug == "logical-access"


def test_a_title_falls_back_to_the_filename_when_there_is_no_heading(tmp_path):
    path = _doc(tmp_path, "procedures/key_rotation-steps.md", "Just a paragraph.\n")

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.title == "Key Rotation Steps"
    assert doc.tier == "procedure"


def test_the_confluence_binding_is_read_from_frontmatter(tmp_path):
    """A repo path and a published page title are different strings, and
    guessing that they match is how a publish lands on the wrong page."""
    path = _doc(
        tmp_path,
        "standards/ac.md",
        "---\ntitle: Access Control Standard\nconfluence:\n  space: ENG\n"
        '  title: "Acme Access Control Standard"\n  page_id: "12345"\n---\n\nBody.\n',
    )

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.space == "ENG"
    assert doc.page_title == "Acme Access Control Standard"
    assert doc.page_id == "12345"


def test_an_unpublished_document_falls_back_to_its_own_title(tmp_path):
    path = _doc(tmp_path, "standards/ac.md", "# Access Control Standard\n")

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.space == ""
    assert doc.page_title == "Access Control Standard"


def test_a_tier_directory_anywhere_in_the_path_counts(tmp_path):
    """Both `standards/access-control.md` and a per-topic layout resolve, so
    the model doesn't dictate one directory shape."""
    path = _doc(tmp_path, "access-control/standards/current.md", "# Current\n")

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.tier == "standard"


def test_broken_frontmatter_names_the_problem_rather_than_raising_later(tmp_path):
    path = _doc(tmp_path, "standards/ac.md", "---\ntitle: [unclosed\n---\n\nBody.\n")

    with pytest.raises(ContentError, match="not valid YAML"):
        parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)


def test_first_heading_ignores_deeper_levels():
    assert first_heading("## Section\n\n# Actual Title\n") == "Actual Title"
    assert first_heading("No headings here.") == ""


# --------------------------------------------------------------------------
# References — the document graph
# --------------------------------------------------------------------------


def test_references_collect_local_links_of_both_flavours(tmp_path):
    path = _doc(
        tmp_path,
        "standards/ac.md",
        "# S\n\nSee [the procedure](../procedures/access-review.md) and "
        "[[Access Control Policy]].\n",
    )

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.references == ["../procedures/access-review.md", "Access Control Policy"]


def test_external_links_are_not_references(tmp_path):
    """The question this answers is 'what else should I read alongside this?',
    and a vendor's documentation is not an answer to it."""
    path = _doc(
        tmp_path,
        "standards/ac.md",
        "# S\n\n[Okta docs](https://okta.com/docs) and [local](./other.md#section).\n",
    )

    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=tmp_path)

    assert doc.references == ["./other.md"]


# --------------------------------------------------------------------------
# Walking the tree
# --------------------------------------------------------------------------


def test_the_tree_loads_every_tier(tmp_path):
    _doc(tmp_path, "policies/ac.md", "# AC Policy\n")
    _doc(tmp_path, "standards/ac.md", "# AC Standard\n")
    _doc(tmp_path, "procedures/ac.md", "# AC Procedure\n")

    documents, problems = load_content_tree(tmp_path)

    assert problems == []
    assert sorted(d.tier for d in documents) == ["policy", "procedure", "standard"]


def test_synthesis_and_dotted_directories_are_not_documents(tmp_path):
    """`synthesis/` holds requirement lists the generator consumes, and
    `.zardoz/` is the corpus itself — ingesting that would have Zardoz
    answering from a copy of its own snapshot."""
    _doc(tmp_path, "standards/ac.md", "# AC Standard\n")
    _doc(tmp_path, "synthesis/ac.md", "---\ntopic: Access\n---\n\n- A requirement\n")
    _doc(tmp_path, ".zardoz/docs/sec-ac.md", "# A cached copy\n")

    documents, _ = load_content_tree(tmp_path)

    assert [d.relative_path for d in documents] == ["standards/ac.md"]


def test_one_broken_file_does_not_cost_the_rest_of_the_tree(tmp_path):
    _doc(tmp_path, "standards/good.md", "# Good\n")
    _doc(tmp_path, "standards/bad.md", "---\ntitle: [unclosed\n---\n\nBody.\n")

    documents, problems = load_content_tree(tmp_path)

    assert [d.slug for d in documents] == ["good"]
    assert len(problems) == 1
    assert problems[0][0] == "standards/bad.md"
    assert "YAML" in problems[0][1]


def test_a_missing_root_is_empty_rather_than_an_error(tmp_path):
    documents, problems = load_content_tree(tmp_path / "nope")

    assert documents == []
    assert problems == []


def test_relative_paths_use_forward_slashes_on_every_platform(tmp_path):
    """The path is a citation target, so it has to be the string a reader can
    paste into a repo URL rather than a Windows-flavoured one."""
    _doc(tmp_path, "standards/nested/ac.md", "# AC\n")

    documents, _ = load_content_tree(tmp_path)

    assert documents[0].relative_path == "standards/nested/ac.md"
    assert "\\" not in documents[0].relative_path
    assert isinstance(documents[0].path, Path)
