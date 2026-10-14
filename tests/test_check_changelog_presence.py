"""check.py runs the changelog PRESENCE guard CI runs (#197).

PR 192 had user-facing changes and no fragment, passed this gate at 16/0/0,
and went red in CI's `changelog` job: the gate ran the fragments' shape check
and not the presence one. These pin how the gate finds a base, what it does
when it can't, the one declared skip (9b's multi-PR batches), and that the
guard is really called.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402

_ID = ["-c", "user.name=t", "-c", "user.email=t@invalid"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *_ID, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    """main at M; a train branched from M at T; HEAD one commit past T."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "work")
    m = _commit(repo, "m")
    t = _commit(repo, "t")
    _commit(repo, "mine")
    return repo, m, t


def _args(**kw) -> argparse.Namespace:
    return argparse.Namespace(
        **{"base": None, "body_file": None, "no_changelog_presence": None, **kw}
    )


def test_the_nearest_train_is_the_derived_base(tmp_path):
    repo, m, t = _repo(tmp_path)
    _git(repo, "update-ref", "refs/remotes/forgejo/main", m)
    _git(repo, "update-ref", "refs/remotes/forgejo/release/9.9", t)
    _git(repo, "update-ref", "refs/remotes/forgejo/ba/release/x", t)  # not a train
    base, how = check.derive_base(repo)
    assert base == "forgejo/release/9.9", how
    assert t[:12] in how and "1 commit(s) back" in how and "2 train ref(s)" in how


def test_an_exact_tie_from_different_merge_bases_refuses(tmp_path):
    """9b on #197: which train the branch is for can't be known, so refuse."""
    repo, m, _ = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "side", m)
    side = _commit(repo, "side")
    _git(repo, "checkout", "-q", "work")
    _git(repo, "update-ref", "refs/remotes/forgejo/release/9.9", _git(repo, "rev-parse", "HEAD~1"))
    # A second train on another line, equally near once merged in (3 and 3).
    _git(repo, "update-ref", "refs/remotes/origin/release/9.8", side)
    _git(repo, "merge", "-q", "--no-edit", "side")
    base, how = check.derive_base(repo)
    assert base is None and how.startswith("tie: "), (base, how)


def test_equally_near_refs_at_one_merge_base_are_one_answer(tmp_path):
    repo, _, t = _repo(tmp_path)
    _git(repo, "update-ref", "refs/remotes/forgejo/release/9.9", t)
    _git(repo, "update-ref", "refs/remotes/origin/release/9.9", t)
    base, _ = check.derive_base(repo)
    assert base == "forgejo/release/9.9"


def test_no_train_ref_fails_loudly_never_checks_against_nothing(tmp_path, monkeypatch, capsys):
    repo, _, _ = _repo(tmp_path)
    assert check.derive_base(repo) == (
        None,
        "no release/* or main remote-tracking ref (fetch first)",
    )
    monkeypatch.setattr(check, "derive_base", lambda: (None, "nothing to derive from"))
    assert check.check_changelog_presence(_args()) is False
    assert "Pass --base" in capsys.readouterr().out


def test_an_explicit_sha_is_passed_to_the_guard(monkeypatch):
    """9b's gate passes the exact train tip."""
    seen = []
    monkeypatch.setattr(check, "run", lambda label, cmd: seen.append(cmd) or True)
    sha = "a" * 40
    assert check.check_changelog_presence(_args(base=sha)) is True
    (cmd,) = seen
    assert cmd[1:] == ["scripts/changelog_guard.py", "--base", sha]


def test_the_body_file_reaches_the_guard_and_a_missing_one_fails(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(check, "run", lambda label, cmd: seen.append(cmd) or True)
    body = tmp_path / "body.txt"
    body.write_text("No changelog entry: tests only\n", encoding="utf-8")
    assert check.check_changelog_presence(_args(base="x", body_file=body)) is True
    assert seen[-1][-2:] == ["--body-file", str(body)]
    assert check.check_changelog_presence(_args(base="x", body_file=tmp_path / "nope")) is False


def test_the_declared_skip_is_reported_skipped_with_its_reason_never_passed(capsys):
    """9b on #197: a multi-PR batch can't hand one body file for several PRs,
    and one member's fragment would cover another's."""
    assert check.check_changelog_presence(_args(no_changelog_presence="batch 5")) is None
    results = {"something": True, check.PRESENCE: None}
    assert check.summarise(results, set(), {check.PRESENCE: "batch 5"}) == 0
    out = capsys.readouterr().out
    assert f"SKIP  {check.PRESENCE}" in out and "(declared: batch 5)" in out
    assert "1 ran, 0 failed, 1 skipped" in out


def test_an_undeclared_presence_skip_stops_the_script():
    """Only the declared flag makes this skip acceptable: a None from it
    without the flag is the BUG branch, never a quiet pass."""
    assert check.summarise({"something": True, check.PRESENCE: None}, set()) == 2


def test_the_flag_is_parsed(tmp_path):
    args = check.parse_args(["--base", "abc", "--no-changelog-presence", "batch 4"])
    assert args.base == "abc" and args.no_changelog_presence == "batch 4"
    assert check.parse_allow_skip(["--base", "abc"]) == set()


def test_the_gate_really_runs_the_step():
    """The arm #197 names: drop the step from `_main`'s results and this goes
    red. Read from the code (ast), because `test_gate_runs_what_ci_runs`
    only asks that check.py names the script, and the function would still
    name it with the step gone."""
    import ast

    tree = ast.parse((ROOT / "scripts" / "check.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_main")
    results = [
        n.value
        for n in ast.walk(main)
        if isinstance(n, ast.AnnAssign)
        and getattr(n.target, "id", "") == "results"
        and isinstance(n.value, ast.Dict)
    ]
    assert len(results) == 1, "no `results: ... = {...}` literal in _main"
    keyed = [
        value
        for key, value in zip(results[0].keys, results[0].values, strict=True)
        if isinstance(key, ast.Name) and key.id == "PRESENCE"
    ]
    assert len(keyed) == 1, "the results dict has no PRESENCE step"
    (call,) = keyed
    assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "check_changelog_presence"


def test_a_blank_skip_reason_fails_not_skips():
    """9b on #211: the guard's own exemption needs a reason, so this does."""
    assert check.check_changelog_presence(_args(no_changelog_presence="   ")) is False
