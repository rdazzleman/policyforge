"""Build P and guard it before the cut (#49), against a synthetic history.

The world: a release tagged v1.0.0 that still carried a licensed parser
(as 1.6.1 did), then a later commit that moves it out (as #50 did). The
rehearsal must pass that clean cut, and must refuse the two ways a cut goes
wrong: public code that still imports the parser (P can't be built), and a
P that builds but that the guard refuses (#49's own defect: an import
spelled in a public docstring).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.licensed_text import unmask as _m

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import public_rehearsal as pr  # noqa: E402

PARSER = '''"""A synthetic licensed parser."""

FRAMEWORK = "SYNTHETIC-LICENSED"


def parse_synthetic_export(rows):
    return [row for row in rows if row]
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str | None], message: str) -> str:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.autocrlf", "false")
    _commit(
        repo,
        {
            "src/policyforge/__init__.py": "",
            "src/policyforge/ingest/__init__.py": "",
            "src/policyforge/ingest/hitrust.py": PARSER,
            "src/policyforge/cli.py": "import json\n",
            "README.md": "public\n",
        },
        "a release that still carried the parser",
    )
    _git(repo, "tag", "v1.0.0")
    moved = _commit(repo, {"src/policyforge/ingest/hitrust.py": None}, "move the parser out")
    monkeypatch.setattr(pr, "REPO_ROOT", repo)
    return {"repo": repo, "moved": moved}


def _run(capsys, *argv) -> tuple[int, str]:
    code = pr.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_a_clean_cut_builds_p_and_passes_the_guard(world, capsys):
    """What it must ALLOW: the parser moved out, nothing public refers to it."""
    code, out = _run(capsys)
    assert code == 0, out
    assert "P is clean" in out


def test_public_code_still_importing_the_parser_cannot_build_p(world, capsys):
    _commit(world["repo"], {"src/policyforge/cli.py": "from .ingest import hitrust\n"}, "stray")
    code, out = _run(capsys)
    assert code == 1, out
    assert "P CANNOT BE BUILT" in out and "src/policyforge/cli.py (import)" in out


def test_a_p_the_guard_refuses_is_refused_before_the_cut(world, capsys):
    """#49 itself: release.py's docstring spelled a licensed import. The
    classifier calls it a mention, so P builds; the guard reads text."""
    doc = _m('"""Reads `from .@HITRUST@ import x`, which a line regex misses."""\n')
    _commit(world["repo"], {"scripts/release_like.py": doc}, "a docstring")
    code, out = _run(capsys)
    assert code == 1, out
    assert "publish_guard REFUSED P" in out and "L3 content" in out


def test_no_release_tag_is_nothing_to_rehearse_never_a_pass(world, capsys):
    _git(world["repo"], "tag", "-d", "v1.0.0")
    code, out = _run(capsys)
    assert code == 2, out
    assert "NOTHING TO REHEARSE" in out


def test_a_git_step_that_fails_is_exit_2_not_the_refusal_code(world, capsys, monkeypatch):
    """An uncaught error exits 1, which is the verdict code. Found live: a
    failed index step crashed with 1 and read as 'refused'."""
    real = pr._git

    def failing(*args, **kwargs):
        if args and args[0] == "write-tree":
            raise RuntimeError("git write-tree failed: simulated")
        return real(*args, **kwargs)

    monkeypatch.setattr(pr, "_git", failing)
    code, out = _run(capsys)
    assert code == 2, out
    assert "COULD NOT REHEARSE" in out


def test_a_cut_other_than_the_checkout_still_builds(world, capsys):
    """Found live at 6b61bc9: `rm --cached` refused a cut whose files differ
    from the checkout. The index step must not compare with the worktree."""
    # L must be non-empty AND differ between the cut and the checkout, as a
    # test importing the parser did at 6b61bc9 (an empty L never runs the step).
    importer = "tests/test_uses_parser.py"
    cut = _commit(
        world["repo"], {importer: _m("from policyforge.ingest import @HITRUST@\n")}, "cut"
    )
    _commit(
        world["repo"], {importer: _m("from policyforge.ingest import @HITRUST@  # edited\n")}, "on"
    )
    code, out = _run(capsys, "--cut", cut)
    assert code == 0, out
    assert f"licensed set L (1): {importer}" in out


def test_nothing_is_left_in_the_repository(world, capsys):
    def loose(repo):
        for line in _git(repo, "count-objects", "-v").splitlines():
            if line.startswith("count:"):
                return int(line.split(":")[1])
        raise AssertionError("no count")

    before = loose(world["repo"])
    code, out = _run(capsys)
    assert code == 0, out
    assert loose(world["repo"]) == before
    assert (
        _git(world["repo"], "for-each-ref", "--format=%(refname)")
        == "refs/heads/main\nrefs/tags/v1.0.0"
    )


def test_p_is_the_same_on_every_run(world, capsys):
    def p_of(out):
        return next(line.split()[1] for line in out.splitlines() if line.startswith("P "))

    first = p_of(_run(capsys)[1])
    second = p_of(_run(capsys)[1])
    assert first == second


def test_the_gate_rehearses_uncommitted_changes_not_only_head(world, capsys):
    """check.py runs BEFORE a commit. Rehearsing HEAD alone would check the
    parent and pass a leak that is only in the working tree."""
    repo = world["repo"]
    (repo / "scripts").mkdir(exist_ok=True)
    (repo / "scripts" / "release_like.py").write_text(
        _m('"""Reads `from .@HITRUST@ import x`."""\n'), encoding="utf-8", newline="\n"
    )
    committed, out_head = _run(capsys)
    assert committed == 0, out_head  # HEAD itself is clean
    code, out = _run(capsys, "--worktree")
    assert code == 1, out
    assert "rehearsing the WORKING TREE" in out and "L3 content" in out
    assert _git(repo, "status", "--porcelain") == "?? scripts/"  # nothing was staged for real


def test_a_clean_working_tree_rehearses_clean(world, capsys):
    code, out = _run(capsys, "--worktree")
    assert code == 0, out


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(128, ["git", "cat-file", "--batch"]),
        OSError("git is not on PATH"),
        ValueError("unexpected"),
    ],
    ids=["called-process-error", "os-error", "anything-else"],
)
def test_any_error_inside_the_guard_is_exit_2_never_the_refusal_code(
    world, capsys, monkeypatch, error
):
    """9b on #58: the guard's `_read_blobs` raises CalledProcessError, which
    an `except RuntimeError` let through, exiting 1, the verdict code."""

    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(pr.publish_guard, "guard", boom)
    code, out = _run(capsys)
    assert code == 2, out
    assert f"COULD NOT REHEARSE: {type(error).__name__}" in out


@pytest.mark.parametrize("module", ["publish_guard", "release"])
def test_a_module_that_cannot_be_imported_is_exit_2_never_the_refusal_code(module):
    """b5 on #58: the imports sat at module level, outside main()'s handlers,
    so an ImportError exited 1. Run as check.py runs it: a child process."""
    code = (
        f"import sys, runpy; sys.modules[{module!r}] = None; "
        f"sys.argv = ['public_rehearsal']; "
        f"runpy.run_path({str(ROOT / 'scripts' / 'public_rehearsal.py')!r}, run_name='__main__')"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert r.returncode == 2, (r.returncode, r.stderr[-400:])
    assert "could not be imported" in r.stderr
