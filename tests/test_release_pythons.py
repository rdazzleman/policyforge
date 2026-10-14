"""The cut's full suite on Python 3.10, 3.11 and 3.13, before approval (#242).

`requires-python` says >=3.10, and per change only 3.12 (CI) and 3.14 (the
local gates) are tested. So each release runs the FULL suite once on the rest,
in containers, each a fresh unhashed install, before the user is asked to
approve main (80's rulings 3955 and 3961). A red version blocks unless the
user acknowledges it, from their own login.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release
from release import Context

ROOT = Path(__file__).resolve().parent.parent
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
}


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        check=True,
    ).stdout.strip()


class Forgejo:
    """The tracking issue: comments by login; POSTs are 9b's."""

    def __init__(self):
        self.comments: list[dict] = []

    def add(self, login: str, line: str) -> None:
        self.comments.append(
            {
                "created_at": f"2026-10-07T00:00:{len(self.comments):02d}Z",
                "user": {"login": login},
                "body": f"x\n\n{line}",
            }
        )

    def __call__(self, method, path, data):
        if method == "POST":
            self.add("policyforge-9b", data["body"].splitlines()[-1])
            return 201, {}, {}
        return 200, list(self.comments), {}


@pytest.fixture
def world(tmp_path):
    """A local release's cut commit in a real repository, a tracking issue,
    and a stand-in runner that answers each version as `answers` says."""
    root = tmp_path / "root"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "core.autocrlf", "false")
    (root / "pyproject.toml").write_bytes(b"[project]\nname = 'x'\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "the cut")
    fake = Forgejo()
    answers: dict[str, str] = {}
    runs: list[tuple[str, list[str]]] = []

    def run_python(version, tar):
        with tarfile.open(tar) as archive:
            runs.append((version, sorted(archive.getnames())))
        return answers.get(version, "passed"), [f"{version}: fake"]

    ctx = Context(
        version="9.9.0",
        root=root,
        run=release._real_run,
        forge="forgejo",
        forgejo=fake,
        tracking=1,
        publish=release.PUBLISH_LOCAL,
        run_python=run_python,
    )
    return {
        "ctx": ctx,
        "fake": fake,
        "answers": answers,
        "runs": runs,
        "head": _git(root, "rev-parse", "HEAD"),
    }


def _records(w) -> list[str]:
    return [
        c["body"].splitlines()[-1] for c in w["fake"].comments if "Release-python:" in c["body"]
    ]


def test_each_version_runs_on_the_approval_commits_own_tree_and_is_recorded(world):
    w = world
    release._run_pythons(w["ctx"])
    assert [v for v, _ in w["runs"]] == list(release.CUT_PYTHONS)
    assert all(names == ["pyproject.toml"] for _, names in w["runs"]), "the cut commit's tree"
    assert _records(w) == [
        f"Release-python: {w['head']} release=9.9.0 python={v} result=passed"
        for v in release.CUT_PYTHONS
    ]
    assert release._pythons_tested(w["ctx"]).ok


@pytest.mark.parametrize("result", ["FAILED", "install-failed", "did-not-run"])
def test_a_version_that_did_not_pass_holds_the_approval(world, result):
    w = world
    w["answers"]["3.11"] = result
    release._run_pythons(w["ctx"])
    check = release._pythons_tested(w["ctx"])
    assert not check.ok
    assert f"Python 3.11 on {w['head'][:12]}: {result}" in check.measured


def test_an_unknown_result_is_recorded_as_failed(world):
    w = world
    w["answers"]["3.13"] = "maybe"
    release._run_pythons(w["ctx"])
    assert _records(w)[-1].endswith("python=3.13 result=FAILED")


def test_the_users_acknowledgement_opens_a_red_version_and_nobody_elses_does(world):
    w = world
    w["answers"]["3.10"] = "FAILED"
    release._run_pythons(w["ctx"])
    ack = f"Release-python-acknowledged: {w['head']} release=9.9.0 python=3.10"
    w["fake"].add("policyforge-9b", ack)
    assert not release._pythons_tested(w["ctx"]).ok, "a session can't acknowledge for the user"
    w["fake"].add("rdazzleman", ack)
    check = release._pythons_tested(w["ctx"])
    assert check.ok, check.measured
    assert any("(acknowledged by the user)" in line for line in check.measured)


def test_an_acknowledgement_without_a_run_does_not_count(world):
    w = world
    w["fake"].add(
        "rdazzleman", f"Release-python-acknowledged: {w['head']} release=9.9.0 python=3.13"
    )
    for v in ("3.10", "3.11"):
        w["fake"].add(
            "policyforge-9b", f"Release-python: {w['head']} release=9.9.0 python={v} result=passed"
        )
    check = release._pythons_tested(w["ctx"])
    assert not check.ok and f"Python 3.13 on {w['head'][:12]}: no record" in check.measured


def test_a_record_for_another_commit_is_stale(world):
    w = world
    other = "b" * 40
    for v in release.CUT_PYTHONS:
        w["fake"].add(
            "policyforge-9b", f"Release-python: {other} release=9.9.0 python={v} result=passed"
        )
    assert not release._pythons_tested(w["ctx"]).ok


def test_a_resumed_run_runs_only_the_versions_without_a_pass(world):
    w = world
    w["fake"].add(
        "policyforge-9b", f"Release-python: {w['head']} release=9.9.0 python=3.10 result=passed"
    )
    w["fake"].add(
        "policyforge-9b", f"Release-python: {w['head']} release=9.9.0 python=3.11 result=FAILED"
    )
    release._run_pythons(w["ctx"])
    assert [v for v, _ in w["runs"]] == ["3.11", "3.13"]


def test_main_waits_for_the_versions():
    """The user's merge is gated on them, beside the install: the install
    alone, passed, no longer opens it."""
    from tests.test_release import CUT, _install_ctx, _main_step, _record

    without = _install_ctx([("2026-09-25T16:00:00Z", _record(CUT))], pythons=False)
    check = _main_step().gate(without)
    assert not check.ok and any("Python 3.10" in line for line in check.measured), check.measured
    with_them = _install_ctx([("2026-09-25T16:00:00Z", _record(CUT))])
    assert _main_step().gate(with_them).ok


def test_the_step_sits_between_the_install_and_the_users_merge():
    for plan in (
        release.steps(),
        release.steps("forgejo"),
        release.steps("forgejo", release.PUBLISH_LOCAL),
    ):
        keys = [s.key for s in plan]
        assert (
            keys.index("head-install") + 1 == keys.index("head-pythons") == keys.index("main") - 1
        )


# --- the real runner ------------------------------------------------------------------


def test_the_extras_are_the_ones_cis_lock_installs():
    """Read from requirements/ci.txt's own header, the other side of the check."""
    header = (ROOT / "requirements" / "ci.txt").read_text(encoding="utf-8")
    extras = re.findall(r"--extra (\S+)", header.split("\n\n", 1)[0])
    assert release.CUT_EXTRAS.split(",") == extras


def test_the_container_gets_the_tree_read_only_and_no_host_variable(tmp_path):
    argv = release._python_docker_argv(tmp_path, "3.11")
    assert not {"-e", "--env", "--env-file"} & set(argv)
    assert f"{tmp_path}:/in:ro" in argv and "python:3.11-slim" in argv
    script = argv[-1]
    assert f'pip install -e ".[{release.CUT_EXTRAS}]"' in script
    assert "--require-hashes" not in script, "a fresh, unhashed resolve: what a user gets (3961)"
    assert "python -m pytest -q -rs -n auto" in script


@pytest.mark.parametrize(
    "code, want",
    [(0, "passed"), (1, "FAILED"), (3, "install-failed"), (4, "did-not-run"), (125, "did-not-run")],
)
def test_the_real_runner_reads_the_containers_exit(tmp_path, monkeypatch, code, want):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    seen = []
    monkeypatch.setattr(
        release,
        "_real_run",
        lambda argv: (seen.append(argv), subprocess.CompletedProcess(argv, code, "out", ""))[1],
    )
    result, lines = release._real_run_python("3.13", tmp_path / "tree.tar")
    assert result == want and lines[-1] == f"docker exit status: {code}"
    assert seen[0][:2] == ["docker", "run"]


def test_without_docker_nothing_runs_and_it_says_so(tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(release, "_real_run", lambda argv: pytest.fail("ran docker"))
    assert release._real_run_python("3.10", tmp_path / "tree.tar") == (
        "did-not-run",
        ["docker is not on PATH"],
    )


# --- GitHub's legs stand in for the local runs, after a green candidate (#398) -------------

CAND = "cccccccccccccccccccccccccccccccccccccccc"


def _legs(**marks):
    """A ci_legs hook answering a settled run: every leg green except `marks`
    (keyed by version with a dot written as underscore: v3_11="FAIL")."""
    jobs = {release._leg_job(v): "success" for v in release.LEG_PYTHONS}
    for key, mark in marks.items():
        jobs[release._leg_job(key.removeprefix("v").replace("_", "."))] = mark
    return lambda ctx, sha: (
        jobs,
        [f"GitHub's run on candidate {CAND[:12]}: completed/success"],
        CAND,
    )


def test_five_green_legs_stand_in_for_the_local_runs(world):
    w = world
    w["ctx"].ci_legs = _legs()
    bodies, fake = [], w["fake"]

    def forgejo(method, path, data):
        if method == "POST":
            bodies.append(data["body"])
        return fake(method, path, data)

    w["ctx"].forgejo = forgejo
    release._run_pythons(w["ctx"])
    assert w["runs"] == [], "nothing ran locally"
    assert _records(w) == [
        f"Release-python: {w['head']} release=9.9.0 python={v} result=passed candidate={CAND}"
        for v in release.CUT_PYTHONS
    ]
    assert release._pythons_tested(w["ctx"]).ok
    assert any("GitHub's `pytest (ubuntu-latest, 3.10)` leg" in body for body in bodies)


def test_a_red_leg_blocks_as_a_failed_record_and_the_others_still_count(world):
    w = world
    w["ctx"].ci_legs = _legs(v3_11="FAIL")
    release._run_pythons(w["ctx"])
    assert w["runs"] == []
    check = release._pythons_tested(w["ctx"])
    assert not check.ok
    assert f"Python 3.11 on {w['head'][:12]}: FAILED" in check.measured
    assert f"Python 3.10 on {w['head'][:12]}: passed" in check.measured


@pytest.mark.parametrize("hook", ["no result", "other leg skipped", "cut leg missing"])
def test_anything_short_of_five_settled_green_legs_runs_every_version_locally(world, hook, capsys):
    """The arm: accepting a missing or unsettled result as a pass would leave
    `runs` empty here, and this fails."""
    w = world
    if hook == "no result":
        w["ctx"].ci_legs = lambda ctx, sha: (
            {},
            ["no GitHub run on candidate abcdef012345 yet"],
            "",
        )
    elif hook == "other leg skipped":
        w["ctx"].ci_legs = _legs(v3_14="skipped")
    elif hook == "cut leg missing":
        jobs = {release._leg_job(v): "success" for v in ("3.10", "3.12", "3.13", "3.14")}
        w["ctx"].ci_legs = lambda ctx, sha: (jobs, ["x"], CAND)
    release._run_pythons(w["ctx"])
    assert [v for v, _ in w["runs"]] == list(release.CUT_PYTHONS)
    said = capsys.readouterr().out
    if hook == "no result":
        assert "falling back to the local runs on every version" in said
    else:
        assert "can't stand in for the local runs" in said and "running locally" in said
    assert release._pythons_tested(w["ctx"]).ok


@pytest.mark.parametrize("version", ["3.12", "3.14"])
def test_a_red_leg_the_cut_never_runs_blocks_with_no_local_run(world, version, capsys):
    """80's ruling on 398: a red 3.12 or 3.14 leg is measured on the candidate's
    own commit and tree, and the local runs never re-test it, so a local green
    would stand in for a red nobody measured. It is a FAILED record, nothing
    runs locally, and the output names the way out."""
    w = world
    w["ctx"].ci_legs = _legs(**{"v" + version.replace(".", "_"): "FAIL"})
    release._run_pythons(w["ctx"])
    assert w["runs"] == [], "no local fallback"
    assert _records(w)[-1] == (
        f"Release-python: {w['head']} release=9.9.0 python={version} result=FAILED candidate={CAND}"
    )
    said = capsys.readouterr().out
    assert "BLOCKED" in said and f"Python {version} red" in said
    assert "a new candidate run on GitHub" in said and "Release-python-acknowledged" in said
    check = release._pythons_tested(w["ctx"])
    assert not check.ok
    assert f"Python {version} on {w['head'][:12]}: FAILED" in check.measured


def test_a_re_run_on_the_same_candidate_posts_no_duplicate_failed_record(world):
    w = world
    w["ctx"].ci_legs = _legs(v3_12="FAIL")
    release._run_pythons(w["ctx"])
    first = list(_records(w))
    release._run_pythons(w["ctx"])
    assert _records(w) == first, "the same leg on the same candidate is recorded once"
    # A new candidate run is new information, so it is recorded.
    other = "d" * 40
    jobs = {release._leg_job(v): "success" for v in release.LEG_PYTHONS}
    jobs[release._leg_job("3.12")] = "FAIL"
    w["ctx"].ci_legs = lambda ctx, sha: (jobs, ["x"], other)
    release._run_pythons(w["ctx"])
    assert len(_records(w)) == len(first) + 1 and _records(w)[-1].endswith(f"candidate={other}")


def test_a_later_green_candidate_supersedes_the_red_record(world):
    w = world
    w["ctx"].ci_legs = _legs(v3_14="FAIL")
    release._run_pythons(w["ctx"])
    assert not release._pythons_tested(w["ctx"]).ok
    other = "d" * 40
    jobs = {release._leg_job(v): "success" for v in release.LEG_PYTHONS}
    w["ctx"].ci_legs = lambda ctx, sha: (jobs, ["x"], other)
    release._run_pythons(w["ctx"])
    assert w["runs"] == []
    assert release._pythons_tested(w["ctx"]).ok


def test_the_users_acknowledgement_opens_a_red_leg_the_cut_never_runs(world):
    w = world
    w["ctx"].ci_legs = _legs(v3_12="FAIL")
    release._run_pythons(w["ctx"])
    assert not release._pythons_tested(w["ctx"]).ok
    w["fake"].add(
        "rdazzleman", f"Release-python-acknowledged: {w['head']} release=9.9.0 python=3.12"
    )
    assert release._pythons_tested(w["ctx"]).ok


def test_a_red_leg_with_another_leg_unsettled_still_runs_nothing_locally(world):
    """b5 on PR 423: the block is not only for the version that is red. With
    3.12 red and 3.11 skipped, 3.11 has no GitHub result, and running it
    locally would spend a run on a release that is already blocked."""
    w = world
    w["ctx"].ci_legs = _legs(v3_12="FAIL", v3_11="skipped")
    release._run_pythons(w["ctx"])
    assert w["runs"] == []
    check = release._pythons_tested(w["ctx"])
    assert not check.ok
    assert f"Python 3.12 on {w['head'][:12]}: FAILED" in check.measured
    assert f"Python 3.11 on {w['head'][:12]}: no record" in check.measured


def test_without_a_legs_reader_everything_runs_locally(world):
    """A context that never set the hook behaves as before this change."""
    w = world
    assert w["ctx"].ci_legs is None
    release._run_pythons(w["ctx"])
    assert [v for v, _ in w["runs"]] == list(release.CUT_PYTHONS)


def _summary(sha8: str, status="completed") -> str:
    """One push's runs in the order the real ci-read log prints them (ops job
    4717): a skipped `macos-wheels` and the `single-commit export` at the same
    sha as `ci`, the `ci` run between them."""
    lines = [
        f"## macos-wheels #4  release/9.9.0 {sha8}  completed/skipped  2026-10-10T19:29:29Z",
        "- skipped: macOS wheel readiness",
        f"## ci #6  release/9.9.0 {sha8}  {status}/success  2026-10-10T19:29:29Z",
    ]
    lines += [f"- success: {release._leg_job(v)}" for v in release.LEG_PYTHONS]
    lines += [
        f"## single-commit export #4  release/9.9.0 {sha8}  completed/success  "
        "2026-10-10T19:29:29Z",
        "- success: pytest-in-an-export",
    ]
    return "\n".join(lines)


@pytest.fixture
def candidate(world, tmp_path, monkeypatch):
    """A bare policyforge-ci whose release/9.9.0 holds the cut commit's tree as
    a different commit, with ci-read stubbed."""
    w = world
    bare = tmp_path / "ci.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    tree = _git(w["ctx"].root, "rev-parse", "HEAD^{tree}")
    commit = _git(w["ctx"].root, "commit-tree", tree, "-m", "candidate")
    _git(w["ctx"].root, "push", "-q", str(bare), f"{commit}:refs/heads/release/9.9.0")
    monkeypatch.setattr(release, "CI_URL", str(bare))
    w["candidate"] = commit
    return w


def test_the_real_reader_takes_jobs_from_the_run_on_the_candidates_own_commit(
    candidate, monkeypatch
):
    w = candidate
    monkeypatch.setattr(
        release, "read_ci_summary", lambda forgejo, sleep=None: _summary(w["candidate"][:8])
    )
    jobs, lines, _ = release._real_ci_legs(w["ctx"], w["head"])
    assert jobs[release._leg_job("3.10")] == "success"
    assert w["candidate"][:12] in lines[0]


def test_a_run_on_another_commit_is_treated_as_missing(candidate, monkeypatch):
    w = candidate
    monkeypatch.setattr(
        release, "read_ci_summary", lambda forgejo, sleep=None: _summary("deadbeef")
    )
    jobs, lines, _ = release._real_ci_legs(w["ctx"], w["head"])
    assert jobs == {} and "no GitHub run on candidate" in lines[0]


def test_a_run_that_has_not_completed_is_treated_as_missing(candidate, monkeypatch):
    w = candidate
    monkeypatch.setattr(
        release,
        "read_ci_summary",
        lambda forgejo, sleep=None: _summary(w["candidate"][:8], "in_progress"),
    )
    jobs, lines, _ = release._real_ci_legs(w["ctx"], w["head"])
    assert jobs == {} and "not completed" in lines[0]


def test_a_candidate_holding_another_tree_is_not_the_cut(candidate, monkeypatch):
    w = candidate
    root = w["ctx"].root
    (root / "extra.txt").write_text("changed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "the tree moved")
    moved = _git(root, "rev-parse", "HEAD")
    monkeypatch.setattr(
        release, "read_ci_summary", lambda forgejo, sleep=None: _summary(w["candidate"][:8])
    )
    jobs, lines, _ = release._real_ci_legs(w["ctx"], moved)
    assert jobs == {} and "not the approval commit's" in lines[0]


def test_an_absent_candidate_branch_is_not_a_result(world, tmp_path, monkeypatch):
    bare = tmp_path / "empty.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    monkeypatch.setattr(release, "CI_URL", str(bare))
    jobs, lines, _ = release._real_ci_legs(world["ctx"], world["head"])
    assert jobs == {} and "absent" in lines[0]
