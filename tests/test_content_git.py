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
    # The page says something the file doesn't: a page that already matches
    # is left alone (#391).
    _patch_confluence(
        monkeypatch,
        pages={"Access Control Standard": "<h1>Access Control Standard</h1><p>Yearly.</p>"},
    )
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

    # Not moved: the round trip closes. And nothing to write, since the file
    # says what the page says (#391); an edit to the file is then published.
    assert [r.action for r in report.results] == [UNCHANGED]
    assert exported == []
    _edit_pulled(tmp_path)
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

    # The pulled edit is no longer a move, and there is nothing new to send.
    assert [r.action for r in report.results] == [UNCHANGED]
    _edit_pulled(tmp_path)
    report = publish_tree(tmp_path, host="https://x", dry_run=False)
    assert [r.action for r in report.results] == [UPDATED]
    assert exported


def _edit_pulled(root: Path) -> None:
    """Change the body of the one pulled file, as a reviewer would."""
    [path] = root.rglob("*.md")
    path.write_text(path.read_text(encoding="utf-8") + "\nReviewed again.\n", encoding="utf-8")


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


# ---- a live edit is a wiki change the repository hasn't seen (383) ---------
#
# `edit-confluence` and `edit-topic --host` write the approved edit straight
# to the live page. It used to carry the publish stamp, and its only local
# record was output/edits/, so the next `publish --apply` from an unchanged
# tree called the page safe and silently reverted the edit.


def _live_edit_message(monkeypatch) -> str:
    """The version message the real `update_page_body` writes, captured from
    a fake Confluence: what `edit-confluence` actually stamps, not a constant
    a test could share with the code under test."""
    import requests

    from policyforge.export import confluence_exporter

    sent: dict = {}

    class _Response:
        def __init__(self, payload):
            self._payload, self.status_code = payload, 200

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def put(url, json=None, **kwargs):
        sent.update(json or {})
        return _Response({"_links": {"webui": "/wiki/page"}})

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Response({"version": {"number": 4}}))
    monkeypatch.setattr(requests, "put", put)
    monkeypatch.setattr(confluence_exporter, "confluence_auth", lambda **k: (None, {}))
    confluence_exporter.update_page_body(
        "Accounts are reviewed every quarter.",
        page_id="id-acs",
        title="Access Control Standard",
        space="SEC",
        host="https://x",
        expected_version=4,
    )
    return sent["version"]["message"]


def _live_edited(message: str) -> FakePage:
    return FakePage(
        id="id-acs",
        title="Access Control Standard",
        storage_body="<h1>Access Control Standard</h1><p>Accounts are reviewed every quarter.</p>",
        version=5,
        version_message=message,
    )


def test_a_live_edit_is_refused_by_the_next_publish_not_reverted(tmp_path, monkeypatch):
    message = _live_edit_message(monkeypatch)
    exported = []
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": _live_edited(message)}, exported=exported
    )
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))  # the tree never changed

    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert exported == [], "the approved live edit must survive"
    assert [r.action for r in report.results] == [MOVED]
    assert "live edit" in report.moved[0].reason, report.moved[0].reason
    assert "Changed on the wiki" in report.format_report()


def test_wiki_drift_reports_a_live_edit_as_changed_on_the_wiki(tmp_path, monkeypatch):
    message = _live_edit_message(monkeypatch)
    _patch_confluence(monkeypatch, pages={"Access Control Standard": _live_edited(message)})
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))

    text = _drift(tmp_path).format_report()

    assert "live edit" in text, text


def test_after_pull_a_live_edit_may_be_published_over(tmp_path, monkeypatch):
    """The loop the refusal asks for: pull records version 5, then publish."""
    message = _live_edit_message(monkeypatch)
    exported = []
    _patch_confluence(
        monkeypatch, pages={"Access Control Standard": _live_edited(message)}, exported=exported
    )

    pull_pages(
        [("SEC", "Access Control Standard", "standard")],
        root=tmp_path,
        host="https://x",
        dry_run=False,
    )
    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in report.results] != [MOVED]


def test_the_live_edit_stamp_is_not_the_publish_stamp():
    """`publish` matches its stamp as a substring, so a live-edit stamp that
    contained it would still read as the tool's own publish."""
    from policyforge.export.confluence_exporter import LIVE_EDIT_MARKER

    assert PUBLISH_MARKER not in LIVE_EDIT_MARKER


# --------------------------------------------------------------------------
# pull writes into the bound file and keeps its frontmatter (#387)
# --------------------------------------------------------------------------


def _generated(title="Access Control Standard", *, path_tier_key="tier: standard\n", block=None):
    """A document as `generate` writes it, bound to its page: the keys a pull
    used to drop sit beside the binding."""
    block = block or "confluence:\n  space: SEC\n  title: Access Control Standard\n  version: 2\n"
    return (
        f"---\ntopic: Access Control\nowner: IAM Engineering\n{path_tier_key}"
        "generated_by:\n  models:\n    - fake-writer\n"
        f"{block}---\n\n# {title}\n\nReviews happen yearly.\n"
    )


def _frontmatter(path: Path) -> dict:
    import frontmatter

    return dict(frontmatter.loads(path.read_text(encoding="utf-8")).metadata)


def _pull_one(tmp_path, monkeypatch, tier="standard"):
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})
    return pull_pages(
        [("SEC", "Access Control Standard", tier)], root=tmp_path, host="https://x", dry_run=False
    )


def _md_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md"))


def test_a_page_is_pulled_into_the_file_bound_to_it_whatever_its_name(tmp_path, monkeypatch):
    """The file is named differently from the page's title. The pull updates
    it in place, and no second copy appears at the title's slug, which
    `check` would reject as two files claiming one page."""
    bound = _write(tmp_path, "standards/ac-review.md", _generated())

    report = _pull_one(tmp_path, monkeypatch)

    assert [(r.path, r.action, r.note) for r in report.results] == [
        ("standards/ac-review.md", WRITTEN, "")
    ]
    assert _md_files(tmp_path) == ["standards/ac-review.md"]
    assert "quarterly" in bound.read_text(encoding="utf-8")


def test_a_pull_keeps_every_key_but_the_binding_and_updates_the_version(tmp_path, monkeypatch):
    bound = _write(tmp_path, "standards/ac-review.md", _generated())

    _pull_one(tmp_path, monkeypatch)

    metadata = _frontmatter(bound)
    assert metadata["topic"] == "Access Control"
    assert metadata["owner"] == "IAM Engineering"
    assert metadata["tier"] == "standard"
    assert metadata["generated_by"] == {"models": ["fake-writer"]}
    assert metadata["confluence"] == {
        "space": "SEC",
        "title": "Access Control Standard",
        "page_id": "id-Access Control Standard",
        "version": 3,
    }
    # Its own title came from its heading; the pull adds none.
    assert "title" not in metadata
    assert check_tree(tmp_path).errors == []


def test_the_owner_survives_a_pull_into_a_file_at_the_slug_too(tmp_path, monkeypatch):
    """The second half of the defect, on its own: a bound file already at the
    slug kept its place, but lost its topic and owner on every pull."""
    bound = _write(tmp_path, "standards/access-control-standard.md", _generated())

    _pull_one(tmp_path, monkeypatch)

    metadata = _frontmatter(bound)
    assert (metadata["topic"], metadata["owner"]) == ("Access Control", "IAM Engineering")


def test_a_page_no_file_is_bound_to_is_placed_by_its_title_and_says_so(tmp_path, monkeypatch):
    from policyforge.export.publisher import PLACED_BY_TITLE

    _write(tmp_path, "standards/other.md", _bound("Other Standard"))

    report = _pull_one(tmp_path, monkeypatch)

    assert [(r.path, r.action, r.note) for r in report.results] == [
        ("standards/access-control-standard.md", WRITTEN, PLACED_BY_TITLE)
    ]
    assert PLACED_BY_TITLE in report.format_report()
    metadata = _frontmatter(tmp_path / "standards" / "access-control-standard.md")
    assert (metadata["title"], metadata["tier"]) == ("Access Control Standard", "standard")


def test_a_file_bound_by_its_directory_keeps_its_tier(tmp_path, monkeypatch):
    """The file's tier comes from `policies/`, with no key. A pull run with
    the registry's or `--tier`'s `standard` must not write a tier into it."""
    bound = _write(
        tmp_path,
        "policies/ac.md",
        _generated(
            path_tier_key="",
            block="confluence:\n  space: SEC\n  title: Access Control Standard\n",
        ),
    )

    _pull_one(tmp_path, monkeypatch, tier="standard")

    assert "tier" not in _frontmatter(bound)


def test_a_binding_under_targets_is_updated_where_it_is(tmp_path, monkeypatch):
    """A file spelling its binding `targets: confluence:` is updated there,
    rather than gaining a second, top-level block for the same store."""
    bound = _write(
        tmp_path,
        "standards/ac-review.md",
        _generated(
            block="targets:\n  confluence:\n    space: SEC\n    title: Access Control Standard\n"
        ),
    )

    _pull_one(tmp_path, monkeypatch)

    metadata = _frontmatter(bound)
    assert "confluence" not in metadata
    assert metadata["targets"]["confluence"]["version"] == 3
    assert _md_files(tmp_path) == ["standards/ac-review.md"]


def test_two_files_bound_to_one_page_are_refused_and_neither_is_written(tmp_path, monkeypatch):
    first = _write(tmp_path, "standards/a.md", _generated())
    second = _write(tmp_path, "standards/b.md", _generated())
    before = (first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8"))

    report = _pull_one(tmp_path, monkeypatch)

    [result] = report.results
    assert (result.action, result.kind) == (REFUSED, "bound-twice")
    assert "standards/a.md" in result.reason and "standards/b.md" in result.reason
    assert (first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")) == before
    assert _md_files(tmp_path) == ["standards/a.md", "standards/b.md"]


def test_a_title_slug_that_another_page_owns_is_refused(tmp_path, monkeypatch):
    """The same title in two spaces shares a slug. Placing the second page by
    title would rebind the first page's file and replace its text."""
    owned = _write(
        tmp_path,
        "standards/access-control-standard.md",
        _bound("Access Control Standard", space="OPS"),
    )
    before = owned.read_text(encoding="utf-8")

    report = _pull_one(tmp_path, monkeypatch)

    [result] = report.results
    assert (result.action, result.kind) == (REFUSED, "slug-taken")
    assert "OPS" in result.reason
    assert owned.read_text(encoding="utf-8") == before


def test_a_generated_document_keeps_what_generate_wrote_through_a_pull(tmp_path, monkeypatch):
    """The two halves together: `generate` writes topic, owner, tier, the
    binding and its provenance stamp, and a pull of the bound page keeps
    every one of them, in the file `generate` wrote."""
    from tests.test_generate_frontmatter import TOPICS_YAML, _run_generate

    result, out_path = _run_generate(tmp_path, monkeypatch, topics_yaml=TOPICS_YAML)
    assert result.exit_code == 0, result.output
    generated = _frontmatter(out_path)
    assert {"topic", "owner", "tier", "confluence", "generated_by"} <= set(generated)

    root = out_path.parent
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})
    report = pull_pages(
        [("SEC", "Access Control Standard", "standard")], root=root, host="https://x", dry_run=False
    )

    assert [(r.path, r.action) for r in report.results] == [(out_path.name, WRITTEN)]
    assert _md_files(root) == [out_path.name]
    pulled = _frontmatter(out_path)
    for key in ("topic", "owner", "tier", "generated_by"):
        assert pulled[key] == generated[key], key
    assert pulled["confluence"]["title"] == "Access Control Standard"
    assert pulled["confluence"]["version"] == 3


def _two_spaces(tmp_path, monkeypatch, *, dry_run):
    """One run pulling the same title from two spaces, as a registry pull
    does when two topics declare it: the tree is read once, before either
    page is written."""
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})
    return pull_pages(
        [
            ("SEC", "Access Control Standard", "standard"),
            ("OPS", "Access Control Standard", "standard"),
        ],
        root=tmp_path,
        host="https://x",
        dry_run=dry_run,
    )


def test_one_run_does_not_put_two_pages_in_one_file(tmp_path, monkeypatch):
    report = _two_spaces(tmp_path, monkeypatch, dry_run=False)

    first, second = report.results
    assert (first.action, first.path) == (WRITTEN, "standards/access-control-standard.md")
    assert (second.action, second.kind) == (REFUSED, "slug-taken")
    assert "SEC" in second.reason and "earlier in this run" in second.reason
    written = tmp_path / "standards" / "access-control-standard.md"
    assert _frontmatter(written)["confluence"]["space"] == "SEC"
    assert "Wrote 1 file(s)" in report.format_report()


def test_a_dry_run_plans_the_same_refusal(tmp_path, monkeypatch):
    """The plan says what --apply would do, so the claim is made without a
    write too."""
    report = _two_spaces(tmp_path, monkeypatch, dry_run=True)

    assert [(r.action, r.kind) for r in report.results] == [(WRITTEN, ""), (REFUSED, "slug-taken")]
    assert _md_files(tmp_path) == []


def test_the_same_page_twice_in_one_run_is_not_a_conflict(tmp_path, monkeypatch):
    """A page listed twice owns its own claim; only another page is refused."""
    _patch_confluence(monkeypatch, pages={"Access Control Standard": PLAIN_PAGE})
    targets = [("SEC", "Access Control Standard", "standard")] * 2

    report = pull_pages(targets, root=tmp_path, host="https://x", dry_run=False)

    assert REFUSED not in [r.action for r in report.results]


# --------------------------------------------------------------------------
# a page that already says what the file says is left alone (#391)
# --------------------------------------------------------------------------


def _remembering_wiki(monkeypatch) -> list[str]:
    """A fake wiki that keeps what publish writes, stamped, so the next run
    reads it back as the tool's own write. Returns the titles written."""
    from policyforge.export.confluence_exporter import markdown_to_confluence

    store: dict[str, FakePage] = {}
    written: list[str] = []

    def _fetch(*, space, title, host, **kwargs):
        if title not in store:
            raise LookupError(f"No Confluence page titled {title!r} in space {space!r}.")
        return store[title]

    def _export(markdown_text, *, space, title, host, **kwargs):
        written.append(title)
        version = store[title].version + 1 if title in store else 1
        store[title] = FakePage(
            id=f"id-{title}",
            title=title,
            storage_body=markdown_to_confluence(markdown_text),
            version=version,
        )
        return f"https://x/wiki/{title}"

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr("policyforge.export.confluence_exporter.export_to_confluence", _export)
    return written


def test_a_second_publish_with_no_edits_writes_nothing(tmp_path, monkeypatch):
    """Every page got a new version on every run, and the next pull then
    rewrote every file's version number."""
    written = _remembering_wiki(monkeypatch)
    for name, title in (
        ("a", "Access Control Standard"),
        ("b", "Audit Standard"),
        ("c", "Backup Standard"),
    ):
        _write(tmp_path, f"standards/{name}.md", _bound(title))

    first = publish_tree(tmp_path, host="https://x", dry_run=False)
    assert [r.action for r in first.results] == [CREATED] * 3
    second = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert [r.action for r in second.results] == [UNCHANGED] * 3
    assert len(written) == 3, "the second run made no update call"
    assert "3 already matched the page and were left alone" in second.format_report()


def test_one_edited_file_is_the_only_update(tmp_path, monkeypatch):
    written = _remembering_wiki(monkeypatch)
    for name, title in (("a", "Access Control Standard"), ("b", "Audit Standard")):
        _write(tmp_path, f"standards/{name}.md", _bound(title))
    publish_tree(tmp_path, host="https://x", dry_run=False)
    written.clear()

    _write(tmp_path, "standards/b.md", _bound("Audit Standard", body="Logs are kept a year."))
    report = publish_tree(tmp_path, host="https://x", dry_run=False)

    assert {r.path: r.action for r in report.results} == {
        "standards/a.md": UNCHANGED,
        "standards/b.md": UPDATED,
    }
    assert written == ["Audit Standard"]


def test_a_dry_run_plans_an_unchanged_page_as_unchanged(tmp_path, monkeypatch):
    _remembering_wiki(monkeypatch)
    _write(tmp_path, "standards/a.md", _bound("Access Control Standard"))
    publish_tree(tmp_path, host="https://x", dry_run=False)

    plan = publish_tree(tmp_path, host="https://x")

    assert [r.action for r in plan.results] == [UNCHANGED]
    assert "Nothing was written" not in plan.format_report()
