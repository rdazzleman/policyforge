"""The check-in's measurements (#7), against a fake Forgejo.

The payload shapes are the ones Forgejo 16.0.5 returned on the team's
server (1d, #7): the milestone object's `open_issues` counts PRs too, a
PR carries `merge_base`, and statuses come newest-first by `created_at`
rather than by id. Each test drives `main()`, the path a check-in takes,
unless the claim is about one helper's arithmetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import checkin  # noqa: E402
import review_lines  # noqa: E402

REPO = checkin.REPO
TIP = "a" * 40
HEAD = "b" * 40
OLD = "c" * 40


def _status(context: str, state: str, created: str, sid: int) -> dict:
    return {"context": context, "status": state, "created_at": created, "id": sid}


class FakeForgejo:
    """Serves the paths `checkin` reads. Lists are paged by `limit`/`page`."""

    def __init__(self):
        self.milestones = [{"id": 7, "title": "1.7", "open_issues": 3}]
        self.issues = [{"number": 1}, {"number": 2}]
        self.milestone_pulls = [{"number": 10}]
        self.pulls = [
            {
                "number": 10,
                "user": {"login": "policyforge-ba"},
                "head": {"sha": HEAD},
                "base": {"ref": "release/1.7"},
                "merge_base": TIP,
            }
        ]
        self.statuses = {HEAD: [_status("ci / test", "success", "2026-09-28T10:00:00Z", 1)]}
        self.combined: dict[str, list] | None = None  # None: derive from `statuses`
        self.paths: list[str] = []

    def _page(self, items, params):
        page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
        return items[(page - 1) * limit : page * limit]

    def __call__(self, path: str):
        self.paths.append(path)
        base, _, query = path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if p)
        r = f"/repos/{REPO}"
        if base.startswith(f"{r}/branches/"):
            return {"commit": {"id": TIP if "release" in base else "d" * 40}}, {}
        if base == f"{r}/milestones":
            return self._page(self.milestones, params), {}
        if base == f"{r}/issues":
            assert params.get("milestones") == "7", "the milestone must be filtered by ID"
            items = self.issues if params.get("type") == "issues" else self.milestone_pulls
            return self._page(items, params), {}
        if base == f"{r}/pulls":
            return self._page(self.pulls, params), {}
        if base.startswith(f"{r}/commits/") and base.endswith("/statuses"):
            sha = base.split("/")[-2]
            return self._page(self.statuses.get(sha, []), params), {}
        if base.startswith(f"{r}/commits/") and base.endswith("/status"):
            sha = base.split("/")[-2]
            if self.combined is not None:
                return {"statuses": self.combined.get(sha, [])}, {}
            # The server's own answer, written here and not borrowed from
            # `checkin`, so the cross-check compares two derivations.
            latest: dict[str, dict] = {}
            for s in sorted(self.statuses.get(sha, []), key=lambda s: s["created_at"]):
                latest[s["context"]] = s
            return {"statuses": list(latest.values())}, {}
        raise AssertionError(f"unexpected path {path}")


@pytest.fixture
def fake(monkeypatch):
    server = FakeForgejo()
    monkeypatch.setattr(review_lines, "_forgejo", server)
    monkeypatch.setattr(review_lines, "fetch_forgejo", lambda n, repo: (HEAD, [], 0))
    return server


def _run(capsys) -> str:
    assert checkin.main([]) == 0
    return capsys.readouterr().out


def _line(sha: str, verdict: str, reviewer: str) -> dict:
    body = f"Prose.\n\nReviewed-SHA: {sha} verdict={verdict} reviewer={reviewer}"
    return {"body": body, "author": reviewer, "source": "comment"}


# --- the milestone ---------------------------------------------------------


def test_issues_and_pulls_are_counted_apart_and_reconcile_with_the_object(fake, capsys):
    """What it must ALLOW: 2 issues + 1 PR = the object's 3."""
    out = _run(capsys)
    assert (
        "2 open issue(s) [1, 2], 1 open PR(s) [10]; object open_issues 3, reconciles: True" in out
    )


def test_a_count_that_differs_from_the_object_does_not_reconcile(fake, capsys):
    fake.milestones[0]["open_issues"] = 2  # the object forgot the PR
    assert "reconciles: False" in _run(capsys)


def test_a_number_listed_twice_does_not_reconcile_even_when_the_counts_agree():
    """Distinct union, not a sum: 2 + 1 listed, 2 distinct, object says 2."""
    ms = checkin.Milestone("1.7", issues=[1, 10], pulls=[10], object_open=2)
    assert not ms.reconciles
    assert checkin.Milestone("1.7", issues=[1], pulls=[10], object_open=2).reconciles


def test_an_unknown_milestone_is_refused_never_zero(fake, capsys):
    """Forgejo ignores a milestone filter it cannot resolve and returns every
    open issue, so a name that matches nothing must not become a count."""
    fake.milestones = [{"id": 8, "title": "1.8", "open_issues": 0}]
    out = _run(capsys)
    assert "milestone '1.7': NOT FOUND (not zero: refused)" in out
    assert not any("/issues?" in p for p in fake.paths)


def test_a_list_is_paged_to_exhaustion(fake, capsys):
    fake.issues = [{"number": n} for n in range(100, 161)]  # 61: two pages of 50
    fake.milestones[0]["open_issues"] = 62
    out = _run(capsys)
    assert "61 open issue(s)" in out and "reconciles: True" in out


# --- the build -------------------------------------------------------------


def test_the_newest_status_wins_by_time_not_by_list_position(fake):
    """Measured on #7: the list is newest-first by created_at, and ids are
    not in time order. A re-run's success must replace the earlier failure."""
    fake.statuses[HEAD] = [
        _status("ci / test", "success", "2026-09-28T11:00:00Z", 3),
        _status("ci / test", "failure", "2026-09-28T10:00:00Z", 9),
    ]
    assert checkin.build_at(HEAD).state == "success"


def test_the_newest_status_wins_whatever_order_the_list_arrives_in(fake):
    """Time, never position: the same two statuses listed oldest-first."""
    fake.statuses[HEAD] = [
        _status("ci / test", "failure", "2026-09-28T10:00:00Z", 9),
        _status("ci / test", "success", "2026-09-28T11:00:00Z", 3),
    ]
    assert checkin.build_at(HEAD).state == "success"


def test_the_two_derivations_disagreeing_reports_no_state(fake, capsys):
    fake.combined = {HEAD: [_status("ci / test", "failure", "2026-09-28T10:00:00Z", 1)]}
    out = _run(capsys)
    assert "build:  DISAGREE" in out
    assert "build:  success" not in out


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["success", "skipped"], "success"),
        (["success", "pending"], "pending"),
        (["success", "failure"], "failure"),
        # 9b on #41: a red context outranks a pending one, in either order.
        (["failure", "pending"], "failure"),
        (["pending", "failure"], "failure"),
        (["error", "pending"], "failure"),
        (["warning", "success"], "failure"),  # a state it does not know is not green
        # b5 on #41: all skipped is nothing ran, not success.
        (["skipped", "skipped"], "nothing ran"),
        ([], "none"),
    ],
)
def test_the_build_state_is_derived_from_every_context(fake, states, expected):
    fake.statuses[HEAD] = [
        _status(f"ci / job{i}", s, "2026-09-28T10:00:00Z", i) for i, s in enumerate(states)
    ]
    build = checkin.build_at(HEAD)
    assert build.state == expected
    if expected in ("success", "none"):
        assert build.not_green == [], build.not_green  # skipped is green, printed nowhere


def test_a_red_context_beside_a_pending_one_is_printed_red(fake, capsys):
    """9b on #41: names alone read "pending (a, b)" and hid the failure."""
    fake.statuses[HEAD] = [
        _status("ci / changelog", "failure", "2026-09-28T10:00:00Z", 1),
        _status("ci / scan-and-test", "pending", "2026-09-28T10:00:00Z", 2),
    ]
    out = _run(capsys)
    assert "build:  failure (ci / changelog=failure, ci / scan-and-test=pending)" in out


def test_a_head_where_every_context_skipped_is_not_reported_green(fake, capsys):
    fake.statuses[HEAD] = [
        _status("ci / changelog", "skipped", "2026-09-28T10:00:00Z", 1),
        _status("ci / scan-and-test", "skipped", "2026-09-28T10:00:00Z", 2),
    ]
    out = _run(capsys)
    assert "build:  nothing ran (ci / changelog=skipped, ci / scan-and-test=skipped)" in out
    assert "build:  success" not in out


def test_a_head_without_the_base_tip_is_flagged(fake, capsys):
    """Forgejo's PR CI tests the head, not the merge: green there is not
    green merged unless the head already holds the base tip."""
    fake.pulls[0]["merge_base"] = OLD
    out = _run(capsys)
    assert "merged-with-tip: NO" in out
    assert "green here is not green merged" in out


def test_a_head_holding_the_base_tip_is_not_flagged(fake, capsys):
    out = _run(capsys)
    assert "merged-with-tip: yes" in out
    assert "not green merged" not in out


# --- the review --------------------------------------------------------------


def _review(monkeypatch, items):
    monkeypatch.setattr(review_lines, "fetch_forgejo", lambda n, repo: (HEAD, items, len(items)))


def test_an_approval_at_head_counts(fake, monkeypatch, capsys):
    _review(monkeypatch, [_line(HEAD, "approved", "policyforge-1d")])
    assert "approved at head by policyforge-1d" in _run(capsys)


def test_an_approval_at_an_older_commit_does_not_count(fake, monkeypatch, capsys):
    _review(monkeypatch, [_line(OLD, "approved", "policyforge-1d")])
    assert "approved at head by nobody" in _run(capsys)


def test_the_authors_own_approval_does_not_count(fake, monkeypatch, capsys):
    _review(monkeypatch, [_line(HEAD, "approved", "policyforge-ba")])
    assert "approved at head by nobody" in _run(capsys)


def test_a_line_posted_by_someone_else_does_not_count(fake, monkeypatch, capsys):
    line = _line(HEAD, "approved", "policyforge-1d")
    line["author"] = "policyforge-9b"
    _review(monkeypatch, [line])
    assert "approved at head by nobody" in _run(capsys)


def test_an_objection_stays_open_until_its_reader_approves_at_head(fake, monkeypatch, capsys):
    blocked = _line(OLD, "changes-requested", "policyforge-9b")
    _review(monkeypatch, [blocked])
    assert "NOT RETIRED AT HEAD: policyforge-9b at ccccccc (review_lines.py 10" in _run(capsys)
    _review(monkeypatch, [blocked, _line(HEAD, "approved", "policyforge-9b")])
    out = _run(capsys)
    assert "NOT RETIRED" not in out
    assert "approved at head by policyforge-9b" in out


def test_a_short_population_is_named_not_read_as_no_review(fake, monkeypatch, capsys):
    def short(number, repo):
        raise review_lines.PopulationShort("timeline names 1, lists 2")

    monkeypatch.setattr(review_lines, "fetch_forgejo", short)
    out = _run(capsys)
    assert "review: population short" in out
    assert "approved at head by nobody" not in out


def test_a_stake_declared_after_an_approval_uncounts_the_reader(fake, monkeypatch, capsys):
    """Same rule as `review_lines.py` (80 on #53): the latest own line decides."""
    staked = _line(HEAD, "approved", "policyforge-1d")
    staked["body"] += " counted=no"
    _review(monkeypatch, [_line(HEAD, "approved", "policyforge-1d"), staked])
    assert "approved at head by nobody" in _run(capsys)
