#!/usr/bin/env python3
"""The check-in's measurements, from the team's Forgejo (#7).

CLAUDE.md's "Checking in" says a check-in is a measurement, not a
conversation: measure before asking, and report review state and build
state as two columns. This takes those measurements in one pass, as the
caller's own bot (FORGEJO_TOKEN), and writes nothing.

    refs        the train and main tips, from the branch API
    milestone   open issues and open PRs, apart, by milestone ID, paged to
                exhaustion, and whether the milestone object reconciles
    each PR     CI at its head; counted approvals and open objections at its
                head; and whether its head is MERGED WITH THE BASE TIP

Every Forgejo trap measured on #7 is handled where it bites:
- The milestone object's `open_issues` counts PRs too (7 = 6 + 1), so issues
  and PRs are listed apart and the object must equal their DISTINCT union.
- A milestone NAME that doesn't exist silently disables the filter (it
  returned all 14 open issues), so the milestone is resolved to its id.
- Statuses come newest-first by TIME, not by id, and a context can hold
  several. The answer is the combined endpoint (`/commits/{sha}/status`,
  latest per context), cross-checked against this script's own time-keyed
  dedupe of the full list. If they disagree, no state is reported.
- Forgejo's PR CI tests the PR HEAD, not a merge with the base (CLAUDE.md).
  So a green head is green merged only when the head already contains the
  base tip; `merged with tip` says whether it does (`merge_base` == tip).

VERDICTS use `review_lines`, the reader #21 ported, so the check-in and
`review_lines.py N` cannot disagree about a line. Two simplifications, both
toward reporting LESS as settled:
- "At head" is exact: a line counts only if its SHA IS the head. The full
  reader also places ancestors and pre-rebase commits, which needs this
  clone to hold them; a check-in cannot fetch, so it does not guess.
- An objection is retired only by the objector's later approval AT HEAD.
  One answered at a stale commit shows as NOT RETIRED AT HEAD here;
  `review_lines.py N` gives the full picture. The PR author's own lines never count (9b's gate
  rule), though an author's objection is still shown.

It reports. It computes no consent and decides no merge.

    FORGEJO_TOKEN=<your own bot's token> python scripts/checkin.py --milestone 1.7
"""

from __future__ import annotations

import argparse
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_lines as rl

REPO = rl.FORGEJO_REPO
TRAIN = "release/1.7"


def _get(path: str):
    body, _ = rl._forgejo(path)
    return body


def _all(path: str) -> list:
    """Every item of a paged list: pages until an empty one."""
    items: list = []
    joiner = "&" if "?" in path else "?"
    for page in range(1, 1000):
        batch = _get(f"{path}{joiner}limit=50&page={page}")
        if not batch:
            break
        items.extend(batch)
    return items


def branch_tip(branch: str) -> str:
    body = _get(f"/repos/{REPO}/branches/{urllib.parse.quote(branch, safe='')}")
    return (body or {}).get("commit", {}).get("id", "")


@dataclass
class Milestone:
    title: str
    issues: list[int]
    pulls: list[int]
    object_open: int | None
    found: bool = True

    @property
    def reconciles(self) -> bool:
        distinct = set(self.issues) | set(self.pulls)
        return self.object_open == len(distinct) and len(distinct) == len(self.issues) + len(
            self.pulls
        )


def milestone(title: str) -> Milestone:
    ms = next((m for m in _all(f"/repos/{REPO}/milestones?state=all") if m["title"] == title), None)
    if ms is None:
        return Milestone(title, [], [], None, found=False)
    base = f"/repos/{REPO}/issues?state=open&milestones={ms['id']}"
    issues = [i["number"] for i in _all(f"{base}&type=issues")]
    pulls = [p["number"] for p in _all(f"{base}&type=pulls")]
    return Milestone(title, sorted(issues), sorted(pulls), ms.get("open_issues"))


def _latest_per_context(statuses: list[dict]) -> dict[str, str]:
    """{context: state} of the newest status per context, keyed on TIME then
    id, never on list position (measured: the list is newest-first by
    `created_at`, not by id)."""
    newest: dict[str, tuple] = {}
    for s in statuses:
        key = (s.get("created_at", ""), s.get("id", 0))
        if s["context"] not in newest or key > newest[s["context"]][0]:
            newest[s["context"]] = (key, s.get("status") or s.get("state", ""))
    return {ctx: state for ctx, (_, state) in newest.items()}


@dataclass
class Build:
    state: str  # failure (outranks pending) | pending | success | nothing ran | none | DISAGREE
    counts: dict[str, int] = field(default_factory=dict)
    not_green: list[str] = field(default_factory=list)


def build_at(sha: str) -> Build:
    combined = _get(f"/repos/{REPO}/commits/{sha}/status") or {}
    by_endpoint = {
        s["context"]: s.get("status") or s.get("state", "") for s in combined.get("statuses") or []
    }
    mine = _latest_per_context(_all(f"/repos/{REPO}/commits/{sha}/statuses"))
    if by_endpoint != mine:
        differ = sorted(set(by_endpoint) | set(mine))
        return Build(
            "DISAGREE",
            not_green=[
                f"{c}: endpoint {by_endpoint.get(c, '-')}, dedupe {mine.get(c, '-')}"
                for c in differ
                if by_endpoint.get(c) != mine.get(c)
            ],
        )
    if not mine:
        return Build("none")
    counts: dict[str, int] = {}
    for state in mine.values():
        counts[state] = counts.get(state, 0) + 1
    not_green = sorted(f"{c}={s}" for c, s in mine.items() if s not in ("success", "skipped"))
    # A red context outranks a pending one (9b on #41): "pending" would hide
    # it. Anything neither green nor pending (failure, error, a state this
    # does not know) reads as failure, never as waiting.
    red = [s for s in mine.values() if s not in ("success", "skipped", "pending")]
    state = "failure" if red else "pending" if "pending" in counts else "success"
    # b5 on #41: every context skipped means nothing ran. That is not a green
    # build, and 9b's merge gate doesn't treat it as one.
    if state == "success" and "success" not in counts:
        state = "nothing ran"
        not_green = sorted(f"{c}={s}" for c, s in mine.items())
    return Build(state, counts, not_green)


@dataclass
class Review:
    approvals: list[str]
    objections: list[str]
    error: str = ""


def review_at(number: int, head: str, author: str) -> Review:
    try:
        _, items, _ = rl.fetch_forgejo(number, REPO)
    except rl.PopulationShort as exc:
        return Review([], [], f"population short: {exc}")
    items = [
        i
        for i in items
        if i.get("source") != "inline"
        and not (i.get("source") == "review" and i.get("state") == "PENDING")
    ]
    lines = []
    for r in rl.verdict_lines(items):
        full = rl.clean(r["sha"])[0]
        at_head = full == head
        lines.append(
            rl.Line(
                sha=r["sha"],
                verdict=r["verdict"],
                reviewer=r["reviewer"],
                shape=rl.shape_of(r["sha"], r["verdict"], r["reviewer"], r["tokens"]),
                location=rl.Location(resolves=at_head, at_head=at_head, ancestor_of_head=False),
                counted=r["counted"],
                poster=r.get("author", ""),
                source=r.get("source", "comment"),
            )
        )
    # Each reviewer's latest own line decides (80's ruling on #53), through
    # the same helper `review_lines.py N` uses, so the two can't disagree.
    approvals = sorted(
        reviewer
        for reviewer, line in rl.latest_own_lines(lines).items()
        if line.verdict == "approved" and line.counts and line.poster != author
    )
    objections = [
        f"{line.reviewer} at {line.sha[:7]}"
        for line, state, _ in rl.objection_states(lines)
        if state != "RETIRED AT HEAD"
    ]
    return Review(approvals, objections)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--milestone", default="1.7")
    parser.add_argument("--train", default=TRAIN)
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    tips = {b: branch_tip(b) for b in (args.train, "main")}
    print(f"check-in at {now}, {REPO} on {rl.FORGEJO_URL}")
    print(f"  {args.train} {tips[args.train][:12] or '?'}    main {tips['main'][:12] or '?'}")

    ms = milestone(args.milestone)
    if not ms.found:
        print(f"  milestone {args.milestone!r}: NOT FOUND (not zero: refused)")
    else:
        print(
            f"  milestone {ms.title}: {len(ms.issues)} open issue(s) {ms.issues}, "
            f"{len(ms.pulls)} open PR(s) {ms.pulls}; object open_issues {ms.object_open}, "
            f"reconciles: {ms.reconciles}"
        )

    pulls = _all(f"/repos/{REPO}/pulls?state=open")
    print(f"\n  {len(pulls)} open PR(s)   [review state | build state are separate columns]")
    for p in sorted(pulls, key=lambda p: p["number"]):
        head, base = p["head"]["sha"], p["base"]["ref"]
        tip = tips.get(base) or branch_tip(base)
        merged = p.get("merge_base") == tip
        b = build_at(head)
        r = review_at(p["number"], head, p["user"]["login"])
        ci = b.state + (f" ({', '.join(b.not_green)})" if b.not_green else "")
        rev = r.error or (
            f"approved at head by {', '.join(r.approvals) or 'nobody'}"
            + (
                f"; NOT RETIRED AT HEAD: {'; '.join(r.objections)}"
                f" (review_lines.py {p['number']} places any answered earlier)"
                if r.objections
                else ""
            )
        )
        print(
            f"  #{p['number']:<4} {p['user']['login']:<16} {head[:7]} -> {base:<12} "
            f"merged-with-tip: {'yes' if merged else 'NO'}\n"
            f"         review: {rev}\n"
            f"         build:  {ci}"
            + ("" if merged else "   (green here is not green merged: head lacks the base tip)")
        )
    print("\n  A report, not a decision: it computes no consent and merges nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
