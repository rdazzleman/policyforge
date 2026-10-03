"""The verdict reader on Forgejo: the population, the order, and who posted.

The fake server's payloads have the shapes Forgejo 16.0.5 returned on a
probe PR (#14, 1d, 2026-09-27), not shapes derived from `fetch_forgejo`:
plain comments carry `id`, the timeline names a comment by `id` and a review
by `review_id`, and inline code comments appear in neither list.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import review_lines  # noqa: E402

REPO = "o/r"


def _user(login: str) -> dict:
    return {"login": login}


def _line(sha: str, verdict: str, reviewer: str) -> str:
    return f"Prose.\n\nReviewed-SHA: {sha} verdict={verdict} reviewer={reviewer}"


class FakeForgejo:
    """Serves one PR. `timeline` pages are served `per_page` at a time AFTER
    removing `hidden` entries, which is how Forgejo filters inline code
    comments out of a page it has already cut."""

    def __init__(self, head, comments, reviews, timeline, inline=None, hidden=(), per_page=50):
        self.head, self.comments, self.reviews = head, comments, reviews
        self.timeline, self.inline, self.hidden = timeline, inline or {}, set(hidden)
        self.per_page = per_page
        self.review_total = len(reviews)

    def __call__(self, path: str):
        base, _, query = path.partition("?")
        params = dict(p.split("=") for p in query.split("&") if p)
        page, limit = int(params.get("page", 1)), int(params.get("limit", 50))
        n = 1
        if base == f"/repos/{REPO}/pulls/{n}":
            return {"head": {"sha": self.head}}, {}
        if base == f"/repos/{REPO}/issues/{n}/comments":
            return self.comments, {"X-Total-Count": str(len(self.comments))}
        if base == f"/repos/{REPO}/pulls/{n}/reviews":
            chunk = self.reviews[(page - 1) * limit : page * limit]
            return chunk, {"X-Total-Count": str(self.review_total)}
        if base == f"/repos/{REPO}/issues/{n}/timeline":
            size = min(limit, self.per_page)
            cut = self.timeline[(page - 1) * size : page * size]
            shown = [t for i, t in enumerate(cut) if (page - 1) * size + i not in self.hidden]
            return shown, {"X-Total-Count": str(len(shown))}
        if base.startswith(f"/repos/{REPO}/pulls/{n}/reviews/") and base.endswith("/comments"):
            return self.inline.get(int(base.split("/")[-2]), []), {}
        raise AssertionError(f"unexpected path {path}")


def _comment(cid, body, login):
    return {"id": cid, "body": body, "user": _user(login)}


def _review(rid, body, login, state="COMMENT", inline=0, dismissed=False):
    return {
        "id": rid,
        "body": body,
        "user": _user(login),
        "state": state,
        "dismissed": dismissed,
        "stale": False,
        "comments_count": inline,
    }


@pytest.fixture
def head() -> str:
    """A real commit, so `location_of` can place lines AT HEAD."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        check=True,
    ).stdout.strip()


def _serve(monkeypatch, fake):
    monkeypatch.setattr(review_lines, "_forgejo", fake)


# --- the population ------------------------------------------------------


def test_comments_and_reviews_come_back_in_timeline_order(monkeypatch, head):
    """Retirement (#237) is decided by posting order, and comments and reviews
    are two lists. Only the timeline interleaves them."""
    fake = FakeForgejo(
        head,
        comments=[_comment(10, "first", "a"), _comment(12, "third", "a")],
        reviews=[_review(1, "second", "b")],
        timeline=[
            {"type": "pull_push", "id": 9},
            {"type": "comment", "id": 10},
            {"type": "review", "id": 11, "review_id": 1},
            {"type": "comment", "id": 12},
        ],
    )
    _serve(monkeypatch, fake)
    _, items, derived = review_lines.fetch_forgejo(1, REPO)
    assert [i["body"] for i in items] == ["first", "second", "third"]
    assert [i["source"] for i in items] == ["comment", "review", "comment"]
    assert derived == 3


def test_a_review_the_timeline_does_not_name_refuses(monkeypatch, head):
    fake = FakeForgejo(
        head,
        comments=[_comment(10, "c", "a")],
        reviews=[_review(1, "r", "b")],
        timeline=[{"type": "comment", "id": 10}],
    )
    _serve(monkeypatch, fake)
    with pytest.raises(review_lines.PopulationShort):
        review_lines.fetch_forgejo(1, REPO)


def test_a_substituted_comment_with_the_same_count_refuses(monkeypatch, head):
    """Compared by id SET: equal counts naming different comments disagree."""
    fake = FakeForgejo(
        head,
        comments=[_comment(10, "c", "a"), _comment(11, "d", "a")],
        reviews=[],
        timeline=[{"type": "comment", "id": 10}, {"type": "comment", "id": 99}],
    )
    _serve(monkeypatch, fake)
    with pytest.raises(review_lines.PopulationShort):
        review_lines.fetch_forgejo(1, REPO)


def test_review_paging_short_of_the_total_refuses(monkeypatch, head):
    fake = FakeForgejo(
        head,
        comments=[],
        reviews=[_review(1, "r", "b")],
        timeline=[{"type": "review", "id": 5, "review_id": 1}],
    )
    fake.review_total = 3  # the server says 3; paging finds 1
    _serve(monkeypatch, fake)
    with pytest.raises(review_lines.PopulationShort):
        review_lines.fetch_forgejo(1, REPO)


def test_a_timeline_page_emptied_by_filtering_refuses_rather_than_passes(monkeypatch, head):
    """Measured on #14: a page cut before inline code comments are removed can
    come back empty mid-list. Stopping there under-reads the timeline, and
    the direction must be a refusal, never a pass."""
    fake = FakeForgejo(
        head,
        comments=[_comment(10, "c", "a")],
        reviews=[_review(1, "late review", "b")],
        timeline=[
            {"type": "comment", "id": 10},
            {"type": "code", "id": 20},
            {"type": "code", "id": 21},
            {"type": "review", "id": 22, "review_id": 1},
        ],
        hidden={1, 2},
        per_page=1,
    )
    _serve(monkeypatch, fake)
    with pytest.raises(review_lines.PopulationShort):
        review_lines.fetch_forgejo(1, REPO)


def test_a_short_population_is_exit_2_not_a_report(monkeypatch, capsys, head):
    def short(number, repo):
        raise review_lines.PopulationShort("timeline names 1, lists 2")

    monkeypatch.setattr(review_lines, "fetch_forgejo", short)
    assert review_lines.report(1, REPO, "forgejo") == 2
    assert "POPULATION SHORT" in capsys.readouterr().err


# --- what a line can prove on Forgejo -----------------------------------


def _report(monkeypatch, capsys, head, items) -> tuple[int, str]:
    # The timeline names comments and reviews, never inline code comments.
    derived = sum(1 for i in items if i["source"] != "inline")
    monkeypatch.setattr(review_lines, "fetch_forgejo", lambda n, r: (head, items, derived))
    code = review_lines.report(1, REPO, "forgejo")
    return code, capsys.readouterr().out


def _item(body, author, source="comment", **extra):
    return {"body": body, "author": author, "source": source, **extra}


def test_a_line_posted_by_its_reviewer_counts(monkeypatch, capsys, head):
    """What it must ALLOW: the ordinary case."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [_item(_line(head, "approved", "policyforge-9b"), "policyforge-9b")],
    )
    assert "approvals at head from counted readers: policyforge-9b" in out
    assert "POSTED BY" not in out


def test_a_line_signed_as_another_reader_does_not_count(monkeypatch, capsys, head):
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [_item(_line(head, "approved", "policyforge-9b"), "policyforge-ba")],
    )
    assert "approvals at head from counted readers: none" in out
    assert "POSTED BY policyforge-ba" in out


def test_a_withdrawal_posted_by_someone_else_leaves_the_objection_open(monkeypatch, capsys, head):
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_line(head, "changes-requested", "policyforge-9b"), "policyforge-9b"),
            _item(_line(head, "approved", "policyforge-9b"), "policyforge-ba"),
        ],
    )
    assert "1 OPEN OBJECTION" in out


def test_a_verdict_ending_a_review_body_is_read(monkeypatch, capsys, head):
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [_item(_line(head, "changes-requested", "policyforge-1d"), "policyforge-1d", "review")],
    )
    assert "in a review body" in out
    assert "1 OPEN OBJECTION" in out


def test_a_formal_request_changes_without_a_line_is_reported(monkeypatch, capsys, head):
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [_item("Please fix.", "policyforge-9b", "review", state="REQUEST_CHANGES")],
    )
    assert "formal review: REQUEST_CHANGES by policyforge-9b" in out
    assert "A formal REQUEST_CHANGES stands" in out


def test_a_reviewers_later_formal_approval_answers_their_formal_objection(
    monkeypatch, capsys, head
):
    """b5 on #21: the report counted 9b's approval AND said 9b's objection
    stood. The reviewer's latest formal state is the one that stands."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item("Please fix.", "policyforge-9b", "review", state="REQUEST_CHANGES"),
            _item("Fixed, thanks.", "policyforge-9b", "review", state="APPROVED"),
        ],
    )
    assert "REQUEST_CHANGES stands" not in out
    assert "formal review: APPROVED by policyforge-9b" in out
    assert "formal review: REQUEST_CHANGES" not in out


def test_an_objection_after_ones_own_approval_stands(monkeypatch, capsys, head):
    """Order decides, both ways: the must-refuse half of the rule above."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item("Looks fine.", "policyforge-9b", "review", state="APPROVED"),
            _item("On second look, no.", "policyforge-9b", "review", state="REQUEST_CHANGES"),
            _item("Fine by me.", "policyforge-ba", "review", state="APPROVED"),
        ],
    )
    assert "A formal REQUEST_CHANGES stands" in out


def test_an_unpublished_draft_is_not_read_as_a_verdict(monkeypatch, capsys, head):
    """Measured on #14: the author sees their own PENDING draft in both lists,
    so it passes the population check. It must not count."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(
                _line(head, "approved", "policyforge-1d"),
                "policyforge-1d",
                "review",
                state="PENDING",
            )
        ],
    )
    assert "unpublished draft" in out
    assert "0 verdict line(s)" in out


def test_a_dismissed_request_changes_is_not_reported_as_standing(monkeypatch, capsys, head):
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [_item("x", "policyforge-9b", "review", state="REQUEST_CHANGES", dismissed=True)],
    )
    assert "REQUEST_CHANGES stands" not in out


def test_a_verdict_in_an_inline_code_comment_is_reported_as_not_read(monkeypatch, capsys, head):
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [_item(_line(head, "approved", "policyforge-1d"), "policyforge-1d", "inline")],
    )
    assert "INLINE code comments" in out
    assert "approvals at head from counted readers" not in out  # no verdict lines at all
    assert "0 verdict line(s)" in out


def test_a_head_this_clone_has_not_fetched_is_refused(monkeypatch, capsys):
    """Otherwise every line reads FABRICATED: a false accusation."""
    monkeypatch.setattr(
        review_lines, "fetch_forgejo", lambda n, r: ("f" * 40, [_item("x", "a")], 1)
    )
    assert review_lines.report(1, REPO, "forgejo") == 2
    assert "HEAD NOT IN THIS CLONE" in capsys.readouterr().err


def test_a_non_http_forgejo_url_is_refused_before_anything_is_opened(monkeypatch):
    """The bound the `nosemgrep` on `_forgejo`'s urlopen rests on."""
    opened = []
    monkeypatch.setenv("FORGEJO_TOKEN", "not-a-real-token")
    monkeypatch.setattr(review_lines.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    for url in ("file:///etc/passwd", "ftp://example.invalid"):
        monkeypatch.setattr(review_lines, "FORGEJO_URL", url)
        with pytest.raises(RuntimeError, match="must be http"):
            review_lines._forgejo("/version")
    assert opened == []


def test_github_lines_are_not_checked_against_their_shared_poster(monkeypatch, capsys, head):
    """Through the REAL GitHub `fetch`, stubbing only `gh`. Every GitHub line
    was posted by one shared account, so its login is no evidence of who
    reviewed; comparing it would count nobody (9b on #21). The earlier tests
    patched `fetch` itself, which is why they could not see this."""
    import json

    body = _line(head, "approved", "policyforge-9b")

    def gh(*args):
        if args[0] == "pr":
            return json.dumps(
                {"headRefOid": head, "comments": [{"body": body, "author": _user("rdazzlebot")}]}
            )
        return "1\n"

    monkeypatch.setattr(review_lines, "_gh", gh)
    assert review_lines.report(1, REPO, "github") == 0
    out = capsys.readouterr().out
    assert "approvals at head from counted readers: policyforge-9b" in out
    assert "POSTED BY" not in out


def test_the_command_line_reads_forgejo_by_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        review_lines, "report", lambda n, repo, forge: seen.update(repo=repo, forge=forge) or 0
    )
    review_lines.main(["5"])
    assert seen == {"repo": review_lines.FORGEJO_REPO, "forge": "forgejo"}
    review_lines.main(["5", "--forge", "github"])
    assert seen == {"repo": review_lines.REPO, "forge": "github"}


# --- a reviewer's LATEST line decides their standing (80's ruling on #53) ----


def _staked(sha: str, reviewer: str) -> str:
    return f"{_line(sha, 'approved', reviewer)} counted=no"


def _summary(out: str) -> dict[str, str]:
    lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("approvals at head")]
    return {ln.split(":", 1)[0]: ln.split(":", 1)[1].strip() for ln in lines}


def test_a_stake_declared_after_an_earlier_line_uncounts_the_reader(monkeypatch, capsys, head):
    """#51 exactly: 1098 carried no token, 1108 restated it with counted=no.
    The reader listed 1d as counted AND staked; the latest line decides."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_line(head, "approved", "policyforge-1d"), "policyforge-1d"),
            _item(_line(head, "approved", "policyforge-80"), "policyforge-80"),
            _item(_staked(head, "policyforge-1d"), "policyforge-1d"),
        ],
    )
    got = _summary(out)
    assert got["approvals at head from counted readers"] == "policyforge-80"
    assert got["approvals at head from readers who declared a stake"] == "policyforge-1d"
    assert "policyforge-1d: counted -> uncounted at" in out


def test_a_later_line_without_the_token_counts_again_and_says_so(monkeypatch, capsys, head):
    """The ruling's other direction: the latest line decides, flag included,
    and the flip is printed rather than silent."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_staked(head, "policyforge-ba"), "policyforge-ba"),
            _item(_line(head, "approved", "policyforge-ba"), "policyforge-ba"),
        ],
    )
    assert _summary(out)["approvals at head from counted readers"] == "policyforge-ba"
    assert "policyforge-ba: uncounted -> counted at" in out


def test_a_latest_line_at_another_sha_leaves_no_head_approval(monkeypatch, capsys, head):
    """9b on #53: approved at head, then a later line at another commit.
    The earlier head approval no longer stands for that reader."""
    parent = subprocess.run(
        ["git", "rev-parse", "HEAD~1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        check=True,
    ).stdout.strip()
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_line(head, "approved", "policyforge-b5"), "policyforge-b5"),
            _item(_line(parent, "approved", "policyforge-b5"), "policyforge-b5"),
        ],
    )
    assert _summary(out)["approvals at head from counted readers"] == "none"


def test_a_line_posted_in_another_readers_name_does_not_supersede_theirs(monkeypatch, capsys, head):
    """Otherwise anyone could uncount a reader by posting a line as them."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_line(head, "approved", "policyforge-9b"), "policyforge-9b"),
            _item(_staked(head, "policyforge-9b"), "policyforge-ba"),
        ],
    )
    assert _summary(out)["approvals at head from counted readers"] == "policyforge-9b"
    assert "policyforge-9b:" not in out.split("approvals at head from counted readers")[1]


def test_one_readers_latest_line_leaves_another_readers_standing_alone(monkeypatch, capsys, head):
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_line(head, "approved", "policyforge-9b"), "policyforge-9b"),
            _item(_line(head, "approved", "policyforge-ba"), "policyforge-ba"),
            _item(_staked(head, "policyforge-ba"), "policyforge-ba"),
        ],
    )
    got = _summary(out)
    assert got["approvals at head from counted readers"] == "policyforge-9b"
    assert got["approvals at head from readers who declared a stake"] == "policyforge-ba"


@pytest.mark.parametrize(
    ("first", "second", "counted", "staked"),
    [
        # b5 on #54: a stake signed with trailing noise is still the same reader.
        ("approved", "staked.", "none", "policyforge-1d"),
        ("staked,", "approved", "policyforge-1d", None),
    ],
    ids=["stake-with-trailing-dot", "stake-with-comma-then-plain"],
)
def test_a_reviewer_name_with_trailing_noise_is_the_same_reader(
    monkeypatch, capsys, head, first, second, counted, staked
):
    def body(kind):
        name = "policyforge-1d" + kind[-1] if kind[-1] in ".," else "policyforge-1d"
        line = f"Prose.\n\nReviewed-SHA: {head} verdict=approved reviewer={name}"
        return line + (" counted=no" if kind.startswith("staked") else "")

    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [_item(body(first), "policyforge-1d"), _item(body(second), "policyforge-1d")],
    )
    got = _summary(out)
    assert got["approvals at head from counted readers"] == counted
    assert got.get("approvals at head from readers who declared a stake") == staked
    assert "policyforge-1d: " in out and " -> " in out  # the flip is printed


def test_a_flip_names_the_readers_line_not_only_the_sha(monkeypatch, capsys, head):
    """ba on #54: on #51 both of 1d's lines sit at one commit."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_line(head, "approved", "policyforge-1d"), "policyforge-1d"),
            _item(_staked(head, "policyforge-1d"), "policyforge-1d"),
        ],
    )
    assert f"counted -> uncounted at their line 2 ({head[:7]})" in out


def test_a_latest_line_at_head_that_cannot_count_says_why(monkeypatch, capsys, head):
    """ba on #54: dropping a reader from the summary in silence reads as
    'never approved'."""
    _, out = _report(
        monkeypatch,
        capsys,
        head,
        [
            _item(_line(head, "approved", "policyforge-9b"), "policyforge-9b"),
            _item(f"{_line(head, 'approved', 'policyforge-9b')} surprise=yes", "policyforge-9b"),
        ],
    )
    assert _summary(out)["approvals at head from counted readers"] == "none"
    assert "policyforge-9b: latest line at head does not count:" in out
