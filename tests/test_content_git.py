"""check / publish / pull — the repo-backed loop.

markdown in a repo -> pull request -> merge -> published to Confluence, and
back again when somebody edits the wiki directly.

The tests that matter are the refusals. Publishing over a page full of
macros this tool cannot round-trip destroys work nobody agreed to lose, and
pulling such a page produces a file that looks correct and does the damage
on its first publish. Both are refused by name rather than warned about
afterwards, and neither should ever become a warning somebody learns to
scroll past.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from click.testing import CliRunner

from policyforge.content.check import check_tree
from policyforge.export.confluence_exporter import PUBLISH_MARKER
from policyforge.export.publish import CREATED, MOVED, SKIPPED, UPDATED, publish_tree
from policyforge.export.pull import REFUSED, UNCHANGED, WRITTEN, pull_pages, target_path

MACRO_PAGE = (
    '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>Careful.</p>'
    "</ac:rich-text-body></ac:structured-macro><h1>Access</h1><p>Quarterly.</p>"
)
PLAIN_PAGE = "<h1>Access Control Standard</h1><p>Reviews happen quarterly.</p>"


@dataclass
class FakePage:
    id: str
    title: str
    storage_body: str
    version: int = 3
    webui_url: str = "https://x/wiki/page"
    labels: list = field(default_factory=list)
    ancestors: list = field(default_factory=list)
    #: Stamped by default: an existing page in these tests is one this tool
    #: last published, unless a test says somebody edited it since.
    version_message: str = PUBLISH_MARKER


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _bound(title, space="SEC", page_title=None, body="Reviews happen quarterly.", version=None):
    page_title = page_title or title
    pulled = f"  version: {version}\n" if version is not None else ""
    return (
        f"---\ntitle: {title}\nowner: IAM Engineering\n"
        f"confluence:\n  space: {space}\n  title: {page_title}\n{pulled}---\n\n"
        f"# {title}\n\n{body}\n"
    )


def _patch_confluence(monkeypatch, *, pages=None, exported=None):
    """Fake the three network functions publish/pull reach for.

    A page is given as its storage body, or as a whole FakePage when a test
    needs to say who wrote the latest version.
    """
    store = pages or {}

    def _fetch(*, space, title, host, **kwargs):
        if title not in store:
            raise LookupError(f"No Confluence page titled {title!r} in space {space!r}.")
        if isinstance(store[title], FakePage):
            return store[title]
        return FakePage(id=f"id-{title}", title=title, storage_body=store[title])

    def _export(markdown_text, *, space, title, host, **kwargs):
        if exported is not None:
            exported.append((space, title, markdown_text))
        return f"https://x/wiki/{title}"

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr("policyforge.export.confluence_exporter.export_to_confluence", _export)
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **kw: {}
    )


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def test_a_clean_tree_reports_nothing(tmp_path):
    _write(tmp_path, "standards/access-control.md", _bound("Access Control Standard"))

    report = check_tree(tmp_path)

    assert report.ok
    assert report.errors == []


def test_two_files_claiming_one_page_is_an_error(tmp_path):
    """Both would publish, the second would overwrite the first, and the repo
    would still contain two files each looking like the source of truth."""
    _write(tmp_path, "standards/a.md", _bound("A", page_title="Access Control Standard"))
    _write(tmp_path, "standards/b.md", _bound("B", page_title="Access Control Standard"))

    report = check_tree(tmp_path)

    assert not report.ok
    assert any("already claims" in f.message for f in report.errors)


def test_the_same_title_in_different_spaces_is_fine(tmp_path):
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard", space="SEC"))
    _write(tmp_path, "standards/b.md", _bound("Access Control Standard", space="ENG"))

    assert check_tree(tmp_path).ok


def test_a_link_to_a_document_that_does_not_exist_is_an_error(tmp_path):
    _write(
        tmp_path,
        "standards/access-control.md",
        _bound("Access", body="See [the procedure](../procedures/review.md)."),
    )

    report = check_tree(tmp_path)

    assert not report.ok
    assert any("does not exist" in f.message for f in report.errors)


def test_a_link_that_resolves_is_not_an_error(tmp_path):
    _write(tmp_path, "procedures/review.md", _bound("Review Procedure"))
    _write(
        tmp_path,
        "standards/access-control.md",
        _bound("Access", body="See [the procedure](../procedures/review.md)."),
    )

    assert check_tree(tmp_path).ok


def test_a_confluence_block_with_no_space_cannot_publish_anywhere(tmp_path):
    _write(
        tmp_path,
        "standards/a.md",
        "---\ntitle: A\nowner: Sec\nconfluence:\n  title: Somewhere\n---\n\n# A\n",
    )

    report = check_tree(tmp_path)

    assert not report.ok
    assert any("no `space:`" in f.message for f in report.errors)


def test_a_missing_owner_warns_without_blocking(tmp_path):
    """A repo mid-migration is full of these, and a gate that cannot be
    satisfied gets switched off."""
    _write(tmp_path, "standards/a.md", "# A Standard\n\nText.\n")

    report = check_tree(tmp_path)

    assert report.ok
    assert any("no owner" in f.message for f in report.warnings)


def test_broken_frontmatter_is_an_error(tmp_path):
    _write(tmp_path, "standards/a.md", "---\ntitle: [unclosed\n---\n\n# A\n")

    assert not check_tree(tmp_path).ok


def test_citations_dropped_since_the_synthesis_are_reported(tmp_path):
    """Invisible in a diff of the prose, and it is the traceability an
    assessor needs."""
    synthesis = tmp_path / "synthesis"
    synthesis.mkdir()
    (synthesis / "access-control.md").write_text(
        "- Accounts are reviewed. [NIST AC-2 | HIPAA 164.308(a)(4)]\n"
        "- Credentials rotate. [NIST IA-5]\n",
        encoding="utf-8",
    )
    root = tmp_path / "docs"
    _write(root, "standards/access-control.md", _bound("Access", body="Reviews. [NIST AC-2]"))

    report = check_tree(root, synthesis_dir=synthesis)

    assert any("missing" in f.message and "IA-5" in f.message for f in report.warnings)


def test_the_check_command_exits_nonzero_on_an_error(tmp_path):
    import policyforge.cli as cli_mod

    _write(tmp_path, "standards/a.md", _bound("A", page_title="Shared"))
    _write(tmp_path, "standards/b.md", _bound("B", page_title="Shared"))

    result = CliRunner().invoke(cli_mod.cli, ["check", "--content-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "already claims" in result.output


def test_strict_makes_warnings_fail_too(tmp_path):
    import policyforge.cli as cli_mod

    _write(tmp_path, "standards/a.md", "# A Standard\n\nText.\n")
    args = ["check", "--content-dir", str(tmp_path)]

    assert CliRunner().invoke(cli_mod.cli, args).exit_code == 0
    assert CliRunner().invoke(cli_mod.cli, [*args, "--strict"]).exit_code == 1


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------


def test_a_dry_run_plans_without_writing(tmp_path, monkeypatch):
    exported = []
    _patch_confluence(monkeypatch, exported=exported)
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = publish_tree(tmp_path, host="https://x")

    assert exported == [], "nothing was published"
    assert [r.action for r in report.results] == [CREATED]
    assert "Would publish" in report.format_report()
    assert "--apply" in report.format_report()


def test_applying_publishes_the_document_body(tmp_path, monkeypatch):
    exported = []
    _patch_confluence(monkeypatch, exported=exported)
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    publish_tree(tmp_path, host="https://x", dry_run=False)

    assert len(exported) == 1
    space, title, body = exported[0]
    assert (space, title) == ("SEC", "Access Control Standard")
    assert "Reviews happen quarterly" in body
    assert "confluence:" not in body, "frontmatter is not published"


def test_an_existing_page_is_an_update_not_a_create(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = publish_tree(tmp_path, host="https://x")

    assert [r.action for r in report.results] == [UPDATED]


def test_a_page_full_of_macros_is_skipped_not_flattened(tmp_path, monkeypatch):
    """Publishing over it would destroy work nobody agreed to lose."""
    exported = []
    _patch_confluence(monkeypatch, pages={"Access Control Standard": MACRO_PAGE}, exported=exported)
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert exported == []
    assert [r.action for r in report.results] == [SKIPPED]
    assert "info" in report.skipped[0].reason


def test_allow_macros_publishes_anyway(tmp_path, monkeypatch):
    exported = []
    _patch_confluence(monkeypatch, pages={"Access Control Standard": MACRO_PAGE}, exported=exported)
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    publish_tree(tmp_path, host="https://x", dry_run=False, allow_macros=True)

    assert len(exported) == 1


def test_a_document_with_no_destination_is_left_alone(tmp_path, monkeypatch):
    """A file with no `confluence:` block is how a draft stays a draft."""
    _patch_confluence(monkeypatch)
    _write(tmp_path, "standards/draft.md", "# A Draft\n\nNot ready.\n")

    report = publish_tree(tmp_path, host="https://x")

    assert report.results == []
    assert report.undeclared == 1


def test_only_narrows_the_run_to_what_a_merge_touched(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch)
    _write(tmp_path, "standards/a.md", _bound("A"))
    _write(tmp_path, "policies/b.md", _bound("B"))

    report = publish_tree(tmp_path, host="https://x", only="policies/")

    assert [r.title for r in report.results] == ["B"]


# --------------------------------------------------------------------------
# pull
# --------------------------------------------------------------------------


def test_a_pulled_page_lands_at_its_tier_and_records_where_it_came_from(tmp_path, monkeypatch):
    """A repo path and a page title are different strings; a pull that did
    not record the correspondence would publish back to the wrong place."""
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})

    report = pull_pages(
        [("SEC", "Access Control Standard", "standard")],
        root=tmp_path,
        host="https://x",
        dry_run=False,
    )

    written = tmp_path / "standards" / "access-control-standard.md"
    assert written.exists()
    text = written.read_text(encoding="utf-8")
    assert "space: SEC" in text
    assert "title: Access Control Standard" in text
    assert "quarterly" in text
    assert [r.action for r in report.results] == [WRITTEN]


def test_a_dry_run_pull_writes_no_files(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})

    report = pull_pages(
        [("SEC", "Access Control Standard", "standard")], root=tmp_path, host="https://x"
    )

    assert list(tmp_path.rglob("*.md")) == []
    assert "--apply" in report.format_report()


def test_pulling_the_same_page_twice_reports_it_unchanged(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})
    targets = [("SEC", "Access Control Standard", "standard")]

    pull_pages(targets, root=tmp_path, host="https://x", dry_run=False)
    report = pull_pages(targets, root=tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in report.results] == [UNCHANGED]


def test_a_page_that_would_not_survive_a_publish_is_refused(tmp_path, monkeypatch):
    """The file would look correct and destroy the macros on first publish."""
    _patch_confluence(monkeypatch, pages={"Runbook": MACRO_PAGE})

    report = pull_pages(
        [("SEC", "Runbook", "procedure")], root=tmp_path, host="https://x", dry_run=False
    )

    assert list(tmp_path.rglob("*.md")) == []
    assert [r.action for r in report.results] == [REFUSED]
    assert "info" in report.refused[0].reason


def test_a_missing_page_is_refused_by_name(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch)

    report = pull_pages(
        [("SEC", "Gone", "standard")], root=tmp_path, host="https://x", dry_run=False
    )

    assert [r.action for r in report.results] == [REFUSED]
    assert "Gone" in report.refused[0].reason


def test_the_tier_decides_where_a_pulled_page_lands(tmp_path):
    assert target_path(tmp_path, tier="policy", slug="ac") == tmp_path / "policies" / "ac.md"
    assert target_path(tmp_path, tier="procedure", slug="ac") == tmp_path / "procedures" / "ac.md"
    assert target_path(tmp_path, tier="", slug="ac") == tmp_path / "ac.md"


def test_a_pulled_page_can_be_published_straight_back(tmp_path, monkeypatch):
    """The round trip has to close, or the repo model does not work."""
    exported = []
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE}, exported=exported)

    pull_pages(
        [("SEC", "Access Control Standard", "standard")],
        root=tmp_path,
        host="https://x",
        dry_run=False,
    )
    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in report.results] == [UPDATED]
    assert exported and exported[0][1] == "Access Control Standard"


# --------------------------------------------------------------------------
# S-05: an edit made on the wiki is not destroyed by the next publish
# --------------------------------------------------------------------------

#: Somebody tidied the page in Confluence after this tool last wrote it. The
#: body differs from what the repository would publish, as a real edit does
#: — otherwise the page is unchanged and publishing it destroys nothing.
HAND_EDITED = FakePage(
    id="id-acs",
    title="Access Control Standard",
    storage_body="<h1>Access Control Standard</h1><p>Accounts are reviewed every quarter.</p>",
    version=5,
    version_message="Tidied the wording",
)


def test_a_page_edited_on_the_wiki_is_reported_not_overwritten(tmp_path, monkeypatch):
    """A publish used to read the live version and increment it, so it always
    won — and an edit somebody made last week was gone the next time any
    document merged, with nothing to say so."""
    exported = []
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": HAND_EDITED}, exported=exported
    )
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert exported == [], "the hand edit must survive"
    assert [r.action for r in report.results] == [MOVED]
    text = report.format_report()
    assert "Changed on the wiki" in text
    assert "version 5" in text
    assert "Pull them" in text


def test_force_overwrites_a_moved_page(tmp_path, monkeypatch):
    exported = []
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": HAND_EDITED}, exported=exported
    )
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = publish_tree(tmp_path, host="https://x", dry_run=False, force=True)

    assert len(exported) == 1
    assert [r.action for r in report.results] == [UPDATED]


def test_a_hand_edit_the_repository_has_pulled_may_be_overwritten(tmp_path, monkeypatch):
    """Once the edit is pulled into the repo it has been seen and reviewed as
    a diff, so publishing the repo's version destroys nothing unseen."""
    exported = []
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": HAND_EDITED}, exported=exported
    )
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard", version=5))

    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in report.results] == [UPDATED]
    assert len(exported) == 1


def test_a_pulled_version_that_is_behind_the_page_is_still_moved(tmp_path, monkeypatch):
    """Pulled at 4, edited again to 5: the second edit is unseen."""
    _patch_confluence(monkeypatch, pages={"Access Control Standard": HAND_EDITED})
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard", version=4))

    report = publish_tree(tmp_path, host="https://x")

    assert [r.action for r in report.results] == [MOVED]
    assert "last pulled version 4" in report.moved[0].reason


def test_a_new_page_is_created_without_asking(tmp_path, monkeypatch):
    """Nothing is on the wiki, so nothing can be lost."""
    _patch_confluence(monkeypatch)
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = publish_tree(tmp_path, host="https://x")

    assert [r.action for r in report.results] == [CREATED]


def test_a_pull_records_the_version_it_brought_in(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch, pages={"Access Control Standard": HAND_EDITED})

    pull_pages(
        [("SEC", "Access Control Standard", "standard")],
        root=tmp_path,
        host="https://x",
        dry_run=False,
    )

    text = (tmp_path / "standards" / "access-control-standard.md").read_text(encoding="utf-8")
    assert "version: 5" in text


def test_pull_then_publish_closes_the_loop_for_a_hand_edit(tmp_path, monkeypatch):
    """The workflow the moved report asks for: pull, review, publish."""
    exported = []
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": HAND_EDITED}, exported=exported
    )
    targets = [("SEC", "Access Control Standard", "standard")]

    pull_pages(targets, root=tmp_path, host="https://x", dry_run=False)
    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in report.results] == [UPDATED]


def test_the_version_message_is_read_from_the_page():
    from policyforge.export.confluence_importer import _parse_page

    page = _parse_page(
        {
            "id": "1",
            "title": "T",
            "version": {"number": 7, "message": PUBLISH_MARKER},
            "_links": {"webui": "/x"},
        },
        base="https://x",
    )

    assert page.version == 7
    assert page.version_message == PUBLISH_MARKER


def test_the_publish_command_fails_the_run_when_a_page_has_moved(tmp_path, monkeypatch):
    """Even alongside pages that did publish: a green CI job with an edit
    sitting unpulled on the wiki is the silent case this check ends."""
    import policyforge.cli as cli_mod

    _patch_confluence(
        monkeypatch,
        pages={"Access Control Standard": HAND_EDITED, "Backup Standard": PLAIN_PAGE},
    )
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))
    _write(tmp_path, "standards/b.md", _bound("Backup Standard"))
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    result = CliRunner().invoke(
        cli_mod.cli, ["publish", "--content-dir", str(tmp_path), "--host", "https://x"]
    )

    assert result.exit_code != 0
    assert "Changed on the wiki" in result.output
    assert "Backup Standard" in result.output


# ---- adopting pages published before the stamp existed --------------------


def _as_published(root, title="Access Control Standard"):
    """The storage this tool would publish for the document titled `title`."""
    from policyforge.content.tree import load_content_tree
    from policyforge.export.confluence_exporter import markdown_to_confluence

    documents, _ = load_content_tree(root)
    return markdown_to_confluence(next(d for d in documents if d.page_title == title).body)


def _legacy(storage_body):
    """A page this tool published before it stamped its writes."""
    return FakePage(
        id="id-acs",
        title="Access Control Standard",
        storage_body=storage_body,
        version=2,
        version_message="",
    )


def test_an_unchanged_page_from_before_the_stamp_is_adopted(tmp_path, monkeypatch):
    """Otherwise the first run after upgrading reports every existing page
    as moved, though overwriting one that already says what the repository
    says destroys nothing."""
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))
    exported = []
    _patch_confluence(
        monkeypatch,
        pages={"Access Control Standard": _legacy(_as_published(tmp_path))},
        exported=exported,
    )

    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in report.results] == [UPDATED]
    assert "adopted" in report.format_report()
    assert len(exported) == 1, "published, and so stamped from here on"


def test_adoption_tolerates_confluence_reflowing_the_markup(tmp_path, monkeypatch):
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))
    reflowed = _as_published(tmp_path).replace("><", ">\n    <").replace("\n", "\n    ")
    _patch_confluence(monkeypatch, pages={"Access Control Standard": _legacy(reflowed)})

    report = publish_tree(tmp_path, host="https://x")

    assert [r.action for r in report.results] == [UPDATED]


def test_an_unstamped_page_that_differs_is_still_moved(tmp_path, monkeypatch):
    """Adoption is by content: any real difference is an edit somebody made."""
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))
    edited = _as_published(tmp_path).replace("quarterly", "monthly")
    exported = []
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": _legacy(edited)}, exported=exported
    )

    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in report.results] == [MOVED]
    assert exported == []


# --------------------------------------------------------------------------
# P-04: wiki drift as a question, not the wreckage of a failed publish
# --------------------------------------------------------------------------


def _drift(root, only=""):
    from policyforge.export.drift import wiki_drift

    return wiki_drift(root, host="https://x", only=only)


def test_a_page_edited_on_the_wiki_is_reported_with_how_to_reconcile_it(tmp_path, monkeypatch):
    """The question a policy owner asks before a review cycle, answered
    without publishing anything to find out."""
    _patch_confluence(monkeypatch, pages={"Access Control Standard": HAND_EDITED})
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = _drift(tmp_path)

    (moved,) = report.moved
    assert moved.title == "Access Control Standard"
    assert "version 5" in moved.reason
    assert moved.reconcile == (
        # Single-quoted because the command is now built with `shlex.quote`,
        # which quotes only what needs it and handles a value containing a
        # quote character -- see #187. The old hand-written `"..."` turned
        # `Vendor "Bring Your Own" Policy` into a command naming the wrong
        # document.
        "policyforge pull --space SEC --title 'Access Control Standard' --tier standard --apply"
    )
    text = report.format_report()
    assert "reconcile: policyforge pull" in text
    assert "standards/a.md" in text


def test_a_page_this_tool_wrote_last_is_in_sync(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = _drift(tmp_path)

    assert report.moved == []
    assert len(report.in_sync) == 1
    assert "Nothing to reconcile" in report.format_report()


def test_a_page_saying_what_the_repository_says_is_in_sync_whoever_wrote_it(tmp_path, monkeypatch):
    """Same content rule as adoption: there is nothing to bring back."""
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": _legacy(_as_published(tmp_path))}
    )

    assert _drift(tmp_path).moved == []


def test_a_declared_page_that_does_not_exist_yet_is_reported_apart(tmp_path, monkeypatch):
    """Absent is not drift: nobody edited a page that was never published."""
    _patch_confluence(monkeypatch)
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    report = _drift(tmp_path)

    assert report.moved == []
    assert len(report.absent) == 1
    assert "not published yet" in report.format_report()


def test_only_narrows_the_drift_check(tmp_path, monkeypatch):
    _patch_confluence(monkeypatch, pages={"A": HAND_EDITED, "B": HAND_EDITED})
    _write(tmp_path, "standards/a.md", _bound("A"))
    _write(tmp_path, "policies/b.md", _bound("B"))

    report = _drift(tmp_path, only="policies/")

    assert [r.title for r in report.results] == ["B"]


def test_wiki_drift_reports_without_failing_unless_asked(tmp_path, monkeypatch):
    """A report that always exited non-zero would be muted inside a month;
    --fail-on-change is what makes a scheduled run the notification."""
    import policyforge.cli as cli_mod

    _patch_confluence(monkeypatch, pages={"Access Control Standard": HAND_EDITED})
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    argv = ["wiki-drift", "--content-dir", str(tmp_path), "--host", "https://x"]

    reported = CliRunner().invoke(cli_mod.cli, argv)
    gated = CliRunner().invoke(cli_mod.cli, [*argv, "--fail-on-change"])

    assert reported.exit_code == 0
    assert "changed on the wiki" in reported.output
    assert "policyforge pull" in reported.output
    assert gated.exit_code == 1
