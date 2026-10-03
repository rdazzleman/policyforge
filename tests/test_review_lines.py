"""Every state the verdict reader can report, made to happen on purpose.

**A reader nobody has seen mislabel a line is a reader nobody has tested**,
and this one adjudicates every other tool's verdict, so the states it can
produce are pinned individually rather than exercised in aggregate.

`ELSEWHERE` is the one that matters most and the one no live PR would show
on demand: a real commit that is not an ancestor of the head, from a review
against a pre-rebase history. It is constructed here from this repository's
own objects rather than mocked, because a mocked SHA cannot fail the way a
real one does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import review_lines  # noqa: E402


def _git(*args: str) -> str:
    """Run git and **fail on a non-zero exit**.

    The first version of this returned `result.stdout.strip()` and dropped
    the status, which is the `cmd | tail` defect in the test file for the
    tool that adjudicates verdicts, on the same branch as the lint for that
    exact shape.

    It was not theoretical. On CI's shallow checkout `git rev-parse HEAD~3`
    **exits 128 and prints `HEAD~3` to stdout**, so the fixture's
    `assert head and ancestor and orphan` passed on a truthy error string
    and the failure surfaced three lines later as *"this clone has no
    history"* — a message that blamed the wrong thing and cost a reproduction
    to disbelieve. Found by policyforge-9b.
    """
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} exited {result.returncode}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


#: `commit-tree` refuses without an identity, and `actions/checkout` sets
#: none — the second, independent reason this fixture failed in CI while
#: passing everywhere else. Two unrelated environment facts, one identical
#: symptom, which is why the old assertion blamed neither correctly.
_IDENTITY = ("-c", "user.name=policyforge-test", "-c", "user.email=test@invalid")


@pytest.fixture(scope="module")
def commits() -> dict[str, str]:
    """A head, an ancestor of it, and a commit that is an ancestor of nothing.

    **Built entirely with `commit-tree`, so it needs no clone history.** The
    first version read `HEAD` and `HEAD~3` from the repository, which works
    on a developer's clone and fails on CI's depth-1 checkout — the rule
    about running it somewhere other than where it was written, arriving in
    a test file about instruments that answer a different question than the
    one asked.

    Real objects rather than mocks, still: a mocked SHA cannot be an
    ancestor of anything, so it cannot distinguish `ELSEWHERE` from
    `FABRICATED`, which is the whole distinction being tested.
    """
    tree = _git("rev-parse", "HEAD^{tree}")
    ancestor = _git(*_IDENTITY, "commit-tree", tree, "-m", "base")
    head = _git(*_IDENTITY, "commit-tree", tree, "-p", ancestor, "-m", "child")
    # No `-p` at all: a real object that is an ancestor of nothing, which is
    # exactly ELSEWHERE. policyforge-9b's construction.
    orphan = _git(*_IDENTITY, "commit-tree", tree, "-m", "orphan")

    assert len({ancestor, head, orphan}) == 3, "the three commits must be distinct"
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", orphan, head], cwd=ROOT, capture_output=True
        ).returncode
        != 0
    ), "the constructed orphan IS an ancestor; the fixture is not testing ELSEWHERE"
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, head], cwd=ROOT, capture_output=True
        ).returncode
        == 0
    ), "the constructed ancestor is NOT one; the fixture is not testing STALE"
    return {"head": head, "ancestor": ancestor, "orphan": orphan}


def _comment(sha: str, verdict: str = "approved", reviewer: str = "policyforge-xx") -> dict:
    return {
        "body": f"Some review prose.\n\nReviewed-SHA: {sha} verdict={verdict} reviewer={reviewer}"
    }


# --- the five states -----------------------------------------------------


def test_at_head(commits):
    loc = review_lines.location_of(commits["head"], commits["head"])
    assert loc.label == "AT HEAD"


def test_stale(commits):
    loc = review_lines.location_of(commits["ancestor"], commits["head"])
    assert loc.label == "STALE"


def test_fabricated():
    """A well-formed SHA naming no object. **This has happened here** — a
    short SHA padded to 40 characters by hand, in the convention its author
    had specified."""
    loc = review_lines.location_of("0" * 39 + "1", "HEAD")
    assert loc.label == "FABRICATED"
    assert not loc.resolves


def test_elsewhere(commits):
    """**The state no reader here had before #204**, and the one a naive
    tool gets wrong in the crediting direction: a real commit reviewed on a
    history this PR no longer carries."""
    loc = review_lines.location_of(commits["orphan"], commits["head"])
    assert loc.label == "ELSEWHERE"
    assert loc.resolves, "an ELSEWHERE commit is real; that is what distinguishes it"
    assert not loc.ancestor_of_head


def test_malformed_is_a_shape_not_a_location(commits):
    """Shape is asked without reference to the repository."""
    assert not review_lines.shape_of(commits["head"][:7], "approved").well_formed
    assert review_lines.shape_of(commits["head"], "approved").well_formed


# --- a well-formed SHA naming nothing splits THREE ways ------------------
#
# **The single most common verdict-line defect on this train**, produced by
# three different sessions in one day. All three were 40 hex, syntactically
# perfect, and named no object -- because the visible prefix of an
# abbreviated display was extended with invented characters.
#
# The three differ in whether the REVIEW survives, which is the whole value
# of splitting them.


def test_the_property_has_no_length_in_it(commits):
    """**policyforge-80's spec keyed on 'exactly 8 common characters' and
    that was the enumerate-the-instance trap in a detector spec.**

    Measured: `git log --oneline` abbreviates to SEVEN here, so the eighth
    character of the instance that prompted the rule matched at one chance
    in sixteen. A detector keyed on 8 classifies that instance correctly by
    coincidence and misses the next seven-character case fifteen times out
    of sixteen.

    The walk finds whatever length is there, and this test uses a length
    the spec never mentioned.
    """
    extended = commits["head"][:9] + "0" * 31
    length, target = review_lines.longest_resolving_prefix(extended)
    assert target == commits["head"], "the walk must find the commit the prefix names"
    assert length >= 9, f"found {length}; the walk must not be pinned to one length"


def test_reconstructed_preserves_the_review(commits):
    """Longest resolving prefix IS the reviewed head.

    **The prefix is evidence the reviewer read the right commit** — a
    correct review with a broken anchor. Recoverable, and it must not be
    reported as though nobody read anything.
    """
    extended = commits["head"][:7] + "0" * 33
    location = review_lines.location_of(extended, commits["head"])
    assert location.label == "RECONSTRUCTED"
    assert "the head of this PR" in location.diagnosis


def test_an_earlier_commit_of_this_pr_is_still_RECONSTRUCTED(commits):
    """**The state is a property of the line PLUS what you compare it to.**

    This test asserted `MISANCHORED` for a prefix naming an ANCESTOR of
    head, because the first version compared `prefix_target == head`. That
    is wrong and it drifts: a line correctly classified `RECONSTRUCTED`
    silently becomes the serious state the moment its author pushes again
    — no event, no diff, nothing about the line changed.

    Measured on a real line on #230: prefix `504994f4` names the commit
    that WAS the head when the line was written. Head-equality called it
    `MISANCHORED`; it is a real commit of this PR and the review is real.
    policyforge-80 found it by **running** its classifier rather than by
    reviewing the spec.

    Staleness is a second, independent axis and is reported as such rather
    than replacing this one.
    """
    extended = commits["ancestor"][:7] + "f" * 33
    location = review_lines.location_of(extended, commits["head"])
    assert location.label == "RECONSTRUCTED"
    assert "STALE" in location.diagnosis, "the second axis must still be reported"


def test_the_same_line_keeps_its_state_when_the_head_MOVES(commits):
    """**The axis the defect actually lived on, and the one my other tests
    could not reach.**

    policyforge-ba's diagnosis: every case here fixes the head and varies
    the prefix. Nothing varied the head with the prefix held fixed — and
    that is the direction reality moves in, because **a verdict line is
    written once and the head moves afterwards.**

    So the defect was invisible to a suite that looked thorough. A line
    correctly classified `RECONSTRUCTED` silently became `MISANCHORED` —
    the serious state — the moment its author pushed again. Drift toward
    the state that loses a real review, with no event and no diff.

    This test writes the line once and then advances the head, which is
    the sequence that happens.
    """
    line_written_against = commits["head"]
    extended = line_written_against[:7] + "0" * 33

    before = review_lines.location_of(extended, line_written_against)
    assert before.label == "RECONSTRUCTED"

    # the author pushes; nothing about the line changes
    moved = _git(
        *_IDENTITY,
        "commit-tree",
        _git("rev-parse", "HEAD^{tree}"),
        "-p",
        line_written_against,
        "-m",
        "a later push",
    )
    after = review_lines.location_of(extended, moved)

    assert after.label == "RECONSTRUCTED", (
        "the same line changed state because someone else pushed -- the "
        "classification must be a property of the line and the BRANCH, "
        "not of the line and today's head"
    )
    assert "STALE" in after.diagnosis, (
        "staleness is a second axis and must now be reported, not swallowed"
    )


def test_misanchored_is_a_commit_not_on_this_branch(commits):
    """The serious case: what was reviewed is unknown, because the prefix
    names nothing this PR ever carried. **Not recoverable**, and a
    two-state classification collapses the recoverable case into it."""
    off_branch = _git(*_IDENTITY, "commit-tree", _git("rev-parse", "HEAD^{tree}"), "-m", "off")
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", off_branch, commits["head"]],
            cwd=ROOT,
            capture_output=True,
        ).returncode
        != 0
    ), "the fixture's off-branch commit IS on the branch; it is not testing MISANCHORED"

    location = review_lines.location_of(off_branch[:7] + "0" * 33, commits["head"])
    assert location.label == "MISANCHORED"
    assert "NOT on this branch" in location.diagnosis


def test_fabricated_is_only_when_no_prefix_resolves():
    """A verdict on a commit nobody read. Distinct from the two above,
    which both have a real commit behind them."""
    location = review_lines.location_of("deadbee" + "f" * 33, "HEAD")
    assert location.label == "FABRICATED"
    assert location.diagnosis == "", "there is nothing to diagnose; no prefix resolves"


def test_the_message_is_a_diagnosis_not_an_accusation(commits):
    """It must name WHERE the SHA came from, because **the remedy is in the
    display, not in the discipline.** The rule *never lengthen an
    abbreviated SHA* has now failed three times: it asks a person to resist
    something the tooling hands them."""
    extended = commits["head"][:7] + "0" * 33
    diagnosis = review_lines.location_of(extended, commits["head"]).diagnosis
    assert commits["head"][:12] in diagnosis, "name the object the prefix resolves to"
    assert "abbreviated display" in diagnosis


# --- the defect this file exists to avoid --------------------------------


def test_a_malformed_line_still_gets_its_ancestry_asked(commits):
    """**policyforge-9b's `if`/`elif` defect, pinned.**

    Chaining shape and location meant a line failing the shape test never
    had its ancestry asked, and the tool reported *"a real review a strict
    reader would lose"* about a commit the PR had never carried. **It
    credited a review that was not there** — the understating direction,
    which nobody catches by reading output they expected to be right.

    A short SHA of an ELSEWHERE commit carries both faults, and both must
    be reported.
    """
    short = commits["orphan"][:8]
    shape = review_lines.shape_of(short, "approved")
    location = review_lines.location_of(short, commits["head"])

    assert not shape.well_formed, "MALFORMED"
    assert location.resolves, "and it resolves -- the ancestry question is answerable"
    assert location.label == "ELSEWHERE", (
        "and it is ELSEWHERE, which the chained version never asked"
    )


def test_a_short_sha_that_resolves_is_reported_as_resolving(commits):
    """`MALFORMED` must not be conflated with `FABRICATED`. A short SHA that
    resolves is a convention violation; one that does not is a fabrication,
    and the remedies differ."""
    resolves = review_lines.location_of(commits["head"][:8], commits["head"])
    does_not = review_lines.location_of("abcdef1", commits["head"])
    assert resolves.resolves and resolves.at_head
    assert not does_not.resolves


# --- parsing -------------------------------------------------------------


def test_only_the_last_non_empty_line_counts(commits):
    """A quoted verdict is indistinguishable from a given one anywhere but
    the end — **discussing a review would create one**, including retracting
    it."""
    quoted = {
        "body": (
            f"I am retracting this:\n\n"
            f"    Reviewed-SHA: {commits['head']} verdict=approved reviewer=policyforge-xx\n\n"
            f"It named the wrong commit."
        )
    }
    assert review_lines.verdict_lines([quoted]) == []


def test_a_real_verdict_at_the_end_is_found(commits):
    assert len(review_lines.verdict_lines([_comment(commits["head"])])) == 1


def test_a_short_sha_verdict_is_not_silently_skipped(commits):
    """**Requirement 2 of this convention's own spec, which its first
    reader failed.** A pattern demanding exactly 40 hex cannot report a
    MALFORMED line — it drops it, and a live short-SHA
    `changes-requested` went unreported."""
    found = review_lines.verdict_lines([_comment(commits["head"][:7], "changes-requested")])
    assert len(found) == 1, "a short-SHA verdict must be FOUND so it can be called MALFORMED"


def test_an_unknown_verdict_word_is_reported_not_dropped(commits):
    found = review_lines.verdict_lines([_comment(commits["head"], "looks-fine")])
    assert len(found) == 1
    assert not review_lines.shape_of(commits["head"], "looks-fine").verdict_known


# --- the four shapes the REAL corpus contains ----------------------------
#
# policyforge-9b scanned all 60 PRs: 24 carry verdict lines, 81 occurrences
# of the key. These four are what is actually out there, and the first
# version of this reader silently dropped two of them. They are facts about
# the corpus and could not have been reached by thinking harder about the
# format.


def test_a_pasted_terminal_ellipsis_does_not_vanish(commits):
    """**`0374a18…` on #172, twice — a display truncation marker, copied.**

    Demanding `[0-9a-f]{4,40}\\s` made this line vanish entirely, because
    the character after the hex was `…` rather than a space. A real verdict
    by a real reviewer, reported as absent. **That is the understating
    direction in the tool whose whole job is to say what was reviewed.**
    """
    body = f"Reviewed-SHA: {commits['head'][:7]}… verdict=approved reviewer=policyforge-80"
    found = review_lines.verdict_lines([{"body": body}])
    assert len(found) == 1, "the line must be FOUND before it can be called malformed"

    shape = review_lines.shape_of(found[0]["sha"], found[0]["verdict"], found[0]["reviewer"])
    assert "MALFORMED" in shape.faults
    assert any("trailing" in f for f in shape.faults), "the ellipsis must be named, not swallowed"

    # and the cleaned SHA still resolves, so the review is placeable
    assert review_lines.location_of(found[0]["sha"], commits["head"]).resolves


def test_markdown_bleeding_into_the_reviewer_is_reported(commits):
    """**`reviewer=policyforge-80**` in the corpus.** A strict
    `reviewer in SESSIONS` test rejects it and a strict pattern drops it;
    either way a real review disappears over a formatting character."""
    body = f"Reviewed-SHA: {commits['head']} verdict=approved reviewer=policyforge-80**"
    found = review_lines.verdict_lines([{"body": body}])
    assert len(found) == 1
    shape = review_lines.shape_of(found[0]["sha"], found[0]["verdict"], found[0]["reviewer"])
    assert "MALFORMED" not in shape.faults, "the SHA is fine; only the reviewer field is dirty"
    assert any("reviewer has trailing" in f for f in shape.faults)


def test_blocked_is_a_real_verdict_with_one_instance(commits):
    """`blocked` appears **once in 81 lines**. A vocabulary check that omits
    it rejects a real verdict — and a rare value is exactly the one an
    author leaves out of a hand-written list."""
    assert "blocked" in review_lines.VERDICTS
    shape = review_lines.shape_of(commits["head"], "blocked", "policyforge-9b")
    assert shape.verdict_known
    assert shape.faults == []


def test_prose_mentioning_the_key_is_not_a_verdict():
    """Six occurrences in the corpus are people *discussing* the convention."""
    prose = {"body": "and it belongs on #181: `Reviewed-SHA:` anchors a verdict to a commit"}
    assert review_lines.verdict_lines([prose]) == []


def test_a_complete_verdict_quoted_MID_LINE_is_not_credited(commits):
    """**This is the case where the `^` anchor actually does work**, and my
    first version of the test above did not reach it.

    Bare prose mentioning the key never matched anyway — the pattern
    requires `verdict=` and `reviewer=` too — so removing the anchor broke
    no test. The case that needs it is a *structurally complete* verdict
    line embedded in a sentence, which is what someone writes when
    discussing or retracting one.

    Found by mutating the anchor away and watching nothing fail. **A guard
    I had not seen fail was a guard I had not tested**, in the file whose
    subject is exactly that.
    """
    # It has to END the line but not START it, or `\\s*$` rejects it for a
    # different reason and the anchor is still untested. My second attempt
    # put trailing prose after it and passed with the anchor removed.
    embedded = {
        "body": (
            "Earlier I wrote, and am now withdrawing: "
            f"Reviewed-SHA: {commits['head']} verdict=approved reviewer=policyforge-xx"
        )
    }
    assert review_lines.verdict_lines([embedded]) == [], (
        "a verdict quoted inside a sentence is a discussion of a review, not one"
    )


def test_exact_duplicates_are_both_reported(commits, monkeypatch, capsys):
    """**Seven exact duplicates exist** — 80 posted the same
    `changes-requested` twice on #204.

    Reported in document order and both shown, because this is *the record*
    rather than a tally. A tool that deduplicated would be answering 'how
    many distinct objections' — a different question, and one nobody asked
    it. See the no-consent boundary below: the moment duplicates matter,
    something is counting toward a decision.
    """
    dupe = _comment(commits["ancestor"], "changes-requested", "policyforge-80")
    monkeypatch.setattr(review_lines, "fetch", lambda n, r: (commits["head"], [dupe, dupe], 2))
    review_lines.report(1, "x/y")
    out = capsys.readouterr().out
    # Count in the record only. The stale-block warning below also names
    # each one, which is right -- two unanswered objections are two things
    # to answer -- but it is a different section.
    record = out.split("!!")[0]
    assert record.count(commits["ancestor"][:12]) == 2, "both occurrences belong in the record"


# --- the population, which is the #228 half ------------------------------


def test_a_short_fetch_refuses_rather_than_reports(commits, monkeypatch, capsys):
    """**#228, inside the instrument that adjudicates every other instrument.**

    A reader that fetches 3 of 8 comments and reports those 3 cleanly is
    this week's dominant failure in the worst possible place. The second
    derivation is GitHub's own comment count, which is independent in
    dimension: server-side metadata against a paginated client fetch.
    """
    monkeypatch.setattr(
        review_lines, "fetch", lambda n, r: (commits["head"], [_comment(commits["head"])], 8)
    )
    assert review_lines.report(1, "x/y") == 2
    assert "POPULATION SHORT" in capsys.readouterr().err


def test_an_agreeing_population_proceeds(commits, monkeypatch, capsys):
    """What it must ALLOW. A guard that refuses every fetch is useless."""
    monkeypatch.setattr(
        review_lines, "fetch", lambda n, r: (commits["head"], [_comment(commits["head"])], 1)
    )
    assert review_lines.report(1, "x/y") == 0
    assert "AT HEAD" in capsys.readouterr().out


# --- stale blocks --------------------------------------------------------


def _report(monkeypatch, capsys, head, comments) -> str:
    monkeypatch.setattr(review_lines, "fetch", lambda n, r: (head, comments, len(comments)))
    review_lines.report(1, "x/y")
    return capsys.readouterr().out


def _section(out: str, marker: str) -> str:
    """The lines of one report section, so an assertion cannot pass on text
    that another section printed."""
    if marker not in out:
        return ""
    return out.split(marker, 1)[1].split("\n\n", 1)[0]


def test_another_reviewers_approval_does_not_answer_an_objection(commits, monkeypatch, capsys):
    """**#237's own fail-on-purpose case.** A block, a push, and an approval
    from somebody ELSE: the objection is still open. A push about something
    else does not answer it, and neither does a different reader's consent."""
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            _comment(commits["head"], "approved", "policyforge-80"),
        ],
    )
    assert "policyforge-9b" in _section(out, "OPEN OBJECTION(S)")


def test_a_block_the_same_reviewer_later_cleared_at_head_is_silent(commits, monkeypatch, capsys):
    """**What it must NOT do.** A warning that fires on resolved objections
    trains the reader to ignore it, which costs more than the warning saves.
    """
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            _comment(commits["head"], "approved", "policyforge-9b"),
        ],
    )
    assert "OPEN OBJECTION" not in out
    assert "retired by their own reviewer" not in out


def test_answered_then_the_head_moved_is_not_reported_as_ignored(commits, monkeypatch, capsys):
    """**Answered vs ignored, #237.** The objector approved at a commit that
    is no longer head. The reader used to print the same STALE BLOCK here as
    for an objection nobody answered. It is a re-read, not an alarm."""
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            _comment(commits["ancestor"], "approved", "policyforge-9b"),
        ],
    )
    assert "OPEN OBJECTION" not in out
    retired = _section(out, "retired by their own reviewer")
    assert "policyforge-9b objected at" in retired
    assert "(STALE)" in retired, "the approval that retired it is itself stale; say so"


def test_a_later_approval_at_head_settles_an_earlier_retirement(commits, monkeypatch, capsys):
    """**Found by running the reader on #273, not by a fixture.** Object,
    approve at a stale head, object again, approve at head: the objector's
    last word is an approval at head, so nothing is left to re-read. The
    first version retired the first objection with the FIRST approval it
    found -- the stale one -- and printed a notice about a settled question.
    """
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            _comment(commits["ancestor"], "approved", "policyforge-9b"),
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            _comment(commits["head"], "approved", "policyforge-9b"),
        ],
    )
    assert "OPEN OBJECTION" not in out
    assert "retired by their own reviewer" not in out


def test_a_block_posted_after_the_reviewers_own_approval_is_open(commits, monkeypatch, capsys):
    """**Order decides, not position.** The reader used to suppress this,
    because the reviewer had an approval at head. Their LATEST word is an
    objection, which is the unsafe direction to get wrong."""
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["head"], "approved", "policyforge-9b"),
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
        ],
    )
    assert "policyforge-9b" in _section(out, "OPEN OBJECTION(S)")


def test_an_open_objection_at_head_is_reported_too(commits, monkeypatch, capsys):
    """OPEN is shown whatever its SHA. A block at head is the most current
    objection there is, and a report that listed only stale ones would make
    the live one the easiest to miss."""
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [_comment(commits["head"], "blocked", "policyforge-9b")],
    )
    assert "(AT HEAD)" in _section(out, "OPEN OBJECTION(S)")


def test_a_malformed_approval_does_not_retire_an_objection(commits, monkeypatch, capsys):
    """Only a well-formed approval retires. A malformed one may be crediting
    a review that was not there, so the objection stays OPEN -- the safe
    direction. Twin: the same approval well-formed retires it (the test
    above that is silent)."""
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            # Same reviewer, so the string match succeeds and ONLY the shape
            # guard can keep this open. (A malformed reviewer would fail the
            # match on its own and test nothing about the guard.)
            _comment(commits["head"][:12] + "…", "approved", "policyforge-9b"),
        ],
    )
    assert not review_lines.shape_of(commits["head"][:12] + "…", "approved").well_formed
    assert "policyforge-9b" in _section(out, "OPEN OBJECTION(S)")


def _off_branch_commit() -> str:
    return _git(*_IDENTITY, "commit-tree", _git("rev-parse", "HEAD^{tree}"), "-m", "off")


@pytest.mark.parametrize(
    ("where", "sha_for"),
    [
        # A real commit, not on this PR: an approval of different code.
        ("ELSEWHERE", lambda c: c["orphan"]),
        # 40 hex whose prefix names a commit NOT on this branch.
        ("MISANCHORED", lambda c: _off_branch_commit()[:7] + "0" * 33),
        # 40 hex that names nothing, and no prefix resolves.
        ("FABRICATED", lambda c: "0" * 39 + "1"),
    ],
)
def test_an_approval_off_this_branch_does_not_retire_an_objection(
    commits, monkeypatch, capsys, where, sha_for
):
    """**9b's finding on #284.** A well-formed approval that points off the
    branch, or at nothing, approved something other than this code, so it
    cannot answer an objection to it. One test per excluded location."""
    sha = sha_for(commits)
    assert review_lines.location_of(sha, commits["head"]).label == where, "fixture mislabelled"
    assert review_lines.shape_of(sha, "approved").well_formed, "only the LOCATION may differ"
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            _comment(sha, "approved", "policyforge-9b"),
        ],
    )
    assert "policyforge-9b" in _section(out, "OPEN OBJECTION(S)"), f"retired by an {where} approval"


def test_a_reconstructed_approval_still_retires_an_objection(commits, monkeypatch, capsys):
    """The passing twin. RECONSTRUCTED names no object, but its prefix is a
    commit of THIS PR: the reviewer read this branch, with a broken anchor.
    Excluding it would re-open objections their own reviewer answered."""
    sha = commits["head"][:7] + "0" * 33
    assert review_lines.location_of(sha, commits["head"]).label == "RECONSTRUCTED"
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-9b"),
            _comment(sha, "approved", "policyforge-9b"),
        ],
    )
    assert "OPEN OBJECTION" not in out
    assert "(RECONSTRUCTED)" in _section(out, "retired by their own reviewer")


def test_a_perishable_session_name_is_named_not_silently_counted(commits, monkeypatch, capsys):
    """**The rename case (#237's comment).** A block signed with a session
    name and an approval signed with the handle are the same reader to a
    person and two to a string match. The report says so, by name."""
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [
            _comment(commits["ancestor"], "changes-requested", "policyforge-78"),
            _comment(commits["head"], "approved", "policyforge-1d"),
        ],
    )
    assert "policyforge-78" in _section(out, "OPEN OBJECTION(S)")
    assert "policyforge-78" in _section(out, "not a stable handle")
    assert "policyforge-1d" not in _section(out, "not a stable handle")


def test_every_stable_handle_passes_without_a_notice(commits, monkeypatch, capsys):
    """The quiet twin: lines signed with the six handles raise no notice."""
    out = _report(
        monkeypatch,
        capsys,
        commits["head"],
        [_comment(commits["head"], "approved", h) for h in sorted(review_lines.STABLE_HANDLES)],
    )
    assert len(review_lines.STABLE_HANDLES) == 6
    assert "not a stable handle" not in out


def test_no_verdicts_is_not_reported_as_no_objection(commits, monkeypatch, capsys):
    """'Nobody has posted one' and 'no objection stands' are different facts
    and a reader cannot tell them apart. Saying so is the whole job."""
    monkeypatch.setattr(review_lines, "fetch", lambda n, r: (commits["head"], [], 0))
    review_lines.report(1, "x/y")
    assert "NOT 'no" in capsys.readouterr().out


# --- the line it must not cross ------------------------------------------


def test_it_does_not_compute_consent():
    """Counting verdicts into a merge decision would turn a written
    convention into an authorisation mechanism, and no such mechanism
    exists here — branch protection does not distinguish these sessions
    from the user.

    Asserted over the source because it is a design boundary, not a
    behaviour: the moment a caller can ask this tool 'may I merge', the
    convention has been promoted to a control it cannot be.
    """
    source = (ROOT / "scripts" / "review_lines.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith(("#", '"', "*"))
    )
    for forbidden in ("is_approved", "may_merge", "can_merge", "sys.exit(1) if"):
        assert forbidden not in code, f"{forbidden!r} turns a report into an authorisation"


def test_the_docstring_lists_every_state_the_code_can_return():
    """**A partition that does not add up, in the file about those.**

    The docstring said *THE FIVE STATES* while `Location.label` returned
    six — omitting `RECONSTRUCTED` and `MISANCHORED`, the two that were the
    point of the push that added them, and including `MALFORMED`, which the
    next paragraph establishes is a different axis. Found by policyforge-ba.

    Derived from the code rather than typed, so a seventh state added later
    is covered by the test that exists. Walks ternaries too: my own first
    count found four, because `return "STALE" if x else "ELSEWHERE"` is not
    a `Return(Constant)` — **an incomplete probe agreeing with an incomplete
    docstring.**
    """
    import ast

    # Scoped to `Location.label`. The first version walked every function
    # named `label` and picked up `Shape.label`'s "well-formed" too --
    # **a probe broader than its subject**, which is the other half of the
    # same mistake as the incomplete one below it.
    tree = ast.parse((ROOT / "scripts" / "review_lines.py").read_text(encoding="utf-8"))
    location = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Location"
    )
    label = next(n for n in location.body if isinstance(n, ast.FunctionDef) and n.name == "label")
    returned = {
        inner.value
        for inner in ast.walk(label)
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
    }
    assert len(returned) >= 6, f"expected at least six states, found {sorted(returned)}"

    doc = review_lines.__doc__ or ""
    missing = [state for state in returned if state not in doc]
    assert not missing, f"states the code returns and the module docstring does not list: {missing}"


# --- #425: a declared stake --------------------------------------------------

_SHA = "c" * 40
_AT_HEAD = review_lines.Location(resolves=True, at_head=True, ancestor_of_head=False)


def _declared(
    verdict: str = "approved", reviewer: str = "policyforge-9b", tail: str = "counted=no"
):
    return {
        "body": f"Read it.\n\nReviewed-SHA: {_SHA} verdict={verdict} reviewer={reviewer} {tail}"
    }


def _line(fields: dict) -> review_lines.Line:
    return review_lines.Line(
        sha=fields["sha"],
        verdict=fields["verdict"],
        reviewer=fields["reviewer"],
        shape=review_lines.shape_of(
            fields["sha"], fields["verdict"], fields["reviewer"], fields["tokens"]
        ),
        location=_AT_HEAD,
        counted=fields["counted"],
    )


def test_a_declared_stake_is_read_not_dropped():
    """Before #425 the pattern ended at `reviewer=`, so this line did not
    parse and the verdict vanished from every report, objection included."""
    (found,) = review_lines.verdict_lines([_declared()])
    assert (found["reviewer"], found["verdict"], found["counted"]) == (
        "policyforge-9b",
        "approved",
        False,
    )
    assert found["tokens"] == ("counted=no",)


def test_a_declared_stake_never_counts_and_the_same_line_without_it_does():
    (declared,) = review_lines.verdict_lines([_declared()])
    (plain,) = review_lines.verdict_lines([_declared(tail="")])
    (explicit,) = review_lines.verdict_lines([_declared(tail="counted=yes")])
    assert not _line(declared).counts
    assert _line(plain).counts
    assert _line(explicit).counts and not _line(explicit).shape.faults


def test_an_unknown_token_is_reported_and_does_not_count():
    """An unknown word on a verdict line is reported, as an unknown verdict
    is, and a line whose meaning this reader cannot tell does not count."""
    for tail in ("lgtm", "counted=maybe"):
        (found,) = review_lines.verdict_lines([_declared(tail=tail)])
        line = _line(found)
        assert any("UNKNOWN TOKEN" in fault for fault in line.shape.faults), tail
        assert not line.counts, tail


def test_a_declared_stake_still_objects_and_can_retire_its_own_objection():
    """A stake changes whose approval is counted, not whose finding stands."""
    objection = _line(review_lines.verdict_lines([_declared("changes-requested")])[0])
    withdrawal = _line(review_lines.verdict_lines([_declared("approved")])[0])
    ((_, alone, _),) = review_lines.objection_states([objection])
    ((_, answered, by),) = review_lines.objection_states([objection, withdrawal])
    assert alone == "OPEN"
    assert answered == "RETIRED AT HEAD" and by is withdrawal


def test_the_report_names_counted_and_declared_readers_apart(monkeypatch, capsys):
    comments = [
        _declared("approved", "policyforge-9b"),
        _declared("approved", "policyforge-1d", tail=""),
    ]
    monkeypatch.setattr(review_lines, "fetch", lambda number, repo: (_SHA, comments, len(comments)))
    monkeypatch.setattr(review_lines, "location_of", lambda sha, head: _AT_HEAD)
    assert review_lines.report(421, "o/r") == 0
    out = capsys.readouterr().out
    assert "approvals at head from counted readers: policyforge-1d" in out
    assert "declared a stake: policyforge-9b" in out
    assert "UNCOUNTED (declared stake)" in out


def test_an_approval_with_an_unknown_token_does_not_retire_an_objection():
    """policyforge-ba on #428: "(retracted)" is reported as a fault on the same
    screen; it must not also quietly withdraw the objection."""
    objection = _line(review_lines.verdict_lines([_declared("changes-requested", tail="")])[0])
    for tail in ("(retracted)", "counted=no.", "COUNTED=NO"):
        approval = _line(review_lines.verdict_lines([_declared("approved", tail=tail)])[0])
        ((_, state, _),) = review_lines.objection_states([objection, approval])
        assert state == "OPEN", tail


def test_both_count_tokens_on_one_line_never_count():
    (both,) = review_lines.verdict_lines([_declared(tail="counted=no counted=yes")])
    assert not _line(both).counts
