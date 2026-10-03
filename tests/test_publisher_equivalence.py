"""The Publisher refactor changed no behaviour for Confluence.

`export/publisher.py` moved the publish, drift and pull loops behind one
interface so a second kind of store can share their guards. The claim that
this changed nothing for the store they were written against is held here
directly: the loops as they stood before the refactor are reproduced below
from `publish.py`, `drift.py` and `pull.py` at 6dce141, and every scenario
the content/git tests exercise is run through both, asserting the same
report, the same formatted output and the same exporter calls, argument
for argument.

What is frozen, and how faithfully, so a reader who checks is not
surprised. `_old_publish_tree`, `_old_wiki_drift`, `_old_pull_pages`,
`_old_moved_since_last_publish`, `_old_unchanged_on_the_wiki`,
`_old_storage_key` and `_old_target_path` are the 6dce141 bodies with only
their imports flattened and their helper calls renamed to the `_old_`
copies. `_OldPublishResult`, `_OldPublishReport`, `_OldDriftResult`,
`_OldDriftReport`, `_OldPullResult` and `_OldPullReport` are the 6dce141
dataclasses, copied whole, because a frozen loop must build the frozen
shape: the live `PublishReport` gained an `undeclared_note` field and a
branch in `format_report` in the refactor, and a first cut of this file
had the frozen loop build the live class, which meant a change to
`format_report` moved both sides together and could never fail the test.
Nothing is simplified.

The frozen copies are the point of the file and must not be "tidied" into
calls to the new code: the moment they delegate, the test proves nothing.

The second half holds the pieces the refactor added — the `targets:`
spelling and its `confluence:` alias, on documents and in the topic
registry — and the one thing 1d asked to be pinned per adapter: that a
cosmetic reflow on Confluence's side is not read as an edit.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from policyforge.content.check import check_tree
from policyforge.content.tree import TIER_DIRS, load_content_tree, render_document
from policyforge.export.confluence_exporter import PUBLISH_MARKER
from policyforge.export.drift import wiki_drift
from policyforge.export.publish import ADOPTED, CREATED, MOVED, SKIPPED, UPDATED, publish_tree
from policyforge.export.publisher import ConfluencePublisher, LivePage
from policyforge.export.pull import REFUSED, UNCHANGED, WRITTEN, pull_pages
from policyforge.textfile import normalise_newlines, write_text_lf
from tests.test_content_git import (
    HAND_EDITED,
    MACRO_PAGE,
    PLAIN_PAGE,
    FakePage,
    _as_published,
    _bound,
    _legacy,
    _patch_confluence,
    _write,
)

# ==========================================================================
# From export/publish.py, export/drift.py and export/pull.py at 6dce141.
# Bodies untouched apart from flattened imports and `_old_` helper names;
# the dataclasses the loops build are copied whole, see the docstring.
# ==========================================================================

IN_SYNC = "in-sync"
ABSENT = "absent"


def _old_moved_since_last_publish(doc, live) -> str:
    if PUBLISH_MARKER in (live.version_message or ""):
        return ""
    recorded = doc.confluence.get("version")
    try:
        recorded = int(recorded) if recorded is not None else None
    except (TypeError, ValueError):
        recorded = None
    if recorded is not None and recorded == live.version:
        return ""

    known = (
        f"the repository last pulled version {recorded}"
        if recorded is not None
        else "the repository has no pulled version on record"
    )
    return (
        f"the live page is at version {live.version}, last written by someone other "
        f"than this tool, and {known}"
    )


def _old_storage_key(storage: str) -> str:
    return re.sub(r">\s+<", "><", storage.strip())


def _old_unchanged_on_the_wiki(doc, live) -> bool:
    from policyforge.export.confluence_exporter import markdown_to_confluence

    return _old_storage_key(markdown_to_confluence(doc.body)) == _old_storage_key(
        live.storage_body or ""
    )


@dataclass
class _OldPublishResult:
    path: str
    space: str
    title: str
    action: str
    reason: str = ""
    url: str = ""


@dataclass
class _OldPublishReport:
    results: list[_OldPublishResult] = field(default_factory=list)
    dry_run: bool = True
    undeclared: int = 0

    def _of(self, action: str) -> list[_OldPublishResult]:
        return [r for r in self.results if r.action == action]

    @property
    def skipped(self) -> list[_OldPublishResult]:
        return self._of(SKIPPED)

    @property
    def moved(self) -> list[_OldPublishResult]:
        return self._of(MOVED)

    @property
    def published(self) -> list[_OldPublishResult]:
        return self._of(CREATED) + self._of(UPDATED)

    def format_report(self) -> str:
        verb = "Would publish" if self.dry_run else "Published"
        lines = [
            f"{verb} {len(self.published)} page(s): "
            f"{len(self._of(CREATED))} new, {len(self._of(UPDATED))} updated"
        ]
        for result in self.published:
            mark = "+" if result.action == CREATED else "~"
            note = f" ({result.reason})" if result.reason else ""
            lines.append(f"  {mark} {result.path} -> {result.space}/{result.title}{note}")
            if result.url:
                lines.append(f"      {result.url}")

        if self.moved:
            lines += [
                "",
                f"Changed on the wiki since this tool last published them ({len(self.moved)}) "
                "— not overwritten. Pull them, review the change, then publish:",
            ]
            lines += [f"  {r.path}: {r.reason}" for r in self.moved]

        if self.skipped:
            lines += ["", f"Skipped {len(self.skipped)}:"]
            lines += [f"  {r.path}: {r.reason}" for r in self.skipped]

        if self.undeclared:
            lines += [
                "",
                f"{self.undeclared} document(s) declare no `confluence:` block and were "
                "left alone.",
            ]

        if self.dry_run and self.published:
            lines += ["", "Nothing was written. Pass --apply to publish."]
        return "\n".join(lines)


def _old_publish_tree(
    root: Path,
    *,
    host: str,
    dry_run: bool = True,
    allow_macros: bool = False,
    only: str = "",
    force: bool = False,
) -> _OldPublishReport:
    from policyforge.content.tree import load_content_tree
    from policyforge.edit.apply import detect_unsupported_macros
    from policyforge.export.confluence_exporter import export_to_confluence
    from policyforge.export.confluence_importer import fetch_confluence_page

    documents, problems = load_content_tree(root)
    report = _OldPublishReport(dry_run=dry_run)
    report.results.extend(
        _OldPublishResult(path=path, space="", title="", action=SKIPPED, reason=reason)
        for path, reason in problems
    )

    for doc in documents:
        if only and only not in doc.relative_path:
            continue
        if not doc.space:
            report.undeclared += 1
            continue

        try:
            live = fetch_confluence_page(space=doc.space, title=doc.page_title, host=host)
        except LookupError:
            live = None

        if live is not None and not allow_macros:
            macros = detect_unsupported_macros(live.storage_body)
            if macros:
                report.results.append(
                    _OldPublishResult(
                        path=doc.relative_path,
                        space=doc.space,
                        title=doc.page_title,
                        action=SKIPPED,
                        reason=(
                            f"the live page uses macros this tool cannot round-trip "
                            f"({', '.join(macros)}); publishing would flatten them"
                        ),
                    )
                )
                continue

        reason = ""
        if live is not None and not force:
            moved = _old_moved_since_last_publish(doc, live)
            if moved and _old_unchanged_on_the_wiki(doc, live):
                moved, reason = "", ADOPTED
            if moved:
                report.results.append(
                    _OldPublishResult(
                        path=doc.relative_path,
                        space=doc.space,
                        title=doc.page_title,
                        action=MOVED,
                        reason=moved,
                        url=live.webui_url,
                    )
                )
                continue

        action = UPDATED if live is not None else CREATED
        url = ""
        if not dry_run:
            url = export_to_confluence(doc.body, space=doc.space, title=doc.page_title, host=host)

        report.results.append(
            _OldPublishResult(
                path=doc.relative_path,
                space=doc.space,
                title=doc.page_title,
                action=action,
                reason=reason,
                url=url or (live.webui_url if live else ""),
            )
        )

    return report


@dataclass(frozen=True)
class _OldDriftResult:
    path: str
    space: str
    title: str
    tier: str
    state: str
    reason: str = ""
    url: str = ""

    @property
    def reconcile(self) -> str:
        return (
            # **This frozen copy moved with the live one, deliberately.**
            # Both sides interpolated a title inside hand-written quotes,
            # so `Vendor "Bring Your Own" Policy` produced a command naming
            # the wrong document. The equivalence test exists to catch
            # UNINTENDED divergence between the two implementations; a
            # defect present in both and fixed in both is not that. The fix
            # itself is covered by tests/test_printed_commands_run.py. #187.
            f"policyforge pull --space {shlex.quote(self.space)} "
            f"--title {shlex.quote(self.title)} "
            f"--tier {self.tier or 'standard'} --apply"
        )


@dataclass
class _OldDriftReport:
    results: list[_OldDriftResult] = field(default_factory=list)
    undeclared: int = 0

    def _of(self, state: str) -> list[_OldDriftResult]:
        return [r for r in self.results if r.state == state]

    @property
    def moved(self) -> list[_OldDriftResult]:
        return self._of(MOVED)

    @property
    def in_sync(self) -> list[_OldDriftResult]:
        return self._of(IN_SYNC)

    @property
    def absent(self) -> list[_OldDriftResult]:
        return self._of(ABSENT)

    def format_report(self) -> str:
        lines = [
            f"{len(self.moved)} page(s) changed on the wiki since this tool last "
            f"published them; {len(self.in_sync)} in sync."
        ]
        for result in self.moved:
            lines += [
                "",
                f"  ~ {result.space}/{result.title}",
                f"      {result.reason}",
                f"      repo: {result.path}",
                f"      reconcile: {result.reconcile}",
            ]
            if result.url:
                lines.append(f"      {result.url}")

        if self.absent:
            lines += ["", f"Declared but not published yet ({len(self.absent)}):"]
            lines += [f"  {r.path} -> {r.space}/{r.title}" for r in self.absent]

        if self.undeclared:
            lines += [
                "",
                f"{self.undeclared} document(s) declare no `confluence:` block and were "
                "not looked up.",
            ]

        if not self.moved:
            lines += ["", "Nothing to reconcile."]
        return "\n".join(lines)


def _old_wiki_drift(root: Path, *, host: str, only: str = "") -> _OldDriftReport:
    from policyforge.content.tree import load_content_tree
    from policyforge.export.confluence_importer import fetch_confluence_page

    documents, _ = load_content_tree(root)
    report = _OldDriftReport()

    for doc in documents:
        if only and only not in doc.relative_path:
            continue
        if not doc.space:
            report.undeclared += 1
            continue

        try:
            live = fetch_confluence_page(space=doc.space, title=doc.page_title, host=host)
        except LookupError:
            report.results.append(
                _OldDriftResult(
                    path=doc.relative_path,
                    space=doc.space,
                    title=doc.page_title,
                    tier=doc.tier,
                    state=ABSENT,
                )
            )
            continue

        reason = _old_moved_since_last_publish(doc, live)
        if reason and _old_unchanged_on_the_wiki(doc, live):
            reason = ""
        report.results.append(
            _OldDriftResult(
                path=doc.relative_path,
                space=doc.space,
                title=doc.page_title,
                tier=doc.tier,
                state=MOVED if reason else IN_SYNC,
                reason=reason,
                url=live.webui_url,
            )
        )

    return report


@dataclass
class _OldPullResult:
    title: str
    path: str
    action: str
    reason: str = ""


@dataclass
class _OldPullReport:
    results: list[_OldPullResult] = field(default_factory=list)
    dry_run: bool = True

    def _of(self, action: str) -> list[_OldPullResult]:
        return [r for r in self.results if r.action == action]

    @property
    def refused(self) -> list[_OldPullResult]:
        return self._of(REFUSED)

    def format_report(self) -> str:
        written = self._of(WRITTEN)
        unchanged = self._of(UNCHANGED)
        verb = "Would write" if self.dry_run else "Wrote"
        lines = [f"{verb} {len(written)} file(s); {len(unchanged)} already matched the tree."]
        lines += [f"  {r.title} -> {r.path}" for r in written]

        if self.refused:
            lines += ["", f"Refused {len(self.refused)}:"]
            lines += [f"  {r.title}: {r.reason}" for r in self.refused]
            lines += [
                "",
                "These pages read fine but would be damaged by a publish, so they are "
                "not brought into the tree. Rewrite them in Confluence without those "
                "macros, or keep them wiki-only.",
            ]

        if self.dry_run and written:
            lines += ["", "Nothing was written. Pass --apply to write these files."]
        return "\n".join(lines)


_OLD_TIER_DIR = {tier: directory for directory, tier in TIER_DIRS.items()}


def _old_target_path(root: Path, *, tier: str, slug: str) -> Path:
    directory = _OLD_TIER_DIR.get(tier)
    return (root / directory / f"{slug}.md") if directory else (root / f"{slug}.md")


def _old_pull_pages(
    pages: list[tuple[str, str, str]],
    *,
    root: Path,
    host: str,
    dry_run: bool = True,
    allow_macros: bool = False,
) -> _OldPullReport:
    from policyforge.edit.apply import detect_unsupported_macros
    from policyforge.export.confluence_importer import (
        confluence_to_markdown,
        extract_user_ids,
        fetch_confluence_page,
    )
    from policyforge.export.confluence_search import fetch_user_names
    from policyforge.zardoz.corpus import slugify

    report = _OldPullReport(dry_run=dry_run)

    for space, title, tier in pages:
        try:
            page = fetch_confluence_page(space=space, title=title, host=host)
        except LookupError as exc:
            report.results.append(
                _OldPullResult(title=title, path="", action=REFUSED, reason=str(exc))
            )
            continue

        macros = detect_unsupported_macros(page.storage_body)
        if macros and not allow_macros:
            report.results.append(
                _OldPullResult(
                    title=title,
                    path="",
                    action=REFUSED,
                    reason=f"uses macros that would not survive a publish ({', '.join(macros)})",
                )
            )
            continue

        names = fetch_user_names(extract_user_ids(page.storage_body), host=host)
        body = confluence_to_markdown(page.storage_body, user_names=names)
        slug = slugify(title) or slugify(page.id)
        destination = _old_target_path(root, tier=tier, slug=slug)

        rendered = render_document(
            {
                "title": title,
                "tier": tier,
                "confluence": {
                    "space": space,
                    "title": title,
                    "page_id": page.id,
                    "version": page.version,
                },
            },
            body,
        )
        relative = destination.relative_to(root).as_posix()

        rendered = normalise_newlines(rendered)
        if destination.exists() and destination.read_text(encoding="utf-8") == rendered:
            report.results.append(_OldPullResult(title=title, path=relative, action=UNCHANGED))
            continue

        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            write_text_lf(destination, rendered)
        report.results.append(_OldPullResult(title=title, path=relative, action=WRITTEN))

    return report


# ==========================================================================
# the scenarios: every situation the content/git tests exercise
# ==========================================================================

ACS = "Access Control Standard"
STAMPED_PLAIN = FakePage(id="id-acs", title=ACS, storage_body=PLAIN_PAGE, version=3)
REFLOWED = "reflowed"  # placeholder resolved against the tree at run time


def _tree_with(*files):
    """A tree builder: (relative path, text) pairs, or a callable given the root."""

    def build(root):
        for relative, text in files:
            _write(root, relative, text)

    return build


def _reflowed_legacy(root):
    return _legacy(_as_published(root).replace("><", ">\n    <").replace("\n", "\n    "))


PUBLISH_SCENARIOS = {
    "new page, dry run": (_tree_with(("standards/a.md", _bound(ACS))), {}, {}),
    "new page, applied": (_tree_with(("standards/a.md", _bound(ACS))), {}, {"dry_run": False}),
    "existing stamped page": (
        _tree_with(("standards/a.md", _bound(ACS))),
        {ACS: STAMPED_PLAIN},
        {"dry_run": False},
    ),
    "macros skipped": (
        _tree_with(("standards/a.md", _bound(ACS))),
        {ACS: MACRO_PAGE},
        {"dry_run": False},
    ),
    "macros allowed": (
        _tree_with(("standards/a.md", _bound(ACS))),
        {ACS: MACRO_PAGE},
        {"dry_run": False, "allow_macros": True},
    ),
    "hand edit is moved": (
        _tree_with(("standards/a.md", _bound(ACS))),
        {ACS: HAND_EDITED},
        {"dry_run": False},
    ),
    "hand edit forced": (
        _tree_with(("standards/a.md", _bound(ACS))),
        {ACS: HAND_EDITED},
        {"dry_run": False, "force": True},
    ),
    "hand edit already pulled": (
        _tree_with(("standards/a.md", _bound(ACS, version=5))),
        {ACS: HAND_EDITED},
        {"dry_run": False},
    ),
    "pulled version behind": (
        _tree_with(("standards/a.md", _bound(ACS, version=4))),
        {ACS: HAND_EDITED},
        {},
    ),
    "pulled version unparseable": (
        _tree_with(("standards/a.md", _bound(ACS, version="five"))),
        {ACS: HAND_EDITED},
        {},
    ),
    "adopted from before the stamp": (
        _tree_with(("standards/a.md", _bound(ACS))),
        lambda root: {ACS: _legacy(_as_published(root))},
        {"dry_run": False},
    ),
    "adopted through a reflow": (
        _tree_with(("standards/a.md", _bound(ACS))),
        lambda root: {ACS: _reflowed_legacy(root)},
        {},
    ),
    "undeclared and only": (
        _tree_with(
            ("standards/a.md", _bound(ACS)),
            ("policies/p.md", _bound("Access Policy")),
            ("standards/draft.md", "---\ntitle: Draft\n---\n\n# Draft\n\nnot yet\n"),
        ),
        {},
        {"only": "policies/"},
    ),
    "broken frontmatter and a moved page together": (
        _tree_with(
            ("standards/a.md", _bound(ACS)),
            ("standards/b.md", _bound("Backup Standard")),
            ("standards/broken.md", "---\ntitle: [unclosed\n---\n\n# Broken\n"),
        ),
        {ACS: HAND_EDITED, "Backup Standard": PLAIN_PAGE},
        {"dry_run": False},
    ),
}


def _run_both(loop_old, loop_new, build, pages, kwargs, tmp_path, monkeypatch):
    """Build the same tree twice, fake the same wiki twice, run each loop."""
    outcomes = []
    for loop, name in ((loop_old, "old"), (loop_new, "new")):
        root = tmp_path / name
        root.mkdir()
        build(root)
        store = pages(root) if callable(pages) else pages
        exported: list = []
        _patch_confluence(monkeypatch, pages=store, exported=exported)
        outcomes.append((loop(root, host="https://x", **kwargs), exported))
    return outcomes


@pytest.mark.parametrize("name", list(PUBLISH_SCENARIOS))
def test_publish_produces_the_same_report_and_the_same_exporter_calls(name, tmp_path, monkeypatch):
    build, pages, kwargs = PUBLISH_SCENARIOS[name]

    (before, exported_before), (after, exported_after) = _run_both(
        _old_publish_tree, publish_tree, build, pages, kwargs, tmp_path, monkeypatch
    )

    def rows(report):
        return [(r.path, r.space, r.title, r.action, r.reason, r.url) for r in report.results]

    # The paths are relative to each root, so the two reports compare
    # field by field: every result, the dry-run flag, the undeclared count,
    # and the formatted report — the only assertion over what a user reads,
    # and the one that must come from a genuinely old formatter.
    assert rows(after) == rows(before)
    assert after.dry_run == before.dry_run
    assert after.undeclared == before.undeclared
    assert exported_after == exported_before, "the exporter must see identical calls"
    assert after.format_report() == before.format_report()


@pytest.mark.parametrize("name", list(PUBLISH_SCENARIOS))
def test_drift_produces_the_same_report(name, tmp_path, monkeypatch):
    build, pages, kwargs = PUBLISH_SCENARIOS[name]
    only = {"only": kwargs["only"]} if "only" in kwargs else {}

    (before, _), (after, _) = _run_both(
        _old_wiki_drift, wiki_drift, build, pages, only, tmp_path, monkeypatch
    )

    def rows(report):
        return [
            (r.path, r.space, r.title, r.tier, r.state, r.reason, r.url, r.reconcile)
            for r in report.results
        ]

    assert rows(after) == rows(before)
    assert after.undeclared == before.undeclared
    assert after.format_report() == before.format_report()


PULL_SCENARIOS = {
    "a plain page": ([("SEC", ACS, "standard")], {ACS: PLAIN_PAGE}, {"dry_run": False}),
    "a plain page, dry run": ([("SEC", ACS, "standard")], {ACS: PLAIN_PAGE}, {}),
    "a hand-edited page records its version": (
        [("SEC", ACS, "policy")],
        {ACS: HAND_EDITED},
        {"dry_run": False},
    ),
    "macros refused": ([("SEC", "Runbook", "procedure")], {"Runbook": MACRO_PAGE}, {}),
    "macros allowed": (
        [("SEC", "Runbook", "procedure")],
        {"Runbook": MACRO_PAGE},
        {"dry_run": False, "allow_macros": True},
    ),
    "a missing page": ([("SEC", "Nowhere", "standard")], {}, {}),
    "no tier": ([("SEC", ACS, "")], {ACS: PLAIN_PAGE}, {"dry_run": False}),
}


@pytest.mark.parametrize("name", list(PULL_SCENARIOS))
def test_pull_writes_the_same_files_and_reports_the_same_results(name, tmp_path, monkeypatch):
    targets, pages, kwargs = PULL_SCENARIOS[name]
    outcomes = []
    for loop, label in ((_old_pull_pages, "old"), (pull_pages, "new")):
        root = tmp_path / label
        root.mkdir()
        _patch_confluence(monkeypatch, pages=pages)
        first = loop(targets, root=root, host="https://x", **kwargs)
        second = loop(targets, root=root, host="https://x", **kwargs)
        files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*.md"))}
        outcomes.append((first, second, files))

    (old_first, old_second, old_files), (new_first, new_second, new_files) = outcomes

    def rows(report):
        return [(r.title, r.path, r.action, r.reason) for r in report.results]

    # Compared the way publish is compared: every result, the dry-run flag
    # that decides what the user is told, and the formatted report itself.
    for new_report, old_report in ((new_first, old_first), (new_second, old_second)):
        assert rows(new_report) == rows(old_report)
        assert new_report.dry_run == old_report.dry_run
        assert new_report.format_report() == old_report.format_report()
    assert new_files == old_files, "byte-identical files, binding and all"


def test_the_pulled_binding_still_uses_the_top_level_spelling(tmp_path, monkeypatch):
    """A pull writes `confluence:` at the top level, as it always has, so a
    tree pulled by this version reads the same under the previous one."""
    _patch_confluence(monkeypatch, pages={ACS: HAND_EDITED})

    pull_pages([("SEC", ACS, "standard")], root=tmp_path, host="https://x", dry_run=False)

    text = (tmp_path / "standards" / "access-control-standard.md").read_text(encoding="utf-8")
    assert "\nconfluence:\n" in text
    assert "targets:" not in text
    assert "version: 5" in text


# ==========================================================================
# the Confluence adapter's own comparison
# ==========================================================================


def _page(body, **kwargs):
    return ConfluencePublisher.live_page(FakePage(id="id", title=ACS, storage_body=body, **kwargs))


def test_a_reflow_on_confluence_is_not_an_edit(tmp_path):
    """Confluence may reflow storage format when it saves. That is not a
    change anybody made, so it must not count as one — this is the rule
    the adapter owns, and the reason `LivePage` carries the native body."""
    _write(tmp_path, "standards/a.md", _bound(ACS))
    (doc,), _ = load_content_tree(tmp_path)
    published = _as_published(tmp_path)
    reflowed = published.replace("><", ">\n    <").replace("\n", "\n    ")

    adapter = ConfluencePublisher(host="https://x")

    assert adapter.same_content(doc, _page(published))
    assert adapter.same_content(doc, _page(reflowed))
    assert not adapter.same_content(doc, _page(published.replace("quarterly", "monthly")))


def test_the_adapter_reads_the_stamp_and_the_macros_into_the_live_page():
    stamped = _page(PLAIN_PAGE, version=7, version_message=PUBLISH_MARKER)
    edited = _page(MACRO_PAGE, version=8, version_message="Tidied")

    assert stamped == LivePage(
        version="7",
        written_by_tool=True,
        url="https://x/wiki/page",
        body=PLAIN_PAGE,
        page_id="id",
    )
    assert edited.written_by_tool is False
    assert edited.version == "8"
    assert edited.unsupported == ("info",)


def test_the_recorded_version_is_read_as_confluence_always_read_it(tmp_path):
    adapter = ConfluencePublisher(host="https://x")
    _write(tmp_path, "standards/a.md", _bound(ACS, version=5))
    _write(tmp_path, "standards/b.md", _bound("B", version="five"))
    _write(tmp_path, "standards/c.md", _bound("C"))
    docs = {d.title: d for d in load_content_tree(tmp_path)[0]}

    assert adapter.recorded_version(docs[ACS]) == "5"
    assert adapter.recorded_version(docs["B"]) is None
    assert adapter.recorded_version(docs["C"]) is None


# ==========================================================================
# `targets:` and its alias
# ==========================================================================

GENERAL = (
    "---\ntitle: Access Control Standard\n"
    "targets:\n  confluence:\n    space: SEC\n    title: Access Control Standard\n"
    "    version: 4\n---\n\n# Access Control Standard\n\nReviews happen quarterly.\n"
)


def test_the_general_spelling_is_read_as_the_confluence_block(tmp_path):
    _write(tmp_path, "standards/a.md", GENERAL)
    (doc,), _ = load_content_tree(tmp_path)

    assert doc.targets == {"confluence": {"space": "SEC", "title": ACS, "version": 4}}
    assert doc.target("confluence") == doc.confluence
    assert doc.space == "SEC"
    assert doc.page_title == ACS
    assert doc.target("github_wiki") == {}


def test_the_top_level_spelling_appears_under_targets(tmp_path):
    _write(tmp_path, "standards/a.md", _bound(ACS, version=5))
    (doc,), _ = load_content_tree(tmp_path)

    assert doc.targets == {"confluence": {"space": "SEC", "title": ACS, "version": 5}}


def test_a_document_declared_the_general_way_publishes_like_the_old_way(tmp_path, monkeypatch):
    exported = []
    _patch_confluence(monkeypatch, pages={ACS: HAND_EDITED}, exported=exported)
    _write(tmp_path, "standards/a.md", GENERAL)

    report = publish_tree(tmp_path, host="https://x")

    assert [r.action for r in report.results] == [MOVED]
    assert "last pulled version 4" in report.moved[0].reason


def test_both_spellings_agreeing_is_fine(tmp_path):
    _write(
        tmp_path,
        "standards/a.md",
        "---\ntitle: A\nconfluence: {space: SEC, title: A}\n"
        "targets:\n  confluence: {space: SEC, title: A}\n---\n\n# A\n\nbody\n",
    )

    assert check_tree(tmp_path).errors == []


def test_both_spellings_disagreeing_is_an_error(tmp_path):
    _write(
        tmp_path,
        "standards/a.md",
        "---\ntitle: A\nconfluence: {space: SEC, title: A}\n"
        "targets:\n  confluence: {space: ENG, title: A}\n---\n\n# A\n\nbody\n",
    )

    report = check_tree(tmp_path)

    assert len(report.errors) == 1
    assert "different contents" in report.errors[0].message


def test_a_topic_declared_the_general_way_lists_the_same_pages():
    from policyforge.topics.registry import parse_topics

    topics = parse_topics(
        {
            "topics": [
                {
                    "name": "Access",
                    "owner": "IAM",
                    "targets": {
                        "confluence": {
                            "space": "SEC",
                            "pages": {"procedure": "P", "policy": "Pol", "standard": "S"},
                        }
                    },
                }
            ]
        }
    )

    topic = topics[0]
    assert topic.confluence == {}
    assert topic.target("confluence")["space"] == "SEC"
    assert topic.pages("confluence") == [("policy", "Pol"), ("standard", "S"), ("procedure", "P")]
    assert topic.confluence_pages() == topic.pages("confluence")
    assert topic.pages("github_wiki") == []


def test_the_top_level_topic_block_wins_over_the_general_one():
    from policyforge.topics.registry import Topic

    topic = Topic(
        name="Access",
        owner="IAM",
        confluence={"space": "SEC", "pages": {"standard": "S"}},
        targets={"confluence": {"space": "ENG", "pages": {"standard": "Other"}}},
    )

    assert topic.confluence_pages() == [("standard", "S")]


def test_a_file_bound_to_a_page_of_another_kind_is_selected(tmp_path):
    """The edit tree's bound-to-page rule reads every declared kind, so a
    file pulled from a store other than Confluence is found the same way."""
    from policyforge.edit.tree import select_topic_files
    from policyforge.topics.registry import Topic

    _write(
        tmp_path,
        "standards/pulled.md",
        "---\ntitle: Pulled\ntargets:\n  github_wiki: {repository: acme/wiki, title: S}\n"
        "---\n\n# S\n\nbody\n",
    )
    topic = Topic(
        name="Access",
        owner="IAM",
        targets={"github_wiki": {"repository": "acme/wiki", "pages": {"standard": "S"}}},
    )

    selection = select_topic_files(tmp_path, topic)

    assert selection.ambiguous == {}
    assert [(tier, f.relative) for tier, f in selection.files.items()] == [
        ("standard", "standards/pulled.md")
    ]
    assert selection.files["standard"].matched_by == "bound to page 'S'"
