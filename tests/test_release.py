"""`scripts/release.py`: the cut as gated steps (#255).

The engine is tested with synthetic steps and spies, so every claim in the
script's contract is shown to hold and to FAIL when it should: a dry run
never acts, a failed gate stops before acting, an outward step needs its
key typed, the wait for the user's merge stops and resumes. The real step
list is tested for its order and for what it must never do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release
from release import ACT, CHECK, WAIT, Check, Context, Step, run_steps


def _ctx(**kw) -> Context:
    ctx = Context(version="9.9.9", run=lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
    for key, value in kw.items():
        setattr(ctx, key, value)
    return ctx


def _step(key, kind=ACT, *, gate=True, post=False, outward=False, acted=None, post_after=True):
    """A synthetic step; `acted` collects the keys of steps whose action ran."""
    state = {"acted": False}

    def act(ctx):
        state["acted"] = True
        if acted is not None:
            acted.append(key)

    def post_fn(ctx):
        return Check(post_after if state["acted"] else post, [f"{key} post"])

    return Step(
        key,
        key,
        kind,
        lambda c: Check(gate, [f"{key} gate"]),
        post_fn,
        act if kind == ACT else None,
        outward=outward,
        would=f"do {key}",
    )


def _lines():
    out: list[str] = []
    return out, out.append


# --- the engine -----------------------------------------------------------------


def test_a_dry_run_never_acts_and_says_how_far_it_reached():
    acted: list[str] = []
    steps = [_step("a", CHECK), _step("b", acted=acted), _step("c", acted=acted)]
    out, emit = _lines()
    assert run_steps(steps, _ctx(), execute=False, out=emit) == 0
    assert acted == []
    text = "\n".join(out)
    assert "reached 2 of 3" in text and "not reached: c" in text


def test_a_failed_gate_stops_before_acting_with_the_steps_code():
    acted: list[str] = []
    steps = [_step("a", acted=acted), _step("b", gate=False, acted=acted), _step("c", acted=acted)]
    assert run_steps(steps, _ctx(), execute=True, out=lambda s: None) == 12
    assert acted == ["a"], "b's gate failed, so neither b nor c may act"


def test_an_outward_step_needs_its_key_typed():
    acted: list[str] = []
    steps = [_step("tag", outward=True, acted=acted)]
    wrong = _ctx(confirm=lambda prompt: "yes")
    assert run_steps(steps, wrong, execute=True, out=lambda s: None) == 6
    assert acted == []
    right = _ctx(confirm=lambda prompt: "tag")
    assert (
        run_steps(
            [_step("tag", outward=True, acted=acted)], right, execute=True, out=lambda s: None
        )
        == 0
    )
    assert acted == ["tag"]


def test_the_wait_stops_and_a_rerun_after_it_resumes():
    """The user's merge: exit 5 until it has happened; afterwards the
    already-done steps are skipped and the rest run."""
    acted: list[str] = []
    before = [_step("a", acted=acted), _step("main", WAIT, post=False), _step("c", acted=acted)]
    assert run_steps(before, _ctx(), execute=True, out=lambda s: None) == 5
    assert acted == ["a"]
    acted.clear()
    after = [
        _step("a", post=True, acted=acted),
        _step("main", WAIT, post=True),
        _step("c", acted=acted),
    ]
    assert run_steps(after, _ctx(), execute=True, out=lambda s: None) == 0
    assert acted == ["c"], "a and main were already done, so only c acts"


def test_a_failed_postcondition_is_its_own_code():
    steps = [_step("a"), _step("b", post_after=False)]
    assert run_steps(steps, _ctx(), execute=True, out=lambda s: None) == 42


# --- the real step list -----------------------------------------------------------


def _keys():
    return [s.key for s in release.steps()]


def test_the_order_is_the_procedure():
    """Two install tests (the user's ruling on #255): the release PR head
    installed BEFORE the user's approval (#381, "Install first"), the
    candidate formula before publishing, the published one last."""
    keys = _keys()
    order = [
        "train-final",
        "resources",
        "changelog",
        "version",
        "release-pr",
        "head-install",
        "main",
        "tag",
        "candidate-install",
        "publish",
        "release-check",
    ]
    assert [k for k in keys if k in order] == order


def test_outward_steps_are_exactly_the_ones_that_leave_this_clone():
    outward = {s.key for s in release.steps() if s.outward}
    # head-install posts its record on the tracking issue: that leaves the clone.
    assert outward == {"release-pr", "head-install", "tag", "publish"}
    assert "branch-install" not in _keys(), "the post-approval install is gone (#381)"


def test_the_user_merges_main_and_the_script_never_does():
    main = next(s for s in release.steps() if s.key == "main")
    assert main.kind == WAIT and main.act is None
    source = (Path(release.__file__)).read_text(encoding="utf-8")
    assert '"merge"' not in source and "'merge'" not in source, "no `gh pr merge` anywhere"


def _fake_run(responses):
    """argv -> CompletedProcess, by the first matching substring of the joined argv."""

    def run(argv):
        joined = " ".join(argv)
        for needle, (code, out) in responses.items():
            if needle in joined:
                return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


# --- #381: install the release PR head BEFORE the user's approval -------------------

CUT = "c" * 40
MOVED = "d" * 40


def _install_ctx(records, pr_head=CUT, state="OPEN", calls=None, cut=CUT):
    """A tracking issue holding `records` (created_at, last line), a release PR
    at `pr_head`, and the cut commit at CUT."""

    # (created_at, last line[, author]); the author defaults to the account
    # every session posts as, which is in RECORD_AUTHORS.
    comments = "\n".join(
        json.dumps([r[0], f"Result.\n\n{r[1]}", r[2] if len(r) > 2 else "rdazzlebot"])
        for r in records
    )
    pr = json.dumps(
        [{"title": "Release 9.9.9", "number": 7, "state": state, "headRefOid": pr_head}]
    )
    fake = _fake_run(
        {
            "issues/99/comments": (0, comments),
            "pr list": (0, pr),
            "rev-parse HEAD": (0, cut),
        }
    )

    def run(argv):
        if calls is not None:
            calls.append(argv)
        return fake(argv)

    return _ctx(tracking=99, run=run)


def _record(sha, result="passed", version="9.9.9"):
    return f"Release-install: {sha} release={version} result={result}"


def _main_step():
    return next(s for s in release.steps() if s.key == "main")


def test_the_wait_opens_only_with_a_passed_install_of_the_current_head():
    """The user approves with the install in hand: the wait's gate holds only
    when the latest record passed and names the PR head and the cut."""
    ctx = _install_ctx([("2026-09-25T16:00:00Z", _record(CUT))])
    assert _main_step().gate(ctx).ok
    assert run_steps([_main_step()], ctx, execute=True, out=lambda s: None) == 5, "waits"


def test_a_head_that_moved_after_the_install_is_refused_before_the_wait():
    """#381's must-show: the PR head moves after the install. The installed
    SHA comes from the record, so the wait refuses (a gate failure, not 5)
    and says the install is stale."""
    ctx = _install_ctx([("2026-09-25T16:00:00Z", _record(CUT))], pr_head=MOVED)
    check = _main_step().gate(ctx)
    assert not check.ok
    assert any("STALE" in line and MOVED[:12] in line for line in check.measured), check.measured
    out: list[str] = []
    assert run_steps([_main_step()], ctx, execute=True, out=out.append) == 11
    assert not any("WAITING" in line for line in out), "never reaches the approval wait"


def test_only_the_record_catches_a_head_the_clone_has_followed():
    """The head moved and this clone's HEAD moved with it (someone pulled):
    the PR head and the cut now AGREE, so only the SHA taken from the record
    shows the install was of another commit. A guard reading the head at
    the moment of checking would pass here by construction."""
    ctx = _install_ctx([("2026-09-25T16:00:00Z", _record(CUT))], pr_head=MOVED, cut=MOVED)
    check = _main_step().gate(ctx)
    assert not check.ok and any("STALE" in line for line in check.measured), check.measured


@pytest.mark.parametrize(
    "records, why",
    [
        ([], "no install recorded"),
        ([("2026-09-25T16:00:00Z", _record(CUT, "FAILED"))], "the install failed"),
        ([("2026-09-25T16:00:00Z", _record(CUT, version="9.9.8"))], "another release's record"),
        (
            [
                ("2026-09-25T16:00:00Z", _record(CUT)),
                ("2026-09-25T17:00:00Z", _record(CUT, "FAILED")),
            ],
            "the LATEST record decides",
        ),
    ],
)
def test_the_wait_refuses_without_a_current_passed_install(records, why):
    assert not _main_step().gate(_install_ctx(records)).ok, why


def test_a_strangers_later_passed_record_is_ignored_and_said():
    """ba and b5 on #383: the repository is public. After a real FAILED, a
    stranger's later "passed" record must not open the wait, and the gate
    must say it ignored one."""
    records = [
        ("2026-09-25T16:00:00Z", _record(CUT, "FAILED")),
        ("2026-09-25T17:00:00Z", _record(CUT), "some-stranger"),
    ]
    check = _main_step().gate(_install_ctx(records))
    assert not check.ok
    assert any("IGNORED" in line and "some-stranger" in line for line in check.measured)


def test_the_users_own_record_counts():
    """rdazzleman (the user) is in RECORD_AUTHORS beside the sessions' account."""
    records = [("2026-09-25T16:00:00Z", _record(CUT), "rdazzleman")]
    assert _main_step().gate(_install_ctx(records)).ok


def test_a_same_second_tie_is_broken_by_comment_order_not_by_text():
    """ba on #383: max() over (created, sha, result) put "passed" after
    "FAILED" at the same second. The later COMMENT decides."""
    at = "2026-09-25T16:00:00Z"
    later_failed = [(at, _record(CUT)), (at, _record(CUT, "FAILED"))]
    assert not _main_step().gate(_install_ctx(later_failed)).ok
    later_passed = [(at, _record(CUT, "FAILED")), (at, _record(CUT))]
    assert _main_step().gate(_install_ctx(later_passed)).ok


def test_the_tag_needs_the_install_record_too():
    tag = next(s for s in release.steps() if s.key == "tag")
    assert not tag.gate(_install_ctx([])).ok
    assert tag.gate(_install_ctx([("2026-09-25T16:00:00Z", _record(CUT))])).ok


def test_the_install_posts_its_record_where_the_wait_reads_it(monkeypatch):
    """The act installs the cut's archive and posts a comment whose LAST line
    is the record `_install_record` parses, for the SHA it installed."""
    calls: list = []
    ctx = _install_ctx([], calls=calls)
    ctx.hash_url = lambda url: "0" * 64
    ctx.install = lambda formula, version: (True, True, ["ok"])
    monkeypatch.setattr(
        release.release_check,
        "fetch_formula",
        lambda url=None: '  homepage "x"\n  url "x"\n  sha256 "' + "0" * 64 + '"\n',
    )
    release._install_head(ctx)
    posted = [a for a in calls if a[:2] == ["gh", "api"] and a[2].endswith("issues/99/comments")]
    assert len(posted) == 1
    body = posted[0][-1].removeprefix("body=")
    last = [line for line in body.splitlines() if line.strip()][-1]
    match = release.INSTALL_LINE.match(last)
    assert match and match.groups() == (CUT, "9.9.9", "passed"), last
    assert CUT in release._head_archive(ctx)


def test_a_dry_run_prints_the_order_with_the_install_before_the_approval(capsys, monkeypatch):
    """The plan is printed before any gate runs; walking the steps is stubbed
    so this touches no network. The default forge is Forgejo, whose gates
    need a token; this test is about the plan, so it supplies one."""
    monkeypatch.setenv("FORGEJO_TOKEN", "not-a-real-token")
    monkeypatch.setattr(release, "run_steps", lambda *a, **k: 0)
    assert release.main(["9.9.9"]) == 0
    text = capsys.readouterr().out
    assert text.index(". head-install:") < text.index(". main:") < text.index(". tag:"), text
    monkeypatch.setattr(release, "run_steps", lambda *a, **k: 0)
    assert release.main(["9.9.9", "--execute"]) == 0
    assert "The cut, in order" not in capsys.readouterr().out, "the plan is a dry-run aid"


def test_the_milestone_must_be_zero_by_both_instruments():
    def ctx(rest, search):
        return _ctx(
            run=_fake_run(
                {
                    "milestones": (0, '[{"title": "9.9.9", "number": 7}]'),
                    "issues?milestone=7": (0, rest),
                    "search/issues": (0, search),
                }
            )
        )

    assert release._milestone_open(ctx("0", "0")).ok
    assert not release._milestone_open(ctx("0", "3")).ok, "REST alone at 0 is the false zero"
    assert not release._milestone_open(ctx("", "0")).ok, "an unanswered instrument is not 0"


def test_resources_must_equal_the_lock(tmp_path, monkeypatch):
    lock = tmp_path / release.release_check.LOCK
    lock.parent.mkdir(parents=True)
    lock.write_text("click==8.1.7 \\\n    --hash=sha256:x\n", encoding="utf-8")
    # The real formula's shape: two-space indent, as `release_check._RESOURCE_RE` requires.
    formula = '  resource "click" do\n    url "https://files/click-8.1.7.tar.gz"\n'
    ctx = _ctx(root=tmp_path)
    ctx.notes["candidate_formula"] = formula
    assert release._resources_match_lock(ctx).ok
    ctx.notes["candidate_formula"] = formula.replace("8.1.7", "8.2.0")
    assert not release._resources_match_lock(ctx).ok
    ctx.notes["candidate_formula"] = "no resources here"
    assert not release._resources_match_lock(ctx).ok, "an empty set is not a match"


@pytest.mark.parametrize("bad", ["1.6", "v1.6.1", "1.6.1-rc1"])
def test_a_version_that_is_not_x_y_z_is_refused(bad):
    assert release.main([bad]) == 2


def test_after_the_cut_commit_the_tree_check_accepts_the_release_head():
    """Resume fix: once the cut is committed, HEAD is the release PR's head,
    not the train's remote tip, and a re-run must not stop at clean-tree."""

    def ctx(pr_head):
        return _ctx(
            run=_fake_run(
                {
                    "rev-parse --abbrev-ref HEAD": (0, "release/9.9.9"),
                    "ls-remote origin refs/heads/release/9.9.9": (
                        0,
                        "a" * 40 + "\trefs/heads/release/9.9.9",
                    ),
                    "rev-parse HEAD": (0, "b" * 40),
                    "status --porcelain": (0, ""),
                    "pr list": (
                        0,
                        '[{"title": "Release 9.9.9", "state": "OPEN", '
                        f'"headRefOid": "{pr_head}"}}]',
                    ),
                }
            )
        )

    assert release._train_tree(ctx("b" * 40)).ok
    assert not release._train_tree(ctx("c" * 40)).ok, "HEAD is neither the train tip nor the cut"


def test_the_status_probe_refuses_anything_but_https(tmp_path):
    """urllib opens `file://`; the probe answers 0 without opening it. The
    file EXISTS, because a missing one raises OSError and reads 0 anyway,
    which would pass this test with the guard deleted."""
    target = tmp_path / "secret.txt"
    target.write_text("x", encoding="utf-8")
    assert release._real_status(target.as_uri()) == 0
    assert release._real_status("http://github.com/x") == 0


# --- #348 review: step 6 against the cut, the homepage, the two artefact gates ----


def _merged_ctx(pr_head: str, cut: str, merge_tree: str, cut_tree: str):
    return _ctx(
        run=_fake_run(
            {
                "pr list": (
                    0,
                    '[{"title": "Release 9.9.9", "state": "MERGED", '
                    f'"headRefOid": "{pr_head}", "mergeCommit": {{"oid": "{"m" * 40}"}}}}]',
                ),
                "merge-base --is-ancestor": (0, ""),
                "rev-parse HEAD": (0, cut),
                f"rev-parse {'m' * 40}^{{tree}}": (0, merge_tree),
                f"rev-parse {cut}^{{tree}}": (0, cut_tree),
            }
        )
    )


def test_step_six_accepts_the_merge_of_the_cut_commit():
    assert release._main_merged(_merged_ctx("c" * 40, "c" * 40, "t1", "t1")).ok


def test_step_six_refuses_a_head_pushed_after_the_cut():
    """policyforge-9b on #348: something pushed to the release branch after
    step 5 was merged, and the old check compared the merge with that PR head
    AS FETCHED NOW, which passes by construction. Against the cut commit this
    script made, it is refused, even if the trees happen to agree."""
    moved = _merged_ctx("d" * 40, "c" * 40, "t1", "t1")
    check = release._main_merged(moved)
    assert not check.ok
    assert any("is the cut commit" in line and "False" in line for line in check.measured)


def test_step_six_refuses_a_merge_whose_tree_is_not_the_cut():
    assert not release._main_merged(_merged_ctx("c" * 40, "c" * 40, "t1", "t2")).ok


def test_the_candidate_formula_names_the_owner_in_homepage_and_url(monkeypatch):
    published = (
        '  homepage "https://github.com/rdazzlebot/policyforge"\n'
        '  url "https://github.com/rdazzlebot/policyforge/archive/refs/tags/v1.6.0.tar.gz"\n'
        f'  sha256 "{"0" * 64}"\n'
    )
    monkeypatch.setattr(release.release_check, "fetch_formula", lambda url=None: published)
    formula = release._formula(_ctx(), release._tag_archive(_ctx()), "1" * 64)
    assert release.release_check.names_canonical_homepage(
        release.release_check.homepage_in_formula(formula)
    )
    assert release.release_check.names_canonical_owner(
        release.release_check.source_url_in_formula(formula)
    )


def _records_ctx(comments, closed=("2026-09-25T01:00:00Z",), tip="a" * 40):
    import json as _json

    lines = "\n".join(
        _json.dumps([c[0], c[1], c[2] if len(c) > 2 else "rdazzlebot"]) for c in comments
    )
    return _ctx(
        tracking=99,
        run=_fake_run(
            {
                "milestones": (0, '[{"title": "9.9.9", "number": 7}]'),
                "state=closed": (0, "\n".join(closed)),
                "issues/99/comments": (0, lines),
                "ls-remote origin refs/heads/release/9.9.9": (
                    0,
                    tip + "\trefs/heads/release/9.9.9",
                ),
            }
        ),
    )


def _review(handle, at="2026-09-25T02:00:00Z", version="9.9.9"):
    return (at, f"Reviewed.\n\nPost-zero-review: {version} reviewer=policyforge-{handle}")


def test_the_post_zero_review_needs_all_three_after_the_zero():
    assert release._post_zero_reviewed(
        _records_ctx([_review("80"), _review("5b"), _review("1d")])
    ).ok
    assert not release._post_zero_reviewed(_records_ctx([_review("80"), _review("5b")])).ok
    early = [_review("80"), _review("5b"), _review("1d", at="2026-09-25T00:30:00Z")]
    assert not release._post_zero_reviewed(_records_ctx(early)).ok, (
        "a review BEFORE the zero is not one"
    )
    other = [_review("80"), _review("5b"), _review("1d", version="9.9.8")]
    assert not release._post_zero_reviewed(_records_ctx(other)).ok, "another release's review"


def test_a_quoted_review_line_is_not_a_review():
    """The last-line rule: discussing a review must not create one.

    Pinned to exactly which reviewer is missing: a bare `not ok` also held
    under a "first line counts" mutant, because that one dropped 80's and
    5b's real reviews instead -- right answer, wrong reason."""
    quoted = (
        "2026-09-25T02:00:00Z",
        "Post-zero-review: 9.9.9 reviewer=policyforge-1d\n\nquoted above.",
    )
    check = release._post_zero_reviewed(_records_ctx([_review("80"), _review("5b"), quoted]))
    assert not check.ok
    assert check.measured[-1].endswith("['1d']"), check.measured


def test_the_notes_measurement_must_name_the_train_tip():
    def measured(sha, at="2026-09-25T02:00:00Z"):
        return (
            at,
            f"Re-measured.\n\nNotes-measured-SHA: {sha} release=9.9.9 reviewer=policyforge-9b",
        )

    assert release._notes_measured(_records_ctx([measured("a" * 40)])).ok
    assert not release._notes_measured(_records_ctx([measured("b" * 40)])).ok, (
        "before the last merge"
    )
    stale_last = [
        measured("a" * 40, at="2026-09-25T02:00:00Z"),
        measured("b" * 40, at="2026-09-25T03:00:00Z"),
    ]
    assert not release._notes_measured(_records_ctx(stale_last)).ok, "the LATEST record decides"


def test_the_other_record_gates_ignore_strangers_too():
    """The same reader serves all three record types (80 on #383)."""
    at = "2026-09-25T02:00:00Z"
    forged_review = (at, "x\n\nPost-zero-review: 9.9.9 reviewer=policyforge-1d", "stranger")
    check = release._post_zero_reviewed(_records_ctx([_review("80"), _review("5b"), forged_review]))
    assert not check.ok and "['1d']" in check.measured[2], check.measured
    assert any("IGNORED" in line and "stranger" in line for line in check.measured)

    def measured(sha, when, *author):
        line = f"x\n\nNotes-measured-SHA: {sha} release=9.9.9 reviewer=policyforge-9b"
        return (when, line, *author)

    forged_tip = [
        measured("b" * 40, "2026-09-25T02:00:00Z"),
        measured("a" * 40, "2026-09-25T03:00:00Z", "stranger"),
    ]
    assert not release._notes_measured(_records_ctx(forged_tip)).ok, (
        "a stranger cannot name the tip"
    )


def test_the_artefact_gates_need_a_tracking_issue():
    assert not release._post_zero_reviewed(_ctx()).ok
    assert not release._notes_measured(_ctx()).ok


def test_the_artefact_gates_come_before_the_changelog():
    keys = _keys()
    assert keys.index("post-zero-review") < keys.index("changelog")
    assert keys.index("notes-measured") < keys.index("changelog")
    kinds = {s.key: s.kind for s in release.steps()}
    assert kinds["post-zero-review"] == CHECK and kinds["notes-measured"] == CHECK, (
        "a CHECK has no action, so --execute cannot satisfy it"
    )


# --- #348, ba's review: against REAL git repos, only `gh` stubbed ----------------
#
# A bare origin, the clone the script runs in, and a second clone that pushes.
# Real git because every defect here was about what git's refs and working
# tree actually hold, which a fake answers however it was written to.


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()


def _identity(repo) -> None:
    _git(repo, "config", "user.email", "release@example.invalid")
    _git(repo, "config", "user.name", "release test")
    _git(repo, "config", "core.autocrlf", "false")


@pytest.fixture
def train(tmp_path):
    """(origin, clone): release/9.9.9 pushed at 9.9.8 with one fragment."""
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    _identity(clone)
    files = {
        "pyproject.toml": 'version = "9.9.8"\n',
        "src/policyforge/__init__.py": '__version__ = "9.9.8"\n',
        "CHANGELOG.md": "# Changelog\n\n## 9.9.8\n\nOlder.\n",
        "changelog.d/README.md": "How to write a fragment.\n",
        "changelog.d/fix.md": "Fixed a thing.\n",
    }
    for path, text in files.items():
        (clone / path).parent.mkdir(parents=True, exist_ok=True)
        (clone / path).write_bytes(text.encode())
    _git(clone, "checkout", "-q", "-b", "release/9.9.9")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "train")
    _git(clone, "push", "-q", "origin", "release/9.9.9")
    return origin, clone


def _real(gh: dict | None = None, *, fail: str = "", calls: list | None = None):
    """Real git; `gh` from a fake; `fail` makes one git subcommand exit 1."""
    fake = _fake_run(gh or {})

    def run(argv):
        if calls is not None:
            calls.append(" ".join(argv))
        if argv[0] != "git":
            return fake(argv)
        if fail and argv[3:4] == [fail]:
            return subprocess.CompletedProcess(argv, 1, "", "refused by a hook")
        return subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )

    return run


def _late_merge(origin, tmp_path) -> str:
    """Another clone pushes to the train; returns the new server tip."""
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", "-q", "-b", "release/9.9.9", str(origin), str(other)],
        check=True,
        capture_output=True,
    )
    _identity(other)
    (other / "late.txt").write_bytes(b"a late merge\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "late merge")
    _git(other, "push", "-q", "origin", "release/9.9.9")
    return _git(other, "rev-parse", "HEAD")


def test_the_train_gates_read_the_server_not_this_clones_last_fetch(train, tmp_path):
    """F1. This clone has not fetched since another clone pushed. Both gates
    that read the train tip must refuse, and notes-measured must refuse the
    measurement taken at the stale tip: the one it exists to refuse."""
    origin, clone = train
    stale = _git(clone, "rev-parse", "HEAD")
    tip = _late_merge(origin, tmp_path)
    assert _git(clone, "rev-parse", "origin/release/9.9.9") == stale, "the clone is stale"

    ctx = _ctx(root=clone, run=_real())
    tree = release._train_tree(ctx)
    assert not tree.ok
    assert any(tip[:12] in line for line in tree.measured), tree.measured

    def notes(sha):
        record = json.dumps(
            [
                "2026-09-25T02:00:00Z",
                f"x\n\nNotes-measured-SHA: {sha} release=9.9.9 reviewer=policyforge-9b",
                "rdazzlebot",
            ]
        )
        return _ctx(root=clone, tracking=99, run=_real({"issues/99/comments": (0, record)}))

    assert not release._notes_measured(notes(stale)).ok, "measured before the late merge"
    assert release._notes_measured(notes(tip)).ok, "and it passes at the real tip"


def _write_cut(clone) -> None:
    for path, old in (
        ("pyproject.toml", "version"),
        ("src/policyforge/__init__.py", "__version__"),
    ):
        (clone / path).write_bytes(f'{old} = "9.9.9"\n'.encode())
    (clone / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## 9.9.9\n\nFixed a thing.\n\n## 9.9.8\n")
    (clone / "changelog.d" / "fix.md").unlink()


def test_a_refused_commit_stops_the_push_and_fails_the_postcondition(train):
    """F2. The commit is refused. Nothing after it may run (the push would
    send the train tip under the release's name), and the postcondition must
    say the cut is not at HEAD, even with a PR reported sitting there."""
    _origin, clone = train
    _write_cut(clone)
    calls: list[str] = []
    head = _git(clone, "rev-parse", "HEAD")
    pr = f'[{{"title": "Release 9.9.9", "number": 9, "state": "OPEN", "headRefOid": "{head}"}}]'
    ctx = _ctx(root=clone, run=_real({"pr list": (0, pr)}, fail="commit", calls=calls))
    release._open_pr(ctx)
    assert not any(" push " in c or "pr create" in c for c in calls), calls
    check = release._pr_open(ctx)
    assert not check.ok
    text = "\n".join(check.measured)
    assert "pyproject 9.9.8" in text and "uncommitted changes: 4" in text, text
    assert "exited 1, so nothing after it ran" in text


def test_a_committed_cut_passes_the_postcondition(train):
    """F2's passing case: the same cut, committed and pushed for real."""
    _origin, clone = train
    _write_cut(clone)
    ctx = _ctx(root=clone, run=_real())
    release._open_pr(ctx)
    head = _git(clone, "rev-parse", "HEAD")
    pushed = _git(clone, "ls-remote", "origin", "refs/heads/9b/release-9.9.9").split()
    assert pushed and pushed[0] == head, "the cut is on the server"
    pr = f'[{{"title": "Release 9.9.9", "number": 9, "state": "OPEN", "headRefOid": "{head}"}}]'
    check = release._pr_open(_ctx(root=clone, run=_real({"pr list": (0, pr)})))
    assert check.ok, check.measured


def test_a_committed_cut_with_uncommitted_changes_beside_it_is_refused(train):
    """F2: HEAD is the cut, but the tree is not what was pushed. The re-run's
    clean-tree would refuse it, so the postcondition says so here, first."""
    _origin, clone = train
    _write_cut(clone)
    release._open_pr(_ctx(root=clone, run=_real()))
    (clone / "CHANGELOG.md").write_bytes(b"# Changelog\n\n## 9.9.9\n\nEdited after the commit.\n")
    head = _git(clone, "rev-parse", "HEAD")
    pr = f'[{{"title": "Release 9.9.9", "number": 9, "state": "OPEN", "headRefOid": "{head}"}}]'
    check = release._pr_open(_ctx(root=clone, run=_real({"pr list": (0, pr)})))
    assert not check.ok and "uncommitted changes: 1" in "\n".join(check.measured)


@pytest.mark.parametrize(
    "undo",
    ["pyproject.toml", "src/policyforge/__init__.py", "CHANGELOG.md", "changelog.d/fix.md"],
)
def test_each_part_of_the_cut_is_read_at_head(train, undo):
    """F2, one part at a time: commit the cut with that part left as it was
    on the train, and the postcondition must refuse."""
    _origin, clone = train
    _write_cut(clone)
    _git(clone, "checkout", "HEAD", "--", undo)
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "partial cut")
    head = _git(clone, "rev-parse", "HEAD")
    pr = f'[{{"title": "Release 9.9.9", "number": 9, "state": "OPEN", "headRefOid": "{head}"}}]'
    assert not release._pr_open(_ctx(root=clone, run=_real({"pr list": (0, pr)}))).ok


def test_declining_the_push_writes_nothing_so_a_rerun_starts_clean():
    """F3. The key for release-pr is asked before the changelog step writes
    anything; declining it stops with nothing acted, and agreeing asks once."""
    acted: list[str] = []

    def steps():
        cut = [_step("changelog", acted=acted), _step("version", acted=acted)]
        for step in cut:
            step.commits_to = "release-pr"
        return [*cut, _step("release-pr", outward=True, acted=acted)]

    asked: list[str] = []
    no = _ctx(confirm=lambda prompt: asked.append(prompt) or "no")
    assert run_steps(steps(), no, execute=True, out=lambda s: None) == 6
    assert acted == [], "nothing written, so clean-tree holds on the re-run"
    assert len(asked) == 1 and "release-pr" in asked[0]

    asked.clear()
    yes = _ctx(confirm=lambda prompt: asked.append(prompt) or "release-pr")
    assert run_steps(steps(), yes, execute=True, out=lambda s: None) == 0
    assert acted == ["changelog", "version", "release-pr"]
    assert len(asked) == 1, "one agreement covers the cut and its push"


def test_the_real_cut_steps_ask_for_the_push_before_writing():
    by_key = {s.key: s for s in release.steps()}
    assert by_key["changelog"].commits_to == "release-pr"
    assert by_key["version"].commits_to == "release-pr"
    assert by_key["release-pr"].outward


def test_an_interrupted_cut_is_named_and_a_foreign_change_is_not(train):
    """F3's residue (a failed postcondition or Ctrl-C at 5 or 6): clean-tree
    still refuses, and names the restore only when the dirt is the cut's."""
    _origin, clone = train
    _write_cut(clone)
    cut = release._train_tree(_ctx(root=clone, run=_real()))
    assert not cut.ok and "git restore" in cut.measured[-1], cut.measured
    (clone / "notes.txt").write_bytes(b"mine\n")
    foreign = release._train_tree(_ctx(root=clone, run=_real()))
    assert not foreign.ok and not any("git restore" in line for line in foreign.measured)


def test_the_install_ends_by_reading_the_installed_version():
    """The one check that reads the artefact: the last link of the `&&` chain
    compares the virtualenv's `policyforge.__version__` with the release."""
    script = release._install_script("9.9.9")
    last = script.split(" && ")[-1]
    assert last.startswith('test "$(') and last.endswith('= "9.9.9"'), last
    assert "policyforge.__version__" in last and "libexec/bin/python" in last


# --- #348, b5's review --------------------------------------------------------


def test_clean_tree_refuses_another_versions_train(train):
    """A: on release/9.9.9, clean and at its server tip, cutting 9.9.7 must
    be refused; with a prefix test it passed and cut 9.9.7 from 9.9.9's code."""
    _origin, clone = train
    assert release._train_tree(_ctx(root=clone, run=_real())).ok, "the right train passes"
    other = _ctx(root=clone, run=_real())
    other.version = "9.9.7"
    check = release._train_tree(other)
    assert not check.ok
    assert "must be 'release/9.9.7'" in check.measured[0], check.measured


def test_clean_tree_refuses_another_versions_train_even_at_the_release_pr_head():
    """A, the resume route: HEAD is the release PR's head (the cut commit),
    so the server-tip half is not needed to pass. The branch must still be
    this version's train."""

    def ctx(branch):
        return _ctx(
            run=_fake_run(
                {
                    "rev-parse --abbrev-ref HEAD": (0, branch),
                    "rev-parse HEAD": (0, "c" * 40),
                    "pr list": (
                        0,
                        '[{"title": "Release 9.9.9", "state": "OPEN", '
                        f'"headRefOid": "{"c" * 40}"}}]',
                    ),
                }
            )
        )

    assert release._train_tree(ctx("release/9.9.9")).ok
    assert not release._train_tree(ctx("release/9.9.8")).ok


def _prs(*entries):
    return json.dumps(
        [{"title": t, "number": n, "state": s, "headRefOid": "a" * 40} for t, n, s in entries]
    )


def test_the_release_pr_is_matched_by_its_exact_title():
    """C: "Release 1.6.10" is not 1.6.1's PR."""
    ctx = _ctx(run=_fake_run({"pr list": (0, _prs(("Release 9.9.90", 5, "OPEN")))}))
    assert release._release_pr(ctx) == {}
    ctx = _ctx(
        run=_fake_run(
            {"pr list": (0, _prs(("Release 9.9.90", 5, "OPEN"), ("Release 9.9.9", 6, "OPEN")))}
        )
    )
    assert release._release_pr(ctx)["number"] == 6


@pytest.mark.parametrize(
    "listed",
    [
        (("Release 9.9.9", 1, "CLOSED"), ("Release 9.9.9", 2, "MERGED")),
        (("Release 9.9.9", 2, "MERGED"), ("Release 9.9.9", 1, "CLOSED")),
    ],
)
def test_a_closed_first_attempt_never_shadows_the_merged_release_pr(listed):
    """C2: whatever order gh lists them in, the merged PR is the one read."""
    ctx = _ctx(run=_fake_run({"pr list": (0, _prs(*listed))}))
    assert release._release_pr(ctx)["number"] == 2


def test_an_open_retry_is_read_before_a_closed_first_attempt():
    ctx = _ctx(
        run=_fake_run(
            {"pr list": (0, _prs(("Release 9.9.9", 1, "CLOSED"), ("Release 9.9.9", 2, "OPEN")))}
        )
    )
    assert release._release_pr(ctx)["number"] == 2


def test_the_wait_refuses_a_closed_release_pr_instead_of_waiting_forever():
    """ba on #348: a closed-unmerged release PR at HEAD made release-pr
    count as done and the wait say "waiting" forever. Its gate now fails,
    naming the PR's state; an open PR is still waited for."""
    main = next(s for s in release.steps() if s.key == "main")

    def ctx(state):
        return _ctx(run=_fake_run({"pr list": (0, _prs(("Release 9.9.9", 4, state)))}))

    closed = main.gate(ctx("CLOSED"))
    assert not closed.ok and "#4 CLOSED" in closed.measured[0]
    # The PR-state half of the wait's gate; since #381 the gate also needs a
    # current install, tested on its own above.
    assert release._pr_still_open(ctx("OPEN")).ok
    assert not release._pr_still_open(_ctx(run=_fake_run({}))).ok, "no PR is nothing to wait for"


# --- #391: a commit-archive formula declares its version; the PR by its branch ---

_PUBLISHED = (
    '  homepage "https://github.com/rdazzlebot/policyforge"\n'
    '  url "https://github.com/rdazzlebot/policyforge/archive/refs/tags/v1.6.0.tar.gz"\n'
    f'  sha256 "{"0" * 64}"\n'
    '  license "Apache-2.0"\n'
)


def test_the_step_8_formula_declares_its_version(monkeypatch):
    """9b at the 1.6.1 cut: Homebrew 7 refuses a formula whose url is a commit
    archive and which declares no version ("version (nil)"). Step 8's formula
    carries it, between url and sha256."""
    monkeypatch.setattr(release.release_check, "fetch_formula", lambda url=None: _PUBLISHED)
    ctx = _ctx(run=_fake_run({"rev-parse HEAD": (0, CUT)}))
    formula = release._formula(ctx, release._head_archive(ctx), "1" * 64)
    lines = formula.splitlines()
    assert '  version "9.9.9"' in lines, formula
    url = next(i for i, line in enumerate(lines) if line.startswith("  url "))
    sha = next(i for i, line in enumerate(lines) if line.startswith("  sha256 "))
    assert lines.index('  version "9.9.9"') == url + 1 < sha


def test_a_tag_formula_declares_none(monkeypatch):
    """Beside a tag url the version is scanned from it, and an explicit one is
    what `brew audit --strict` calls redundant: the candidate formula has none."""
    monkeypatch.setattr(release.release_check, "fetch_formula", lambda url=None: _PUBLISHED)
    formula = release._formula(_ctx(), release._tag_archive(_ctx()), "1" * 64)
    assert "version " not in formula, formula


def test_the_install_chain_updates_homebrew_first():
    """The image ships Homebrew 4.6.20; users have the current one. The chain
    updates before anything is installed, so the result is the user's."""
    steps = release._install_script("9.9.9").split(" && ")
    assert steps[0] == "brew update --quiet"
    assert steps.index("brew update --quiet") < next(
        i for i, s in enumerate(steps) if s.startswith("brew install")
    )


def test_the_release_pr_is_found_by_its_branch_not_by_search():
    """9b at the 1.6.1 cut: `--search` is an index and missed a PR opened a
    moment before. The lookup names the branch `_open_pr` pushes."""
    calls: list = []

    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, _prs(("Release 9.9.9", 390, "OPEN")), "")

    assert release._release_pr(_ctx(run=run))["number"] == 390
    (argv,) = [a for a in calls if a[:3] == ["gh", "pr", "list"]]
    assert "--search" not in argv
    assert argv[argv.index("--head") + 1] == "9b/release-9.9.9"


def test_a_version_the_published_formula_already_has_is_replaced_not_doubled(monkeypatch):
    """ba on #392: "replaced, never duplicated" was stated and untested. A
    published formula that already declares a version gets exactly one, the
    release's, on a commit archive, and none on a tag url."""
    published = _PUBLISHED.replace('  sha256 "', '  version "1.6.0"\n  sha256 "', 1)
    monkeypatch.setattr(release.release_check, "fetch_formula", lambda url=None: published)
    ctx = _ctx(run=_fake_run({"rev-parse HEAD": (0, CUT)}))
    commit = release._formula(ctx, release._head_archive(ctx), "1" * 64).splitlines()
    assert [line for line in commit if line.startswith("  version ")] == ['  version "9.9.9"']
    tag = release._formula(_ctx(), release._tag_archive(_ctx()), "1" * 64)
    assert "version " not in tag, tag


def test_a_crlf_published_formula_still_gets_its_version(monkeypatch):
    """b5 on #392: the patterns anchor on "\n", so a CRLF formula silently
    got no version line and would fail step 8 like #391. Normalised first."""
    crlf = _PUBLISHED.replace("\n", "\r\n")
    monkeypatch.setattr(release.release_check, "fetch_formula", lambda url=None: crlf)
    ctx = _ctx(run=_fake_run({"rev-parse HEAD": (0, CUT)}))
    formula = release._formula(ctx, release._head_archive(ctx), "1" * 64)
    assert '  version "9.9.9"' in formula.splitlines(), formula
    assert "\r" not in formula


# --- #389: the formula's pins are checked before anything installs with them ---


def test_the_pins_are_checked_before_the_head_install_and_the_tag():
    """#389: `_formula` keeps the published formula's resource pins, so they
    are checked before step 8 installs with them, not after the tag."""
    keys = _keys()
    assert keys.index("resources") < keys.index("head-install") < keys.index("tag")


def test_the_pins_are_checked_before_the_cut_is_written():
    """policyforge-ba on #419: after `release-pr`, a mismatch was found with
    the cut PR already open. Nothing outward, and nothing written, comes
    before the check."""
    steps = release.steps()
    first_writer = next(i for i, s in enumerate(steps) if s.kind == ACT)
    assert [s.key for s in steps].index("resources") < first_writer


def test_a_missing_formula_file_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        release.main(["9.9.9", "--formula", str(tmp_path / "absent.rb")])
    assert stop.value.code == 2
    assert "no such file" in capsys.readouterr().err


def test_a_stale_pin_is_refused_before_the_install_and_says_what_to_do(tmp_path, monkeypatch):
    """Before any candidate exists, the published formula is what is read,
    and a mismatch says to regenerate the resource stanzas."""
    lock = tmp_path / release.release_check.LOCK
    lock.parent.mkdir(parents=True)
    lock.write_text("click==8.2.0 \\\n    --hash=sha256:x\n", encoding="utf-8")
    published = '  resource "click" do\n    url "https://files/click-8.1.7.tar.gz"\n'
    monkeypatch.setattr(release.release_check, "fetch_formula", lambda url=None: published)
    step = next(s for s in release.steps() if s.key == "resources")
    check = step.gate(_ctx(root=tmp_path))
    assert not check.ok
    assert any("regenerate the formula's resource stanzas" in line for line in check.measured)


def _lock(tmp_path, pin):
    lock = tmp_path / release.release_check.LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    # The lock's real shape: a pin, a line continuation, then its hash.
    lock.write_text(f"click=={pin} \\\n    --hash=sha256:x\n", encoding="utf-8")


def _resource_formula(pin):
    return f'  resource "click" do\n    url "https://files/click-{pin}.tar.gz"\n'


def test_a_lock_change_without_a_regenerated_formula_names_the_flag(tmp_path, monkeypatch):
    """The lock moved (1.7: anthropic, wcwidth); the published formula has the
    old pins; the gate refuses and says how through: --formula."""
    _lock(tmp_path, "8.2.0")
    monkeypatch.setattr(
        release.release_check, "fetch_formula", lambda url=None: _resource_formula("8.1.7")
    )
    check = next(s for s in release.steps() if s.key == "resources").gate(_ctx(root=tmp_path))
    assert not check.ok
    assert any("the published formula" in line for line in check.measured)
    assert any("--formula PATH" in line for line in check.measured)


def test_a_reconciled_formula_passes_and_is_what_the_cut_edits(tmp_path, monkeypatch):
    """--formula is read by the gate AND is the base _formula edits, so the
    pins checked are the pins installed."""
    _lock(tmp_path, "8.2.0")
    monkeypatch.setattr(
        release.release_check, "fetch_formula", lambda url=None: _resource_formula("8.1.7")
    )
    reconciled = tmp_path / "policyforge.rb"
    reconciled.write_text(
        '  url "x"\n  sha256 "' + "0" * 64 + '"\n' + _resource_formula("8.2.0"), encoding="utf-8"
    )
    ctx = _ctx(root=tmp_path, formula_path=reconciled)
    check = next(s for s in release.steps() if s.key == "resources").gate(ctx)
    assert check.ok, check.measured
    assert any(f"--formula {reconciled}" in line for line in check.measured)
    built = release._formula(ctx, release._tag_archive(ctx), "1" * 64)
    assert "click-8.2.0" in built and "click-8.1.7" not in built


def test_an_unreconciled_formula_is_refused(tmp_path, monkeypatch):
    """`update-python-resources` proposes PyPI's latest; the lock decides. A
    --formula with a pin off the lock is refused, not trusted for being given."""

    def no_network(url=None):
        raise AssertionError("--formula was given, so the published formula must not be read")

    monkeypatch.setattr(release.release_check, "fetch_formula", no_network)
    _lock(tmp_path, "8.2.0")
    proposed = tmp_path / "policyforge.rb"
    proposed.write_text(_resource_formula("8.3.0"), encoding="utf-8")
    ctx = _ctx(root=tmp_path, formula_path=proposed)
    assert not next(s for s in release.steps() if s.key == "resources").gate(ctx).ok


# --- steps 13 and 14 as the 1.6.1 cut ran them (#415, #416, #407) -------------


def test_step_14_argv_parses_with_release_checks_own_parser():
    """#415: the 1.6.1 cut passed the version positionally and argparse exited
    2, so the final check never ran. The argv step 14 builds is parsed here by
    release_check's real parser, not by a description of it."""
    seen: list[list[str]] = []
    ctx = _ctx(run=lambda argv: seen.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    step = next(s for s in release.steps() if s.key == "release-check")
    assert step.gate(ctx).ok
    (argv,) = seen
    assert Path(argv[1]) == release.REPO_ROOT / "scripts" / "release_check.py"
    assert Path(argv[1]).is_file()
    args = release.release_check.parser().parse_args(argv[2:])
    assert args.version == ctx.version
    assert args.allow_skip == [], "step 14 must not excuse any check"


_TIP = "a" * 40
_PUSHED = "b" * 40


def _tap_run(calls, *, tip=_TIP, served="", fail="", identity=("Rel Ease", "rel@example.invalid")):
    """git and gh as the publish steps meet them. `fail` names the git
    subcommand that exits 128, as `commit` did on the 1.6.1 release machine."""

    def run(argv):
        calls.append(argv)
        if "config" in argv:
            value = identity[0] if argv[-1] == "user.name" else identity[1]
            return subprocess.CompletedProcess(argv, 0 if value else 1, value + "\n", "")
        if "ls-remote" in argv:
            return subprocess.CompletedProcess(
                argv, 0, f"{tip}\trefs/heads/main\n" if tip else "", ""
            )
        if argv[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(argv, 0, served, "")
        if fail and fail in argv:
            return subprocess.CompletedProcess(
                argv, 128, "", "fatal: unable to auto-detect email address"
            )
        if "clone" in argv:
            (Path(argv[-1]) / "Formula").mkdir(parents=True)
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, _PUSHED + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def _stale_raw(monkeypatch):
    monkeypatch.setattr(
        release.release_check, "fetch_formula", lambda url=None: "class Stale; end\n"
    )


def test_publish_postcondition_reads_the_api_at_the_servers_tip_not_raw(monkeypatch):
    """#416: raw.githubusercontent.com served 1.6.0 for ~300 s after a correct
    push. The postcondition asks the contents API at the tip ls-remote names."""
    _stale_raw(monkeypatch)
    calls: list[list[str]] = []
    ctx = _ctx(run=_tap_run(calls, served="class Candidate; end\n"))
    ctx.notes["candidate_formula"] = "class Candidate; end\n"
    check = release._published(ctx)
    assert check.ok, check.measured
    (api,) = [c for c in calls if c[:2] == ["gh", "api"]]
    assert api[-1].endswith(f"contents/{release.TAP_FORMULA_PATH}?ref={_TIP}")
    assert "Accept: application/vnd.github.raw" in api


def test_publish_postcondition_fails_on_the_old_formula_and_on_no_tip(monkeypatch):
    _stale_raw(monkeypatch)
    ctx = _ctx(run=_tap_run([], served="class Old; end\n"))
    ctx.notes["candidate_formula"] = "class Candidate; end\n"
    assert not release._published(ctx).ok
    ctx = _ctx(run=_tap_run([], tip="", served="class Candidate; end\n"))
    ctx.notes["candidate_formula"] = "class Candidate; end\n"
    assert not release._published(ctx).ok


@pytest.mark.parametrize("failing", ["clone", "commit"])
def test_publish_stops_at_the_first_failure_and_never_pushes(failing, tmp_path, monkeypatch):
    """#407: commit exited 128, push then exited 0 on nothing, and no status was
    read. Now the first failure stops the step, is named, and the
    postcondition fails on it even while the tap still serves a match."""
    monkeypatch.setattr(release.tempfile, "mkdtemp", lambda: str(tmp_path / "tap"))
    calls: list[list[str]] = []
    ctx = _ctx(run=_tap_run(calls, fail=failing, served="class Candidate; end\n"))
    ctx.notes["candidate_formula"] = "class Candidate; end\n"
    release._publish(ctx)
    assert not any("push" in c for c in calls), calls
    assert failing in ctx.notes["publish"] and "exited 128" in ctx.notes["publish"]
    check = release._published(ctx)
    assert not check.ok
    assert any(line.startswith("publish: refused") for line in check.measured)


def test_publish_needs_an_identity_and_passes_it_to_the_commit_only(tmp_path, monkeypatch):
    monkeypatch.setattr(release.tempfile, "mkdtemp", lambda: str(tmp_path / "tap"))
    calls: list[list[str]] = []
    ctx = _ctx(run=_tap_run(calls, identity=("", "")))
    ctx.notes["candidate_formula"] = "class Candidate; end\n"
    release._publish(ctx)
    assert "refused" in ctx.notes["publish"]
    assert not any("clone" in c or "push" in c for c in calls)

    calls.clear()
    ctx = _ctx(run=_tap_run(calls))
    ctx.notes["candidate_formula"] = "class Candidate; end\n"
    release._publish(ctx)
    assert "publish" not in ctx.notes, ctx.notes
    (commit,) = [c for c in calls if "commit" in c]
    assert "user.name=Rel Ease" in commit and "user.email=rel@example.invalid" in commit
    assert (tmp_path / "tap" / release.TAP_FORMULA_PATH).read_text() == "class Candidate; end\n"
    assert ctx.notes["tap_sha"] == _PUSHED
    assert any("push" in c for c in calls)


def test_a_failed_push_is_refused_even_while_the_tap_already_matches(tmp_path, monkeypatch):
    """#407, policyforge-b5 on #419: with only clone and commit parametrised, a
    `_publish` that ignored the push's status survived. The input that tells
    them apart is a push that fails while the tap already serves a match."""
    monkeypatch.setattr(release.tempfile, "mkdtemp", lambda: str(tmp_path / "tap"))
    ctx = _ctx(run=_tap_run([], fail="push", served="class Candidate; end\n"))
    ctx.notes["candidate_formula"] = "class Candidate; end\n"
    release._publish(ctx)
    assert "push" in ctx.notes["publish"] and "exited 128" in ctx.notes["publish"]
    check = release._published(ctx)
    assert not check.ok, check.measured


# --- #413: the install pins move with the cut ----------------------------------

_OWNER = release.release_check.REPOSITORY  # "<owner>/policyforge"


def _git_repo(tmp_path, files: dict[str, str]) -> Path:
    """A real repository, so `git grep` itself is what is measured."""
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    for argv in (["init", "-q"], ["add", "-A"], [*ident, "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(tmp_path), *argv], check=True, capture_output=True)
    return tmp_path


def _pin_ctx(root: Path) -> Context:
    return _ctx(root=root, run=release._real_run)


def test_instruction_pins_move_and_history_and_fixtures_do_not(tmp_path):
    root = _git_repo(
        tmp_path,
        {
            "README.md": (
                f"pipx install git+https://github.com/{_OWNER}@v1.6.0\n"
                f"and again {_OWNER}.git@v1.5.0\n"
            ),
            "CHANGELOG.md": f"## 1.6.0\n\nInstall with {_OWNER}@v1.6.0\n",
            "tests/fixture.md": f"{_OWNER}@v1.6.0\n",
        },
    )
    ctx = _pin_ctx(root)
    gate = release._pins_ready(ctx)
    assert (
        gate.ok
        and "install pins: instruction 2, history 1, test 1, unclassified 0" in gate.measured
    )
    assert not release._pins_current(ctx).ok
    release._repin(ctx)
    after = release._pins_current(ctx)
    assert after.ok, after.measured
    assert (root / "README.md").read_text() == (
        f"pipx install git+https://github.com/{_OWNER}@v9.9.9\nand again {_OWNER}.git@v9.9.9\n"
    )
    assert "@v1.6.0" in (root / "CHANGELOG.md").read_text()
    assert "@v1.6.0" in (root / "tests" / "fixture.md").read_text()


def test_a_pin_naming_another_owner_is_refused_not_moved(tmp_path):
    """A redirect is not a reference: the old owner is fixed by hand, not
    silently re-versioned into a working-looking stale URL."""
    root = _git_repo(
        tmp_path,
        {"README.md": "pipx install git+https://github.com/rdazzlebot/policyforge@v1.6.0\n"},
    )
    gate = release._pins_ready(_pin_ctx(root))
    assert not gate.ok and any("another owner" in line for line in gate.measured)


def test_no_answer_from_git_grep_is_not_zero_pins():
    failed = _ctx(
        run=lambda argv: subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository")
    )
    assert not release._pins_ready(failed).ok
    assert not release._pins_current(failed).ok


def test_a_pin_lost_by_the_rewrite_fails_the_step(tmp_path):
    root = _git_repo(tmp_path, {"README.md": f"{_OWNER}@v1.6.0\n{_OWNER}@v1.6.0\n"})
    ctx = _pin_ctx(root)
    assert release._pins_ready(ctx).ok
    (root / "README.md").write_text(f"{_OWNER}@v9.9.9\n", encoding="utf-8")
    check = release._pins_current(ctx)
    assert not check.ok and any("2 before, 1 after" in line for line in check.measured)


def test_the_cut_at_head_reads_the_committed_pins(tmp_path):
    root = _git_repo(tmp_path, {"README.md": f"{_OWNER}@v1.6.0\n"})
    ctx = _pin_ctx(root)
    pins_row = lambda: next(r for r in release._cut_at_head(ctx) if "install pins" in r[1])  # noqa: E731
    release._repin(ctx)
    assert pins_row()[0] is False, "rewritten but not committed: HEAD still says 1.6.0"
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-qam",
            "cut",
        ],
        check=True,
    )
    assert pins_row()[0] is True


def test_the_pins_move_inside_the_cut_commit():
    keys = _keys()
    assert keys.index("version") < keys.index("install-pins") < keys.index("release-pr")
    step = next(s for s in release.steps() if s.key == "install-pins")
    assert step.commits_to == "release-pr"


def test_this_repositorys_install_pins_all_name_the_canonical_owner():
    """The population the step will meet: README's pipx lines (80's sweep on
    #413 found two, both in README). Asserted as a property, not a count."""
    pins = release._pins(_ctx(root=release.REPO_ROOT, run=release._real_run))
    assert pins is not None
    instructions = [p for p in pins if p.kind == "instruction"]
    assert instructions and all(p.canonical for p in instructions)
    assert any(p.path == "README.md" for p in instructions)


def test_a_pin_in_another_case_is_moved_and_keeps_its_spelling(tmp_path):
    """policyforge-ba on #430: GitHub installs `rdazzleman/PolicyForge@v1.6.0`,
    and a case-sensitive match skipped it."""
    owner = _OWNER.split("/")[0]
    spelled = f"{owner.upper()}/PolicyForge.GIT@v1.6.0"
    root = _git_repo(tmp_path, {"README.md": f"pipx install git+https://github.com/{spelled}\n"})
    ctx = _pin_ctx(root)
    assert release._pins_ready(ctx).ok
    release._repin(ctx)
    assert release._pins_current(ctx).ok
    assert (root / "README.md").read_text() == (
        f"pipx install git+https://github.com/{owner.upper()}/PolicyForge.GIT@v9.9.9\n"
    )


def test_a_capital_v_names_no_tag_and_is_refused(tmp_path):
    root = _git_repo(tmp_path, {"README.md": f"{_OWNER}@V1.6.0\n"})
    gate = release._pins_ready(_pin_ctx(root))
    assert not gate.ok and any("names no tag" in line for line in gate.measured)


def test_a_pin_quoted_as_a_record_elsewhere_is_refused_not_rewritten(tmp_path):
    """policyforge-b5 on #430: "epoch 24 installed ...@v1.6.0" is a record. Read
    as an instruction it became the new release, and the conserved count
    called that success (#252's shape). A pin outside INSTALL_DOCS stops
    the cut, naming the file, and nothing is written."""
    record = f"Epoch 24 installed {_OWNER}@v1.6.0.\n"
    root = _git_repo(tmp_path, {"README.md": f"{_OWNER}@v1.6.0\n", "MEASUREMENTS.md": record})
    gate = release._pins_ready(_pin_ctx(root))
    assert not gate.ok
    assert any("MEASUREMENTS.md:1" in line and "INSTALL_DOCS" in line for line in gate.measured)
    assert "unclassified 1" in gate.measured[0]
    assert (root / "MEASUREMENTS.md").read_text() == record


def test_a_pre_release_pin_moves_whole_and_a_full_stop_stays(tmp_path):
    """policyforge-b5 on #430: `1.7.0rc1` was skipped and `1.7.0-rc.1` half-moved."""
    # `d` is the case that tells a suffix ending on a letter or digit from one
    # that may end on a dot: the full stop after `rc1` is not the version's.
    text = (
        f"a {_OWNER}@v1.7.0rc1\nb {_OWNER}@v1.7.0-rc.1\nc {_OWNER}@v1.6.0.\nd {_OWNER}@v1.7.0rc1.\n"
    )
    root = _git_repo(tmp_path, {"README.md": text})
    ctx = _pin_ctx(root)
    assert release._pins_ready(ctx).ok
    release._repin(ctx)
    assert release._pins_current(ctx).ok
    assert (root / "README.md").read_text() == (
        f"a {_OWNER}@v9.9.9\nb {_OWNER}@v9.9.9\nc {_OWNER}@v9.9.9.\nd {_OWNER}@v9.9.9.\n"
    )
