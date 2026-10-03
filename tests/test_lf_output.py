"""Markdown this tool writes carries no CR, on any platform.

`Path.write_text` translates `\\n` to the platform's line separator, so on
Windows every markdown writer that passed `encoding="utf-8"` and nothing
else produced CRLF — and `mdformat`, which the repository's gate runs over
every markdown file, rejects a CR. Generated Standards, pulled pages and
applied revisions all failed a check the tool itself had just written the
input for, on the one platform CI does not run.

Two kinds of test. The behavioural ones write through the real writers and
read the bytes back; on Windows they failed before `textfile.write_text_lf`
existed. The structural one walks the writers' source and refuses a direct
`write_text` call, because the behavioural tests cover the writers that are
cheap to call and the site that forgets is the one that ships.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from policyforge.textfile import write_text_lf

SRC = Path(__file__).resolve().parent.parent / "src" / "policyforge"

#: Every module that writes markdown or a diff a person will read or the
#: gate will check. A module added here must route its writes through
#: `write_text_lf`; a module that starts writing markdown belongs here.
LF_WRITERS = [
    "export/markdown_exporter.py",
    # The pull loop, shared by every kind of live page store.
    "export/publisher.py",
    # Wiki pages are files in a git repository, and a CRLF written from
    # Windows would be a diff against every page the wiki already holds.
    "export/github_wiki.py",
    "edit/tree.py",
    "history/version_store.py",
    "cli/documents.py",
    "cli/content.py",
    # Crosswalk overlays: YAML a person edits and reviews by diff.
    "cli/crosswalk.py",
    # The synced corpus snapshot. Found by the sweep below, not by the
    # report that prompted this file — which is what the sweep is for.
    "zardoz/corpus.py",
    # Catalog JSON and framework.yaml: committed under data/frameworks, where
    # a CRLF write shows as a whole-file diff and moves the provenance hash.
    "cli/etl.py",
    "ingest/provenance.py",
]


# ---- the helper -----------------------------------------------------------


def test_the_helper_writes_lf_whatever_the_platform(tmp_path):
    path = write_text_lf(tmp_path / "doc.md", "# Title\n\nbody\n")

    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw == b"# Title\n\nbody\n"


def test_the_helper_folds_crlf_it_was_handed(tmp_path):
    """A model reply or a Confluence page can arrive with CRLF already in it.

    The guarantee callers want is that the file has none, not that none was
    added.
    """
    path = write_text_lf(tmp_path / "doc.md", "a\r\nb\rc\n")

    assert path.read_bytes() == b"a\nb\nc\n"


def test_the_helper_round_trips_non_ascii(tmp_path):
    text = "§ 164.308(a)(1) — “Security management process”\n"
    path = write_text_lf(tmp_path / "doc.md", text)

    assert path.read_text(encoding="utf-8") == text


# ---- the writers, behaviourally -------------------------------------------


def test_a_generated_document_is_written_with_lf(tmp_path):
    from policyforge.export.markdown_exporter import check_markdown_quality, write_markdown

    path = write_markdown("# Standard\n\nA requirement.\n", output_dir=tmp_path, filename="s.md")

    assert b"\r" not in path.read_bytes()
    # The same tool the gate runs. On Windows, before the fix, this said
    # a freshly generated file was badly formatted.
    assert check_markdown_quality(path)


def test_a_recorded_version_and_its_diff_are_written_with_lf(tmp_path):
    from policyforge.history.version_store import record_version

    history = tmp_path / ".history"
    record_version(history, "standard/x", "# v1\n\nfirst\n", source="generate")
    record_version(history, "standard/x", "# v1\n\nsecond\n", source="generate")

    stored = list(history.rglob("v*.md")) + list(history.rglob("v*.diff"))
    assert len(stored) == 4
    for path in stored:
        assert b"\r" not in path.read_bytes(), path


def test_an_applied_revision_is_written_with_lf(tmp_path):
    from policyforge.edit import tree

    source = tmp_path / "x.md"
    original = "---\ntitle: X\n---\n\n# X\n\nold\n"
    write_text_lf(source, original)
    frontmatter, _body = tree.split_frontmatter(original)
    tree_file = tree.TreeFile(
        path=source,
        relative="x.md",
        frontmatter=frontmatter,
        read_digest=tree.digest(original),
    )

    tree.write_revision(tree_file, "# X\n\nnew\n")

    raw = source.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"# X\n\nnew\n")


def test_a_pulled_page_is_written_with_lf(tmp_path, monkeypatch):
    """A pulled page lands in a tracked tree, where the gate runs mdformat."""
    from policyforge.export.pull import WRITTEN, pull_pages

    class FakePage:
        id = "id-1"
        title = "Access Control Standard"
        version = 3
        webui_url = "https://x/wiki/page"
        labels: list = []
        storage_body = "<h1>Access Control Standard</h1><p>Reviews happen quarterly.</p>"

    monkeypatch.setattr(
        "policyforge.export.confluence_importer.fetch_confluence_page",
        lambda *, space, title, host, **kwargs: FakePage(),
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **kw: {}
    )

    report = pull_pages(
        [("SEC", "Access Control Standard", "standard")],
        root=tmp_path,
        host="https://x",
        dry_run=False,
    )

    assert [r.action for r in report.results] == [WRITTEN]
    (written,) = tmp_path.rglob("*.md")
    raw = written.read_bytes()
    assert b"\r" not in raw
    assert b"quarterly" in raw


# ---- the writers, structurally --------------------------------------------


def _direct_write_text_calls(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ):
            lines.append(node.lineno)
    return lines


@pytest.mark.parametrize("relative", LF_WRITERS)
def test_a_markdown_writer_never_calls_write_text_directly(relative):
    """`write_text` translates newlines; `write_text_lf` does not.

    Listed by module rather than by call, so a new write in one of these
    files is held to the rule without anyone remembering it exists.
    """
    source = (SRC / relative).read_text(encoding="utf-8")

    assert _direct_write_text_calls(source) == [], (
        f"{relative} calls Path.write_text directly; route it through "
        "policyforge.textfile.write_text_lf"
    )
    assert "write_text_lf" in source, f"{relative} does not use write_text_lf at all"


def test_the_writer_list_is_complete():
    """Every module under src/ that writes a .md or .diff is in LF_WRITERS.

    Found by the file suffix in the call's path expression, which is how
    every markdown writer in this codebase names its destination.
    """
    suspects = []
    for path in SRC.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if not _direct_write_text_calls(source):
            continue
        for lineno in _direct_write_text_calls(source):
            line = source.splitlines()[lineno - 1]
            if ".md" in line or ".diff" in line:
                suspects.append(f"{path.relative_to(SRC).as_posix()}:{lineno}")
    listed = set(LF_WRITERS)
    unlisted = [s for s in suspects if s.split(":")[0] not in listed]
    assert unlisted == [], f"markdown writers not held to LF: {unlisted}"


def test_plan_files_beside_revisions_are_lf_too(tmp_path):
    """Not markdown, but written beside it into the same tracked tree."""
    path = write_text_lf(tmp_path / "x.plan.json", json.dumps({"a": 1}, indent=2) + "\n")

    assert b"\r" not in path.read_bytes()
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_a_page_carrying_crlf_pulls_as_unchanged_the_second_time(tmp_path, monkeypatch):
    """`read_text` folds CRLF on the way in; the comparison must fold too.

    Paragraph text has its whitespace collapsed on the way to markdown, but
    a `<pre>` block or a code macro keeps what the editor pasted, carriage
    returns included. Such a page rendered to a string with CRLF, was
    written as LF, and was then compared against the LF file it had just
    written: changed on every pull, forever.
    """
    from policyforge.export.pull import UNCHANGED, WRITTEN, pull_pages

    class FakePage:
        id = "id-1"
        title = "Access Control Standard"
        version = 3
        webui_url = "https://x/wiki/page"
        labels: list = []
        storage_body = (
            "<h1>Access Control Standard</h1><p>Reviews happen quarterly.</p>"
            "<pre>export the user list\r\nreconcile it against HR</pre>"
        )

    monkeypatch.setattr(
        "policyforge.export.confluence_importer.fetch_confluence_page",
        lambda *, space, title, host, **kwargs: FakePage(),
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **kw: {}
    )
    targets = [("SEC", "Access Control Standard", "standard")]

    first = pull_pages(targets, root=tmp_path, host="https://x", dry_run=False)
    second = pull_pages(targets, root=tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in first.results] == [WRITTEN]
    assert [r.action for r in second.results] == [UNCHANGED]
    (written,) = tmp_path.rglob("*.md")
    assert b"\r" not in written.read_bytes()


def test_normalise_newlines_is_what_the_writer_applies(tmp_path):
    from policyforge.textfile import normalise_newlines

    text = "a\r\nb\rc\n"
    path = write_text_lf(tmp_path / "x.md", text)

    assert normalise_newlines(text) == "a\nb\nc\n"
    assert path.read_bytes().decode("utf-8") == normalise_newlines(text)
