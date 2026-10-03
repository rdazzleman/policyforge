"""Report pull requests that are ready to merge and have not been.

On 2026-09-19 a pull request sat `CLEAN` with zero pending checks for **ten
hours**. Nothing was wrong with it. It was approved, green, and mergeable,
and the release manager — whose role is to merge anyone's pull request —
listed it in three separate status reports as "open, other sessions' work".

That phrase is a fact about whose *branch* it is. It was filed as a fact
about whose *turn* it is. The two read identically and mean opposite things.

**The failure has no artefact, which is why it needs a check rather than a
habit.** A forgotten extension point fails the first time someone uses it. A
queue nobody polls produces no signal at all: green accumulates, every check
passes, and there is nothing to be red. Every pull request that moved that
day had been handed over by name and moved within minutes; the one nobody
announced was invisible, because the queue was only ever examined when
something arrived.

So this asks the question nobody was asking — *what is ready that nobody has
picked up?* — and answers it with names and durations rather than a count, so
the output is a worklist instead of a nag.

**A draft is not stale.** Neither is one waiting on checks, on review, or on
a conflict: each of those is someone's turn and the queue is working. Only
`CLEAN` with nothing pending means *the machine has no further objection and
a person has not acted*, which is the state that produced the ten hours.

Prints what it examined either way, including on a clean run, because a
check that goes quiet when it passes is indistinguishable from one that did
not run — the defect #112 fixed in the gate, which this would otherwise
reintroduce in a new file.

THE STEP BEFORE (#178). Ready-and-unmerged is the LAST place finished work
waits. It was built at the point that last hurt, which is one step below the
next place things pile up: a pull request nobody has read. #161 sat two
hours with no comment and nobody named, while a status report called the
queue healthy because every PR was green. So two more questions:

    UNREAD           open, not a draft, and no verdict line at all -- or
                     none at the current head, because an approval does not
                     survive a new commit. Over the threshold, it fails.
    PUSHED, NO PR    a branch on the remote with no pull request of any
                     state. Reported and never failed: parking finished
                     work on the remote without a PR is a legitimate thing
                     to do, and a check that fails on it forever is one
                     people stop reading. It is shown so it is a choice
                     someone can see, not a branch nobody remembers.

Verdict lines are read with `review_lines.verdict_lines` -- the same parser
the verdict reader uses -- so the two tools cannot disagree about what a
review is. "Read" means a `Reviewed-SHA:` line exists, not that anyone was
ASKED; being asked has no artefact, and a verdict line is the nearest one.
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - runs `gh`, the tool this repository already uses
import sys
from datetime import datetime, timezone

import review_lines

#: Ready and untouched for longer than this is worth saying out loud.
#: A number rather than a judgement: the incident ran to ten hours, and an
#: hour is long enough that a merge in progress does not trip it while being
#: short enough that a working day cannot hide one.
DEFAULT_THRESHOLD_MINUTES = 60

#: mergeStateStatus values that reliably mean "someone else's turn": a
#: conflict, a protection rule unmet, a check GitHub already calls failed. Any
#: OTHER value -- UNKNOWN above all -- is not evidence of readiness either way.
#: GitHub computes the field lazily and returns UNKNOWN for most pull
#: requests, so gating readiness on CLEAN meant a ready pull request mostly
#: read UNKNOWN and was never flagged: #297 did, approved at head with every
#: check green (80, on #299). The build half now comes from head-scoped
#: check-runs; this set is only the negatives the field is trusted for.
NOT_READY_STATES = frozenset({"DIRTY", "BLOCKED", "UNSTABLE"})

#: Check-run conclusions that are not a failure.
_PASSING = frozenset({"success", "skipped", "neutral"})

FIELDS = (
    "number,title,headRefName,updatedAt,mergeStateStatus,isDraft,author,"
    "createdAt,headRefOid,comments"
)
# NOT `commits`: asked for across a 100-PR listing it takes GitHub's GraphQL
# query past its node limit (1,000,000 requested against 500,000) and the
# whole listing is refused -- found by the first live run of #298, which no
# fixture could show. The head commit's time is fetched per PR instead.

#: A SHA prefix shorter than this names too many commits to count as "at head".
_MIN_PREFIX = 7

#: How many pull requests each listing asks for. A result that fills its limit
#: may be cut short by it, and is SAID to be (#298) -- a silently short list is
#: the population failure this project keeps finding.
OPEN_LIMIT = 100
ALL_LIMIT = 1000


def open_pull_requests(repo: str | None = None) -> list[dict]:
    """Every open pull request, as `gh` reports them."""
    command = ["gh", "pr", "list", "--state", "open", "--json", FIELDS, "--limit", str(OPEN_LIMIT)]
    if repo:
        command += ["--repo", repo]
    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        command, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {result.stderr.strip()}")
    return json.loads(result.stdout or "[]")


def head_checks(sha: str, repo: str | None = None) -> tuple[int, int] | None:
    """(pending, failing) among the check-runs AT THE HEAD, or None if unread.

    Scoped to the head commit and the LATEST run of each check name. `gh pr
    checks` aggregates across workflow runs, so a stale green run can sit
    beside a current red one -- the charge's #232 finding -- and it is not
    used here. None (nothing readable) is reported as "no checks", as before.
    """
    path = (
        f"repos/{repo}/commits/{sha}/check-runs"
        if repo
        else (f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs")
    )
    try:
        runs = json.loads(_gh(["api", path, "--jq", ".check_runs"]) or "[]")
    except (RuntimeError, ValueError):
        return None
    if not runs:
        return None
    latest: dict[str, dict] = {}
    for run in runs:
        name = run.get("name") or ""
        if name not in latest or (run.get("started_at") or "") > (
            latest[name].get("started_at") or ""
        ):
            latest[name] = run
    pending = sum(1 for r in latest.values() if r.get("status") != "completed")
    failing = sum(
        1
        for r in latest.values()
        if r.get("status") == "completed" and (r.get("conclusion") or "") not in _PASSING
    )
    return pending, failing


def _at_head(sha: str, head: str) -> bool:
    cleaned, _ = review_lines.clean(sha)
    return len(cleaned) >= _MIN_PREFIX and head.startswith(cleaned.lower())


def read_state(pull: dict) -> str:
    """`read at head`, `read, not at head`, or `unread` -- from verdict lines.

    A verdict counts as at head when its SHA, cleaned of trailing formatting,
    is a prefix of the head of at least `_MIN_PREFIX` characters. Anything
    looser would call a review of an older commit current."""
    head = pull.get("headRefOid") or ""
    verdicts = review_lines.verdict_lines(pull.get("comments") or [])
    if not verdicts:
        return "unread"
    if any(_at_head(v["sha"], head) for v in verdicts):
        return "read at head"
    return "read, not at head"


def approved_at_head(pull: dict) -> bool:
    """Approved at head with no objection standing (#298).

    `mergeStateStatus` CLEAN says the machine has no objection; it says
    nothing about the readers. It called #294 and #296 ready while each
    carried a changes-requested at head. So readiness also asks the verdict
    reader's own question, through `review_lines.objection_states`: at least
    one well-formed approval at head, and every objection RETIRED AT HEAD by
    its own reviewer. An objection answered at an older commit is not enough
    -- the objector approved code that has since changed.

    Locations are decided by SHA prefix against the head, not by resolving
    ancestry, so this runs without a local clone. That is exact for the one
    distinction readiness needs: at head, or not."""
    head = pull.get("headRefOid") or ""
    lines = []
    for verdict in review_lines.verdict_lines(pull.get("comments") or []):
        at = _at_head(verdict["sha"], head)
        lines.append(
            review_lines.Line(
                sha=verdict["sha"],
                verdict=verdict["verdict"],
                reviewer=verdict["reviewer"],
                shape=review_lines.shape_of(
                    verdict["sha"], verdict["verdict"], verdict["reviewer"], verdict["tokens"]
                ),
                location=review_lines.Location(resolves=True, at_head=at, ancestor_of_head=not at),
                counted=verdict["counted"],
            )
        )
    approved = any(line.verdict == "approved" and line.counts for line in lines)
    settled = all(
        state == "RETIRED AT HEAD" for _, state, _ in review_lines.objection_states(lines)
    )
    return approved and settled


def waiting_since(pull: dict) -> str:
    """When the current head began waiting for a reader (#298).

    The latest of: the head commit, the pull request's creation, and the last
    comment carrying a verdict line. NOT `updatedAt`, which any comment
    resets -- so a pull request people discussed and nobody reviewed never
    aged into "unread", the one shape "unread" exists to catch (9b, on #293).

    Stated limit: a commit's `committedDate` is when it was made, not pushed.
    Force-pushing an old commit onto an existing pull request can read as
    older than it is; creation time bounds that for a new pull request."""
    moments = [pull.get("createdAt") or pull.get("updatedAt"), pull.get("headCommittedAt")]
    for comment in pull.get("comments") or []:
        if review_lines.verdict_lines([comment]):
            moments.append(comment.get("createdAt"))
    stamps = [m for m in moments if m]
    return max(stamps, key=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")))


def head_committed_at(sha: str, repo: str | None = None) -> str | None:
    """When the head commit was made, or None if that cannot be read.

    One small REST call per open pull request, rather than a `commits` field
    on the listing (see FIELDS). None leaves `waiting_since` on the other two
    sources, which is a smaller clock, never a missing report."""
    path = f"repos/{repo}/commits/{sha}" if repo else f"repos/{{owner}}/{{repo}}/commits/{sha}"
    try:
        return _gh(["api", path, "--jq", ".commit.committer.date"]).strip() or None
    except RuntimeError:
        return None


def branches_without_pull_request(repo: str | None = None) -> tuple[list[str], bool]:
    """Remote branches no pull request of any state was ever opened from.

    Compared by name against every PR's head, open, closed or merged, so a
    merged branch someone kept is not reported. A branch that is the BASE of
    any pull request is not parked work either -- the default branch, and an
    integration branch like a release train, which PRs are opened into and
    never from. Derived from the pull requests, not a list of names: the
    first run named `release/1.6.1` as parked, because only the default
    branch was excluded.

    Returns the names and whether the pull-request listing filled its limit:
    if it did, a branch whose PR fell past the limit would read as parked."""
    target = repo or _gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    target = target.strip()
    default = _gh(
        ["repo", "view", target, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"]
    ).strip()
    names = json.loads(
        _gh(["api", "--paginate", "--slurp", f"repos/{target}/branches?per_page=100"])
    )
    branches = {b["name"] for page in names for b in page}
    pulls = json.loads(
        _gh(
            [
                "pr",
                "list",
                "--repo",
                target,
                "--state",
                "all",
                "--limit",
                str(ALL_LIMIT),
                "--json",
                "headRefName,baseRefName",
            ]
        )
    )
    heads = {p["headRefName"] for p in pulls}
    bases = {p["baseRefName"] for p in pulls}
    return sorted(branches - heads - bases - {default}), len(pulls) >= ALL_LIMIT


def _gh(args: list[str]) -> str:
    result = subprocess.run(  # nosec B603 - fixed argv, no shell
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def idle_minutes(updated_at: str, *, now: datetime | None = None) -> float:
    """Minutes since the pull request last changed."""
    moment = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    return ((now or datetime.now(timezone.utc)) - moment).total_seconds() / 60


def describe(minutes: float) -> str:
    """`9h 12m`, because "552" is a number a reader has to convert."""
    hours, rest = divmod(int(minutes), 60)
    return f"{hours}h {rest:02d}m" if hours else f"{rest}m"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--minutes",
        type=int,
        default=DEFAULT_THRESHOLD_MINUTES,
        help=f"how long ready-and-untouched is too long (default {DEFAULT_THRESHOLD_MINUTES})",
    )
    parser.add_argument("--repo", default=None, help="owner/name, for running outside a checkout")
    args = parser.parse_args(argv or [])

    try:
        pulls = open_pull_requests(args.repo)
    except Exception as exc:  # noqa: BLE001 - not knowing is a failure, not a pass
        print(f"could not list pull requests: {type(exc).__name__}: {exc}")
        print("Cannot tell whether anything is waiting. Treating as FAILED.")
        return 1

    print(f"{len(pulls)} open pull request(s); ready and untouched over {args.minutes}m is stale")
    if len(pulls) >= OPEN_LIMIT:
        print(
            f"  !! the listing returned {len(pulls)}, its limit: open pull requests past it "
            "are NOT examined, so this report may be short."
        )
    print("=" * 72)

    stale: list[str] = []
    unread: list[str] = []
    for pull in pulls:
        number, branch = pull["number"], pull["headRefName"]
        state = pull.get("mergeStateStatus") or "UNKNOWN"
        waited = idle_minutes(pull["updatedAt"])

        if pull.get("isDraft"):
            print(f"  #{number:<5} {describe(waited):>8}  draft        {branch}")
            continue

        # The step before ready (#178): has anyone read THIS head? Timed from
        # when the head began waiting -- not updatedAt, which any comment
        # resets (#298).
        reading = read_state(pull)
        if "headCommittedAt" not in pull:
            pull["headCommittedAt"] = head_committed_at(pull.get("headRefOid") or "", args.repo)
        waiting = idle_minutes(waiting_since(pull))
        if reading != "read at head" and waiting >= args.minutes:
            unread.append(f"#{number} {branch} — {reading} for {describe(waiting)}")
        if state in NOT_READY_STATES:
            # Not ready is not stale: a conflict, a protection rule or a
            # failed check is someone's turn, and the queue is working.
            print(f"  #{number:<5} {describe(waited):>8}  {state:<12} {branch}")
            continue
        if not approved_at_head(pull):
            # The machine's verdict is not the readers'. An open objection or
            # no approval at head is a reader's turn (#298).
            print(f"  #{number:<5} {describe(waited):>8}  not approved {branch}")
            continue

        checks = head_checks(pull.get("headRefOid") or "", args.repo)
        if checks is None:
            print(f"  #{number:<5} {describe(waited):>8}  no checks    {branch}")
        elif checks[0]:
            print(f"  #{number:<5} {describe(waited):>8}  {checks[0]} pending    {branch}")
            continue
        elif checks[1]:
            print(f"  #{number:<5} {describe(waited):>8}  {checks[1]} failing    {branch}")
            continue
        elif waited < args.minutes:
            print(f"  #{number:<5} {describe(waited):>8}  ready        {branch}")
            continue

        author = (pull.get("author") or {}).get("login", "?")
        stale.append(
            f"#{number} {branch} — ready and untouched for {describe(waited)} (author {author})"
        )
        print(f"  #{number:<5} {describe(waited):>8}  STALE        {branch}")

    print("=" * 72)
    if stale:
        print(f"  {len(stale)} ready and nobody has acted:\n")
        for line in stale:
            print(f"    {line}")
        print(
            "\n  Merging an existing pull request is the release manager's for any\n"
            "  branch. Whose branch it is is not whose turn it is."
        )
    else:
        # Said out loud on a pass too: a check that only speaks when it fails
        # cannot be told apart from one that did not run.
        print(f"  Nothing ready has been waiting over {args.minutes}m.")

    if unread:
        print(f"\n  {len(unread)} waiting on a reader at head for over {args.minutes}m:\n")
        for line in unread:
            print(f"    {line}")
        print("\n  'Unread' means no Reviewed-SHA line; nothing records who was ASKED.")
    else:
        print(
            f"  Every open pull request has a verdict at head, or changed within {args.minutes}m."
        )

    # Informational only: parking a pushed branch with no PR is legitimate.
    try:
        parked, truncated = branches_without_pull_request(args.repo)
    except Exception as exc:  # noqa: BLE001 - said, not hidden; it gates nothing
        # The message, not only the type: 9b's live run hit a GitHub 503 and a
        # bare `RuntimeError` said nothing about why (#298).
        print(f"\n  could not list branches: {type(exc).__name__}: {exc}")
        print("  This report is INCOMPLETE: pushed-with-no-PR was not checked, not found empty.")
    else:
        if truncated:
            print(
                f"\n  !! the pull-request listing filled its limit ({ALL_LIMIT}): a branch whose "
                "PR fell past it would read as parked below."
            )
        if parked:
            print(f"\n  {len(parked)} pushed branch(es) with no pull request of any state")
            print("  (reported, not failed -- parking is allowed; this is so it is seen):\n")
            for name in parked:
                print(f"    {name}")
        else:
            print("  No pushed branch is without a pull request.")

    return 1 if stale or unread else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
