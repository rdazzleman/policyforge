"""Tests for handling UTF-8 byte-order mark (BOM) in frontmatter parsing (#424)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from policyforge.cli import cli
from policyforge.content.tree import parse_document


def test_parse_document_drops_leading_bom_before_frontmatter():
    """A hand-written body with a leading BOM and frontmatter: the reader
    returns the frontmatter fields, and the body without the BOM or the
    frontmatter. The same body without a BOM gives the same result."""
    text_with_bom = (
        "\ufeff---\ntopic: Demo\ntitle: Demo Standard\n---\n\n# Demo Standard\n\nBody content.\n"
    )
    text_without_bom = (
        "---\ntopic: Demo\ntitle: Demo Standard\n---\n\n# Demo Standard\n\nBody content.\n"
    )

    doc_with_bom = parse_document(text_with_bom, path=Path("demo.md"), root=Path("."))
    doc_without_bom = parse_document(text_without_bom, path=Path("demo.md"), root=Path("."))

    assert doc_with_bom.metadata == {"topic": "Demo", "title": "Demo Standard"}
    assert doc_with_bom.title == "Demo Standard"
    assert doc_with_bom.body == "# Demo Standard\n\nBody content."
    assert "\ufeff" not in doc_with_bom.body
    assert doc_with_bom.metadata == doc_without_bom.metadata
    assert doc_with_bom.body == doc_without_bom.body


def test_parse_document_drops_leading_bom_without_frontmatter():
    text_with_bom = "\ufeff# Just a Document\n\nBody content.\n"
    text_without_bom = "# Just a Document\n\nBody content.\n"

    doc_with_bom = parse_document(text_with_bom, path=Path("demo.md"), root=Path("."))
    doc_without_bom = parse_document(text_without_bom, path=Path("demo.md"), root=Path("."))

    assert doc_with_bom.metadata == {}
    assert doc_with_bom.title == "Just a Document"
    assert doc_with_bom.body == "# Just a Document\n\nBody content."
    assert "\ufeff" not in doc_with_bom.body
    assert doc_with_bom.metadata == doc_without_bom.metadata
    assert doc_with_bom.body == doc_without_bom.body


def test_export_confluence_dry_run_with_bom_prefixed_file(tmp_path: Path):
    """export-confluence --dry-run on a BOM-prefixed document converts cleanly
    without printing frontmatter into the page body."""
    bom_file = tmp_path / "bom_doc.md"
    clean_file = tmp_path / "clean_doc.md"

    content = "---\ntopic: Demo\n---\n\n# Demo Standard\n\nSome text.\n"
    bom_file.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
    clean_file.write_bytes(content.encode("utf-8"))

    runner = CliRunner()
    result_bom = runner.invoke(
        cli,
        [
            "export-confluence",
            "--doc",
            str(bom_file),
            "--space",
            "X",
            "--title",
            "Demo",
            "--host",
            "https://example.invalid",
            "--dry-run",
        ],
    )
    result_clean = runner.invoke(
        cli,
        [
            "export-confluence",
            "--doc",
            str(clean_file),
            "--space",
            "X",
            "--title",
            "Demo",
            "--host",
            "https://example.invalid",
            "--dry-run",
        ],
    )

    assert result_bom.exit_code == 0, result_bom.output
    assert result_clean.exit_code == 0, result_clean.output
    assert "topic: Demo" not in result_bom.output
    assert "<hr" not in result_bom.output
    assert "\ufeff" not in result_bom.output
    assert "<h1>Demo Standard</h1>" in result_bom.output
    assert result_bom.output == result_clean.output


# ---- every frontmatter reader, not one (#424, carried by ba) ----------------------

_SYNTHESIS = (
    "---\ntopic: Access\ncontent_class: licensed\nderived_from:\n- hitrust-csf\n---\n\n"
    "- Review access.\n"
)
_BOM = chr(0xFEFF)


def test_read_synthesis_reads_a_bom_prefixed_synthesis():
    """The reader `generate` takes the content class from: with a BOM it
    returned no metadata, losing `content_class: licensed`."""
    from policyforge.synthesis.merge import read_synthesis

    assert read_synthesis(_BOM + _SYNTHESIS) == read_synthesis(_SYNTHESIS)
    assert read_synthesis(_BOM + _SYNTHESIS)[0]["content_class"] == "licensed"


def test_the_existing_frontmatter_generate_merges_is_read_through_a_bom():
    from policyforge.content.provenance import existing_metadata

    assert existing_metadata(_BOM + _SYNTHESIS) == existing_metadata(_SYNTHESIS) != {}


def test_the_edit_path_splits_a_bom_prefixed_file_and_writes_its_bytes_back():
    """The edit path writes the file back: the BOM stays in the raw block,
    and the body the model sees holds no frontmatter."""
    from policyforge.edit.tree import join_frontmatter, split_frontmatter

    block, body = split_frontmatter(_BOM + _SYNTHESIS)
    assert block.startswith(_BOM + "---\n") and "content_class" in block
    assert body == "- Review access.\n"
    assert join_frontmatter(block, body) == _BOM + _SYNTHESIS
    assert split_frontmatter(_SYNTHESIS) == (block[1:], body)


def test_no_module_parses_frontmatter_but_through_the_one_reader():
    """Every `frontmatter.load`/`loads`, aliased or not, is in `textfile`:
    a reader added elsewhere would hide a BOM'd file's frontmatter again.
    Derived by `ast` over every module, and the population is asserted.
    What it reads is an attribute on a name the module bound to
    `frontmatter`, and `from frontmatter import load(s)`; a call through
    `__import__` or `importlib` isn't seen (measured: an arm written that way
    passes it; the reader tests above fail it instead)."""
    import ast

    root = Path(__file__).resolve().parent.parent / "src" / "policyforge"
    modules = sorted(root.rglob("*.py"))
    offenders = []
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = {"frontmatter"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases |= {a.asname or a.name for a in node.names if a.name == "frontmatter"}
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "frontmatter"
                and any(a.name in {"load", "loads"} for a in node.names)
            ):
                offenders.append(f"{path.name}: from frontmatter import load(s)")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in {"load", "loads"}
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
                and path.name != "textfile.py"
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert len(modules) > 100  # extent: the source tree was read
    assert offenders == []


def test_a_bom_prefixed_file_that_opens_with_a_rule_is_refused_as_without_one():
    """b5 on PR 433: the rule check reads the text the frontmatter reader
    parsed; without that, the prose between the rules was dropped silently."""
    import pytest

    from policyforge.content.tree import ContentError

    text = "---\nSome prose between rules.\n---\n\n# Title\n\nBody.\n"
    for body in (text, _BOM + text):
        with pytest.raises(ContentError, match="horizontal rule"):
            parse_document(body, path=Path("x.md"), root=Path("."))


def test_exactly_one_bom_is_looked_past_by_every_reader():
    """80's ruling: one. With two, the second still hides the `---`, and the
    content tree and the synthesis reader agree on that."""
    from policyforge.synthesis.merge import read_synthesis

    two = _BOM + _BOM + _SYNTHESIS
    assert read_synthesis(two)[0] == {}
    assert parse_document(two, path=Path("x.md"), root=Path(".")).metadata == {}


def test_the_class_edit_scopes_on_is_read_through_a_bom():
    """b5 on PR 433: `_declared_class` is the class `edit` scopes on, the
    same boundary as `read_synthesis`."""
    from policyforge.cli.content import _declared_class

    assert _declared_class(_BOM + _SYNTHESIS) == _declared_class(_SYNTHESIS) == "licensed"


def test_a_stamped_document_saved_with_a_bom_is_still_unedited():
    """b5 on PR 433 (not blocking): the overwrite guard reads the stamp and
    the body hash through a BOM."""
    from policyforge.content.provenance import (
        UNEDITED,
        body_digest,
        edit_state,
        stamp_with_body_hash,
    )

    stamped = stamp_with_body_hash("# Title\n\nBody.\n", {"model": "fake"})
    assert body_digest(_BOM + stamped) == body_digest(stamped)
    assert edit_state(_BOM + stamped) == edit_state(stamped) == UNEDITED
