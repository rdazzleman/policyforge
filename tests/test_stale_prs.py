"""The queue check fires on the state that produced the ten hours, and on no other.

Written against the incident rather than against the idea of one: on
2026-09-19 a pull request sat `CLEAN` with zero pending checks for ten
hours while being listed in status reports as "other sessions' work" — a
fact about whose branch it is, read as a fact about whose turn it is.

The risk in a check like this is not missing that case. It is firing on the
four states that look similar and are someone's turn — checks running,
review outstanding, a conflict, a draft. A queue check that nags is one
people stop reading, and then the queue has no owner again with a script
running over it.
"""

from __future__ import annotations

import contextlib
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import stale_prs


def _ago(minutes: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return moment.isoformat().replace("+00:00", "Z")


HEAD = "a" * 40


def _verdict(sha: str = HEAD, verdict: str = "approved") -> dict:
    return {"body": f"Read it.\n\nReviewed-SHA: {sha} verdict={verdict} reviewer=policyforge-9b"}


def _pull(**overrides):
    pull = {
        "number": 132,
        "title": "a change",
        "headRefName": "fix/local-provider-accounting",
        "updatedAt": _ago(600),
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
        "author": {"login": "e2"},
        "createdAt": _ago(700),
        "headRefOid": HEAD,
        # Read at head by default, so the ready-and-unmerged tests below
        # exercise only what they were written for.
        "comments": [_verdict()],
    }
    pull.update(overrides)
    return pull


def _run(monkeypatch, pulls, pending=0, argv=None, parked=()):
    monkeypatch.setattr(stale_prs, "open_pull_requests", lambda repo=None: pulls)
    # `pending` keeps its old meaning; an int is (pending, 0 failing), a tuple
    # passes through, None is "no check-runs readable".
    checks = pending if pending is None or isinstance(pending, tuple) else (pending, 0)
    monkeypatch.setattr(stale_prs, "head_checks", lambda sha, repo=None: checks)
    monkeypatch.setattr(
        stale_prs, "branches_without_pull_request", lambda repo=None: (list(parked), False)
    )
    monkeypatch.setattr(stale_prs, "head_committed_at", lambda sha, repo=None: None)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = stale_prs.main(argv or [])
    return code, out.getvalue()


def test_the_incident_fires(monkeypatch):
    """`CLEAN`, nothing pending, ten hours: the machine is finished and a
    person has not acted. This is the only state that means that."""
    code, output = _run(monkeypatch, [_pull()])

    assert code == 1
    assert "#132" in output
    assert "10h 00m" in output


def test_it_names_the_branch_and_the_wait_rather_than_a_count(monkeypatch):
    """A count is a nag; a name and a duration are a worklist."""
    _, output = _run(monkeypatch, [_pull()])

    assert "fix/local-provider-accounting" in output
    assert "ready and untouched for 10h 00m" in output
    assert "author e2" in output


@pytest.mark.parametrize(
    "overrides, pending, why",
    [
        ({"updatedAt": _ago(20)}, 0, "ready, but not for long enough"),
        ({}, 3, "checks still running"),
        ({"mergeStateStatus": "UNSTABLE"}, 0, "checks not green"),
        ({"mergeStateStatus": "DIRTY"}, 0, "conflicting"),
        ({"mergeStateStatus": "BLOCKED"}, 0, "review outstanding"),
        ({"isDraft": True}, 0, "draft"),
    ],
)
def test_someone_elses_turn_does_not_fire(monkeypatch, overrides, pending, why):
    """Each of these is a state where the queue is working.

    Firing on them is how a check becomes a nag, and a nag is how the queue
    ends up unowned again with a script running over it.
    """
    code, _ = _run(monkeypatch, [_pull(**overrides)], pending=pending)

    assert code == 0, why


def test_a_clean_run_still_says_what_it_examined(monkeypatch):
    """A check that only speaks when it fails cannot be told apart from one
    that did not run — the defect #112 fixed in the gate."""
    code, output = _run(monkeypatch, [])

    assert code == 0
    assert "0 open pull request(s)" in output
    assert "Nothing ready has been waiting" in output


def test_not_knowing_is_a_failure_rather_than_a_pass(monkeypatch):
    """If the queue cannot be read, the question is unanswered.

    Same rule as the release check: "I could not find out" is not "nothing
    is waiting", and going quiet exactly when it cannot answer is what this
    file exists to prevent.
    """

    def boom(repo=None):
        raise RuntimeError("gh exploded")

    monkeypatch.setattr(stale_prs, "open_pull_requests", boom)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = stale_prs.main([])

    assert code == 1
    assert "Cannot tell whether anything is waiting" in out.getvalue()


def test_the_threshold_is_a_number_a_caller_can_move(monkeypatch):
    """Ten hours passed a sixty-minute bar; it also has to pass a stricter
    one, or the default is doing all the work and the flag is decoration."""
    code, _ = _run(monkeypatch, [_pull(updatedAt=_ago(90))], argv=["--minutes", "30"])
    assert code == 1

    code, _ = _run(monkeypatch, [_pull(updatedAt=_ago(90))], argv=["--minutes", "240"])
    assert code == 0


def test_durations_are_written_for_a_reader(monkeypatch):
    """`552` is a number someone has to convert before they can care."""
    assert stale_prs.describe(45) == "45m"
    assert stale_prs.describe(600) == "10h 00m"
    assert stale_prs.describe(92) == "1h 32m"


# ---- #178: the step before ready -- a pull request nobody has read ------------


def test_an_open_pull_request_with_no_verdict_line_is_unread(monkeypatch):
    """**#161's shape.** Open, not a draft, no Reviewed-SHA line, and waiting
    past the threshold: a worklist item, and the run fails."""
    code, out = _run(monkeypatch, [_pull(mergeStateStatus="BLOCKED", comments=[])])
    assert code == 1
    # Timed from creation (700m ago), not updatedAt (600m) -- #298.
    assert "#132 fix/local-provider-accounting — unread for 11h 40m" in out


def test_a_verdict_on_an_older_commit_is_not_a_reading_of_this_head(monkeypatch):
    """An approval does not survive a new commit, so a verdict on another SHA
    leaves the current head unread."""
    code, out = _run(
        monkeypatch, [_pull(mergeStateStatus="BLOCKED", comments=[_verdict("b" * 40)])]
    )
    assert code == 1
    assert "read, not at head for" in out


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"comments": [_verdict()]}, "read at head"),
        ({"comments": [], "headCommittedAt": _ago(10)}, "head committed within the threshold"),
        ({"comments": [], "isDraft": True}, "a draft"),
        ({"comments": [_verdict(HEAD[:7])]}, "a 7-character prefix of head"),
        ({"comments": [_verdict(HEAD + "**")]}, "trailing formatting is cleaned"),
    ],
)
def test_the_quiet_twins_of_unread(monkeypatch, overrides, why):
    code, out = _run(monkeypatch, [_pull(mergeStateStatus="BLOCKED", **overrides)])
    assert "waiting on a reader" not in out, why
    assert code == 0, why


def test_a_six_character_prefix_is_too_short_to_count_as_at_head(monkeypatch):
    """The boundary, from the other side of `_MIN_PREFIX`."""
    _, out = _run(monkeypatch, [_pull(mergeStateStatus="BLOCKED", comments=[_verdict(HEAD[:6])])])
    assert "read, not at head" in out


def test_a_verdict_quoted_mid_comment_is_not_a_reading(monkeypatch):
    """Read with the verdict reader's own parser: only the LAST non-empty line
    of a comment is a verdict, so discussing one does not create one."""
    quoted = {"body": f"Reviewed-SHA: {HEAD} verdict=approved reviewer=policyforge-9b\n\nnot yet"}
    code, out = _run(monkeypatch, [_pull(mergeStateStatus="BLOCKED", comments=[quoted])])
    assert code == 1 and "unread" in out


def test_it_reads_verdicts_with_the_verdict_readers_parser():
    """One instrument, not two: no second Reviewed-SHA pattern in this file."""
    source = (Path(stale_prs.__file__)).read_text(encoding="utf-8")
    assert "review_lines.verdict_lines" in source
    assert r"Reviewed-SHA:\s" not in source and "re.compile" not in source


# ---- #178: pushed branches with no pull request -------------------------------


def test_a_parked_branch_is_shown_and_does_not_fail(monkeypatch):
    """Parking finished work on the remote without a PR is allowed, so it is
    reported by name and never fails the run."""
    code, out = _run(monkeypatch, [], parked=["salvage/onc-170-315-roman-numeral"])
    assert code == 0
    assert "1 pushed branch(es) with no pull request" in out
    assert "salvage/onc-170-315-roman-numeral" in out


def test_no_parked_branch_says_so(monkeypatch):
    _, out = _run(monkeypatch, [])
    assert "No pushed branch is without a pull request." in out


def test_a_failed_branch_listing_is_said_and_gates_nothing(monkeypatch):
    def boom(repo=None):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(stale_prs, "open_pull_requests", lambda repo=None: [])
    monkeypatch.setattr(stale_prs, "branches_without_pull_request", boom)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = stale_prs.main([])
    assert code == 0
    text = out.getvalue()
    assert "RuntimeError: rate limited" in text, "the message, not only the type"
    assert "INCOMPLETE" in text and "not found empty" in text


def test_the_branch_listing_excludes_the_default_branch_and_every_pr_head(monkeypatch):
    """Heads of MERGED and closed pull requests count too: a kept branch whose
    work landed is not parked work."""
    import json as _json

    replies = {
        ("repo", "view"): "rdazzleman/policyforge",
        ("api", "--paginate"): _json.dumps(
            [
                [{"name": "main"}, {"name": "ba/merged"}, {"name": "salvage/x"}],
                [{"name": "1d/open"}],
            ]
        ),
    }
    replies[("api", "--paginate")] = _json.dumps(
        [
            [{"name": "main"}, {"name": "ba/merged"}, {"name": "salvage/x"}],
            [{"name": "1d/open"}, {"name": "release/1.6.1"}],
        ]
    )
    # Honours --state, so asking for only OPEN pull requests would miss the
    # merged head -- a fake that ignored the flag could not see that mistake.
    pulls_by_state = {
        "open": [{"headRefName": "1d/open", "baseRefName": "release/1.6.1"}],
        "all": [
            {"headRefName": "ba/merged", "baseRefName": "release/1.6.1"},
            {"headRefName": "1d/open", "baseRefName": "release/1.6.1"},
        ],
    }

    def fake(args):
        if args[:2] == ["repo", "view"] and "defaultBranchRef" in args:
            return "main\n"
        if args[:2] == ["pr", "list"]:
            return _json.dumps(pulls_by_state[args[args.index("--state") + 1]])
        return replies[tuple(args[:2])]

    monkeypatch.setattr(stale_prs, "_gh", fake)
    # main: the default. release/1.6.1: a base, found on the real repo's first
    # run. ba/merged: a merged head. 1d/open: an open head.
    assert stale_prs.branches_without_pull_request("rdazzleman/policyforge") == (
        ["salvage/x"],
        False,
    )


# ---- #298: "ready" is the readers' verdict too, not only the machine's --------


def _say(sha, verdict, reviewer, at=None):
    body = f"Read it.\n\nReviewed-SHA: {sha} verdict={verdict} reviewer={reviewer}"
    comment = {"body": body}
    if at is not None:
        comment["createdAt"] = _ago(at)
    return comment


def test_clean_and_green_with_an_objection_at_head_is_not_ready(monkeypatch):
    """**The incident #298 was filed for.** mergeStateStatus CLEAN, no checks
    pending -- and a changes-requested at head. It was labelled ready; it is a
    reader's turn, so it is not stale and does not fail the run on this."""
    comments = [
        _say(HEAD, "approved", "policyforge-80"),
        _say(HEAD, "changes-requested", "policyforge-9b"),
    ]
    code, out = _run(monkeypatch, [_pull(comments=comments)])
    assert "not approved" in out
    assert "STALE" not in out and "ready and nobody has acted" not in out
    assert code == 0


def test_the_objector_approving_at_head_makes_it_ready(monkeypatch):
    """The passing twin: the same objection, retired at head by its own
    reviewer. Ready, and stale past the threshold."""
    comments = [
        _say(HEAD, "changes-requested", "policyforge-9b"),
        _say(HEAD, "approved", "policyforge-9b"),
    ]
    code, out = _run(monkeypatch, [_pull(comments=comments)])
    assert "STALE" in out and code == 1


@pytest.mark.parametrize(
    ("comments", "why"),
    [
        (
            [
                _say("b" * 40, "changes-requested", "policyforge-9b"),
                _say("b" * 40, "approved", "policyforge-9b"),
                _say(HEAD, "approved", "policyforge-80"),
            ],
            "objection answered at an older commit only",
        ),
        (
            [
                _say(HEAD, "changes-requested", "policyforge-9b"),
                _say(HEAD, "approved", "policyforge-80"),
            ],
            "another reviewer's approval does not answer it",
        ),
        (
            [_say(HEAD[:7], "approved", "policyforge-80")],
            "a truncated approval is not a well-formed verdict",
        ),
        ([_say("b" * 40, "approved", "policyforge-80")], "approved, but not at head"),
    ],
)
def test_not_ready_unless_approved_at_head_with_nothing_standing(monkeypatch, comments, why):
    # The exit code differs by case (approved-not-at-head is also UNREAD), so
    # only readiness is asserted here.
    _, out = _run(monkeypatch, [_pull(comments=comments)])
    assert "not approved" in out, why
    assert "ready and nobody has acted" not in out, why


# ---- #298: the unread clock -------------------------------------------------


def test_a_discussed_but_unreviewed_head_still_ages_into_unread(monkeypatch):
    """**9b's shape.** Fresh comments reset updatedAt; none is a verdict, the
    head is old. The wait is the head's, so it is reported unread."""
    pull = _pull(
        mergeStateStatus="BLOCKED",
        updatedAt=_ago(2),
        createdAt=_ago(300),
        headCommittedAt=_ago(300),
        comments=[{"body": "what about the edge case?", "createdAt": _ago(2)}],
    )
    code, out = _run(monkeypatch, [pull])
    assert "unread for 5h 00m" in out and code == 1


def test_a_recent_verdict_restarts_the_clock(monkeypatch):
    """The twin: the last VERDICT is recent (on an older commit), so the head
    has not been waiting past the threshold for a reader."""
    pull = _pull(
        mergeStateStatus="BLOCKED",
        createdAt=_ago(300),
        headCommittedAt=_ago(300),
        comments=[_say("b" * 40, "changes-requested", "policyforge-9b", at=5)],
    )
    code, out = _run(monkeypatch, [pull])
    assert "waiting on a reader" not in out and code == 0


def test_waiting_since_is_the_latest_of_its_three_sources():
    pull = _pull(
        createdAt="2026-09-24T01:00:00Z",
        headCommittedAt="2026-09-24T03:00:00Z",
        comments=[
            {"body": "chat", "createdAt": "2026-09-24T09:00:00Z"},
            {**_say(HEAD, "approved", "policyforge-9b"), "createdAt": "2026-09-24T05:00:00Z"},
        ],
    )
    assert stale_prs.waiting_since(pull) == "2026-09-24T05:00:00Z", "chat must not count"


# ---- #298: a listing that fills its limit says so ---------------------------


def test_an_open_listing_at_its_limit_is_flagged(monkeypatch):
    drafts = [_pull(number=n, isDraft=True) for n in range(stale_prs.OPEN_LIMIT)]
    _, out = _run(monkeypatch, drafts)
    assert "its limit" in out and "may be short" in out


def test_an_open_listing_under_its_limit_is_not(monkeypatch):
    drafts = [_pull(number=n, isDraft=True) for n in range(stale_prs.OPEN_LIMIT - 1)]
    _, out = _run(monkeypatch, drafts)
    assert "may be short" not in out


def test_a_pr_listing_at_its_limit_is_flagged_by_the_branch_check(monkeypatch):
    import json as _json

    pulls = [{"headRefName": f"b{n}", "baseRefName": "main"} for n in range(stale_prs.ALL_LIMIT)]

    def fake(args):
        if args[:2] == ["repo", "view"]:
            return "main\n" if "defaultBranchRef" in args else "o/r"
        if args[:2] == ["pr", "list"]:
            return _json.dumps(pulls)
        return _json.dumps([[{"name": "main"}]])

    monkeypatch.setattr(stale_prs, "_gh", fake)
    assert stale_prs.branches_without_pull_request("o/r") == ([], True)
    pulls.pop()
    assert stale_prs.branches_without_pull_request("o/r") == ([], False)


def test_a_truncated_branch_check_is_said_in_the_report(monkeypatch):
    monkeypatch.setattr(stale_prs, "open_pull_requests", lambda repo=None: [])
    monkeypatch.setattr(stale_prs, "branches_without_pull_request", lambda repo=None: ([], True))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        stale_prs.main([])
    assert "filled its limit" in out.getvalue()


# ---- #299: the build half comes from check-runs at head, not mergeStateStatus -


def test_unknown_merge_state_with_green_checks_and_an_approval_is_ready(monkeypatch):
    """**80's finding on #299.** mergeStateStatus reads UNKNOWN for most pull
    requests, because GitHub computes it lazily. #297 did, approved at head with
    every check green. It is ready, and stale past the threshold."""
    code, out = _run(monkeypatch, [_pull(mergeStateStatus="UNKNOWN")], pending=(0, 0))
    assert "STALE" in out and code == 1


@pytest.mark.parametrize(
    ("checks", "shown"),
    [((0, 1), "1 failing"), ((2, 0), "2 pending")],
)
def test_unknown_merge_state_is_decided_by_the_checks(monkeypatch, checks, shown):
    code, out = _run(monkeypatch, [_pull(mergeStateStatus="UNKNOWN")], pending=checks)
    assert shown in out and "STALE" not in out and code == 0


def _check_runs(monkeypatch, runs):
    import json as _json

    monkeypatch.setattr(stale_prs, "_gh", lambda args: _json.dumps(runs))


def test_only_the_latest_run_of_each_check_counts(monkeypatch):
    """The charge's #232 finding: `gh pr checks` blends runs, so a stale green
    can sit beside a current red. Latest per NAME at the head decides."""
    _check_runs(
        monkeypatch,
        [
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-09-24T10:00:00Z",
            },
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-09-24T11:00:00Z",
            },
            {
                "name": "lint",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-09-24T11:00:00Z",
            },
        ],
    )
    assert stale_prs.head_checks(HEAD, "o/r") == (0, 1)


def test_a_later_green_rerun_supersedes_an_earlier_failure(monkeypatch):
    """The twin: the same two runs in the other order."""
    _check_runs(
        monkeypatch,
        [
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-09-24T10:00:00Z",
            },
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-09-24T11:00:00Z",
            },
        ],
    )
    assert stale_prs.head_checks(HEAD, "o/r") == (0, 0)


@pytest.mark.parametrize(
    ("conclusion", "failing"),
    [
        ("success", 0),
        ("skipped", 0),
        ("neutral", 0),
        ("failure", 1),
        ("timed_out", 1),
        ("cancelled", 1),
        ("action_required", 1),
    ],
)
def test_check_conclusions_are_classified(monkeypatch, conclusion, failing):
    _check_runs(
        monkeypatch,
        [
            {
                "name": "c",
                "status": "completed",
                "conclusion": conclusion,
                "started_at": "2026-09-24T10:00:00Z",
            }
        ],
    )
    assert stale_prs.head_checks(HEAD, "o/r") == (0, failing)


def test_a_running_check_is_pending(monkeypatch):
    _check_runs(
        monkeypatch,
        [
            {
                "name": "c",
                "status": "in_progress",
                "conclusion": None,
                "started_at": "2026-09-24T10:00:00Z",
            }
        ],
    )
    assert stale_prs.head_checks(HEAD, "o/r") == (1, 0)


def test_unreadable_check_runs_are_none(monkeypatch):
    def boom(args):
        raise RuntimeError("503")

    monkeypatch.setattr(stale_prs, "_gh", boom)
    assert stale_prs.head_checks(HEAD, "o/r") is None
    _check_runs(monkeypatch, [])
    assert stale_prs.head_checks(HEAD, "o/r") is None


def test_newest_first_api_order_with_a_red_newest_run_is_failing(monkeypatch):
    """**b5's finding on #299.** The check-runs API lists the NEWEST run first
    (measured on #234's head). Both earlier latest-run fixtures listed it
    second, so "the last run in list order wins" passed every test -- and on
    real API order it resolves to the OLDEST run, a stale green masking a
    current red (#232). Here the newest run is red and listed first."""
    _check_runs(
        monkeypatch,
        [
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-09-24T11:00:00Z",
            },
            {
                "name": "pytest",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-09-24T10:00:00Z",
            },
        ],
    )
    assert stale_prs.head_checks(HEAD, "o/r") == (0, 1)


def test_the_real_234_shape_reads_as_green(monkeypatch):
    """#234's `changelog` check, as the API returned it: success, then an
    earlier failure, then an older skip. The newest is green, so it passes."""
    _check_runs(
        monkeypatch,
        [
            {
                "name": "changelog",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-09-24T00:15:19Z",
            },
            {
                "name": "changelog",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-09-24T00:14:36Z",
            },
            {
                "name": "changelog",
                "status": "completed",
                "conclusion": "skipped",
                "started_at": "2026-09-21T20:18:52Z",
            },
        ],
    )
    assert stale_prs.head_checks(HEAD, "o/r") == (0, 0)


def test_an_approval_from_a_declared_stake_alone_is_not_ready():
    """#425: on #421 a stake-holder's approval could stand in for a counted
    reader's. `counted=no` keeps it out of readiness; a counted one is needed."""
    staked = _say(HEAD, "approved", "policyforge-9b")
    staked["body"] += " counted=no"
    counted = _say(HEAD, "approved", "policyforge-1d")
    assert not stale_prs.approved_at_head({"headRefOid": HEAD, "comments": [staked]})
    assert stale_prs.approved_at_head({"headRefOid": HEAD, "comments": [staked, counted]})


@pytest.mark.parametrize("token", ["counted=no.", "COUNTED=NO", "(retracted)"])
def test_an_approval_with_an_unknown_token_alone_is_not_ready(token):
    """policyforge-b5 on #428: without the tokens passed to `shape_of`, a
    mistyped stake read as a counted approval, and the suite stayed green."""
    approval = _say(HEAD, "approved", "policyforge-9b")
    approval["body"] += f" {token}"
    assert not stale_prs.approved_at_head({"headRefOid": HEAD, "comments": [approval]})
