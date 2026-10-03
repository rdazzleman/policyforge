"""Two gate checks for defects the rest of the gate only caught by accident.

CRLF: a conflict-resolution script using `Path.write_text` on Windows
converted 1,768 lines of CHANGELOG.md. mdformat caught it — but only
because mdformat rejects any CR, which is a symptom of the cause rather
than the cause, and nothing covers `.yaml`, `.json`, `.toml` or `.py`.

Conflict markers: `git add -A` mid-rebase staged a file git still reported
as unresolved, and the rebase completed clean. The gate caught that one
only because a conflict marker is a Python syntax error. In `.yaml` — every
workflow file, and `framework.yaml` — nothing in the gate parses it at all.

Both tests build throwaway repositories rather than stubbing git, because
the thing under test is what git reports, not what we believe it reports.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check


def _repo(root: Path, *, autocrlf: str, attributes: str | None = None) -> Path:
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    for key, value in (
        ("core.autocrlf", autocrlf),
        ("user.email", "t@example.invalid"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "config", key, value], cwd=root, check=True)
    if attributes is not None:
        (root / ".gitattributes").write_bytes(attributes.encode())
    return root


def _add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


# --------------------------------------------------------------- line endings


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    root = _repo(tmp_path, autocrlf="false")
    (root / "conf.yaml").write_bytes(b"a: 1\nb: 2\n")
    _add_all(root)
    assert check.check_line_endings(root) is True


def test_a_crlf_yaml_fails(tmp_path: Path) -> None:
    """The unprotected case: autocrlf off, and no attribute covers .yaml."""
    root = _repo(tmp_path, autocrlf="false")
    (root / "conf.yaml").write_bytes(b"a: 1\r\nb: 2\r\n")
    _add_all(root)
    assert check.check_line_endings(root) is False


def test_a_crlf_python_file_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, autocrlf="false")
    (root / "mod.py").write_bytes(b"x = 1\r\n")
    _add_all(root)
    assert check.check_line_endings(root) is False


def test_markdown_is_already_protected_by_gitattributes(tmp_path: Path) -> None:
    """Not a gap — a record of why this check is not about .md.

    `*.md text eol=lf` normalises the blob even with autocrlf off, so CRLF
    markdown cannot reach a commit. The check exists for the file types
    that attribute does not name.
    """
    root = _repo(tmp_path, autocrlf="false", attributes="*.md text eol=lf\n")
    (root / "doc.md").write_bytes(b"line one\r\nline two\r\n")
    _add_all(root)
    assert check.check_line_endings(root) is True


def test_a_windows_checkout_is_not_flagged(tmp_path: Path) -> None:
    """autocrlf=true gives a CRLF *working tree* and an LF *index*.

    Reading bytes off disk would fail on every Windows machine. The check
    asks git about the blob, which is what ships.
    """
    root = _repo(tmp_path, autocrlf="true")
    (root / "conf.yaml").write_bytes(b"a: 1\r\nb: 2\r\n")
    _add_all(root)
    assert check.check_line_endings(root) is True


def test_git_failing_is_a_skip_not_a_pass(tmp_path: Path) -> None:
    """Not a repository at all — must decline, never report success."""
    assert check.check_line_endings(tmp_path) is None


# ------------------------------------------------------------ conflict markers

# Assembled at runtime, never written out. A literal marker at the start of
# a line in this file would be a real conflict marker in a tracked file, and
# the check under test would report this file — which is exactly what
# happened: CI went red on `tests/test_tree_hygiene.py:106`. An example of a
# conflict marker, in a tree scanned for conflict markers, is a conflict
# marker; nothing about the intent behind it is visible to a grep.
#
# The local gate passed beforehand only because this file was still
# untracked when it ran, and `git grep` searches tracked files. A green gate
# on a tree that does not yet contain the file under test is not evidence.
OPEN = b"<" * 7 + b" HEAD"
SPLIT = b"=" * 7
CLOSE = b">" * 7 + b" other"
BASE = b"|" * 7 + b" base"

MARKED_YAML = (
    b"jobs:\n" + OPEN + b"\n  build: {runs-on: ubuntu-latest}\n" + SPLIT + b"\n"
    b"  build: {runs-on: windows-latest}\n" + CLOSE + b"\n"
)


def test_a_clean_tree_has_no_markers(tmp_path: Path) -> None:
    root = _repo(tmp_path, autocrlf="false")
    (root / "ci.yaml").write_bytes(b"jobs:\n  build: {}\n")
    _add_all(root)
    assert check.check_conflict_markers(root) is True


def test_a_marked_yaml_fails(tmp_path: Path) -> None:
    """The file type with no incidental catch anywhere in the gate."""
    root = _repo(tmp_path, autocrlf="false")
    (root / "ci.yaml").write_bytes(MARKED_YAML)
    _add_all(root)
    assert check.check_conflict_markers(root) is False


def test_a_marked_json_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, autocrlf="false")
    (root / "data.json").write_bytes(b"{\n" + OPEN + b'\n  "a": 1\n' + CLOSE + b"\n}\n")
    _add_all(root)
    assert check.check_conflict_markers(root) is False


@pytest.mark.parametrize(
    "body",
    [
        b"Heading\n=======\n\nprose\n",
        b"```\nCoverage\n" + b"=" * 60 + b"\n  In scope  287\n```\n",
        b"x = '======='\n",
    ],
    ids=["setext-underline", "fenced-sample-output", "string-literal"],
)
def test_equals_runs_alone_are_not_flagged(tmp_path: Path, body: bytes) -> None:
    """The false positive this check must not have.

    Seven equals signs are a legal setext underline, and a tree-wide grep
    for `=======` really did report two hits in README.md — 60-character
    rules inside a fenced sample-output block. git never writes a
    `=======` without the `<<<<<<< ` that opens the conflict, so only the
    unambiguous markers are searched.
    """
    root = _repo(tmp_path, autocrlf="false")
    (root / "doc.md").write_bytes(body)
    _add_all(root)
    assert check.check_conflict_markers(root) is True


def test_a_diff3_base_marker_fails(tmp_path: Path) -> None:
    """`merge.conflictStyle = diff3` adds a ||||||| section."""
    root = _repo(tmp_path, autocrlf="false")
    (root / "ci.yaml").write_bytes(
        b"a\n" + OPEN + b"\nb\n" + BASE + b"\nc\n" + SPLIT + b"\nd\n" + CLOSE + b"\n"
    )
    _add_all(root)
    assert check.check_conflict_markers(root) is False


def test_conflict_marker_check_on_a_non_repository_skips(tmp_path: Path) -> None:
    assert check.check_conflict_markers(tmp_path) is None


def test_an_untracked_marked_file_fails(tmp_path: Path) -> None:
    """The incident this whole file exists for, as a test.

    On 2026-09-18 the gate ran while `tests/test_tree_hygiene.py` was still
    untracked, printed "321 tracked files scanned, 0 conflict marker(s)
    found", and the file was committed a minute later. CI then failed on
    it. A tracked-only scan is green before the commit and red after, and
    the untracked file is precisely the one about to become a commit.

    Every other test here commits before checking, so the suite could not
    see this. Noted by 80 in review, with a scratch repository proving it,
    after the tracked-only version had already shipped.
    """
    root = _repo(tmp_path, autocrlf="false")
    (root / "kept.txt").write_bytes(b"ok\n")
    _add_all(root)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)

    (root / "ci.yaml").write_bytes(MARKED_YAML)  # written, never staged
    assert check.check_conflict_markers(root) is False


def test_an_ignored_marked_file_is_not_flagged(tmp_path: Path) -> None:
    """Scanning untracked files must not mean scanning junk.

    `git grep --untracked` still honours `.gitignore`, so scratch output,
    virtualenvs and build directories stay out. Without this the check
    would fire on files nobody is about to commit, and a check that cries
    wolf on ignored paths gets turned off.
    """
    root = _repo(tmp_path, autocrlf="false")
    (root / ".gitignore").write_bytes(b"scratch/\n")
    _add_all(root)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)

    (root / "scratch").mkdir()
    (root / "scratch" / "ci.yaml").write_bytes(MARKED_YAML)
    assert check.check_conflict_markers(root) is True


def test_the_check_names_the_tree_it_examined(capsys: pytest.CaptureFixture[str]) -> None:
    """A count says how much was examined, never what.

    "321 tracked files scanned, 0 found" was true of the tree the gate ran
    on and false of the tree pushed a minute later. Both checks now print
    the HEAD sha, whether the tree is clean, and how many untracked files
    there are, so the answer is attached to a tree rather than floating.
    """
    check.check_conflict_markers()
    out = capsys.readouterr().out
    assert "tree: HEAD " in out
    assert "untracked (not ignored)" in out


def test_the_line_ending_check_states_what_it_did_not_examine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Untracked files are out of scope here, and that is stated, not silent.

    Unlike conflict markers, a CRLF working-tree file is not yet a defect:
    `core.autocrlf` and `.gitattributes` normalise at `git add`, so it may
    well become an LF blob. Flagging it would fire on every Windows
    checkout. The count is printed so the gap is visible.
    """
    root = _repo(tmp_path, autocrlf="false")
    (root / "tracked.yaml").write_bytes(b"a: 1\n")
    _add_all(root)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    (root / "loose.yaml").write_bytes(b"b: 2\r\n")

    assert check.check_line_endings(root) is True
    assert "1 untracked (not ignored)" in capsys.readouterr().out


def test_this_repository_has_no_conflict_markers_or_crlf() -> None:
    """Run both checks against the real tree, not a fixture.

    Everything above builds a throwaway repository, so all of it passed
    while the file it lives in was itself putting a conflict marker into
    the tracked tree — the marker was in this file's own fixtures, and CI
    reported `tests/test_tree_hygiene.py:106`. The local gate had been
    green because the file was still untracked when it ran, and `git grep`
    searches tracked files.

    A test that only ever examines a fixture cannot notice that the suite
    is the thing breaking the invariant. This one looks at the tree the
    checks actually ship to guard.
    """
    assert check.check_conflict_markers() is True
    assert check.check_line_endings() is True
