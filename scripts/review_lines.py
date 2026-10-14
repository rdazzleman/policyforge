#!/usr/bin/env python3
"""Read the `Reviewed-SHA:` verdict lines on a pull request, and say what they
are claims ABOUT.

WHY THIS EXISTS. Every session here authenticates as the same GitHub account,
so `gh pr review --approve` is refused as self-approval and `reviewDecision`
never reaches `APPROVED`. The team's review record is therefore a convention:
a comment whose **last non-empty line** reads

    Reviewed-SHA: <40-hex> verdict=approved|changes-requested|blocked reviewer=<session>

A convention has no validator, so every failure it has had has been silent.
This reports; it does not decide.

**IT DOES NOT COMPUTE CONSENT.** Counting verdicts into a merge decision
would turn a written convention into an authorisation mechanism, and no such
mechanism exists here — branch protection does not distinguish these sessions
from the user. A reader reports; a person decides. If you find yourself adding
`--is-approved`, that is the line.

A DECLARED STAKE (#425). A reader with a stake in a PR (a finder, a ruler,
the author of the issue) is not a counted reader, and says so by ending
the line with a token:

    Reviewed-SHA: <40-hex> verdict=approved reviewer=<session> counted=no

Such a line is read, reported as UNCOUNTED, and never makes `counts` true,
so `stale_prs` does not call a PR ready on it. **It still objects, and its
reviewer can still retire their own objection**: a stake changes whose
approval is counted, not whose finding stands. **This tool cannot DETECT a
stake**; it honours one a reader declares. An undeclared stake is still a
review judgement, and the charge's rule is still the source. Before #425
this pattern ended at `reviewer=`, so a line carrying the token did not
parse and the verdict VANISHED, objection included (measured).

The tokens, exactly (policyforge-b5 and policyforge-ba on #428):

    counted=no     a declared stake: read, reported, never counts
    counted=yes    known, and counts, as a line with no token does
    both           never counts: the line says two things, so the
                   safe reading wins
    anything else  UNKNOWN TOKEN: kept and reported rather than dropped,
                   as an unknown verdict is; never counts, and never
                   retires an objection, since it might be crediting a
                   review that was not there (`reviewer=x (retracted)`,
                   `counted=no.`, `COUNTED=NO`)

**A token goes LAST.** Placed before `reviewer=`, the line still does not
parse and vanishes, as any reordered line always has.

THE SIX LOCATION STATES. **`MALFORMED` is not among them** -- it is a
property of the line's SHAPE, asked on its own axis, and listing it here
was how the first version of this docstring came to say FIVE while
`Location.label` returned six. Found by policyforge-ba, and it is the same
partition-that-does-not-add-up as #229.

The SHA resolves:

    AT HEAD      the PR's current head. The only state that counts.
    STALE        a real commit, an ancestor of head, but not head.
    ELSEWHERE    a real commit NOT an ancestor of this head -- reviewed
                 against a pre-rebase history. Neither valid nor stale,
                 and the state no reader here had before #204.

The SHA resolves to nothing, and then what matters is whether the REVIEW
survives:

    RECONSTRUCTED  some prefix names a commit ON THIS BRANCH. The prefix is
                   evidence the reviewer read this PR: a correct review
                   with a broken anchor. Recoverable, and possibly also
                   stale -- a second axis, reported alongside.
    MISANCHORED    some prefix names a commit NOT on this branch. What was
                   reviewed is unknown. Not recoverable.
    FABRICATED     no prefix resolves. Nobody read anything identifiable.

**SHAPE AND LOCATION ARE INDEPENDENT QUESTIONS AND ARE ASKED INDEPENDENTLY.**
policyforge-9b's version chained them as `if`/`elif`, so a line that failed
the shape test never had its ancestry asked -- and the tool reported *"a real
review a strict reader would lose"* about a commit the PR had never carried.
**It credited a review that was not there**, which is the understating
direction: nobody catches that by reading output they expected to be right. A
line can carry both faults and the report has to be able to say so.

A STALE APPROVAL AND A STALE BLOCK ARE OPPOSITE FACTS. An expiring approval
is the safe direction -- the work changed, read it again. An expiring
`changes-requested` is the unsafe one: the author pushes something unrelated
and an unanswered objection silently becomes `blocking verdicts at head: 0`.
That exact line told policyforge-1d nothing was sitting with them on #221
while 9b's block stood untouched.

ONLY THE OBJECTOR RETIRES AN OBJECTION, AND ORDER DECIDES (#237). Each
reviewer's lines are walked in posting order. A block is retired only by a
LATER approval from the SAME reviewer, at any SHA, so an approval from
anyone else never retires it and silence stays silence. That gives three
states, reported apart:

    OPEN                      nothing retired it -- shown whatever its SHA
    RETIRED, approval stale   the objector answered it, then the head moved:
                              re-read, which is the safe direction
    RETIRED at head           silent; a warning that fires on resolved
                              objections trains the reader to ignore it

This used to be decided by position alone: a block was suppressed when its
reviewer had any approval at head. So "answered, and the head moved on"
printed the same STALE BLOCK as "never answered", and a block posted AFTER
its reviewer's own approval was suppressed -- the unsafe direction.

No field in the line names which objection an approval answers, and none is
needed: only the objector can retire one, and an objector who approves while
another concern stands can post `changes-requested` last.

RETIREMENT MATCHES `reviewer=` AS A STRING, so it only works across a
session rename if both lines carry the same stable handle. A line signed
with anything else -- a per-run session name -- is flagged by name rather
than silently treated as a new reviewer.

THE POPULATION IS CHECKED AGAINST A SECOND DERIVATION IN THE SAME UNIT.
A reader that fetches 3 of 8 comments and reports those 3 cleanly would put
this project's dominant failure of the week inside the instrument that
adjudicates every other instrument. So:

    gh api repos/<o>/<r>/issues/<n> --jq .comments   a count GitHub maintains
    gh pr view <n> --json comments | length          the list this process got

Independent in *dimension* -- server-side metadata against a paginated client
fetch -- which is the property a cruder same-shape census would have lacked
(#228). Measured on live PRs: #229 3 vs 3, #231 2 vs 2. A short fetch refuses
rather than reports.

**ON FORGEJO THAT DERIVATION DOES NOT EXIST** (measured, 2026-09-27; see
`fetch_forgejo`). The issue's comment counter tracks neither list once a
formal review exists, so the second derivation there is the timeline,
compared by id set. Forgejo also changes what a line can prove: each handle
posts as its own bot, so **a line whose `reviewer=` is not the login that
posted it is reported, never counts, and never retires an objection.** On
GitHub every line was posted by one account and the check had nothing to
compare.

Formal reviews are read too: a verdict line ending a review body counts as
one ending a comment, a standing `REQUEST_CHANGES` is reported with or
without a line, and a verdict-shaped line in an INLINE code comment is
reported as not read.

**The standing warning for this file specifically**, policyforge-b5's
formulation: *the shrink is invisible precisely because the check was made
more specific.* Every narrowing of the line pattern below improves what it
asserts and silently reduces what it covers. A parser for a line format gets
narrowed repeatedly, so before tightening `_VERDICT`, check that the number of
lines found did not drop.

Usage:

    FORGEJO_TOKEN=<your own bot's token> python scripts/review_lines.py 10
    python scripts/review_lines.py 229 --forge github
    python scripts/review_lines.py 229 --forge github --repo owner/name

Locations are resolved in THIS clone, so fetch the PR head first
(`fj <handle> git fetch forgejo "+refs/pull/N/head:refs/remotes/forgejo-pr/N"`).
A Forgejo head that does not resolve here is refused, not reported as
FABRICATED lines.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

#: The canonical owner since the transfer (#244). The old path still works
#: through GitHub's redirect, which is why nothing failed while it read
#: `rdazzlebot` -- and why it would keep "working" if anything were ever
#: created at the old name. Overridable with `--repo`.
REPO = "rdazzleman/policyforge"

#: Where the team works since 2026-09-27 (CLAUDE.md, "Where work lives"): a
#: local Forgejo, with GitHub as the public mirror. `--forge github` still
#: reads the old threads. The URL is overridable with FORGEJO_URL.
#: 127.0.0.1, not `localhost`: the container publishes on 127.0.0.1 only, and
#: on this Windows host `localhost` tries ::1 first -- measured 2.1 s against
#: 0.01 s per request, and a report makes several.
FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://127.0.0.1:3000").rstrip("/")
FORGEJO_REPO = "policyforge/policyforge"

#: The convention's line. Anchored to the END of the comment because a quoted
#: line is otherwise indistinguishable from a given one -- **discussing a
#: review would create one**, including retracting it.
#:
#: It is anchored at LINE START as well. Measured across the corpus, six
#: occurrences of the key are prose -- people discussing the convention --
#: and a reader matching mid-line credits all six as verdicts.
#:
#: **MATCH LOOSELY, REPORT STRICTLY.** The fields are captured as
#: non-whitespace runs and validated afterwards, rather than being required
#: to be clean in order to match at all. A pattern that demands well-formed
#: fields cannot report a malformed one -- it drops the line, and a dropped
#: line is indistinguishable from no review.
#:
#: This is not hypothetical. Measured by policyforge-9b across all 60 PRs,
#: 81 occurrences, and the first version of THIS file silently dropped two
#: of the four real shapes:
#:
#:     0374a18…                  a terminal's truncation marker, pasted
#:     reviewer=policyforge-80** markdown bold bleeding into the value
#:
#: Both are real verdicts by real reviewers. Demanding `[0-9a-f]{4,40}\s`
#: made the first vanish because the next character was `…` rather than a
#: space, which is the understating direction in the tool whose entire job
#: is to say what was reviewed.
_VERDICT = re.compile(
    r"^\s*Reviewed-SHA:\s+(?P<sha>\S+)\s+"
    r"verdict=(?P<verdict>\S+)\s+"
    r"reviewer=(?P<reviewer>\S+)"
    # Trailing `key=value` tokens: `counted=no` (#425), and anything else,
    # which is kept and reported rather than dropping the whole line.
    r"(?P<tokens>(?:\s+\S+)*)\s*$"
)

#: Trailing characters that are formatting rather than value. Stripped
#: before comparing, and **reported** rather than silently removed.
_TRAILING_NOISE = "*_`.,;:)]}…"

VERDICTS = ("approved", "changes-requested", "blocked")
OBJECTIONS = ("changes-requested", "blocked")

#: The team's stable reviewer handles (convention of 2026-09-23): the name
#: each session had before its first rename. Session names change every run,
#: so a verdict signed with one cannot be matched to the same reader's other
#: verdicts. A handle added to the team is added here.
STABLE_HANDLES = frozenset(
    f"policyforge-{handle}" for handle in ("80", "1d", "9b", "ba", "b5", "5b")
)


def _gh(*args: str) -> str:
    exe = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    result = subprocess.run(
        [exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def clean(value: str) -> tuple[str, str]:
    """Split a captured field into its value and the noise around it.

    Returns `(value, noise)` so the caller can report the noise instead of
    pretending it was not there. `0374a18…` -> `("0374a18", "…")`.
    """
    stripped = value.rstrip(_TRAILING_NOISE)
    return stripped, value[len(stripped) :]


@dataclass(frozen=True)
class Shape:
    """What the line looks like. Asked without reference to the repository."""

    well_formed: bool  # exactly 40 hex, after cleaning
    verdict_known: bool
    sha_noise: str = ""
    reviewer_noise: str = ""
    #: Trailing tokens this reader does not know (#425).
    unknown_tokens: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "well-formed" if self.well_formed else "MALFORMED"

    @property
    def faults(self) -> list[str]:
        """Every fault, not the first one. A line can carry several."""
        found = []
        if not self.well_formed:
            found.append("MALFORMED")
        if not self.verdict_known:
            found.append("UNKNOWN VERDICT")
        if self.sha_noise:
            found.append(f"SHA has trailing {self.sha_noise!r}")
        if self.reviewer_noise:
            found.append(f"reviewer has trailing {self.reviewer_noise!r}")
        if self.unknown_tokens:
            found.append(f"UNKNOWN TOKEN {' '.join(self.unknown_tokens)!r}")
        return found


def longest_resolving_prefix(sha: str) -> tuple[int, str]:
    """Walk back until a prefix of `sha` names a real commit.

    Returns `(length, full_sha)`, or `(0, "")` if none does.

    **This is the property, and it deliberately has no length in it.**
    The first version of this requirement keyed on *exactly eight common
    characters*, because the instance that prompted it had eight. Measured:
    `git log --oneline` in this repository abbreviates to **seven**, so the
    eighth character matched at one chance in sixteen. A detector keyed on
    8 would have classified that instance correctly by coincidence and
    misclassified the next seven-character case fifteen times out of
    sixteen. policyforge-80 caught its own spec doing it.

    Walking down from 39 terminates: every prefix of a real SHA resolves,
    and a prefix shorter than 4 is refused by git anyway.
    """
    for length in range(min(len(sha), 39), 3, -1):
        resolved = _git("rev-parse", "--verify", f"{sha[:length]}^{{commit}}")
        if resolved.returncode == 0:
            return length, resolved.stdout.strip()
    return 0, ""


@dataclass(frozen=True)
class Location:
    """Where the SHA sits. Asked without reference to the line's shape.

    **Separate from `Shape` because they are separate questions**, and the
    defect this file exists partly to avoid was asking the second only when
    the first passed.
    """

    resolves: bool
    at_head: bool
    ancestor_of_head: bool
    #: When the SHA itself resolves to nothing: how much of it does, and to
    #: what. Zero and "" when no prefix resolves either.
    prefix_length: int = 0
    prefix_target: str = ""
    #: Is the commit the prefix names ON THIS PR'S BRANCH -- not merely
    #: equal to its current head. **The state is a property of the line
    #: PLUS what you compare it against**, and comparing to the head makes
    #: it drift: a line classified RECONSTRUCTED silently becomes the
    #: serious state the moment its author pushes again, with no event,
    #: no diff, and nothing about the line having changed.
    #:
    #: Measured on a real line on #230: prefix `504994f4` names
    #: 504994f480d9, which WAS the head when the line was written and is
    #: now an ancestor of 5938199. Head-equality called it MISANCHORED.
    #: policyforge-80 found it by RUNNING its classifier rather than by
    #: reviewing the spec -- the third correction to this spec, and the
    #: only one that needed the thing to exist first.
    prefix_on_branch: bool = False
    prefix_is_head: bool = False

    @property
    def label(self) -> str:
        if self.resolves:
            if self.at_head:
                return "AT HEAD"
            return "STALE" if self.ancestor_of_head else "ELSEWHERE"
        # A well-formed SHA naming no object splits three ways, and the
        # three differ in whether the REVIEW survives.
        if not self.prefix_target:
            return "FABRICATED"
        if self.prefix_on_branch:
            # The prefix names a real commit OF THIS PR: evidence the
            # reviewer read this branch. A correct review with a broken
            # anchor. Recoverable -- and possibly ALSO stale, which is a
            # second independent axis, not a replacement for this one.
            return "RECONSTRUCTED"
        # The prefix names something else, so what was read is unknown.
        # Not recoverable, and the worse of the two.
        return "MISANCHORED"

    @property
    def diagnosis(self) -> str:
        """Why the SHA names nothing, in terms that point at the tooling.

        *"names no commit; `504994f4` is the head of this PR, so this was
        probably extended from an abbreviated display"* is a diagnosis. **An
        accusation would be "fabricated".** Three sessions produced one of
        these in a day; the remedy is in the display, not in the discipline,
        because the old rule -- never lengthen an abbreviated SHA -- asks a
        person to resist something the tooling hands them.
        """
        if self.resolves or not self.prefix_target:
            return ""
        if self.prefix_is_head:
            what = "the head of this PR"
        elif self.prefix_on_branch:
            what = "an earlier commit of this PR, so the review is also STALE"
        else:
            what = "a commit NOT on this branch"
        return (
            f"names no commit, but its first {self.prefix_length} characters do: "
            f"{self.prefix_target[:12]} ({what}). Probably extended from an "
            f"abbreviated display."
        )


@dataclass(frozen=True)
class Line:
    sha: str
    verdict: str
    reviewer: str
    shape: Shape
    location: Location
    #: False when the reader declared a stake with `counted=no` (#425).
    counted: bool = True
    #: The login that POSTED the line, where the forge says. On GitHub every
    #: session posted as one account, so this said nothing; on Forgejo each
    #: handle is its own bot, and a line whose `reviewer=` is not its poster
    #: is reported. Empty when unknown.
    poster: str = ""
    #: "comment", or "review" for a formal review's body.
    source: str = "comment"

    @property
    def poster_mismatch(self) -> bool:
        return bool(self.poster) and clean(self.reviewer)[0] != self.poster

    @property
    def counts(self) -> bool:
        """A well-formed line at head, from a counted reader, with no token
        this reader does not know. Only such a line is a counted verdict on
        this code."""
        return (
            self.shape.well_formed
            and self.location.at_head
            and self.counted
            and not self.shape.unknown_tokens
            # Signed as one reader, posted by another: whose review it is,
            # is unknown, so it is nobody's counted approval.
            and not self.poster_mismatch
        )


def shape_of(sha: str, verdict: str, reviewer: str = "", tokens: tuple[str, ...] = ()) -> Shape:
    hexed, sha_noise = clean(sha)
    verdict_value, _ = clean(verdict)
    _, reviewer_noise = clean(reviewer)
    return Shape(
        well_formed=len(hexed) == 40 and all(c in "0123456789abcdef" for c in hexed.lower()),
        verdict_known=verdict_value in VERDICTS,
        sha_noise=sha_noise,
        reviewer_noise=reviewer_noise,
        unknown_tokens=tuple(t for t in tokens if t not in KNOWN_TOKENS),
    )


def location_of(sha: str, head: str) -> Location:
    """Resolve `sha` in this clone and place it relative to `head`.

    Asked for **every** line, whatever its shape. A short SHA that resolves
    is still somewhere, and where it is may be the finding.
    """
    sha, _ = clean(sha)
    resolved = _git("rev-parse", "--verify", f"{sha}^{{commit}}")
    if resolved.returncode != 0:
        length, target = longest_resolving_prefix(sha)
        return Location(
            resolves=False,
            at_head=False,
            ancestor_of_head=False,
            prefix_length=length,
            prefix_target=target,
            prefix_on_branch=bool(target)
            and _git("merge-base", "--is-ancestor", target, head).returncode == 0,
            prefix_is_head=bool(target) and target == head,
        )
    full = resolved.stdout.strip()
    return Location(
        resolves=True,
        at_head=full == head,
        ancestor_of_head=_git("merge-base", "--is-ancestor", full, head).returncode == 0,
    )


#: The trailing tokens a verdict line may carry (#425).
KNOWN_TOKENS = frozenset({"counted=no", "counted=yes"})


def verdict_lines(comments: list[dict]) -> list[Line]:
    """Every comment whose LAST NON-EMPTY line is a verdict."""
    found = []
    for comment in comments:
        lines = [ln for ln in (comment.get("body") or "").splitlines() if ln.strip()]
        if not lines:
            continue
        match = _VERDICT.search(lines[-1].strip())
        if match:
            found.append(
                {
                    **_fields(match),
                    "author": comment.get("author") or "",
                    "source": comment.get("source") or "comment",
                }
            )
    return found


def _fields(match: re.Match) -> dict:
    """sha, verdict, reviewer, the trailing tokens, and whether the reader
    declared a stake. `counted` is False only for an exact `counted=no`."""
    tokens = tuple(match.group("tokens").split())
    return {
        "sha": match.group("sha"),
        "verdict": match.group("verdict"),
        "reviewer": match.group("reviewer"),
        "tokens": tokens,
        "counted": "counted=no" not in tokens,
    }


#: Where an approval must point to retire an objection: a commit of THIS PR.
#: AT HEAD and STALE resolve on the branch; RECONSTRUCTED names no object but
#: its prefix is a commit of this PR, so the reviewer read this branch.
#: ELSEWHERE, MISANCHORED and FABRICATED point off the branch or at nothing,
#: so what was approved is not this code -- they leave the objection OPEN.
#: Found by policyforge-9b on #284: well-formed alone let all three retire.
RETIRING_LOCATIONS = frozenset({"AT HEAD", "STALE", "RECONSTRUCTED"})


def objection_states(lines: list[Line]) -> list[tuple[Line, str, Line | None]]:
    """Every objection, in posting order, with its state and what retired it.

    `lines` must be in posting order. The state is `OPEN`, `RETIRED STALE`
    or `RETIRED AT HEAD`. Only a well-formed approval retires: a malformed
    one might be crediting a review that was not there, so it leaves the
    objection OPEN, which is the safe direction.

    Every later approval from the objector is considered, not the first
    one. On #273 the objector approved at a stale head, objected again, then
    approved at head: the first approval alone called the first objection
    "retired, approval stale" -- a re-read notice about a question its own
    reviewer had since settled at head.
    """
    states = []
    for index, line in enumerate(lines):
        if line.verdict not in OBJECTIONS:
            continue
        retiring = [
            later
            for later in lines[index + 1 :]
            if later.reviewer == line.reviewer
            and later.verdict == "approved"
            and later.shape.well_formed
            # A token this reader does not know might be a retraction
            # (policyforge-ba on #428): the objection stays OPEN.
            and not later.shape.unknown_tokens
            and later.location.label in RETIRING_LOCATIONS
            # Only the objector retires an objection, and on Forgejo the
            # poster is who that is. A withdrawal signed with the objector's
            # handle but posted by someone else leaves it OPEN.
            and not later.poster_mismatch
        ]
        # `location.at_head`, not `counts`: a declared stake (#425) changes
        # whose approval is counted, not who may withdraw their own objection.
        at_head = [later for later in retiring if later.location.at_head]
        if at_head:
            states.append((line, "RETIRED AT HEAD", at_head[-1]))
        elif retiring:
            states.append((line, "RETIRED STALE", retiring[-1]))
        else:
            states.append((line, "OPEN", None))
    return states


def fetch(number: int, repo: str) -> tuple[str, list[dict], int]:
    """Return (head, comments, the count GitHub reports).

    The third value is the second derivation. It is fetched from a different
    endpoint than the list, which is what makes it able to disagree.
    """
    data = json.loads(
        _gh("pr", "view", str(number), "--repo", repo, "--json", "headRefOid,comments")
    )
    reported = int(_gh("api", f"repos/{repo}/issues/{number}", "--jq", ".comments").strip())
    # No poster: every session posted to GitHub as one account (rdazzlebot),
    # so the login names nobody, and comparing it with `reviewer=` would call
    # every line a mismatch and count none (9b on #21).
    comments = [{**c, "author": "", "source": "comment"} for c in data.get("comments") or []]
    return data["headRefOid"], comments, reported


class PopulationShort(RuntimeError):
    """The two derivations of a PR's population disagree. Report nothing."""


def _forgejo(path: str) -> tuple[object, dict]:
    """GET one Forgejo API path as the caller's own bot. Returns (json, headers)."""
    token = os.environ.get("FORGEJO_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "FORGEJO_TOKEN is not set. Export your OWN bot's token (never another "
            "handle's): the server requires sign-in to read anything."
        )
    # http(s) only, refused in code rather than only suppressed: urllib also
    # opens `file://`, and FORGEJO_URL comes from the environment. The worst
    # case left is the caller pointing their OWN token at a host they chose,
    # which the token's owner can do with curl anyway. A test sets a file://
    # URL and asserts nothing is opened.
    if urllib.parse.urlsplit(FORGEJO_URL).scheme not in ("http", "https"):
        raise RuntimeError(f"FORGEJO_URL must be http(s); got {FORGEJO_URL!r}")
    request = urllib.request.Request(
        f"{FORGEJO_URL}/api/v1{path}", headers={"Authorization": f"token {token}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310  # nosemgrep
        return json.load(response), dict(response.headers)


def fetch_forgejo(number: int, repo: str) -> tuple[str, list[dict], int]:
    """Return (head, comments and review bodies in posting order, their count).

    **Measured on Forgejo 16.0.5 (1d, #7), and every one is a trap from the
    GitHub recipe:**

        issues/N/comments   ignores limit/page and returns every PLAIN
                            comment. Review bodies are not in it.
        issues/N .comments  a counter that is NOT the length of any list: on a
                            probe PR it read 2 for 1 plain comment and 3
                            reviews, and did not move on the next two reviews.
                            GitHub's second derivation is unusable here.
        pulls/N/reviews     paged; X-Total-Count is the true total.
        issues/N/timeline   paged, FILTERED AFTER PAGING (inline code comments
                            are dropped, so a page can come back short or
                            empty mid-list), and its X-Total-Count is the
                            page's length, not the total.

    So the second derivation is the TIMELINE, compared by ID SET in the same
    unit: its `comment` events against the comments list, its `review` events
    against the reviews list. A set, not a count, so a substitution cannot
    balance. The timeline also gives the only true interleaved POSTING ORDER
    of comments and reviews, which retirement (#237) depends on;
    timestamps tie at one-second resolution.

    Paging the timeline stops at the first empty page, which the filtering can
    produce early. That direction makes the timeline SHORT, the sets disagree,
    and this refuses: a false alarm, never a false pass.
    """
    pull, _ = _forgejo(f"/repos/{repo}/pulls/{number}")
    head = pull["head"]["sha"]
    comments, _ = _forgejo(f"/repos/{repo}/issues/{number}/comments")

    reviews: list[dict] = []
    total = None
    for page in range(1, 1000):
        batch, headers = _forgejo(f"/repos/{repo}/pulls/{number}/reviews?limit=50&page={page}")
        total = int(headers.get("X-Total-Count", len(batch)))
        reviews.extend(batch)
        if not batch or len(reviews) >= total:
            break
    if total is not None and len(reviews) != total:
        raise PopulationShort(
            f"Forgejo reports {total} review(s) on #{number}; paging returned {len(reviews)}."
        )

    timeline: list[dict] = []
    for page in range(1, 1000):
        batch, _ = _forgejo(f"/repos/{repo}/issues/{number}/timeline?limit=50&page={page}")
        if not batch:
            break
        timeline.extend(batch)

    by_comment = {c["id"]: c for c in comments}
    by_review = {r["id"]: r for r in reviews}
    seen_comments = [t["id"] for t in timeline if t.get("type") == "comment"]
    seen_reviews = [t.get("review_id") for t in timeline if t.get("type") == "review"]
    if set(seen_comments) != set(by_comment) or set(seen_reviews) != set(by_review):
        raise PopulationShort(
            f"the timeline of #{number} names comments {sorted(seen_comments)} and reviews "
            f"{sorted(seen_reviews)}; the lists returned comments {sorted(by_comment)} and "
            f"reviews {sorted(by_review)}."
        )

    ordered: list[dict] = []
    for event in timeline:
        if event.get("type") == "comment":
            c = by_comment[event["id"]]
            ordered.append(
                {"body": c.get("body"), "author": c["user"]["login"], "source": "comment"}
            )
        elif event.get("type") == "review":
            r = by_review[event["review_id"]]
            ordered.append(
                {
                    "body": r.get("body"),
                    "author": r["user"]["login"],
                    "source": "review",
                    "state": r.get("state", ""),
                    "dismissed": bool(r.get("dismissed")),
                    "stale": bool(r.get("stale")),
                }
            )
            # Inline code comments are not verdicts, but a verdict-shaped line
            # posted there is one somebody believes they gave. Carried so the
            # report can say it was NOT read, rather than lose it silently.
            if r.get("comments_count"):
                inline, _ = _forgejo(f"/repos/{repo}/pulls/{number}/reviews/{r['id']}/comments")
                ordered.extend(
                    {"body": i.get("body"), "author": i["user"]["login"], "source": "inline"}
                    for i in inline
                )
    # The timeline's own count, from the other endpoint: equal by the id-set
    # check above, and returned so the report's header says what was compared.
    return head, ordered, len(seen_comments) + len(seen_reviews)


def _who(line: Line) -> str:
    """The reviewer a line names, as `poster_mismatch` reads it: trailing
    noise (`policyforge-1d.`, `policyforge-1d,`) is the same reader. Keyed on
    the raw string, a stake signed with a trailing dot left the reader counted
    AND staked with no flip, #53's shape by a second route (b5 on #54)."""
    return clean(line.reviewer)[0]


def latest_own_lines(lines: list[Line]) -> dict[str, Line]:
    """{reviewer: the latest line they posted themselves}, in posting order.

    A line signed as one reader and posted by another is skipped: it can't
    count (see `Line.counts`), and it must not supersede, or anyone could
    un-count a reader by posting a line in their name."""
    latest: dict[str, Line] = {}
    for line in lines:
        if not line.poster_mismatch:
            latest[_who(line)] = line
    return latest


def counted_flips(lines: list[Line]) -> dict[str, str]:
    """{reviewer: "counted -> uncounted at their line 2 (<sha>)" …} for each
    reviewer whose own lines changed the counted flag, so a flip is shown,
    never silent. The line's ordinal names it where a SHA can't: on #51 both
    of 1d's lines sit at one commit (ba on #54)."""
    flips: dict[str, list[str]] = {}
    previous: dict[str, bool] = {}
    seen: dict[str, int] = {}
    for line in lines:
        if line.poster_mismatch:
            continue
        who = _who(line)
        seen[who] = seen.get(who, 0) + 1
        before = previous.get(who)
        if before is not None and before != line.counted:
            flips.setdefault(who, []).append(
                f"{'counted' if before else 'uncounted'} -> "
                f"{'counted' if line.counted else 'uncounted'} "
                f"at their line {seen[who]} ({line.sha[:7]})"
            )
        previous[who] = line.counted
    return {r: "; ".join(f) for r, f in flips.items()}


def uncounted_reasons(lines: list[Line]) -> dict[str, str]:
    """{reviewer: why} for a reader dropped from "approvals at head" for a
    reason other than a declared stake, so nobody is dropped in silence:

    - their latest line is an approval at head that still doesn't count
      (ba on #54);
    - their latest line isn't at head, while an earlier line of theirs WAS a
      counted approval at head (#55). Under 80's #53 ruling the latest line
      decides, so that approval rightly stops counting, and the commonest
      cause is a one-character SHA typo, which says why rather than reading
      as "never approved".

    Latest and earlier lines are read by the report's own rules,
    `latest_own_lines` and `_who`, so the two can't drift. A line posted as
    someone else neither supersedes (it isn't anyone's latest own line) nor
    is superseded (`Line.counts` is False for it)."""
    reasons: dict[str, str] = {}
    for who, line in latest_own_lines(lines).items():
        at_head_approval = line.verdict == "approved" and line.location.at_head
        if at_head_approval and line.counted and not line.counts:
            why = list(line.shape.faults) or ["reason not recorded"]
            reasons[who] = "latest line at head does not count: " + ", ".join(why)
        elif not line.location.at_head and any(
            earlier is not line
            and _who(earlier) == who
            and earlier.verdict == "approved"
            and earlier.counts
            for earlier in lines
        ):
            reasons[who] = (
                f"latest line is {line.location.label} ({line.sha[:12]}), "
                "superseding an approval at head"
            )
    return reasons


def report(number: int, repo: str, forge: str = "github") -> int:
    forge_name = "Forgejo" if forge == "forgejo" else "GitHub"
    try:
        head, comments, reported = (fetch_forgejo if forge == "forgejo" else fetch)(number, repo)
    except PopulationShort as exc:
        print(
            f"\nPOPULATION SHORT: {exc}\n"
            f"  Every verdict below would be a claim about a truncated list.",
            file=sys.stderr,
        )
        return 2
    inline = [c for c in comments if c.get("source") == "inline"]
    comments = [c for c in comments if c.get("source") != "inline"]

    # THE FIRST THREE LINES, deliberately. A report that was true when taken
    # and false when read cost a real misroute today -- #229 was described as
    # having zero verdicts by someone who measured before the verdict was
    # posted. The reader of this output has to see the staleness and the
    # population BEFORE the verdicts, not in a footer.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print(f"#{number} on {repo} -- read at {now}")
    print(f"head {head}")
    what = "comment(s) and review(s)" if forge == "forgejo" else "comment(s)"
    # Say which second derivation spoke: on Forgejo it is the timeline, by id.
    other = "the timeline names" if forge == "forgejo" else "GitHub reports"
    print(f"{len(comments)} {what} fetched; {other} {reported}")

    if len(comments) != reported:
        print(
            f"\nPOPULATION SHORT: {forge_name} reports {reported} {what} on "
            f"#{number}; this fetch returned {len(comments)}.\n"
            f"  Every verdict below would be a claim about a truncated list.",
            file=sys.stderr,
        )
        return 2

    # Location is resolved in THIS clone. A head this clone has not fetched
    # makes every line read FABRICATED -- a false accusation -- so refuse.
    if forge == "forgejo" and _git("rev-parse", "--verify", f"{head}^{{commit}}").returncode:
        print(
            f"\nHEAD NOT IN THIS CLONE: {head} does not resolve here, so no line "
            f"can be placed.\n  Fetch it first, with your own bot:\n"
            f'    fj <handle> git fetch forgejo "+refs/pull/{number}/head:'
            f'refs/remotes/forgejo-pr/{number}"',
            file=sys.stderr,
        )
        return 2

    # An unpublished draft (PENDING) is visible to its own author in BOTH the
    # reviews list and the timeline (measured on #14), so it passes the
    # population check -- but nobody posted it, so it is not a verdict.
    drafts = [c for c in comments if c.get("source") == "review" and c.get("state") == "PENDING"]
    comments = [
        c for c in comments if not (c.get("source") == "review" and c.get("state") == "PENDING")
    ]

    raw = verdict_lines(comments)
    lines = [
        Line(
            sha=r["sha"],
            verdict=r["verdict"],
            reviewer=r["reviewer"],
            shape=shape_of(r["sha"], r["verdict"], r["reviewer"], r["tokens"]),
            location=location_of(r["sha"], head),
            counted=r["counted"],
            poster=r.get("author", ""),
            source=r.get("source", "comment"),
        )
        for r in raw
    ]

    # Forgejo's own review states. The convention's record is the verdict
    # LINE, but a formal REQUEST_CHANGES is an objection somebody made, and a
    # reader of lines alone would not see one posted without a line.
    #
    # Each reviewer's STANDING state is their latest undismissed
    # REQUEST_CHANGES or APPROVED, in posting (timeline) order: an approval
    # after one's own objection answers it, as with verdict lines (b5 on #21).
    # Whether Forgejo also marks the earlier review dismissed is NOT measured
    # here (it needs a second identity reviewing); reading the latest is right
    # either way. A dismissed latest review stands for nothing, so an earlier
    # objection shows through: the conservative direction.
    standing: dict[str, dict] = {}
    for c in comments:
        if (
            c.get("source") == "review"
            and c.get("state") in ("REQUEST_CHANGES", "APPROVED")
            and not c.get("dismissed")
        ):
            standing[c["author"]] = c
    formal = list(standing.values())
    if drafts:
        print(
            f"?? {len(drafts)} unpublished draft review(s) of your own. NOT read: "
            f"a draft is not a verdict until it is submitted."
        )
    for review in formal:
        print(
            f"  formal review: {review['state']} by {review['author']}"
            + (" (stale: the head moved)" if review.get("stale") else "")
        )
    if any(r["state"] == "REQUEST_CHANGES" for r in formal):
        print("!! A formal REQUEST_CHANGES stands. It is an objection, line or no line.")
    inline_verdicts = verdict_lines(inline)
    if inline_verdicts:
        print(
            f"?? {len(inline_verdicts)} verdict-shaped line(s) in INLINE code comments "
            f"({', '.join(sorted({v['reviewer'] for v in inline_verdicts}))}). "
            f"NOT read as verdicts: post them as a comment or a review body."
        )

    print(f"{len(lines)} verdict line(s)\n")
    if not lines:
        print("  No verdict lines. That is 'nobody has posted one', NOT 'no")
        print("  objection stands' -- a reader cannot tell those apart and")
        print("  neither can this tool.")
        return 0

    for line in lines:
        # Both facts, always. A line can be MALFORMED *and* ELSEWHERE, and
        # the version that chained these credited a review that was not there.
        marks = [line.location.label, *line.shape.faults]
        if not line.counted:
            marks.append("UNCOUNTED (declared stake)")
        if line.poster_mismatch:
            marks.append(f"POSTED BY {line.poster}")
        if line.source == "review":
            marks.append("in a review body")
        print(f"  {line.sha[:12]:14} {line.verdict:18} {line.reviewer:16} {' + '.join(marks)}")

    # Who read the head, split by the one thing a line can say about its
    # reader's standing (#425). Names, not a total: a count here would be
    # read as consent, and this reports.
    #
    # Each reviewer's LATEST line of their own supersedes their earlier ones
    # entirely, counted flag included (80's ruling on #53). Counting every
    # approving line let an earlier line with no token keep a reader counted
    # after they declared a stake: #51 listed 1d as counted and staked at once
    # (1098, then 1108). And a latest line at another SHA leaves no earlier
    # head approval standing (9b on #53). A line posted by someone else under
    # a reviewer's name never supersedes that reviewer's own.
    latest = latest_own_lines(lines)
    counted = sorted(r for r, line in latest.items() if line.verdict == "approved" and line.counts)
    declared = sorted(
        r
        for r, line in latest.items()
        if line.verdict == "approved" and not line.counted and line.location.at_head
    )
    print(f"\n  approvals at head from counted readers: {', '.join(counted) or 'none'}")
    if declared:
        print(f"  approvals at head from readers who declared a stake: {', '.join(declared)}")
    for reviewer, flip in counted_flips(lines).items():
        print(f"  {reviewer}: {flip} (their latest line decides)")
    for reviewer, why in uncounted_reasons(lines).items():
        print(f"  {reviewer}: {why}")

    # Objections by state (#237). Only the objector's own later approval
    # retires one; nothing about the SHA does.
    states = objection_states(lines)
    still_open = [line for line, state, _ in states if state == "OPEN"]
    answered_then_moved = [(line, by) for line, state, by in states if state == "RETIRED STALE"]
    if still_open:
        print(
            f"\n!! {len(still_open)} OPEN OBJECTION(S) — no later approval from the objector.\n"
            f"   Expiry is not resolution, and another reviewer's approval does not answer it."
        )
        for line in still_open:
            print(f"     {line.reviewer} at {line.sha[:12]} ({line.location.label})")
    if answered_then_moved:
        print(
            f"\n   {len(answered_then_moved)} objection(s) retired by their own reviewer at a "
            f"commit that is no longer head.\n"
            f"   Answered, then the code moved: read the delta again."
        )
        for line, by in answered_then_moved:
            print(
                f"     {line.reviewer} objected at {line.sha[:12]}, approved at {by.sha[:12]} "
                f"({by.location.label})"
            )

    mismatched = [line for line in lines if line.poster_mismatch]
    if mismatched:
        print(
            f"\n!! {len(mismatched)} line(s) signed as one reader and POSTED BY another. "
            f"None counts, and none retires an objection:"
        )
        for line in mismatched:
            print(f"     reviewer={line.reviewer} posted by {line.poster} at {line.sha[:12]}")

    # Retirement matches `reviewer=` as a string, so a perishable name makes
    # the matching partial. Said, so it is not read as whole.
    unknown = sorted({line.reviewer for line in lines if line.reviewer not in STABLE_HANDLES})
    if unknown:
        print(
            f"\n?? {len(unknown)} reviewer name(s) that are not a stable handle: "
            f"{', '.join(unknown)}\n"
            f"   A line signed this way can neither retire nor be retired by a "
            f"handle-signed line."
        )

    print("\n  This is a report. It does not compute consent, and no count")
    print("  below is an authorisation -- a person decides.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Read a PR's Reviewed-SHA verdict lines.")
    parser.add_argument("number", type=int)
    parser.add_argument(
        "--forge",
        choices=("forgejo", "github"),
        default="forgejo",
        help="Where the PR lives (default: the team's Forgejo; github reads the old threads).",
    )
    parser.add_argument("--repo", default=None)
    args = parser.parse_args(argv)
    repo = args.repo or (FORGEJO_REPO if args.forge == "forgejo" else REPO)
    return report(args.number, repo, args.forge)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
