"""What `import-confluence` writes about a page it pulls back (#197).

80's ruling on the issue, one test per clause:

- `content_class` is carried from a local predecessor and never invented;
- with no predecessor the command says the class is unknown;
- `models` is never claimed for imported text;
- the attribution summary says "imported".

**Why the class matters:** the GitHub-wiki exporter refuses a public wiki
to a `licensed` document and lets an unclassified one through on the
wiki's visibility rule. As measured on the train before this change, an
import had no frontmatter at all. It was published nowhere until a person
declared a wiki target on it, and after that it was published with no
ceiling. The last test here runs that path through the real exporter.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest
from click.testing import CliRunner

from policyforge.export.confluence_importer import ConfluencePage

TITLE = "Access Review Standard"
BODY_HTML = "<h1>Access Review Standard</h1><p>Licensed wording.</p>"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A clean cwd, so the command's default `output/` paths land in tmp."""
    monkeypatch.chdir(tmp_path)
    import policyforge.export.confluence_importer as importer

    monkeypatch.setattr(
        importer,
        "fetch_confluence_page",
        lambda **_: ConfluencePage(
            id="1", title=TITLE, version=7, storage_body=BODY_HTML, webui_url="/x"
        ),
    )
    return tmp_path


def _import(*extra: str):
    from policyforge.cli import cli

    return CliRunner().invoke(
        cli,
        [
            "import-confluence",
            "--tier",
            "standard",
            "--name",
            "access-review",
            "--space",
            "ENG",
            "--title",
            TITLE,
            "--host",
            "https://example.atlassian.net/wiki",
            *extra,
        ],
    )


def _written(path: str = "output/standards/access-review.imported.md") -> dict:
    return frontmatter.loads(Path(path).read_text(encoding="utf-8")).metadata


def _generated(content_class: str | None, *, top_level: str | None = None) -> None:
    """A predecessor as `generate` writes one: a stamp naming the models."""
    stamp = {"models": ["glm-5.3-flash"], "calls": 3}
    if content_class is not None:
        stamp["content_class"] = content_class
    meta = {"generated_by": stamp}
    if top_level is not None:
        meta["content_class"] = top_level
    path = Path("output/standards/access-review.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(frontmatter.dumps(frontmatter.Post("# Old draft\n", **meta)), encoding="utf-8")


# -- the class is carried, never invented ------------------------------------------


def test_a_licensed_predecessor_gives_a_licensed_import(workdir):
    _generated("licensed")
    result = _import()
    assert result.exit_code == 0, result.output
    written = _written()
    assert written["content_class"] == "licensed"
    assert "carried from the local predecessor" in result.output
    assert "unknown" not in result.output


def test_with_no_predecessor_the_class_is_unknown_and_not_invented(workdir):
    result = _import()
    assert result.exit_code == 0, result.output
    assert "Content class unknown" in result.output
    assert "content_class" not in _written()


def test_a_recorded_version_is_a_predecessor_too(workdir):
    """The generated file may be gone; `generate` also recorded its class."""
    from policyforge.history.version_store import record_version

    record_version(
        Path("output/.history"),
        "standard/access-review",
        "# Old\n",
        source="generate",
        metadata={"content_class": "licensed"},
    )
    result = _import()
    assert result.exit_code == 0, result.output
    assert _written()["content_class"] == "licensed"


@pytest.mark.parametrize(
    "in_file, in_history",
    [("organization-internal", "licensed"), ("licensed", "organization-internal")],
)
def test_a_disagreement_never_lowers_the_ceiling(workdir, in_file, in_history):
    """Two KNOWN classes, in both orders. The first version of this test
    used `org-internal`, which is not a class, so it was dropped before any
    comparison and "the least guarded wins" survived it."""
    from policyforge.history.version_store import record_version
    from policyforge.llm.boundary import CONTENT_CLASSES

    assert {in_file, in_history} <= set(CONTENT_CLASSES)
    _generated(in_file)
    record_version(
        Path("output/.history"),
        "standard/access-review",
        "# Old\n",
        source="generate",
        metadata={"content_class": in_history},
    )
    assert _import().exit_code == 0
    assert _written()["content_class"] == "licensed"


def test_a_second_import_keeps_what_the_first_carried(workdir):
    """With the generated file gone and no history, the previous import at
    --out is the only thing that knows. Overwriting it must not forget."""
    _generated("licensed")
    assert _import("--history-dir", "h1").exit_code == 0
    Path("output/standards/access-review.md").unlink()
    result = _import("--history-dir", "h2")
    assert result.exit_code == 0, result.output
    assert _written()["content_class"] == "licensed"


def test_a_top_level_class_is_read_as_the_exporter_reads_it(workdir):
    _generated(None, top_level=" Licensed ")
    assert _import().exit_code == 0
    assert _written()["content_class"] == "licensed"


def test_a_blank_top_level_class_does_not_hide_the_stamped_one(workdir):
    """A hand-edited `content_class: " "` says nothing. It must not shadow
    `generated_by.content_class: licensed` beneath it."""
    _generated("licensed", top_level="  ")
    assert _import().exit_code == 0
    assert _written()["content_class"] == "licensed"


def test_an_unknown_class_value_is_not_carried_or_replaced(workdir):
    _generated("secret")
    result = _import()
    assert result.exit_code == 0, result.output
    assert "content_class" not in _written()
    assert "Content class unknown" in result.output


# -- the import is recorded, and models are not claimed ------------------------------


def test_the_import_is_recorded_and_models_are_never_claimed(workdir):
    _generated("licensed")
    assert _import().exit_code == 0
    written = _written()
    assert "generated_by" not in written
    assert written["imported_from"]["space"] == "ENG"
    assert written["imported_from"]["title"] == TITLE
    assert written["imported_from"]["page_version"] == 7
    assert written["imported_from"]["date"]
    # The earlier generation is history, under a key attribution does not read.
    assert written["previously_generated_by"]["models"] == ["glm-5.3-flash"]

    from policyforge.content.provenance import attribution
    from policyforge.content.tree import parse_document

    path = Path("output/standards/access-review.imported.md")
    doc = parse_document(path.read_text(encoding="utf-8"), path=path, root=Path("output"))
    found = attribution(doc)
    assert found.models == ()
    assert found.imported


def test_the_history_record_says_what_was_carried(workdir):
    from policyforge.history.version_store import load_history

    _generated("licensed")
    assert _import().exit_code == 0
    (record,) = load_history(Path("output/.history"), "standard/access-review")
    assert record.source == "confluence-import"
    assert record.metadata["content_class"] == "licensed"
    assert record.metadata["page_version"] == 7


# -- the reader follows the writer ------------------------------------------------


class _Doc:
    def __init__(self, path, metadata):
        self.relative_path = path
        self.metadata = metadata


def test_the_summary_labels_an_import_as_imported():
    from policyforge.content.provenance import attribution_summary

    summary = attribution_summary(
        [
            _Doc("standards/generated.md", {"generated_by": {"models": ["glm-5.3-flash"]}}),
            _Doc("standards/access.imported.md", {"imported_from": {"space": "ENG"}}),
            _Doc("standards/handwritten.md", {}),
        ]
    )
    assert "1 document(s) imported from Confluence" in summary
    assert "standards/access.imported.md" in summary
    # Counted once, as imported, and not also among the unstamped.
    assert "1 document(s) carry no stamp" in summary


def test_the_summary_labels_imports_in_a_tree_with_no_generated_documents():
    from policyforge.content.provenance import attribution_summary

    summary = attribution_summary([_Doc("a.imported.md", {"imported_from": {"space": "ENG"}})])
    assert "No document in this tree records the model" in summary
    assert "1 document(s) imported from Confluence" in summary


# -- the ceiling, end to end through the real exporter --------------------------------


def test_a_licensed_import_stays_off_a_public_wiki_once_someone_declares_one(workdir):
    """The path that was live before this change: a person adds a wiki
    target to an import by hand. The exporter must still refuse it."""
    from policyforge.content.tree import load_content_tree
    from policyforge.export.github_wiki import GitHubWikiPublisher

    _generated("licensed")
    assert _import().exit_code == 0
    path = Path("output/standards/access-review.imported.md")
    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    post.metadata["targets"] = {"github_wiki": {"title": TITLE}}
    path.write_text(frontmatter.dumps(post), encoding="utf-8")

    docs, problems = load_content_tree(Path("output"))
    assert problems == []
    (doc,) = [d for d in docs if d.relative_path.endswith("access-review.imported.md")]
    wiki = GitHubWikiPublisher("o/r", workdir=workdir / "wiki", public=True, allow_public=True)
    assert wiki.location(doc) == "o/r", "the probe must reach the exporter's refusal"
    assert "licensed" in wiki.refuse(doc)


# -- a Policy generated before #411 lives in policys/ ------------------------------


def test_a_policy_generated_into_the_old_directory_still_hands_on_its_class(workdir):
    """#411 moved `generate`'s Policies from `policys/` to `policies/`. A tree
    generated earlier still has its Policy in `policys/`: the import reads
    it there too, so a licensed class is not dropped in the move."""
    meta = {"generated_by": {"models": ["glm-5.3-flash"], "calls": 3, "content_class": "licensed"}}
    old = Path("output/policys/access-review.md")
    old.parent.mkdir(parents=True)
    old.write_text(frontmatter.dumps(frontmatter.Post("# Old draft\n", **meta)), encoding="utf-8")
    from policyforge.cli import cli

    result = CliRunner().invoke(
        cli,
        ["import-confluence", "--tier", "policy", "--name", "access-review", "--space", "ENG",
         "--title", TITLE, "--host", "https://example.atlassian.net/wiki"],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert _written("output/policies/access-review.imported.md")["content_class"] == "licensed"
