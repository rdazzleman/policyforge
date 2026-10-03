"""The GitHub-wiki adapter, against a local bare repository.

No network anywhere in this file. A wiki is a git repository, so a real
one — `git init --bare`, seeded through a temporary clone — is a complete
test double: the same commits, the same trailers, the same `git log`
output the adapter reads in production. Nothing here fakes git, which is
the point: the parts most likely to be wrong are the exact arguments and
what git does with them.

The one thing that is faked is GitHub's visibility answer, because it is
the only fact the adapter gets from the API rather than from the clone.
"""

from __future__ import annotations

import subprocess  # nosec B404 - argv only, test fixture
from pathlib import Path

import pytest

from policyforge.content.tree import load_content_tree
from policyforge.export.github_wiki import (
    PUBLISH_MARKER,
    GitHubWikiPublisher,
    page_filename,
)
from policyforge.export.publisher import (
    ABSENT,
    CREATED,
    IN_SYNC,
    MOVED,
    SKIPPED,
    UPDATED,
    drift_documents,
    publish_documents,
)

TITLE = "Access Review Standard"
BODY = "## Purpose\n\nEntitlements are recertified quarterly. [NIST AC-2]\n"


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 B607 - fixed argv, no shell
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


@pytest.fixture
def remote(tmp_path) -> Path:
    """A bare repository standing in for `<owner>/<repo>.wiki.git`."""
    bare = tmp_path / "wiki.git"
    bare.mkdir()
    git("init", "--bare", "--initial-branch=master", cwd=bare)
    return bare


def seed(remote: Path, tmp_path, pages: dict[str, str], *, message: str = "Seed") -> str:
    """Put pages on the wiki the way a person would, and return the sha."""
    clone = tmp_path / "seed"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    for title, body in pages.items():
        (clone / page_filename(title)).write_text(body, encoding="utf-8", newline="\n")
    git("add", "-A", cwd=clone)
    git(
        "-c",
        "user.name=Dana Okafor",
        "-c",
        "user.email=dana@example.com",
        "commit",
        "-m",
        message,
        cwd=clone,
    )
    git("push", "origin", "HEAD:refs/heads/master", cwd=clone)
    sha = git("rev-parse", "HEAD", cwd=clone).stdout.strip()
    return sha


def tree(tmp_path, *, frontmatter: str = "", body: str = BODY, name: str = "access-review.md"):
    """A content tree with one standard carrying a wiki target."""
    root = tmp_path / "content"
    (root / "standards").mkdir(parents=True, exist_ok=True)
    front = frontmatter or (
        f"---\ntitle: {TITLE}\ntargets:\n  github_wiki:\n    title: {TITLE}\n---\n\n"
    )
    (root / "standards" / name).write_text(front + body, encoding="utf-8", newline="\n")
    documents, problems = load_content_tree(root)
    return root, documents, problems


def publisher(remote: Path, tmp_path, **kwargs) -> GitHubWikiPublisher:
    return GitHubWikiPublisher(
        "acme/policies",
        workdir=tmp_path / "output" / ".wiki" / "acme-policies",
        remote=str(remote),
        public=False,
        **kwargs,
    )


# --------------------------------------------------------------------------
# creating and updating
# --------------------------------------------------------------------------


def test_a_page_that_does_not_exist_yet_is_created_and_stamped(remote, tmp_path):
    _, documents, problems = tree(tmp_path)
    wiki = publisher(remote, tmp_path)

    report = publish_documents(wiki, documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [CREATED]
    clone = tmp_path / "read-back"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    assert (clone / "Access-Review-Standard.md").read_text(encoding="utf-8") == BODY
    message = git("log", "-1", "--format=%B", cwd=clone).stdout
    assert PUBLISH_MARKER in message
    assert "standards/access-review.md" in message


def test_a_dry_run_reads_the_wiki_and_writes_nothing(remote, tmp_path):
    _, documents, problems = tree(tmp_path)
    wiki = publisher(remote, tmp_path)

    report = publish_documents(wiki, documents, problems, dry_run=True)

    assert [r.action for r in report.results] == [CREATED]
    clone = tmp_path / "read-back"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    assert not (clone / "Access-Review-Standard.md").exists()
    assert git("rev-list", "--count", "--all", cwd=clone).stdout.strip() == "0"


def test_the_tools_own_page_is_updated_without_complaint(remote, tmp_path):
    _, documents, problems = tree(tmp_path)
    wiki = publisher(remote, tmp_path)
    publish_documents(wiki, documents, problems, dry_run=False)

    _, changed, problems2 = tree(
        tmp_path, body=BODY.replace("quarterly", "every 90 days"), name="access-review.md"
    )
    again = publish_documents(publisher(remote, tmp_path), changed, problems2, dry_run=False)

    assert [r.action for r in again.results] == [UPDATED]
    clone = tmp_path / "read-back-2"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    assert "every 90 days" in (clone / "Access-Review-Standard.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the moved guard
# --------------------------------------------------------------------------


def test_a_hand_edit_on_the_wiki_is_reported_with_who_made_it(remote, tmp_path):
    """The guard's whole purpose: an edit nobody in the repository has seen
    is not overwritten, and the report says who to go and ask."""
    seed(
        remote,
        tmp_path,
        {TITLE: "## Purpose\n\nSomeone else wrote this.\n"},
        message="Fix the cadence",
    )
    _, documents, problems = tree(tmp_path)

    report = publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [MOVED]
    reason = report.results[0].reason
    assert "Dana Okafor" in reason, reason
    assert "last written by" in reason

    clone = tmp_path / "read-back"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    assert "Someone else wrote this." in (clone / page_filename(TITLE)).read_text(encoding="utf-8")


def test_force_overwrites_a_moved_page(remote, tmp_path):
    seed(remote, tmp_path, {TITLE: "## Purpose\n\nSomeone else wrote this.\n"})
    _, documents, problems = tree(tmp_path)

    report = publish_documents(
        publisher(remote, tmp_path), documents, problems, dry_run=False, force=True
    )

    assert [r.action for r in report.results] == [UPDATED]


def test_a_recorded_commit_makes_the_page_safe_to_overwrite(remote, tmp_path):
    """What `pull` writes into frontmatter: this edit has been seen."""
    sha = seed(remote, tmp_path, {TITLE: "## Purpose\n\nSomeone else wrote this.\n"})
    front = (
        f"---\ntitle: {TITLE}\ntargets:\n  github_wiki:\n"
        f"    title: {TITLE}\n    commit: {sha}\n---\n\n"
    )
    _, documents, problems = tree(tmp_path, frontmatter=front)

    report = publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [UPDATED]


def test_a_byte_difference_on_the_wiki_is_a_change(remote, tmp_path):
    """The mirror of the Confluence reflow case, and the opposite answer.

    Confluence reflows storage format on save, so its adapter forgives
    whitespace between tags. A git-backed wiki changes nothing on its own:
    every byte that differs was changed by somebody, so a single space is
    an edit and the page is not overwritten.
    """
    seed(remote, tmp_path, {TITLE: BODY.replace("quarterly", "quarterly ")})
    _, documents, problems = tree(tmp_path)
    wiki = publisher(remote, tmp_path)

    report = publish_documents(wiki, documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [MOVED]


def test_an_identical_page_is_adopted_rather_than_reported(remote, tmp_path):
    """Nothing to lose: the page already says what the repository says."""
    seed(remote, tmp_path, {TITLE: BODY})
    _, documents, problems = tree(tmp_path)

    report = publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [UPDATED]
    assert "adopted" in report.results[0].reason


# --------------------------------------------------------------------------
# formats this tool cannot round-trip
# --------------------------------------------------------------------------


def test_a_page_in_another_format_is_refused_by_name(remote, tmp_path):
    """A wiki's analogue of a macro: AsciiDoc would not survive the round
    trip, and publishing markdown beside it would replace somebody's page."""
    clone = tmp_path / "seed"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    (clone / "Access-Review-Standard.asciidoc").write_text("= Title\n", encoding="utf-8")
    git("add", "-A", cwd=clone)
    git(
        "-c",
        "user.name=Dana",
        "-c",
        "user.email=d@example.com",
        "commit",
        "-m",
        "AsciiDoc",
        cwd=clone,
    )
    git("push", "origin", "HEAD:refs/heads/master", cwd=clone)

    _, documents, problems = tree(tmp_path)
    report = publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [SKIPPED]
    assert ".asciidoc" in report.results[0].reason


# --------------------------------------------------------------------------
# a public wiki
# --------------------------------------------------------------------------


def _licensed_tree(tmp_path, *, stamped: bool, value: str = "licensed"):
    block = (
        f'generated_by:\n  content_class: "{value}"\n' if stamped else f'content_class: "{value}"\n'
    )
    front = f"---\ntitle: {TITLE}\n{block}targets:\n  github_wiki:\n    title: {TITLE}\n---\n\n"
    return tree(tmp_path, frontmatter=front)


@pytest.mark.parametrize("stamped", [False, True])
@pytest.mark.parametrize("value", ["licensed", "LICENSED", " licensed ", "licensed\t"])
def test_a_licensed_class_is_read_however_it_was_typed(remote, tmp_path, stamped, value):
    """Frontmatter is hand-editable, so the value arrives however somebody
    typed it. A trailing space is not a different content class, and the
    ceiling must not turn on one — this refused nothing before the value was
    stripped, which meant licensed text reached a public wiki because of
    whitespace."""
    _, documents, problems = _licensed_tree(tmp_path, stamped=stamped, value=value)
    wiki = publisher(remote, tmp_path)
    wiki.public = True
    wiki.allow_public = True

    report = publish_documents(wiki, documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [SKIPPED]
    assert "licensed content is never published to a public wiki" in report.results[0].reason


def test_an_unrecognised_class_is_not_treated_as_licensed(remote, tmp_path):
    """The stated asymmetry, pinned so it stays a decision rather than
    becoming an accident. Unknown *visibility* counts as public; an unknown
    *content class* counts as not licensed, because most documents carry no
    class and refusing them all would get the check turned off. What stands
    between such a document and the world is the visibility rule."""
    _, documents, problems = _licensed_tree(tmp_path, stamped=False, value="licensed-content")
    wiki = publisher(remote, tmp_path)
    wiki.public = True
    wiki.allow_public = True

    report = publish_documents(wiki, documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [CREATED]


@pytest.mark.parametrize("stamped", [False, True])
def test_licensed_content_never_reaches_a_public_wiki(remote, tmp_path, stamped):
    """Whatever --allow-public says. The flag is the operator accepting a
    public audience for their own documents; it says nothing about somebody
    else's licensed text, so it cannot lift this ceiling."""
    _, documents, problems = _licensed_tree(tmp_path, stamped=stamped)
    wiki = publisher(remote, tmp_path)
    wiki.public = True
    wiki.allow_public = True

    report = publish_documents(wiki, documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [SKIPPED]
    assert "licensed content is never published to a public wiki" in report.results[0].reason


def test_licensed_content_publishes_to_a_private_wiki(remote, tmp_path):
    _, documents, problems = _licensed_tree(tmp_path, stamped=True)

    report = publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [CREATED]


def test_unknown_visibility_is_treated_as_public(remote, tmp_path):
    """Fail closed, the direction `llm/boundary.py` already takes: an
    unanswered question about who can read this is not a yes."""
    _, documents, problems = _licensed_tree(tmp_path, stamped=True)
    wiki = publisher(remote, tmp_path)
    wiki.public = None

    report = publish_documents(wiki, documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [SKIPPED]
    assert "PUBLIC" in report.header
    assert "could not be determined" in report.header


def test_the_plan_says_the_visibility_on_its_first_line(remote, tmp_path):
    _, documents, problems = tree(tmp_path)
    wiki = publisher(remote, tmp_path)
    wiki.public = True

    report = publish_documents(wiki, documents, problems, dry_run=True)

    assert report.format_report().splitlines()[0].startswith("target wiki: PUBLIC")


# --------------------------------------------------------------------------
# cross-references
# --------------------------------------------------------------------------


def _two_documents(tmp_path, *, both_published: bool = True):
    root = tmp_path / "content"
    (root / "standards").mkdir(parents=True, exist_ok=True)
    (root / "procedures").mkdir(parents=True, exist_ok=True)
    (root / "standards" / "access-review.md").write_text(
        f"---\ntitle: {TITLE}\ntargets:\n  github_wiki:\n    title: {TITLE}\n---\n\n"
        "See [the procedure](../procedures/quarterly-review.md).\n",
        encoding="utf-8",
        newline="\n",
    )
    target = (
        "targets:\n  github_wiki:\n    title: Quarterly Review Procedure\n"
        if both_published
        else "confluence:\n  space: SEC\n"
    )
    (root / "procedures" / "quarterly-review.md").write_text(
        f"---\ntitle: Quarterly Review Procedure\n{target}---\n\nSteps.\n",
        encoding="utf-8",
        newline="\n",
    )
    return load_content_tree(root)


def test_a_link_between_two_published_documents_becomes_a_wiki_link(remote, tmp_path):
    documents, problems = _two_documents(tmp_path)
    wiki = publisher(remote, tmp_path)

    publish_documents(wiki, documents, problems, dry_run=False)

    clone = tmp_path / "read-back"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    page = (clone / page_filename(TITLE)).read_text(encoding="utf-8")
    assert "[[the procedure|Quarterly Review Procedure]]" in page
    assert "../procedures" not in page


def test_a_link_to_a_document_with_no_wiki_page_is_left_alone_and_said_once(remote, tmp_path):
    documents, problems = _two_documents(tmp_path, both_published=False)
    wiki = publisher(remote, tmp_path)

    report = publish_documents(wiki, documents, problems, dry_run=False)

    clone = tmp_path / "read-back"
    git("clone", str(remote), str(clone), cwd=tmp_path)
    page = (clone / page_filename(TITLE)).read_text(encoding="utf-8")
    assert "[the procedure](../procedures/quarterly-review.md)" in page
    assert len([n for n in report.notes if "quarterly-review.md" in n]) == 1


def test_a_wiki_link_comes_back_as_a_tree_path(remote, tmp_path):
    """The other half of the round trip, so a pulled page is a tree file
    again rather than one carrying wiki syntax nobody else understands."""
    documents, _ = _two_documents(tmp_path)
    wiki = publisher(remote, tmp_path)
    wiki.prepare(documents)

    live = type("L", (), {"body": "See [[the procedure|Quarterly Review Procedure]].\n"})()

    assert wiki.as_markdown(live) == "See [the procedure](procedures/quarterly-review.md).\n"


# --------------------------------------------------------------------------
# drift, and what pull records
# --------------------------------------------------------------------------


def test_drift_reports_absent_then_in_sync_then_moved(remote, tmp_path):
    _, documents, problems = tree(tmp_path)

    absent = drift_documents(publisher(remote, tmp_path), documents)
    assert [r.state for r in absent.results] == [ABSENT]
    assert "--target github-wiki" in absent.results[0].reconcile

    publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)
    assert [r.state for r in drift_documents(publisher(remote, tmp_path), documents).results] == [
        IN_SYNC
    ]

    seed(remote, tmp_path, {TITLE: "## Purpose\n\nEdited by hand.\n"}, message="By hand")
    moved = drift_documents(publisher(remote, tmp_path), documents)
    assert [r.state for r in moved.results] == [MOVED]


def test_the_binding_a_pull_writes_carries_the_commit(remote, tmp_path):
    sha = seed(remote, tmp_path, {TITLE: BODY})
    wiki = publisher(remote, tmp_path)

    live = wiki.fetch("acme/policies", TITLE)
    binding = wiki.binding("acme/policies", TITLE, live)

    assert binding["targets"]["github_wiki"]["commit"] == sha
    assert binding["targets"]["github_wiki"]["title"] == TITLE
    assert live.version == sha


# --------------------------------------------------------------------------
# where a document goes
# --------------------------------------------------------------------------


def test_a_file_declaring_no_wiki_block_is_left_alone(remote, tmp_path):
    root = tmp_path / "content"
    (root / "standards").mkdir(parents=True)
    (root / "standards" / "draft.md").write_text(
        f"---\ntitle: {TITLE}\n---\n\n{BODY}", encoding="utf-8", newline="\n"
    )
    documents, problems = load_content_tree(root)

    report = publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)

    assert report.results == []
    assert report.undeclared == 1
    assert "github_wiki" in report.undeclared_note


def test_an_empty_block_publishes_to_the_configured_wiki(remote, tmp_path):
    """Declaring intent is carrying the block; the repository is config's
    to supply, so a tree does not repeat it in all 59 files."""
    front = f"---\ntitle: {TITLE}\ntargets:\n  github_wiki: {{}}\n---\n\n"
    _, documents, problems = tree(tmp_path, frontmatter=front)

    report = publish_documents(publisher(remote, tmp_path), documents, problems, dry_run=False)

    assert [r.action for r in report.results] == [CREATED]
    assert report.results[0].space == "acme/policies"
    assert report.results[0].title == TITLE


def test_a_block_may_override_the_configured_repository(remote, tmp_path):
    front = (
        f"---\ntitle: {TITLE}\ntargets:\n  github_wiki:\n"
        f"    repository: acme/other\n    title: {TITLE}\n---\n\n"
    )
    _, documents, _ = tree(tmp_path, frontmatter=front)

    assert publisher(remote, tmp_path).location(documents[0]) == "acme/other"


def test_the_working_clone_lives_under_output_wiki(remote, tmp_path):
    """Gitignored, and outside the Docker build context, so a wiki clone
    never lands in a tracked tree or in an image."""
    _, documents, problems = tree(tmp_path)
    wiki = publisher(remote, tmp_path)

    publish_documents(wiki, documents, problems, dry_run=False)

    assert wiki.workdir == tmp_path / "output" / ".wiki" / "acme-policies"
    assert (wiki.workdir / ".git").is_dir()
