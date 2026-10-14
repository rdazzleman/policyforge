#!/usr/bin/env python3
"""Cut a release as an ordered list of gated steps, not a remembered procedure.

WHY THIS EXISTS (#255, split from #198). The cut used to be prose in
CONTRIBUTING.md: assemble the changelog, bump the version, merge to main,
tag, update the formula, install it, push it, check it. #198's evidence was
a step skipped because nothing made it come first -- the container install
at 1.6.0 ran only because the user asked. `release_check.py` (#200) made the
skip VISIBLE after the fact; this makes it impossible, because each step
checks the one before it actually happened.

THE CONTRACT, stated once and held by every step:

- **Each step has a GATE that must hold before it acts, and a POSTCONDITION
  checked after.** Both compare values and print them. None reads an exit
  status and calls it a result -- the `cmd; echo; tail` shape
  `release_check.py`'s header records.
- **A step whose postcondition already holds is reported done and skipped**,
  so re-running after an interruption, or after the user's merge to main,
  resumes where the release actually is.
- **`--dry-run` is the default.** It evaluates gates until the first step
  that would ACT, then lists the rest as not reached, "reached k of N". A
  later gate evaluated against a state an earlier step has not produced
  would be a measurement of nothing.
- **An OUTWARD step -- one that changes something outside this clone (push,
  tag, GitHub Release, the tap) -- needs `--execute` AND its key typed.** A
  stray invocation cannot cut a release. **`release-pr`'s key is asked
  BEFORE the changelog step writes anything** (policyforge-ba on #348):
  declining it after the cut was written left a dirty tree that stopped
  every re-run at clean-tree. A cut interrupted any other way (a failed
  postcondition, Ctrl-C) still stops there, and clean-tree prints the
  `git restore` that puts it back rather than running it.
- **The train is read from the server** (`git ls-remote`), never from this
  clone's `origin/*` refs, which answer about the last fetch.
- **The script never merges to main.** Final approval to main is the user's,
  in GitHub, and the ruleset makes it impossible anyway. That step is a WAIT:
  it reports what it is waiting for and exits 5, and a re-run after the
  user's merge continues.

TWO INSTALL TESTS (the user's ruling on #255, relayed by 80), the first of
them BEFORE the user's approval ("Install first", the user to 1d directly,
2026-09-25, #381): the release PR HEAD's archive is installed in a clean
container and the result posted on the tracking issue as a
`Release-install:` record, so the user approves main with it in hand. The
wait refuses a head that moved after the install (the installed SHA is read
from the record, never fetched at the moment of checking), and main's step
then proves its merge tree is that head's, so the install carries across the
merge. Then the CANDIDATE formula (the tag's archive) is installed before it
is published to the tap, and `release_check.py` checks the PUBLISHED formula
last.

A LOCAL RELEASE (#90; the user, to 9b directly, 2026-09-29: "Yes, local
first"): `--publish local` cuts on Forgejo alone. Every step that reaches
GitHub (P, the candidate branch, GitHub main, the GitHub Release, the tap,
the published-formula check) is DEFERRED: printed, never evaluated, and
named in a `Release-local:` record on the tracking issue. Both installs run
from Forgejo archives that the HOST fetches with the operator's token and
proves are the commit's tree, blob by blob; the container gets the file,
read-only, and checks its sha256 itself -- never the token or a Forgejo URL.
The tag goes to Forgejo. A later public run of the same version refuses if
that tag's tree has moved. Needs `--forge forgejo`, `--formula` (without it
the base formula comes from GitHub) and `--tracking-issue`.

EXIT CODES: 0 done; 5 waiting on the user; 6 an outward step not confirmed;
10+i a failed gate at step i; 40+i a failed postcondition at step i; 2 a
version that is not X.Y.Z. A wrong tree is not a refusal before the steps:
it fails at step 2 (clean-tree), and there is no platform check -- a
Windows host with Docker is what the container steps need.

Usage:

    python scripts/release.py 1.6.1              # dry-run: what would happen
    python scripts/release.py 1.6.1 --execute    # act, step by step
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import changelog_fragments  # noqa: E402
import release_check  # noqa: E402

TRAIN_PREFIX = "release/"
TAP_REPOSITORY = f"{release_check.OWNER}/homebrew-tap"
TAP_FORMULA_PATH = "Formula/policyforge.rb"
CONTAINER_IMAGE = release_check.CONTAINER_IMAGE

#: The team's side since 2026-09-27 (CLAUDE.md, "Where work lives"): the
#: milestone, the train, the tracking records, the release PR and main live
#: on a local Forgejo. What a USER fetches -- the tag's archive, the tap, the
#: published formula -- stays on GitHub, so steps 12 onward are unchanged
#: and read GitHub whatever `forge` says (80's rulings on #7, comment 319).
FORGEJO_URL = os.environ.get("FORGEJO_URL", "http://127.0.0.1:3000").rstrip("/")
FORGEJO_REPO = "policyforge/policyforge"
#: The private repository BY URL, never through a remote name (80's ruling
#: (b) on #124). A clone whose remote is `origin` read the tag as absent; and
#: the shared clone's `forgejo` remote carried another account's token in its
#: URL (b5 measured it), so every read and push through it went to a URL
#: holding a credential that wasn't the operator's. The operator's own token
#: goes as a header (`_git_env`), and a URL with userinfo is refused
#: (`userinfo_refusal`).
FORGEJO_REPO_URL = f"{FORGEJO_URL}/{FORGEJO_REPO}.git"
#: Where `_main_merged` puts Forgejo's main, fetched by URL: a ref of the
#: tooling's own, so no remote-tracking ref (and no FETCH_HEAD, which the
#: shared clone's main checkout shares) stands in for it.
FORGEJO_MAIN_REF = "refs/release-tooling/forgejo/main"

#: The public repository, BY URL (#33): what users fetch. Never a remote
#: name, which could resolve to Forgejo, and never a mirror. Every push to it
#: is an explicit refspec this script prints first.
GITHUB_URL = f"https://github.com/{release_check.REPOSITORY}.git"

#: The licensed-framework parsers that stay private from 1.7 on (the user's
#: decision; #32, #33). Their paths are fixed; everything else that belongs
#: with them is DERIVED from the tree at build time (`licensed_set`).
LICENSED_MODULES = release_check.LICENSED_MODULES
LICENSED_PATHS = release_check.LICENSED_PATHS
LICENSED_DOTTED = frozenset(f"policyforge.ingest.{m}" for m in LICENSED_MODULES)


def candidate_ref(version: str) -> str:
    """The one GitHub branch the public commit is proposed on before `main`:
    GitHub CI runs on it, and the approval install fetches its archive."""
    return f"refs/heads/release-candidate/{version}"


#: Where a release goes (#90). PUBLIC: the Forgejo cut, then GitHub (P, the
#: tag, the GitHub Release, the tap). LOCAL: Forgejo only -- the user's merge
#: to main, a tag on Forgejo, and both installs from Forgejo archives -- with
#: every step that reaches GitHub DEFERRED: printed, and named in the cut
#: record, never skipped silently. The user, to 9b directly on 2026-09-29,
#: asked because 80's filing of #90 was a relay: "Yes, local first."
PUBLISH_PUBLIC, PUBLISH_LOCAL = "public", "local"
#: The steps a local release defers. Not a list kept right by hand:
#: `tests/test_release_local.py` runs every step of the public plan against
#: recording fakes, and fails if a step reaches GitHub and is neither in this
#: tuple nor replaced by `_localised` (so a new GitHub step can't slip into a
#: local release unclassified).
DEFERRED_LOCAL = ("public-build", "public-candidate", "public-main", "publish", "release-check")
#: Where a local install's source tarball sits in the container: beside the
#: formula in the read-only `/candidate` mount. The HOST fetched it from
#: Forgejo with the operator's token and proved its tree; the container gets
#: the bytes, never the token or a Forgejo URL (80's condition on #90).
LOCAL_SOURCE = "/candidate/source.tar.gz"


CHECK, ACT, WAIT, DEFER = "check", "act", "wait", "defer"


@dataclass
class Check:
    """A gate or postcondition's answer, with the values it compared."""

    ok: bool
    measured: list[str] = field(default_factory=list)


@dataclass
class Step:
    key: str
    title: str
    # CHECK: gate only; ACT: gate, act, postcondition; WAIT: the user acts;
    # DEFER: a local release's GitHub step, said and never run (#90)
    kind: str
    gate: Callable[[Context], Check]
    post: Callable[[Context], Check]
    act: Callable[[Context], None] | None = None
    outward: bool = False
    #: One line saying what the step would do, for the dry-run.
    would: str = ""
    #: The key of a LATER outward step this one commits the run to. It is
    #: asked for here, before this step writes anything, and not again when
    #: that step is reached in the same run (policyforge-ba on #348).
    commits_to: str = ""


@dataclass
class Context:
    """Everything a step touches outside Python, injectable for tests."""

    version: str
    root: Path = REPO_ROOT
    #: Runs argv, returns CompletedProcess(returncode, stdout, stderr). Never raises.
    run: Callable[[list[str]], subprocess.CompletedProcess] = None  # type: ignore[assignment]
    #: HTTP status of a URL.
    status_of: Callable[[str], int] = None  # type: ignore[assignment]
    #: sha256 of the bytes a URL serves.
    hash_url: Callable[[str], str] = None  # type: ignore[assignment]
    #: Installs a formula text in a clean container and checks the installed
    #: package's version is this one: (formula, version) -> (ran, ok, lines).
    install: Callable[[str, str], tuple[bool, bool, list[str]]] = None  # type: ignore[assignment]
    #: Runs the full suite on one Python version over a tree archive (#242):
    #: (version, tar) -> (result, lines), result one of PYTHON_RESULTS.
    run_python: Callable[[str, Path], tuple[str, list[str]]] = None  # type: ignore[assignment]
    #: GitHub's pytest legs on the candidate that holds the approval commit's
    #: tree (#398): (ctx, sha) -> ({job name: mark}, lines, candidate commit).
    #: Empty jobs when no completed run on such a candidate could be read;
    #: `lines` say why.
    #: None (a test's default) reads nothing, so every version runs locally.
    ci_legs: Callable[[Context, str], tuple[dict[str, str], list[str], str]] = None  # type: ignore[assignment]
    #: Reads the typed confirmation for an outward step.
    confirm: Callable[[str], str] = input
    #: The private repository's URL (#124): every Forgejo read and push names
    #: it, never a remote. A test points it at a scratch repository.
    forgejo_url: str = FORGEJO_REPO_URL
    #: The release's tracking issue, where the two artefact gates read their records.
    tracking: int | None = None
    #: The operator's regenerated and reconciled formula (`--formula`), the
    #: base every formula this script writes is edited from (#389). None
    #: means the published formula, which is right only while the lock is
    #: unchanged since the last release.
    formula_path: Path | None = None
    #: Values steps hand to later steps (main's merge SHA, the candidate formula).
    notes: dict[str, str] = field(default_factory=dict)
    #: Where the team-side steps read: "github" (as before) or "forgejo".
    #: The user-facing steps (tag archive, tap, published formula) read
    #: GitHub either way.
    forge: str = "github"
    #: Forgejo's API as the caller's own bot: (method, path, json body or
    #: None) -> (HTTP status, decoded JSON, headers). Injectable for tests,
    #: as `status_of` is. Only called when `forge` is "forgejo".
    forgejo: Callable[[str, str, object], tuple[int, object, dict]] = None  # type: ignore[assignment]
    #: PUBLISH_PUBLIC or PUBLISH_LOCAL (#90).
    publish: str = PUBLISH_PUBLIC
    #: Fetches a Forgejo API path (an archive) to a file as the caller's own
    #: bot: (path, destination) -> HTTP status. Local releases only.
    fetch_archive: Callable[[str, Path], int] = None  # type: ignore[assignment]
    #: Rehearses the cut on HEAD and runs the tests on the result (#120):
    #: (version, root) -> Check. `main` passes `_real_rehearse`; a context
    #: without one fails the changelog step's gate rather than skipping it.
    rehearse: Callable[[str, Path], Check] = None  # type: ignore[assignment]

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    def git(self, *args: str) -> str:
        return (self.run(["git", "-C", str(self.root), *args]).stdout or "").strip()


# --- the engine --------------------------------------------------------------


def run_steps(steps: list[Step], ctx: Context, *, execute: bool, out=print) -> int:
    """Walk the steps in order; the return value is the exit code."""
    confirmed: set[str] = set()
    for index, step in enumerate(steps, 1):
        out(f"\n[{index}/{len(steps)}] {step.key}: {step.title}")
        if step.kind == DEFER:
            # Never gated, acted on or checked: a deferred step touches
            # nothing, and says so rather than disappearing (#90).
            out(f"    DEFERRED: {step.would}")
            continue
        done = step.post(ctx) if step.kind != CHECK else Check(False)
        if done.ok:
            for line in done.measured:
                out(f"    {line}")
            out("    already done; skipping")
            continue

        gate = step.gate(ctx)
        for line in gate.measured:
            out(f"    gate: {line}")
        if not gate.ok:
            out(f"    GATE FAILED at step {index} ({step.key}); nothing was changed by this step")
            return 10 + index

        if step.kind == CHECK:
            out("    holds")
            continue

        if step.kind == WAIT:
            out(f"    WAITING: {step.would}")
            out("    re-run this command once that has happened; the steps before are done")
            return 5

        if not execute:
            out(f"    would: {step.would}")
            out(
                f"\nDRY RUN: reached {index} of {len(steps)} steps; "
                "the rest depend on this one acting."
            )
            for later in steps[index:]:
                out(f"    not reached: {later.key}: {later.title}")
            return 0

        needs = step.commits_to or (step.key if step.outward else "")
        if needs and needs not in confirmed:
            later = next((s for s in steps if s.key == needs), step)
            prompt = f"    type '{needs}' to {later.would}"
            if needs != step.key:
                prompt += f" (asked now: {step.key} writes the cut, and only {needs} commits it)"
            typed = ctx.confirm(prompt + ": ").strip()
            if typed != needs:
                out(f"    not confirmed (typed {typed!r}); stopping before {step.key}")
                return 6
            confirmed.add(needs)

        assert step.act is not None, f"{step.key} is an ACT step with no action"
        step.act(ctx)
        after = step.post(ctx)
        for line in after.measured:
            out(f"    after: {line}")
        if not after.ok:
            out(f"    POSTCONDITION FAILED at step {index} ({step.key})")
            return 40 + index
    out(f"\nRelease {ctx.tag}: all {len(steps)} steps hold.")
    return 0


# --- the steps ---------------------------------------------------------------


def _versions(ctx: Context) -> tuple[str, str]:
    pyproject = (ctx.root / "pyproject.toml").read_text(encoding="utf-8")
    init = (ctx.root / "src" / "policyforge" / "__init__.py").read_text(encoding="utf-8")
    a = re.search(r'^version = "([^"]+)"', pyproject, re.M)
    b = re.search(r'^__version__ = "([^"]+)"', init, re.M)
    return (a.group(1) if a else "?", b.group(1) if b else "?")


def _fj_list(ctx: Context, path: str) -> list | None:
    """Every item of a paged Forgejo list, or None if any page did not answer.

    Paged to exhaustion (an empty page ends it): the filtered issues list
    honours `limit`/`page`, measured on #7, so one page would be a sample."""
    items: list = []
    joiner = "&" if "?" in path else "?"
    for page in range(1, 1000):
        status, batch, _ = ctx.forgejo("GET", f"{path}{joiner}limit=50&page={page}", None)
        if status != 200 or not isinstance(batch, list):
            return None
        if not batch:
            break
        items.extend(batch)
    return items


def milestone_titles(version: str) -> tuple[str, ...]:
    """The milestone titles a version may be cut from (80's ruling on #28).

    `X.Y.0` is cut from the milestone titled `X.Y.0` or `X.Y`: the team's
    milestones carry the short title (`1.7`), and releases are cut as
    X.Y.Z. A patch, `X.Y.Z` with Z > 0, only from its exact title, so
    `1.7.1` never reaches `1.7`."""
    major, minor, patch = version.split(".")
    return (version, f"{major}.{minor}") if patch == "0" else (version,)


def pick_milestone(milestones: list, version: str) -> tuple[dict | None, str]:
    """(the one milestone `version` resolves to, "") or (None, why not).

    Exactly one: a version that finds both titles (`1.7.0` and `1.7`) is
    refused, naming both, rather than one being picked (9b on #28)."""
    titles = milestone_titles(version)
    found = [m for m in milestones if m.get("title") in titles]
    if len(found) == 1:
        return found[0], ""
    named = " or ".join(repr(t) for t in titles)
    if not found:
        return None, f"no milestone titled {named}"
    return None, (
        f"{len(found)} milestones match {version}: "
        f"{', '.join(repr(m.get('title')) for m in found)}; exactly one may"
    )


def _fj_milestone(ctx: Context) -> tuple[dict | None, str]:
    """The milestone `ctx.version` resolves to, by `pick_milestone`. By id
    afterwards, never by name in a filter: `issues?milestones=<a name that
    does not exist>` IGNORES the filter and returned all 14 open issues
    (measured on #7), so a typo would read as a population, not as nothing."""
    milestones = _fj_list(ctx, f"/repos/{FORGEJO_REPO}/milestones?state=all")
    if milestones is None:
        # Not "no milestone titled ...": an unanswered list says nothing
        # about which milestones exist (ba on #28).
        return None, "the milestone list did not answer"
    return pick_milestone(milestones, ctx.version)


def _milestone_open(ctx: Context) -> Check:
    """Open issues on the version's milestone, by REST, confirmed by search.

    Two instruments because `gh issue list --milestone` returned a false 0 for
    1.6.1 while 19 were open (CLAUDE.md), and zero is the value that lets a
    release out.

    **On Forgejo the second instrument is the milestone object**, and it is
    not an issue count: its `open_issues` counts open PULL REQUESTS too
    (measured on #7: 7 = 6 issues + 1 PR). So the gate is the issues list
    at zero, and the object must RECONCILE with issues plus PRs; a
    disagreement means one of the two did not see the whole population.
    """
    if ctx.forge == "forgejo":
        milestone, why = _fj_milestone(ctx)
        if milestone is None:
            return Check(False, [f"{why} on Forgejo"])
        base = f"/repos/{FORGEJO_REPO}/issues?state=open&milestones={milestone['id']}"
        issues = _fj_list(ctx, f"{base}&type=issues")
        pulls = _fj_list(ctx, f"{base}&type=pulls")
        if issues is None or pulls is None:
            return Check(False, ["the milestone's issue or pull list did not answer"])
        # DISTINCT numbers, not counts (ba on #28): a list read page by page
        # can skip an item and repeat another, and a skip in one list with a
        # repeat in the other leaves the COUNT unchanged while an issue goes
        # unseen. A repeat means the read was not a snapshot, so it refuses.
        numbers = [i["number"] for i in issues] + [p["number"] for p in pulls]
        repeats = sorted({n for n in numbers if numbers.count(n) > 1})
        if repeats:
            return Check(
                False,
                [
                    f"the milestone's lists repeat {', '.join(f'#{n}' for n in repeats)}: "
                    "not one snapshot, so no count from them is trusted. Re-run."
                ],
            )
        counted = milestone.get("open_issues")
        reconciles = counted == len(set(numbers))
        named = ", ".join(f"#{p['number']}" for p in pulls) or "none"
        return Check(
            not issues and reconciles,
            [
                f"{ctx.version} is cut from milestone {milestone.get('title')!r}",
                f"open issues on {ctx.version}: {len(issues)} (must be 0)"
                + (f": {', '.join('#' + str(i['number']) for i in issues)}" if issues else ""),
                f"open PRs on {ctx.version}: {len(pulls)} ({named})",
                f"milestone object open_issues {counted} = issues + PRs "
                f"{len(issues) + len(pulls)}: {reconciles}",
            ],
        )
    repo = release_check.REPOSITORY
    milestones = ctx.run(["gh", "api", f"repos/{repo}/milestones?state=all&per_page=100"]).stdout
    milestone, why = pick_milestone(json.loads(milestones or "[]"), ctx.version)
    if milestone is None:
        return Check(False, [why])
    number, title = milestone["number"], milestone.get("title")
    rest = ctx.run(
        [
            "gh",
            "api",
            f"repos/{repo}/issues?milestone={number}&state=open&per_page=100",
            "--jq",
            "[.[]|select(.pull_request|not)]|length",
        ]
    ).stdout.strip()
    search = ctx.run(
        [
            "gh",
            "api",
            "-X",
            "GET",
            "search/issues",
            "-f",
            # The RESOLVED title: the search names a milestone by its text,
            # so `1.7.0` must search `milestone:"1.7"` when that is its title.
            f'q=repo:{repo} is:issue is:open milestone:"{title}"',
            "--jq",
            ".total_count",
        ]
    ).stdout.strip()
    ok = rest == "0" and search == "0"
    return Check(
        ok,
        [
            f"open issues on {ctx.version}: REST {rest or '?'}, search {search or '?'} "
            "(both must be 0)"
        ],
    )


def _server_tip(ctx: Context, branch: str) -> str:
    """`branch`'s tip ON THE SERVER, never this clone's `origin/<branch>` ref.

    policyforge-ba on #348, measured with a second clone: nothing here fetched
    the train, so a gate reading `origin/release/X` answered about whenever
    this clone last fetched. After a late merge pushed from elsewhere,
    clean-tree AND notes-measured both passed on the stale tip, the second on
    exactly the stale measurement it exists to refuse. `ls-remote` asks the
    server every time. No answer is "", which fails every gate that reads it.

    On Forgejo, the branch API asks the server the same question, and needs
    no git credentials to do it.
    """
    return _server_answer(ctx, branch)[1]


def _server_answer(ctx: Context, branch: str) -> tuple[bool, str]:
    """(answered, tip) for `branch` ON THE SERVER. `answered` is False when the
    server couldn't be asked (a failed `ls-remote`, a Forgejo error), which is
    NOT the same as the branch being absent (`(True, "")`). ba measured on #168
    that folding the two together let a failed lookup of one train name hide a
    real ambiguity between the two names (#109)."""
    if ctx.forge == "forgejo":
        import urllib.parse

        status, body, _ = ctx.forgejo(
            "GET", f"/repos/{FORGEJO_REPO}/branches/{urllib.parse.quote(branch, safe='')}", None
        )
        if status == 404:
            return True, ""
        sha = ((body or {}).get("commit") or {}).get("id", "") if status == 200 else ""
        ok = status == 200 and bool(re.fullmatch(r"[0-9a-f]{40}", sha or ""))
        return ok, sha if ok else ""
    out = ctx.run(["git", "-C", str(ctx.root), "ls-remote", "origin", f"refs/heads/{branch}"])
    if out.returncode != 0:
        return False, ""
    for line in (out.stdout or "").splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{branch}" and re.fullmatch(r"[0-9a-f]{40}", sha):
            return True, sha
    return True, ""


def _remote_tip(ctx: Context, remote: str, branch: str) -> str:
    """`branch`'s tip as `remote` reports it now; "" when it does not answer."""
    out = ctx.run(["git", "-C", str(ctx.root), "ls-remote", remote, f"refs/heads/{branch}"])
    for line in (out.stdout or "").splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() == f"refs/heads/{branch}" and re.fullmatch(r"[0-9a-f]{40}", sha):
            return sha
    return ""


#: What steps 5 and 6 write. A dirty tree made only of these is an
#: interrupted cut, and clean-tree says how to put it back.
CUT_PATHS = ("CHANGELOG.md", "changelog.d/", "pyproject.toml", "src/policyforge/__init__.py")


def _dirty_paths(ctx: Context) -> list[str]:
    """Paths `git status --porcelain` names, read unstripped: its first column
    is significant, and `ctx.git` strips the output."""
    out = ctx.run(["git", "-C", str(ctx.root), "status", "--porcelain"]).stdout or ""
    return [line[3:] for line in out.splitlines() if line.strip()]


def _train_branches(ctx: Context) -> tuple[str, ...]:
    """The names this version's train may have, as `train-final` already reads
    them (#109): `release/X.Y.Z`, and for X.Y.0 also `release/X.Y`, the name the
    1.7 train had. A patch release has only its own name, so 1.7.1 never cuts
    from `release/1.7`."""
    full = f"{TRAIN_PREFIX}{ctx.version}"
    if ctx.version.endswith(".0"):
        return (full, f"{TRAIN_PREFIX}{ctx.version[:-2]}")
    return (full,)


def _train_tips(ctx: Context, branches: tuple[str, ...]) -> tuple[dict[str, str], Check | None]:
    """{name: tip} for each train name the server holds, or a refusal: a name
    the server didn't ANSWER for (it may exist at another commit, ba on #168),
    or both names present at different commits."""
    answers = {b: _server_answer(ctx, b) for b in branches}
    unanswered = [b for b, (answered, _) in answers.items() if not answered]
    if unanswered:
        return {}, Check(
            False,
            [f'the server didn\'t answer for {", ".join(unanswered)}: no answer is not "absent"'],
        )
    tips = {b: tip for b, (_, tip) in answers.items() if tip}
    if len(set(tips.values())) > 1:
        named = " and ".join(f"{b} at {t[:12]}" for b, t in tips.items())
        return {}, Check(False, [f"ambiguous train on server: {named}"])
    return tips, None


def _train_tree(ctx: Context) -> Check:
    # THIS version's train, not any train (policyforge-b5 on #348): with a
    # prefix test, `release.py 1.7.0` on a release/1.6.1 checkout passed
    # here and cut 1.7.0 from 1.6.1's code, and notes-measured, reading
    # release/1.7.0's tip, could not see it. The names are exact (#109).
    branches = _train_branches(ctx)
    branch = ctx.git("rev-parse", "--abbrev-ref", "HEAD")
    head = ctx.git("rev-parse", "HEAD")
    valid_tips, refused = _train_tips(ctx, branches)
    if refused:
        return refused

    train = branch if branch in branches else branches[0]
    remote = valid_tips.get(train, "")
    cut = _release_pr(ctx).get("headRefOid", "")
    dirty = _dirty_paths(ctx)

    ok = branch in branches and head in {remote, cut} - {""} and not dirty
    expected_branches = " or ".join(repr(b) for b in branches)
    measured = [
        f"branch {branch!r} (must be {expected_branches})",
        f"HEAD {head[:12]}: origin/{train} ON THE SERVER {remote[:12] or '-'}, "
        f"release PR head {cut[:12] or '-'} (must be one)",
        f"uncommitted changes: {len(dirty)} (must be 0)",
    ]
    if dirty and all(path.startswith(CUT_PATHS) for path in dirty):
        # Steps 5 and 6 wrote these and the run stopped before release-pr
        # committed them (a failed postcondition, a failed commit, Ctrl-C).
        # Said, not done: discarding a working tree is the operator's call.
        measured.append(
            "these are only the cut's own files, from an interrupted run; to start "
            "the cut again: git restore --source=HEAD --staged --worktree -- " + " ".join(CUT_PATHS)
        )
    return Check(ok, measured)


def _fragments_ready(ctx: Context) -> Check:
    found = changelog_fragments.fragments(ctx.root / "changelog.d")
    problems = changelog_fragments.check(ctx.root / "changelog.d")
    return Check(
        bool(found) and not problems,
        [
            f"fragments: {len(found)} (must be > 0)",
            f"fragment problems: {len(problems)} (must be 0)",
        ],
    )


def _changelog_cut(ctx: Context) -> Check:
    left = changelog_fragments.fragments(ctx.root / "changelog.d")
    text = (ctx.root / "CHANGELOG.md").read_text(encoding="utf-8")
    has = f"\n## {ctx.version}\n" in "\n" + text
    return Check(
        has and not left,
        [f"`## {ctx.version}` in CHANGELOG.md: {has}", f"fragments left: {len(left)}"],
    )


def _rehearsed(ctx: Context) -> Check:
    """The tree's tests pass once it is cut (#120). #118's test read a fragment
    by name, passed on every train gate, and failed only on the release PR,
    the first tree without fragments. So before the cut assembles them, the
    same cut is rehearsed on HEAD in a throwaway worktree and the suite runs
    there (`scripts/cut_rehearsal.py`; a pass is remembered per tree)."""
    if ctx.rehearse is None:
        return Check(False, ["cut rehearsal: NOT RUN (this context has no rehearse)"])
    return ctx.rehearse(ctx.version, ctx.root)


def _real_rehearse(version: str, root: Path) -> Check:
    import cut_rehearsal

    lines: list[str] = []
    ok = cut_rehearsal.rehearse(version, root=root, out=lines.append)
    said = [line for text in lines for line in text.splitlines() if line.startswith("cut ")]
    return Check(ok, said[-6:] or ["cut rehearsal: said nothing"])


def _assemble(ctx: Context) -> None:
    changelog_fragments.main(["--version", ctx.version])


def _bump_ready(ctx: Context) -> Check:
    a, b = _versions(ctx)
    return Check(a == b, [f"pyproject {a} / __version__ {b} (must agree before bumping)"])


def _bumped(ctx: Context) -> Check:
    a, b = _versions(ctx)
    return Check(
        a == b == ctx.version, [f"pyproject {a} / __version__ {b} (must both be {ctx.version})"]
    )


def _bump(ctx: Context) -> None:
    for path, pattern in (
        (ctx.root / "pyproject.toml", r'^(version = ")[^"]+(")'),
        (ctx.root / "src" / "policyforge" / "__init__.py", r'^(__version__ = ")[^"]+(")'),
    ):
        text = path.read_text(encoding="utf-8")
        path.write_text(
            re.sub(pattern, rf"\g<1>{ctx.version}\g<2>", text, count=1, flags=re.M),
            encoding="utf-8",
            newline="\n",
        )


# --- the install pins (#413) --------------------------------------------------
#
# README tells a user without Homebrew to run
# `pipx install git+https://github.com/<owner>/policyforge@vX.Y.Z`. The 1.6.1
# cut moved nothing there, so the released README installed 1.6.0 (5b; 80's
# ruling on #413: the pins stay, and the cut moves them). The population is
# DERIVED, every pin in every tracked text file, and CLASSIFIED rather than
# filtered, so a pin in a new file is rewritten or refused, never skipped:
#
#     instruction   INSTALL_DOCS      rewritten to this release
#     history       CHANGELOG.md      what a past release said; never touched
#     test          under tests/      fixtures; never touched
#     unclassified  anywhere else     REFUSED, naming the file
#
# **Instructions are declared, not inferred** (policyforge-b5 on #430). A
# pin elsewhere may be a record, not a command: "epoch 24 installed
# ...@v1.6.0" in docs/measurements/epochs.md must not become the new release, and a
# conserved count cannot see that over-replacement (#252's shape). So a
# pin outside these files stops the cut until someone says which it is.

#: The files whose pins ARE install instructions (80's sweep on #413).
INSTALL_DOCS = frozenset({"README.md"})

#: A pin of `<owner>/policyforge` at a release, as an install command writes
#: it. The owner is captured so a pin naming the redirect's old owner is
#: refused rather than moved (CLAUDE.md: a redirect is not a reference).
#:
#: **Case-insensitive** (policyforge-ba on #430): GitHub resolves
#: `rdazzleman/PolicyForge` as this repository, so a case-sensitive match
#: skipped a pin that installs. The `v` is captured apart, because a tag
#: IS case-sensitive: `@V1.6.0` names no tag, and is refused, not moved.
#:
#: The version includes a pre-release suffix (`1.7.0rc1`, `1.7.0-rc.1`), so
#: one is rewritten whole rather than skipped or half-moved (policyforge-b5
#: on #430). It ends on a letter or digit, so a sentence's full stop after
#: the pin is not part of it.
_PIN_RE = re.compile(
    r"([A-Za-z0-9_.-]+)/policyforge(?:\.git)?@(v)"
    r"(\d+\.\d+\.\d+(?:-?[A-Za-z](?:[0-9A-Za-z.]*[0-9A-Za-z])?)?)(?![0-9A-Za-z])",
    re.IGNORECASE,
)
#: The same shape for `git grep -E`, which finds the lines `_PIN_RE` reads.
_PIN_ERE = r"[A-Za-z0-9_.-]+/policyforge(\.git)?@v[0-9]+\.[0-9]+\.[0-9]+"


@dataclass(frozen=True)
class Pin:
    path: str
    line: int
    owner: str
    version: str
    #: The tag's `v` as written; `V` names no tag (git tags are case-sensitive).
    marker: str = "v"

    @property
    def kind(self) -> str:
        if self.path == "CHANGELOG.md":
            return "history"
        if self.path.startswith("tests/"):
            return "test"
        if self.path in INSTALL_DOCS:
            return "instruction"
        return "unclassified"

    @property
    def canonical(self) -> bool:
        return f"{self.owner}/policyforge".lower() == release_check.REPOSITORY.lower()

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.owner}/policyforge@v{self.version}"


def _pins(ctx: Context, at: str = "") -> list[Pin] | None:
    """Every pin in every tracked text file, in the working tree or at `at`
    (a commit). None when `git grep` fails: no answer is not zero pins."""
    argv = ["git", "-C", str(ctx.root), "grep", "-n", "-I", "-i", "-E", _PIN_ERE]
    if at:
        argv.append(at)
    out = ctx.run(argv)
    if out.returncode not in (0, 1):  # 1 is "no match"; anything else, no answer
        return None
    found = []
    for row in (out.stdout or "").splitlines():
        if at:
            row = row[len(at) + 1 :]  # `HEAD:path:line:text`
        path, number, text = row.split(":", 2)
        for match in _PIN_RE.finditer(text):
            found.append(Pin(path, int(number), match.group(1), match.group(3), match.group(2)))
    return found


def _pin_lines(pins: list[Pin]) -> list[str]:
    by_kind = {
        kind: [p for p in pins if p.kind == kind]
        for kind in ("instruction", "history", "test", "unclassified")
    }
    return [f"install pins: {', '.join(f'{k} {len(v)}' for k, v in by_kind.items())}"]


def _pins_ready(ctx: Context) -> Check:
    pins = _pins(ctx)
    if pins is None:
        return Check(False, ["git grep failed, so the pins are unknown (not zero)"])
    instructions = [p for p in pins if p.kind == "instruction"]
    foreign = [p for p in instructions if not p.canonical]
    untagged = [p for p in instructions if p.marker != "v"]
    unclassified = [p for p in pins if p.kind == "unclassified"]
    ctx.notes["pins"] = str(len(instructions))
    return Check(
        not foreign and not untagged and not unclassified,
        [
            *_pin_lines(pins),
            *(f"names another owner, rewrite it by hand first: {p}" for p in foreign),
            *(
                f"`@V` names no tag (tags are case-sensitive), fix it by hand: {p}"
                for p in untagged
            ),
            *(
                f"a pin outside INSTALL_DOCS, instruction or record? Declare the file, "
                f"or keep it out of the tree: {p}"
                for p in unclassified
            ),
        ],
    )


def _pins_current(ctx: Context) -> Check:
    pins = _pins(ctx)
    if pins is None:
        return Check(False, ["git grep failed, so the pins are unknown (not current)"])
    instructions = [p for p in pins if p.kind == "instruction"]
    stale = [p for p in instructions if p.version != ctx.version or not p.canonical]
    # Conservation: the step moves pins, it never loses one. Known only after
    # the gate has counted them in this run.
    before = ctx.notes.get("pins")
    kept = before is None or int(before) == len(instructions)
    return Check(
        not stale and kept,
        [
            *_pin_lines(pins),
            *(f"not at v{ctx.version}: {p}" for p in stale),
            *([] if kept else [f"instruction pins {before} before, {len(instructions)} after"]),
        ],
    )


def _repin(ctx: Context) -> None:
    pins = _pins(ctx) or []
    for path in sorted({p.path for p in pins if p.kind == "instruction" and p.canonical}):
        file = ctx.root / path
        text = file.read_text(encoding="utf-8")
        text = _PIN_RE.sub(
            lambda m: (
                m.group(0)[: m.start(3) - m.start(0)]
                + ctx.version
                + m.group(0)[m.end(3) - m.start(0) :]
                if f"{m.group(1)}/policyforge".lower() == release_check.REPOSITORY.lower()
                and m.group(2) == "v"
                else m.group(0)
            ),
            text,
        )
        file.write_text(text, encoding="utf-8", newline="\n")


def _release_pr(ctx: Context) -> dict:
    if ctx.forge == "forgejo":
        # The same selection as below, on Forgejo's list: by the branch
        # `_open_pr` pushes and the exact title, MERGED then OPEN. Mapped
        # into the shape `gh` returns, so every gate reading it is unchanged.
        pulls = _fj_list(ctx, f"/repos/{FORGEJO_REPO}/pulls?state=all") or []
        mine = [
            p
            for p in pulls
            if (p.get("base") or {}).get("ref") == "main"
            and (p.get("head") or {}).get("ref") == f"9b/release-{ctx.version}"
            and p.get("title", "") == f"Release {ctx.version}"
        ]
        shaped = [
            {
                "number": p.get("number"),
                "state": "MERGED"
                if p.get("merged")
                else "OPEN"
                if p.get("state") == "open"
                else "CLOSED",
                "headRefOid": (p.get("head") or {}).get("sha", ""),
                "mergeCommit": {"oid": p.get("merge_commit_sha")} if p.get("merged") else None,
                "title": p.get("title", ""),
            }
            for p in mine
        ]
        rank = {"MERGED": 0, "OPEN": 1}
        shaped.sort(key=lambda p: rank.get(p["state"], 2))
        return shaped[0] if shaped else {}
    out = ctx.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            release_check.REPOSITORY,
            "--base",
            "main",
            "--state",
            "all",
            # By the branch `_open_pr` pushes, not `--search`: search is an
            # index, and it missed a PR created a moment earlier, at the 1.6.1
            # cut (policyforge-9b on #380). `--head` filters the list itself.
            "--head",
            f"9b/release-{ctx.version}",
            "--json",
            "number,state,headRefOid,mergeCommit,title",
        ]
    ).stdout
    # The exact title `_open_pr` writes: a prefix took "Release 1.6.10" for
    # 1.6.1. Then MERGED, then OPEN, before a closed first attempt, whatever
    # order `gh` lists them in (policyforge-b5 on #348).
    prs = [p for p in json.loads(out or "[]") if p.get("title", "") == f"Release {ctx.version}"]
    rank = {"MERGED": 0, "OPEN": 1}
    prs.sort(key=lambda p: rank.get(p.get("state", ""), 2))
    return prs[0] if prs else {}


def _pr_ready(ctx: Context) -> Check:
    return Check(True, ["the cut commit will be pushed and a PR opened against main"])


def _cut_at_head(ctx: Context) -> list[tuple[bool, str]]:
    """Whether HEAD's COMMITTED content is the cut: both versions X, `## X` in
    the changelog, no fragments left, and nothing uncommitted.

    policyforge-ba on #348: the postcondition used to ask only whether a PR
    sat at HEAD. With the commit refused (a hook, no identity), the push sent
    the train tip, a PR opened there, and "ok" was printed over a tree whose
    bump was still uncommitted, so 9.9.9 could have been tagged on 9.9.8 code.
    This reads HEAD, not the working tree, because HEAD is what gets pushed.
    """

    def show(path: str) -> str:
        return ctx.run(["git", "-C", str(ctx.root), "show", f"HEAD:{path}"]).stdout or ""

    a = re.search(r'^version = "([^"]+)"', show("pyproject.toml"), re.M)
    b = re.search(r'^__version__ = "([^"]+)"', show("src/policyforge/__init__.py"), re.M)
    a, b = (a.group(1) if a else "?"), (b.group(1) if b else "?")
    section = f"\n## {ctx.version}\n" in "\n" + show("CHANGELOG.md")
    listed = ctx.run(["git", "-C", str(ctx.root), "ls-tree", "--name-only", "HEAD", "changelog.d/"])
    left = [
        name
        for name in (listed.stdout or "").splitlines()
        if name.endswith(".md") and Path(name).name not in changelog_fragments.NOT_A_FRAGMENT
    ]
    dirty = _dirty_paths(ctx)
    pins = _pins(ctx, "HEAD")
    stale = (
        None
        if pins is None
        else [p for p in pins if p.kind == "instruction" and p.version != ctx.version]
    )
    return [
        (
            a == b == ctx.version,
            f"at HEAD: pyproject {a} / __version__ {b} (must both be {ctx.version})",
        ),
        (section, f"at HEAD: `## {ctx.version}` in CHANGELOG.md: {section}"),
        (not left, f"at HEAD: fragments left: {len(left)} (must be 0)"),
        (
            stale == [],
            f"at HEAD: install pins not at v{ctx.version}: "
            + ("unknown, git grep failed" if stale is None else str(len(stale))),
        ),
        (not dirty, f"uncommitted changes: {len(dirty)} (must be 0: the cut is committed)"),
    ]


def _pr_open(ctx: Context) -> Check:
    pr = _release_pr(ctx)
    head = ctx.git("rev-parse", "HEAD")
    at_head = bool(pr) and (pr.get("state") == "MERGED" or pr.get("headRefOid") == head)
    content = _cut_at_head(ctx)
    measured = [
        f"release PR: {'#' + str(pr['number']) + ' ' + pr['state'] if pr else 'none'}, "
        f"at HEAD {head[:12]}: {at_head}",
        *(line for _, line in content),
    ]
    if ctx.notes.get("release-pr"):
        measured.append(ctx.notes["release-pr"])
    return Check(at_head and all(ok for ok, _ in content), measured)


def _open_pr(ctx: Context) -> None:
    """Commit, push, open, and STOP at the first that fails (policyforge-ba
    on #348): a refused commit followed by a push sends the train tip under
    the release's name. What failed is left for the postcondition to print."""
    branch = f"9b/release-{ctx.version}"
    if ctx.forge == "forgejo":
        body = (
            f"The {ctx.version} cut, prepared by scripts/release.py. "
            "Merging it is the user's approval."
        )
        for argv in (
            ["git", "-C", str(ctx.root), "commit", "-am", f"Release {ctx.version}"],
            ["git", "-C", str(ctx.root), "push", ctx.forgejo_url, f"HEAD:refs/heads/{branch}"],
        ):
            proc = ctx.run(argv)
            if proc.returncode != 0:
                said = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
                ctx.notes["release-pr"] = (
                    f"`{' '.join(argv[3:5])}` exited {proc.returncode}, so nothing after it ran"
                    + (f": {said[-1]}" if said else "")
                )
                return
        status, _, _ = ctx.forgejo(
            "POST",
            f"/repos/{FORGEJO_REPO}/pulls",
            {"base": "main", "head": branch, "title": f"Release {ctx.version}", "body": body},
        )
        if status != 201:
            ctx.notes["release-pr"] = f"opening the PR on Forgejo answered HTTP {status}"
        return
    for argv in (
        ["git", "-C", str(ctx.root), "commit", "-am", f"Release {ctx.version}"],
        ["git", "-C", str(ctx.root), "push", "origin", f"HEAD:refs/heads/{branch}"],
        [
            "gh",
            "pr",
            "create",
            "--repo",
            release_check.REPOSITORY,
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            f"Release {ctx.version}",
            "--body",
            f"The {ctx.version} cut, prepared by scripts/release.py. "
            "Merging it is the user's approval.",
        ],
    ):
        proc = ctx.run(argv)
        if proc.returncode != 0:
            said = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()
            what = " ".join(argv[3:5] if argv[0] == "git" else argv[:3])
            ctx.notes["release-pr"] = (
                f"`{what}` exited {proc.returncode}, so nothing after it ran"
                + (f": {said[-1]}" if said else "")
            )
            return


def _pr_still_open(ctx: Context) -> Check:
    """The wait's gate: there is a release PR to wait FOR.

    policyforge-ba on #348: if the only release PR was closed unmerged (at
    HEAD, so release-pr counts as done), the wait said "waiting" forever.
    Safe, but silent. Now it stops and says the PR is closed."""
    pr = _release_pr(ctx)
    state = pr.get("state", "") if pr else ""
    return Check(
        state == "OPEN",
        [
            f"release PR: {'#' + str(pr['number']) + ' ' + state if pr else 'none'} "
            "(must be OPEN to wait for; a CLOSED one will never be merged: "
            "reopen it, or close the cut and start again)"
        ],
    )


def _main_merged(ctx: Context) -> Check:
    pr = _release_pr(ctx)
    merge = (pr.get("mergeCommit") or {}).get("oid", "") if pr else ""
    if not merge:
        return Check(False, [f"release PR merged into main: {'no' if pr else 'no PR'}"])
    if ctx.forge == "forgejo":
        remote, main = "Forgejo", FORGEJO_MAIN_REF
        ctx.run(
            ["git", "-C", str(ctx.root), "fetch", "-q", ctx.forgejo_url, f"+refs/heads/main:{main}"]
        )
    else:
        remote, main = "origin", "origin/main"
        ctx.run(["git", "-C", str(ctx.root), "fetch", "-q", "origin", "main"])
    on_main = (
        ctx.run(["git", "-C", str(ctx.root), "merge-base", "--is-ancestor", merge, main]).returncode
        == 0
    )
    # Against the cut commit THIS script made (local HEAD, which survives
    # the exit-5 re-run), never against the PR's head as fetched now: that
    # value passes by construction if anything was pushed to the release
    # branch after step 5 (policyforge-9b on #348, the --match-head-commit
    # failure the charge names).
    cut = ctx.git("rev-parse", "HEAD")
    head_is_cut = pr.get("headRefOid") == cut
    same_tree = ctx.git("rev-parse", f"{merge}^{{tree}}") == ctx.git("rev-parse", f"{cut}^{{tree}}")
    ok = on_main and head_is_cut and same_tree
    if ok:
        ctx.notes["main_sha"] = merge
    return Check(
        ok,
        [
            f"merge commit {merge[:12]} on {remote}/main: {on_main}",
            f"the PR's head {str(pr.get('headRefOid'))[:12]} is the cut commit {cut[:12]}: "
            f"{head_is_cut}",
            f"the merge's tree equals the cut commit's: {same_tree}",
        ],
    )


def _is_local(ctx: Context) -> bool:
    return ctx.publish == PUBLISH_LOCAL


def _tag_remote(ctx: Context) -> str:
    """Where the tag lives: Forgejo for a local release (#90), GitHub by URL
    on the Forgejo path (#33), `origin` otherwise."""
    if _is_local(ctx):
        return ctx.forgejo_url
    return GITHUB_URL if ctx.forge == "forgejo" else "origin"


def _no_tag(ctx: Context) -> Check:
    local = ctx.git("tag", "--list", ctx.tag)
    remote = (
        ctx.run(
            ["git", "-C", str(ctx.root), "ls-remote", "--tags", _tag_remote(ctx), ctx.tag]
        ).stdout
        or ""
    ).strip()
    return Check(
        not local and not remote,
        [f"tag {ctx.tag}: local {bool(local)}, remote {bool(remote)} (both must be absent)"],
    )


def _base_formula(ctx: Context) -> str:
    """The formula this cut edits: `--formula` if given, else the published one.

    CONTRIBUTING's release step regenerates the resource stanzas with
    `brew update-python-resources` and reconciles each to the lock. This is
    how that file reaches the script, instead of being published to the
    tap before the release to get read (#389)."""
    if ctx.formula_path is not None:
        return ctx.formula_path.read_text(encoding="utf-8")
    return release_check.fetch_formula()


def _formula(ctx: Context, url: str, sha: str) -> str:
    """The published formula with its source `url`, `sha256` and `homepage` set.

    `homepage` from the one `OWNER` constant, as `url` is (80's ruling on
    #348): the published formula still names the old owner there, and
    `brew info` shows it to users (policyforge-9b)."""
    # LF once, here: every pattern below anchors on "\n", and on a CRLF
    # formula the version line would silently not be added, failing step 8
    # exactly as #391 did (policyforge-b5 on #392; the live formula is LF).
    text = _base_formula(ctx).replace("\r\n", "\n")
    text = re.sub(
        r'^(\s*homepage ")[^"]+(")',
        rf"\g<1>{release_check.CANONICAL_HOMEPAGE}\g<2>",
        text,
        count=1,
        flags=re.M,
    )
    text = re.sub(r'^(\s*url ")[^"]+(")', rf"\g<1>{url}\g<2>", text, count=1, flags=re.M)
    # A commit archive carries no version, so the formula declares one (#391).
    # Homebrew takes a formula's version from its url; from `v1.6.1.tar.gz`
    # that is 1.6.1, but from `archive/<sha>.tar.gz` there is none. Homebrew
    # 4.6 guessed one ("stable 3", from the SHA's last digit) and installed;
    # 7.0.6 refuses: "invalid attribute for formula ...: version (nil)"
    # (policyforge-9b, at the 1.6.1 cut). Only for a commit archive: beside a
    # tag url an explicit version is one `brew audit --strict` calls redundant.
    text = re.sub(r'^\s*version "[^"]*"\n', "", text, flags=re.M)
    # A local release's `file://` tarball carries no version either (#90).
    if _COMMIT_ARCHIVE_RE.search(url) or url.startswith("file://"):
        text = re.sub(
            r'^(\s*url "[^"]+"\n)', rf'\g<1>  version "{ctx.version}"\n', text, count=1, flags=re.M
        )
    return re.sub(r'^(  sha256 ")[0-9a-f]{64}(")', rf"\g<1>{sha}\g<2>", text, count=1, flags=re.M)


#: A GitHub archive of a commit, not of a tag: `/archive/<40 hex>.tar.gz`.
_COMMIT_ARCHIVE_RE = re.compile(r"/archive/[0-9a-f]{40}\.tar\.gz$")


# --- the public release commit P (#33) -----------------------------------------
#
# From 1.7 the licensed parsers stay private (the user's decision), and the
# Forgejo train carries them in its HISTORY. So the public release is never a
# push of the train: it is ONE commit P, built here, whose parent is GitHub's
# current `main` (G) and whose tree is the cut commit's with the licensed set
# removed. Pushing P sends only P and the objects its tree needs that GitHub
# lacks. 1d's guard (`scripts/publish_guard.py P G`) decides whether P may
# leave; this script branches on its exit code alone.


def imports_of(source: str, path: str) -> set[str]:
    """Every module `source` imports, dotted, with relative imports resolved
    against `path`'s package. `ast`, not a line regex: a parenthesised
    multi-line import and a relative `from .<module> import x` are both
    invisible to one, and missing an importer is the dangerous direction.
    (Written with a placeholder, not a module's name: #35's guard reads an
    import's TEXT anywhere in a published file, docstrings included, and
    this file ships in P. ba on #33.)"""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    parts = path[len("src/") :].removesuffix(".py").split("/") if path.startswith("src/") else []
    # A module and a package `__init__` both sit in `parts[:-1]`.
    package = parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = (
                    package[: len(package) - node.level + 1] if node.level <= len(package) else []
                )
                module = ".".join([*base, *([node.module] if node.module else [])])
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


#: A licensed module named inside a STRING: a dotted `policyforge.ingest.<m>`,
#: a relative `from .<m>`, or `from policyforge.ingest import ... <m>`. An
#: import held in a string (a harness, `importlib.import_module(...)`, a
#: monkeypatch target) is invisible to the AST's import nodes (ba on #37:
#: `parser_gate.py`'s trial-run harness).
_STRING_REF = re.compile(
    r"policyforge\.ingest\.(?:{m})(?![\w])|from\s+\.\s*(?:{m})(?![\w])"
    r"|from\s+policyforge\.ingest\s+import\s+[^\n]*?(?<![\w])(?:{m})(?![\w])".format(
        m="|".join(release_check.LICENSED_MODULES)
    )
)


def string_refs_of(source: str) -> bool | None:
    """Whether a non-docstring string literal in `source` names a licensed
    module; None when `source` does not parse (then it cannot be cleared).
    Docstrings are prose: a docstring naming a module is a mention."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and _STRING_REF.search(node.value)
        for node in ast.walk(tree)
    )


#: Calls that load a module by a name they are GIVEN. A static import names
#: its module, and these may not: `import_module(f"policyforge.ingest.{m}")`
#: at licensed_builtin.py (#36) loads a licensed module that no string or
#: import node names (b5 on #37).
_DYNAMIC_LOADERS = frozenset(
    {"import_module", "__import__", "find_spec", "spec_from_file_location", "run_module"}
)

#: A load whose name is only known at run time.
UNKNOWN_LOAD = "?"

#: Public files that load a module by a name known only at run time, each
#: reviewed, with why it may ship. Keyed by path, never by line. A test fails
#: when an entry no longer loads dynamically, so the list cannot go stale
#: quietly. Adding an entry is a review decision, not a way to clear a build.
REVIEWED_DYNAMIC = {
    "src/policyforge/plugins.py": (
        "the plugin seam: loads what `BUILTIN` and the `policyforge.licensed` entry "
        "points name, and refuses loudly when nothing is registered; every install "
        "runs the seam checks against it"
    ),
}


def _licensed_file(path: str) -> str:
    """What a constant `spec_from_file_location` path loads, if it is a
    licensed parser's file: its dotted module, or `UNKNOWN_LOAD`; else "".

    Matched on the FILE NAME, not a path suffix (#47, 1d on #37): the full
    `policyforge/ingest/<m>.py` was the only form read, so a shorter path to
    the same file (`ingest/<m>.py`, `../ingest/<m>.py`) classified as a
    mention and shipped. Read as the OS and Python read it (b5 on #57): the
    path is normalised (`./`, `//`, `..`), compared case-insensitively
    (Windows loads `Ingest/<M>.PY` as the same file), each name's trailing
    dots and spaces dropped (Windows opens `<m>.py.` as `<m>.py`), and the
    parser's compiled bytecode counts as the parser (`ingest/<m>.pyc`,
    `ingest/__pycache__/<m>.<tag>.pyc`), and so does a compiled EXTENSION
    module built from it (#59; b5 measured `ExtensionFileLoader` loading one
    on #57): `<m>.pyd` or `<m>.so`, with any ABI tags (`.cp312-win_amd64`,
    `.cpython-312-x86_64-linux-gnu`, `.abi3`) and a debug build's `_d`.
    They're spelled out, not taken from the running interpreter's
    `EXTENSION_SUFFIXES`, because the reader's suffixes aren't the build
    machine's (a Windows build reads a Linux `.so`). A test checks that every
    suffix in the running interpreter's list is read. Any tag is accepted, so
    a name no interpreter would load (say `<m>.x.so`) is still read as the
    parser: that errs towards refusing.

    Directly under an `ingest/` segment (or its `__pycache__/`) it is that
    module. Anywhere else (`<m>.py`, `lib/<m>.py`) the file it loads depends
    on the working directory, so it is unknown: never cleared.

    `licensed_set` searches for a module's file name case-insensitively, so an
    upper-case path reaches this at all (b5 on #57)."""
    import posixpath

    parts = [
        part.rstrip(". ").casefold()
        for part in posixpath.normpath(path.replace("\\", "/")).split("/")
    ]
    name, parent = parts[-1], parts[-2] if len(parts) >= 2 else ""
    if parent == "__pycache__":
        parent = parts[-3] if len(parts) >= 3 else ""
    for module in LICENSED_MODULES:
        compiled = re.fullmatch(rf"{re.escape(module)}(?:\.[\w-]+)*\.pyc", name)
        extension = re.fullmatch(rf"{re.escape(module)}(?:_d)?(?:\.[\w-]+)*\.(?:pyd|so)", name)
        if name == f"{module}.py" or compiled or extension:
            return f"policyforge.ingest.{module}" if parent == "ingest" else UNKNOWN_LOAD
    return ""


def dynamic_loads_of(source: str) -> set[str]:
    """What `source`'s dynamic-loader calls load: a dotted name where the
    call spells it whole (a relative name resolved against a constant
    `package`; each `fromlist` entry of `__import__`; the module a constant
    `spec_from_file_location` path names, by its file name under an
    `ingest/` segment however the path is spelled (`_licensed_file`, #47),
    or `file:<path>`), else
    `UNKNOWN_LOAD`. A name or path built with `+`, an f-string, a variable,
    a loop over names or a non-literal `fromlist` are all unknown.

    A loader rebound to another name is followed ONE level (an `as` import,
    or `x = importlib.import_module`).

    NOT SEEN, stated so it is not read as proof: a loader reached some other
    way (`getattr(importlib, name)`, an alias of an alias, a loader passed
    as an argument, `exec` of a built string), and a loader in a file whose
    text names no licensed
    module, which `licensed_set` never reads (a name from config or argv; at
    a97b331, 10 files load by a computed name and name no licensed module).
    A `spec_from_file_location` path is read by its FILE NAME (#47), so a
    licensed parser copied under another name is not seen here either:
    `publish_guard.py`'s L2 (byte-identical blobs) and L3 (licensed-only
    names) hold that. The install's seam checks run the public commands
    against P; they are what covers a load this cannot see."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    # A loader rebound to another name, followed one level (b5 on #37):
    # `from importlib import import_module as load` and `load = importlib.import_module`.
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(
                (a.asname, a.name) for a in node.names if a.asname and a.name in _DYNAMIC_LOADERS
            )
        elif isinstance(node, ast.Assign) and isinstance(node.value, (ast.Attribute, ast.Name)):
            value = node.value
            loader = value.attr if isinstance(value, ast.Attribute) else value.id
            if loader in _DYNAMIC_LOADERS:
                aliases.update((t.id, loader) for t in node.targets if isinstance(t, ast.Name))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        name = aliases.get(name, name) if isinstance(func, ast.Name) else name
        if name not in _DYNAMIC_LOADERS:
            continue
        args = list(node.args)
        package = next((k.value for k in node.keywords if k.arg == "package"), None)
        if package is None and name == "import_module" and len(args) > 1:
            package = args[1]
        first = args[0] if args else None
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            found.add(UNKNOWN_LOAD)
            continue
        # A spec built from a file loads whatever that PATH holds, whatever
        # name it is given (1d on #37): the path is the load.
        if name == "spec_from_file_location":
            location = next((k.value for k in node.keywords if k.arg == "location"), None)
            location = location if location is not None else (args[1] if len(args) > 1 else None)
            if location is not None:
                if not (isinstance(location, ast.Constant) and isinstance(location.value, str)):
                    found.add(UNKNOWN_LOAD)
                    continue
                path = location.value.replace("\\", "/")
                found.add(_licensed_file(path) or f"file:{path}")
                continue
        # `__import__("pkg", fromlist=["m"])` loads pkg.m too (1d on #37).
        if name == "__import__":
            fromlist = next((k.value for k in node.keywords if k.arg == "fromlist"), None)
            fromlist = fromlist if fromlist is not None else (args[3] if len(args) > 3 else None)
            if fromlist is not None:
                items = getattr(fromlist, "elts", None)
                if items is None or not all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str) for e in items
                ):
                    found.add(UNKNOWN_LOAD)
                    continue
                found.update(f"{first.value}.{e.value}" for e in items)
        target = first.value
        if target.startswith("."):
            if not (isinstance(package, ast.Constant) and isinstance(package.value, str)):
                found.add(UNKNOWN_LOAD)
                continue
            dots = len(target) - len(target.lstrip("."))
            parts = package.value.split(".")
            base = parts[: len(parts) - dots + 1] if dots <= len(parts) else []
            target = ".".join([*base, target.lstrip(".")]) if target.lstrip(".") else ".".join(base)
        found.add(target)
    return found


def licensed_set(
    ctx: Context, treeish: str
) -> tuple[list[str], dict[str, str], dict[str, list[str]]]:
    """(paths P must not carry, {refused path: why}, {class: every text hit}).

    Every file whose text names a licensed module is CLASSIFIED, never
    dropped (ba on #37; the charge's conservation shape): `import` (the AST
    has an import of one, or a dynamic loader spells one whole), `dynamic`
    (a dynamic loader's name is known only at run time, b5 on #37), `string`
    (a string literal names one: an import held in a string, which the AST's
    import nodes cannot see), `unparsed` (the file does not parse, so it
    cannot be cleared), or `mention` (prose, comments, docstrings). The
    counts of all five are printed by the build.

    Removed from P: the five modules present in `treeish`, and every TEST
    file in `import`, `dynamic` or `string`; derived here, not hand-kept (9b
    on #33, comment 535). Refused, naming them: any file outside `tests/` in
    `import`, `dynamic`, `string` or `unparsed`, except a `dynamic` file in
    `REVIEWED_DYNAMIC`. Removing it would cut public code, and leaving it
    ships a reference to a module P does not contain."""
    tracked = set(ctx.git("ls-tree", "-r", "--name-only", treeish).splitlines())
    removed = sorted(p for p in LICENSED_PATHS if p in tracked)
    # Module names as Python spells them (imports are case-sensitive, even on
    # Windows), plus a module's FILE NAME in any case (`<m>.`: `.py`, `.pyc`,
    # `.py.`, `.pyd`, `.so`), because Windows opens `INGEST/<M>.PY` as the same
    # file (b5 on #57), and a debug build's extension module `<m>_d.` (#59).
    # Not `-i` on the names: that made a class like `<M>BuildError` a
    # candidate, and a public test whose census loads by computed name left P
    # with it.
    hits = ctx.git(
        "grep", "-l", "-F", *(f"-e{m}" for m in LICENSED_MODULES), treeish, "--", "*.py"
    ).splitlines()
    file_names = [f"-e{m}{end}" for m in LICENSED_MODULES for end in (".", "_d.")]
    hits += ctx.git("grep", "-l", "-F", "-i", *file_names, treeish, "--", "*.py").splitlines()
    candidates = sorted({h.split(":", 1)[1] for h in hits if ":" in h} - set(removed))
    classes: dict[str, list[str]] = {
        "import": [],
        "dynamic": [],
        "string": [],
        "unparsed": [],
        "mention": [],
    }
    for path in candidates:
        source = ctx.git("show", f"{treeish}:{path}")
        strings = string_refs_of(source)
        loads = dynamic_loads_of(source)
        if strings is None:
            kind = "unparsed"
        elif (imports_of(source, path) | loads) & LICENSED_DOTTED:
            kind = "import"
        elif UNKNOWN_LOAD in loads:
            kind = "dynamic"
        elif strings:
            kind = "string"
        else:
            kind = "mention"
        classes[kind].append(path)
    blocking = ("import", "dynamic", "string", "unparsed")
    tests = [p for kind in blocking for p in classes[kind] if p.startswith("tests/")]
    refused = {
        p: kind
        for kind in blocking
        for p in classes[kind]
        if not p.startswith("tests/") and not (kind == "dynamic" and p in REVIEWED_DYNAMIC)
    }
    return sorted(removed + tests), dict(sorted(refused.items())), classes


def github_main(ctx: Context) -> str:
    """GitHub `main`'s commit, asked of the server, or "" if it did not answer."""
    out = ctx.run(["git", "ls-remote", GITHUB_URL, "refs/heads/main"]).stdout or ""
    first = out.split()
    return first[0] if first and len(first[0]) == 40 else ""


def _github_ref(ctx: Context, ref: str) -> str:
    out = (ctx.run(["git", "ls-remote", GITHUB_URL, ref]).stdout or "").split()
    return out[0] if out else ""


def build_public(ctx: Context) -> tuple[str, list[str]]:
    """Build P and return (P, lines). P is "" when it cannot be built; the
    lines say why. Deterministic: the same cut and the same G give the same P,
    because P's bytes are written here from fixed inputs and stored with
    `hash-object`. No clock, no environment. The script exits at the user's
    approval and is re-run, and a P that changed across that pause would be
    a commit nobody installed (9b's plan, #33 comment 587)."""
    cut = ctx.git("rev-parse", "HEAD")
    base = github_main(ctx)
    lines = [f"cut commit {cut[:12] or '-'}; GitHub main (G) {base[:12] or 'no answer'}"]
    if not (cut and base):
        return "", lines
    ctx.run(["git", "-C", str(ctx.root), "fetch", "-q", GITHUB_URL, "refs/heads/main"])
    if ctx.git("rev-parse", "FETCH_HEAD") != base:
        return "", [*lines, "G could not be fetched as the commit ls-remote named"]
    removed, refused, classes = licensed_set(ctx, cut)
    lines.append(f"licensed set L ({len(removed)}): {', '.join(removed) or 'none'}")
    lines.append(
        "files naming a licensed module, by class: "
        + ", ".join(f"{kind} {len(paths)}" for kind, paths in classes.items())
    )
    lines += [
        f"dynamic, reviewed: {path} ({REVIEWED_DYNAMIC[path]})"
        for path in classes["dynamic"]
        if path in REVIEWED_DYNAMIC
    ]
    if refused:
        return "", [
            *lines,
            "REFUSED: public code still references a licensed module (the plugin seam, #32, "
            "is not in this cut): "
            + ", ".join(f"{path} ({kind})" for path, kind in refused.items()),
        ]
    # A throwaway worktree for its own index: read the cut's tree, drop L,
    # write the tree. Nothing is checked out and nothing is committed here.
    work = Path(tempfile.mkdtemp()) / "public"
    ctx.git("worktree", "add", "-q", "--no-checkout", "--detach", str(work), cut)
    try:
        ctx.run(["git", "-C", str(work), "read-tree", cut])
        if removed:
            ctx.run(["git", "-C", str(work), "rm", "-q", "--cached", "--", *removed])
        tree = (ctx.run(["git", "-C", str(work), "write-tree"]).stdout or "").strip()
    finally:
        ctx.git("worktree", "remove", "--force", str(work))
    name, email = ctx.git("config", "user.name"), ctx.git("config", "user.email")
    when = ctx.git("show", "-s", "--format=%ct %cI", cut).split()
    if not (tree and name and email and len(when) == 2):
        return "", [
            *lines,
            f"cannot write P: tree {tree or '-'}, identity {name or '-'} "
            f"<{email or '-'}>, date {' '.join(when) or '-'}",
        ]
    epoch, iso = when
    offset = "+0000" if iso.endswith("Z") else iso[-6:].replace(":", "")
    who = f"{name} <{email}> {epoch} {offset}"
    body = (
        f"tree {tree}\nparent {base}\nauthor {who}\ncommitter {who}\n\n"
        f"Release {ctx.version}\n\nThe public tree of {ctx.version}. The licensed-framework "
        "parsers are not part of the public tool (from 1.7, #32).\n"
    )
    raw = Path(tempfile.mkdtemp()) / "commit"
    raw.write_bytes(body.encode("utf-8"))
    commit = ctx.git("hash-object", "-t", "commit", "-w", str(raw))
    lines.append(f"P {commit[:12] or '-'}: tree {tree[:12]}, parent {base[:12]}")
    return commit, lines


def guard_argv(ctx: Context, script: Path, public: str, base: str) -> list[str]:
    """1d's interface (#35): named, never positional. The source is the cut
    commit, whose HISTORY carries the licensed modules even after #32 takes
    them out of its tree. (A positional `P G` was this PR's first version:
    argparse would have exited 2 and refused every build; 9b on #35.)"""
    return [
        sys.executable,
        str(script),
        "--candidate",
        public,
        "--base",
        base,
        "--source",
        ctx.git("rev-parse", "HEAD"),
        "--root",
        str(ctx.root),
    ]


def _guard(ctx: Context, public: str, base: str) -> Check:
    """1d's guard on P, by EXIT CODE alone (1d, #33 comment 584). A missing
    guard is a refusal: until it exists nothing may leave."""
    script = ctx.root / "scripts" / "publish_guard.py"
    if not script.exists():
        return Check(False, ["publish_guard.py is not in this tree: nothing may be published"])
    code = ctx.run(guard_argv(ctx, script, public, base)).returncode
    return Check(
        code == 0, [f"publish_guard.py {public[:12]} {base[:12]}: exit {code} (must be 0)"]
    )


def _public_release_allowed(ctx: Context) -> Check:
    """The user's cadence (2026-09-28, to 80; #33, CLAUDE.md): every MINOR
    release (X.Y.0) goes public as a filtered snapshot; PATCH releases
    (X.Y.Z, Z > 0) stay private on Forgejo. So nothing of a patch release
    reaches GitHub, at any step. No override: publishing a patch is exactly
    what the decision rules out, and a switch for it is the user's to add."""
    patch = ctx.version.split(".")[2] if ctx.version.count(".") == 2 else "?"
    ok = patch == "0"
    return Check(
        ok,
        [
            f"{ctx.version} is a {'minor' if ok else 'PATCH'} release: "
            + (
                "published to GitHub as a filtered snapshot"
                if ok
                else "patch releases stay private on Forgejo (the user's cadence); "
                "nothing goes to GitHub"
            )
        ],
    )


def _public_built(ctx: Context) -> Check:
    """P builds from the cut, and 1d's guard passes it. Re-derived on every
    run; P's determinism is what makes that the same P as before. Never for
    a patch release, which stays private."""
    allowed = _public_release_allowed(ctx)
    if not allowed.ok:
        return allowed
    public, lines = build_public(ctx)
    if not public:
        return Check(False, lines)
    base = github_main(ctx)
    guard = _guard(ctx, public, base)
    if guard.ok:
        ctx.notes["public_sha"], ctx.notes["public_base"] = public, base
    return Check(guard.ok, [*lines, *guard.measured])


def _push(ctx: Context, refspec: str, *extra: str) -> bool:
    """One explicit refspec to GitHub by URL, printed first with what it sends."""
    public = ctx.notes.get("public_sha", "")
    base = ctx.notes.get("public_base", "")
    count = len(ctx.git("rev-list", "--objects", f"{base}..{public}").splitlines())
    print(f"    push {GITHUB_URL} {refspec} {' '.join(extra)}".rstrip())
    print(
        f"      objects not already on GitHub: {count} "
        f"(rev-list --objects {base[:12]}..{public[:12]})"
    )
    return (
        ctx.run(["git", "-C", str(ctx.root), "push", *extra, GITHUB_URL, refspec]).returncode == 0
    )


def _candidate_pushed(ctx: Context) -> Check:
    public = ctx.notes.get("public_sha", "")
    there = _github_ref(ctx, candidate_ref(ctx.version))
    return Check(
        bool(public) and there == public,
        [
            f"GitHub {candidate_ref(ctx.version)} -> {there[:12] or 'absent'} "
            f"(must be P {public[:12] or '?'})"
        ],
    )


def _candidate_ready(ctx: Context) -> Check:
    allowed = _public_release_allowed(ctx)
    if not allowed.ok:
        return allowed
    base, now = ctx.notes.get("public_base", ""), github_main(ctx)
    return Check(
        bool(ctx.notes.get("public_sha")) and bool(base) and now == base,
        [
            f"P built this run: {bool(ctx.notes.get('public_sha'))}; "
            f"GitHub main still G: {now == base}"
        ],
    )


def _push_candidate(ctx: Context) -> None:
    _push(ctx, f"{ctx.notes['public_sha']}:{candidate_ref(ctx.version)}")


def _refused_first(check: Callable[[Context], Check]) -> Callable[[Context], Check]:
    """A gate that refuses a patch release BEFORE anything else runs.
    `_all(...)` evaluates every check it is given before combining them, so
    a cadence refusal passed into it would still query Forgejo and GitHub
    (found by this PR's own test: the Forgejo client was called)."""

    def gate(ctx: Context) -> Check:
        allowed = _public_release_allowed(ctx)
        return allowed if not allowed.ok else check(ctx)

    return gate


def _public_main_ready(ctx: Context) -> Check:
    return _refused_first(
        lambda c: _all(_main_merged(c), _candidate_ready(c), _candidate_pushed(c))
    )(ctx)


def _public_main_done(ctx: Context) -> Check:
    public = ctx.notes.get("public_sha", "")
    main, candidate = github_main(ctx), _github_ref(ctx, candidate_ref(ctx.version))
    return Check(
        bool(public) and main == public and not candidate,
        [
            f"GitHub main -> {main[:12] or '-'} (must be P {public[:12] or '?'})",
            f"GitHub {candidate_ref(ctx.version)}: {'still there' if candidate else 'deleted'}",
        ],
    )


def _push_public_main(ctx: Context) -> None:
    """A plain push, NO force of any kind. P's parent is G, so while GitHub
    main is still G this is a fast-forward and needs none; if main moved,
    git refuses the non-fast-forward itself. (The plan said
    `--force-with-lease`: that only adds a force capability this step never
    needs, so it is gone. A test adds `--force` and must go red.)"""
    if _push(ctx, f"{ctx.notes['public_sha']}:refs/heads/main"):
        _push(ctx, f":{candidate_ref(ctx.version)}")


def _released_commit(ctx: Context) -> str:
    """What the tag names: P on the public Forgejo path, main's merge on
    GitHub's and on a local release (#90, which builds no P)."""
    public = ctx.forge == "forgejo" and not _is_local(ctx)
    return ctx.notes.get("public_sha" if public else "main_sha", "")


def _installed_commit(ctx: Context) -> str:
    """What the approval install installs: P, the public tree, on the public
    Forgejo path (#33: an install of the cut would pass on a tree the public
    never gets); the cut commit on GitHub's, and on a local release (#90),
    whose released tree IS the cut: Forgejo main gets exactly it."""
    if ctx.forge == "forgejo" and not _is_local(ctx):
        return ctx.notes.get("public_sha", "")
    return ctx.git("rev-parse", "HEAD")


def _head_archive(ctx: Context) -> str:
    """The archive the approval install fetches: P's on the Forgejo path
    (served from the release-candidate branch), the cut commit's otherwise."""
    sha = _installed_commit(ctx)
    return f"https://github.com/{release_check.REPOSITORY}/archive/{sha}.tar.gz"


#: The install record, a tracking-issue comment's LAST line, as the other
#: artefact gates' records are: which commit was installed, for which
#: release, and how it went. Persistent because the script exits at the
#: user's approval (5) and is re-run after it, and the tag needs to know.
INSTALL_LINE = re.compile(
    r"^Release-install: ([0-9a-f]{40}) release=(\S+) result=(passed|FAILED|did-not-run|refused)\s*$"
)


def _install_record(ctx: Context) -> tuple[str, str]:
    """(sha, result) of the LATEST install record for this release, or ("", "")."""
    found = [
        (created, position, match.group(1), match.group(3))
        for created, position, line in _tracking_records(ctx)
        if (match := INSTALL_LINE.match(line)) and match.group(2) == ctx.version
    ]
    if not found:
        return "", ""
    _, _, sha, result = max(found)
    return sha, result


def _head_installed(ctx: Context) -> Check:
    """The release PR's head passed a clean-container install, and is still
    the head (#381, the user's "Install first").

    **The installed SHA comes from the install record, never from a fetch
    at the moment of checking** (the charge's --match-head-commit rule): it
    is compared with the PR's head as it is now and with the cut commit
    this script made. If the head moved after the install, the install is
    of a commit nobody is being asked to approve, so it is STALE.
    """
    if not ctx.tracking:
        return Check(False, ["no --tracking-issue given, so there is no install record to read"])
    sha, result = _install_record(ctx)
    pr = _release_pr(ctx)
    head = pr.get("headRefOid", "") if pr else ""
    cut = ctx.git("rev-parse", "HEAD")
    measured = [
        f"install record for {ctx.version} on #{ctx.tracking}: "
        f"{sha[:12] or 'none'} {result or ''}".rstrip(),
        f"release PR head now {head[:12] or '-'}, cut commit {cut[:12] or '-'}",
    ]
    if sha and head and sha != head:
        measured.append(
            f"STALE: the install was of {sha[:12]}, and the PR head is now {head[:12]}; "
            "whatever moved the head must be installed before anyone is asked to approve it"
        )
    measured += _ignored(ctx, INSTALL_LINE)
    if ctx.forge == "forgejo" and not _is_local(ctx):
        # The record names P; P is re-derived from the cut this run, so a
        # cut that moved gives a different P and the record goes stale.
        public = ctx.notes.get("public_sha", "")
        measured.append(f"public commit P this run {public[:12] or '-'} (the record must name it)")
        return Check(bool(sha) and result == "passed" and sha == public and head == cut, measured)
    return Check(bool(sha) and result == "passed" and sha == head == cut, measured)


# --- GitHub's results, read through ops' ci-read (#326, #398) ------------------------

#: The private test repo whose release/X.Y.Z carries a candidate to GitHub's
#: Actions, and ops' ci-read workflow, the only holder of the user's read token.
CI_REPO = "policyforge/policyforge-ci"
CI_URL = f"{FORGEJO_URL}/{CI_REPO}.git"
OPS_REPO = "policyforge/ops"
CI_READ_WORKFLOW = "ci-read.yml"

_CI_RUN = re.compile(
    r"^## (?P<name>.+?) #\d+\s+(?P<branch>\S+) (?P<sha>[0-9a-f]{7,40})\s+"
    r"(?P<status>[^/\s]+)/(?P<conclusion>\S+)"
)
_CI_JOB = re.compile(r"^- (?P<mark>[^:]+): (?P<name>.+)$")
#: Forgejo's job log starts each line with the runner's timestamp.
_LOG_STAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")


def ci_run_for(summary: str, branch: str, candidate: str, workflow: str = "ci") -> dict | None:
    """The newest run of `workflow` in ci-read's summary on `branch` at
    `candidate`, as {status, conclusion, jobs: [(mark, name)]}, or None.
    ci-read prints runs newest first and each run's sha as 8 hex digits, so a
    run matches on the branch, that prefix of the candidate AND the workflow's
    name: one push starts several workflows at the same sha (`macos-wheels`,
    `single-commit export`, `ci`), and only `ci` carries the pytest legs."""
    run = None
    for line in summary.splitlines():
        line = _LOG_STAMP.sub("", line.rstrip())
        header = _CI_RUN.match(line)
        if header:
            if run is not None:
                return run
            if (
                header["name"] == workflow
                and header["branch"] == branch
                and candidate.startswith(header["sha"])
            ):
                run = {"status": header["status"], "conclusion": header["conclusion"], "jobs": []}
            continue
        job = _CI_JOB.match(line)
        if run is not None and job:
            run["jobs"].append((job["mark"].strip(), job["name"].strip()))
    return run


def read_ci_summary(forgejo: Callable, sleep: Callable[[float], None] = time.sleep) -> str | None:
    """Dispatch ops' ci-read, wait for it, and return its job log: the
    summary it prints. None when any of that fails, which a caller reads as
    "not yet", never as a result."""
    status, body, _ = forgejo(
        "POST",
        f"/repos/{OPS_REPO}/actions/workflows/{CI_READ_WORKFLOW}/dispatches",
        {"ref": "main", "inputs": {"runs": "10"}, "return_run_info": True},
    )
    run_id = body.get("id") if status == 201 and isinstance(body, dict) else None
    if not run_id:
        return None
    for _ in range(40):
        status, run, _ = forgejo("GET", f"/repos/{OPS_REPO}/actions/runs/{run_id}", None)
        if (
            status == 200
            and isinstance(run, dict)
            and run.get("status") not in (None, "", "waiting", "running", "blocked")
        ):
            break
        sleep(15)
    else:
        return None
    status, jobs, _ = forgejo("GET", f"/repos/{OPS_REPO}/actions/runs/{run_id}/jobs", None)
    jobs = jobs.get("jobs") if isinstance(jobs, dict) else jobs
    if status != 200 or not jobs:
        return None
    status, text, _ = _real_forgejo(
        "GET", f"/repos/{OPS_REPO}/actions/jobs/{jobs[0]['id']}/logs", None, text=True
    )
    return text if status == 200 and isinstance(text, str) else None


# --- the full suite on the Pythons nothing else tests, before approval (#242) ---------

#: `requires-python` claims >=3.10, and only 3.12 (CI) and 3.14 (the local
#: gates) are tested per change. So each release runs the FULL suite on the
#: rest once, before the user is asked to approve main (80's ruling 3955).
CUT_PYTHONS = ("3.10", "3.11", "3.13")
#: The extras CI's lock installs (requirements/ci.txt's header; a test holds
#: them equal), installed here FRESH and unhashed: what a user `pip install`ing
#: on that version actually gets (80's ruling 3961, (A)).
CUT_EXTRAS = "dev,mcp,litellm,bedrock,vertex"
PYTHON_RESULTS = ("passed", "FAILED", "install-failed", "did-not-run")
PYTHON_LINE = re.compile(
    r"^Release-python: ([0-9a-f]{40}) release=(\S+) python=(3\.\d+) "
    r"result=(passed|FAILED|install-failed|did-not-run)(?: candidate=([0-9a-f]{40}))?\s*$"
)
#: A red version is a release blocker unless the USER acknowledges it at the
#: cut (3955), on the tracking issue, from their own login only.
ACKNOWLEDGER = "rdazzleman"
PYTHON_ACK_LINE = re.compile(
    r"^Release-python-acknowledged: ([0-9a-f]{40}) release=(\S+) python=(3\.\d+)\s*$"
)


def _python_results(ctx: Context, sha: str) -> dict[str, str]:
    """{version: the latest recorded result} for this release at `sha`."""
    results: dict[str, str] = {}
    for _, _, line in sorted(_tracking_records(ctx)):
        found = PYTHON_LINE.match(line)
        if found and found[2] == ctx.version and found[1] == sha:
            results[found[3]] = found[4]
    return results


def _python_candidates(ctx: Context, sha: str) -> dict[str, tuple[str, str]]:
    """{version: (latest result, the candidate it was read from)} at `sha`; the
    candidate is "" for a record from a local run."""
    latest: dict[str, tuple[str, str]] = {}
    for _, _, line in sorted(_tracking_records(ctx)):
        found = PYTHON_LINE.match(line)
        if found and found[2] == ctx.version and found[1] == sha:
            latest[found[3]] = (found[4], found[5] or "")
    return latest


def _pythons_tested(ctx: Context) -> Check:
    """Every version in CUT_PYTHONS has a record for the approval commit,
    and each passed or was acknowledged by the user. A record for another
    commit is stale: the head moved, so it says nothing about this one."""
    sha = _installed_commit(ctx)
    results = _python_results(ctx, sha) if sha else {}
    acked = {
        found[3]
        for _, _, line in _tracking_records(ctx, only=frozenset({ACKNOWLEDGER}))
        if (found := PYTHON_ACK_LINE.match(line)) and found[2] == ctx.version and found[1] == sha
    }
    ok, measured = bool(sha), []
    # 3.12 and 3.14 have no local run, so a version outside CUT_PYTHONS counts
    # only when GitHub's leg for it was red and recorded (80's ruling on 398).
    extra = [v for v in LEG_PYTHONS if v not in CUT_PYTHONS and v in results]
    for version in (*CUT_PYTHONS, *extra):
        result = results.get(version, "")
        acknowledged = bool(result) and result != "passed" and version in acked
        ok = ok and (result == "passed" or acknowledged)
        measured.append(
            f"Python {version} on {sha[:12] or '?'}: {result or 'no record'}"
            + (" (acknowledged by the user)" if acknowledged else "")
        )
    return Check(ok, measured)


#: Every Python `requires-python` claims has one pytest leg on GitHub's CI. The
#: cut reads all five (#398): the three it would otherwise run locally, and the
#: two (3.12, 3.14) that are only there to show the set is whole and green.
LEG_PYTHONS = ("3.10", "3.11", "3.12", "3.13", "3.14")


def _leg_job(version: str) -> str:
    return f"pytest (ubuntu-latest, {version})"


def _github_leg_results(ctx: Context, sha: str) -> tuple[dict[str, str], list[str], str]:
    """({version: "passed" or "FAILED"}, lines, candidate) from GitHub's legs.

    EMPTY, never a pass, unless GitHub's run on a candidate holding this
    commit's tree has settled all five legs, so every version runs locally
    when a leg is missing, skipped, still running, or the run unreadable. A red
    leg is different (80's ruling on 398): it was measured on the candidate's
    own commit and tree, and the local runs never re-test 3.12 or 3.14, so a
    local green would stand in for a red it did not measure. A red leg among
    the five, 3.12 and 3.14 included, is a FAILED result for that version and
    blocks, with no local run."""
    if ctx.ci_legs is None:
        return {}, [], ""
    jobs, lines, candidate = ctx.ci_legs(ctx, sha)
    if not jobs:
        return {}, [*lines, "falling back to the local runs on every version"], ""
    marks = {v: jobs.get(_leg_job(v), "missing") for v in LEG_PYTHONS}
    shown = [*lines, *(f"  {_leg_job(v)}: {marks[v]}" for v in LEG_PYTHONS)]
    red = [v for v in LEG_PYTHONS if marks[v] == "FAIL"]
    if red:
        results = dict.fromkeys(red, "FAILED")
        results.update({v: "passed" for v in LEG_PYTHONS if marks[v] == "success"})
        way_out = (
            f"BLOCKED: Python {', '.join(red)} red on GitHub's candidate {candidate[:12]}; "
            "nothing runs locally. The way out: a new candidate run on GitHub where "
            "every leg passes, or the user's acknowledgement on the tracking issue, "
            f"`Release-python-acknowledged: {sha} release={ctx.version} python=<version>`."
        )
        return results, [*shown, way_out], candidate
    unsettled = [v for v in LEG_PYTHONS if marks[v] != "success"]
    if unsettled:
        why = f"the legs can't stand in for the local runs (not settled green: {unsettled})"
        return {}, [*shown, f"{why}: running locally"], ""
    return dict.fromkeys(LEG_PYTHONS, "passed"), shown, candidate


def _real_ci_legs(ctx: Context, sha: str) -> tuple[dict[str, str], list[str], str]:
    """GitHub's jobs for the run on policyforge-ci's release/X.Y.Z, only when
    that candidate's TREE is `sha`'s and the run is on the candidate's own
    commit (ci-read prints each run's sha), with that candidate's commit.
    {} with the reason otherwise."""
    branch = f"release/{ctx.version}"
    ref = f"refs/heads/{branch}"
    tree = ctx.git("rev-parse", f"{sha}^{{tree}}")
    found = (ctx.run(["git", "-C", str(ctx.root), "ls-remote", CI_URL, ref]).stdout or "").split()
    tip = found[0] if found else ""
    if not tree:
        return {}, [f"no tree for the approval commit {sha[:12]}"], ""
    if not tip:
        return {}, [f"{CI_REPO} {branch} is absent: no candidate"], ""
    ctx.run(["git", "-C", str(ctx.root), "fetch", "-q", CI_URL, ref])
    held = ctx.git("rev-parse", f"{tip}^{{tree}}")
    if held != tree:
        return (
            {},
            [
                f"candidate {tip[:12]} holds tree {held[:12] or '?'}, not the approval "
                f"commit's {tree[:12]}"
            ],
            "",
        )
    summary = read_ci_summary(ctx.forgejo)
    if summary is None:
        return {}, ["ci-read could not be read this time"], ""
    run = ci_run_for(summary, branch, tip)
    if run is None:
        return {}, [f"no GitHub run on candidate {tip[:12]} yet"], ""
    if run["status"] != "completed":
        return {}, [f"GitHub's run on candidate {tip[:12]} is {run['status']}, not completed"], ""
    said = f"GitHub's run on candidate {tip[:12]} (tree {tree[:12]}): completed/{run['conclusion']}"
    return {name: mark for mark, name in run["jobs"]}, [said], tip


def _run_pythons(ctx: Context) -> None:
    """Run the full suite on each version without a passed record for the
    approval commit, and post one record per version on the tracking issue.
    The tree goes in as the host's own archive of that commit."""
    sha = _installed_commit(ctx)
    if not sha:
        return
    work = Path(tempfile.mkdtemp())
    tar = work / "tree.tar"
    ctx.run(
        [
            "git",
            "-C",
            str(ctx.root),
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "archive",
            "--format=tar",
            "-o",
            str(tar),
            sha,
        ]
    )
    done = _python_results(ctx, sha)
    recorded = _python_candidates(ctx, sha)
    github, source, candidate = _github_leg_results(ctx, sha)
    for line in source:
        print(f"      {line}")
    for version in (
        *CUT_PYTHONS,
        *(v for v in LEG_PYTHONS if v in github and v not in CUT_PYTHONS),
    ):
        if version in github:
            result = github[version]
            # A re-run of the step on the same candidate adds nothing: the
            # record is already there, and a duplicate FAILED would read as a
            # second failure (b5 on 398).
            # 3.12 and 3.14 are recorded only to supersede an earlier red one.
            only_to_supersede = version not in CUT_PYTHONS and version not in done
            if recorded.get(version) == (result, candidate) or (
                result == "passed" and (done.get(version) == "passed" or only_to_supersede)
            ):
                continue
            shown = "\n".join(f"    {line}" for line in source)
            _post_tracking(
                ctx,
                f"The full suite on Python {version}, read from GitHub's "
                f"`{_leg_job(version)}` leg over a candidate holding the {ctx.version} approval "
                f"commit `{sha}`'s tree, by `scripts/release.py`: **{result}**.\n\n"
                f"{shown}\n\n"
                f"Release-python: {sha} release={ctx.version} python={version} "
                f"result={result} candidate={candidate}",
            )
            continue
        if done.get(version) == "passed" or "FAILED" in github.values():
            continue
        result, lines = ctx.run_python(version, tar)
        result = result if result in PYTHON_RESULTS else "FAILED"
        shown = "\n".join(f"    {line}" for line in lines[-12:])
        _post_tracking(
            ctx,
            f"The full suite on Python {version} (`python:{version}-slim`, a fresh unhashed "
            f"install of `.[{CUT_EXTRAS}]`) over the {ctx.version} approval commit `{sha}`, "
            f"by `scripts/release.py`: **{result}**.\n\n{shown}\n\n"
            f"Release-python: {sha} release={ctx.version} python={version} result={result}",
        )


def _post_tracking(ctx: Context, body: str) -> bool:
    """Post `body` on the tracking issue, on whichever forge the run uses."""
    if ctx.forge == "forgejo":
        status, _, _ = ctx.forgejo(
            "POST", f"/repos/{FORGEJO_REPO}/issues/{ctx.tracking}/comments", {"body": body}
        )
        return status == 201
    return (
        ctx.run(
            [
                "gh",
                "api",
                f"repos/{release_check.REPOSITORY}/issues/{ctx.tracking}/comments",
                "-f",
                f"body={body}",
            ]
        ).returncode
        == 0
    )


#: The in-container run. Setup failing (exit 4) is not a test result:
#: did-not-run. The install failing (exit 3) is a finding in itself: a user on
#: that version can't install the release. Any pytest failure exits 1.
_PYTHON_SCRIPT = "\n".join(
    [
        "set -u",
        "exec 2>&1",
        "export DEBIAN_FRONTEND=noninteractive PIP_ROOT_USER_ACTION=ignore",
        'setup() { echo "cut-python: setup failed: $1"; exit 4; }',
        "apt-get update -qq > /dev/null && apt-get install -y -qq --no-install-recommends git "
        "> /dev/null || setup apt",
        "mkdir /work && tar -xf /in/tree.tar -C /work && cd /work || setup tree",
        "git init -q && git add -A && git -c user.name=cut -c user.email=cut@example.invalid "
        "commit -qm 'the tree under test' || setup git",
        f'python -m pip install -e ".[{CUT_EXTRAS}]" > /tmp/pip.log 2>&1 '
        '|| { tail -8 /tmp/pip.log; echo "cut-python: the fresh install failed"; exit 3; }',
        "python --version",
        "python -m pytest -q -rs -n auto -p no:cacheprovider; t=$?",
        'echo "cut-python: pytest exit $t"',
        '[ "$t" = 0 ] || exit 1',
    ]
)


def _python_docker_argv(work: Path, version: str) -> list[str]:
    """The tree's directory read-only at /in and the script, nothing else: no
    `-e`, `--env` or `--env-file`, so no host variable (FORGEJO_TOKEN
    included) reaches the container, as `_docker_argv`."""
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{work}:/in:ro",
        f"python:{version}-slim",
        "bash",
        "-c",
        _PYTHON_SCRIPT,
    ]


def _real_run_python(version: str, tar: Path) -> tuple[str, list[str]]:
    import shutil

    if shutil.which("docker") is None:
        return "did-not-run", ["docker is not on PATH"]
    proc = _real_run(_python_docker_argv(tar.parent, version))
    lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-15:]
    lines.append(f"docker exit status: {proc.returncode}")
    result = {0: "passed", 1: "FAILED", 3: "install-failed"}.get(proc.returncode, "did-not-run")
    return result, lines


def _install_head(ctx: Context) -> None:
    """Install the approval commit's archive (P on the Forgejo path), then post
    the record on the tracking issue, where the user reads it before
    approving the main merge."""
    sha = _installed_commit(ctx)
    if not sha:
        # No P this run: an install of nothing would post a record naming an
        # empty SHA (measured on this PR's first test run), so nothing runs.
        ctx.notes["head install"] = "refused: no public commit P was built this run"
        return
    if _is_local(ctx):
        url = f"Forgejo's archive of {sha[:12]}, fetched and tree-proved by the host"
        _install_local(ctx, sha, sha, "head install")
    else:
        url = _head_archive(ctx)
        _install(ctx, url, "head install")
    said = ctx.notes.get("head install", "did not run")
    result = {"passed": "passed", "FAILED": "FAILED", "did not run": "did-not-run"}.get(
        said, "refused"
    )
    public = ctx.forge == "forgejo" and not _is_local(ctx)
    body = (
        f"Container install of the {ctx.version} "
        f"{'public release commit' if public else 'release PR head'} `{sha}` "
        f"({url}), by `scripts/release.py`: **{said}**.\n\n"
        f"Release-install: {sha} release={ctx.version} result={result}"
    )
    if ctx.forge == "forgejo":
        status, _, _ = ctx.forgejo(
            "POST", f"/repos/{FORGEJO_REPO}/issues/{ctx.tracking}/comments", {"body": body}
        )
        failed = status != 201
    else:
        failed = (
            ctx.run(
                [
                    "gh",
                    "api",
                    f"repos/{release_check.REPOSITORY}/issues/{ctx.tracking}/comments",
                    "-f",
                    f"body={body}",
                ]
            ).returncode
            != 0
        )
    if failed:
        ctx.notes["head install"] = f"{said}, but the record was not posted to #{ctx.tracking}"


def _tag_archive(ctx: Context) -> str:
    return f"https://github.com/{release_check.REPOSITORY}/archive/refs/tags/{ctx.tag}.tar.gz"


def _install_check(ctx: Context, url: str, note: str) -> Check:
    if ctx.notes.get(note):
        return Check(ctx.notes[note] == "passed", [f"{note}: {ctx.notes[note]}"])
    return Check(False, [f"{note}: not yet run"])


def _all(*checks: Check) -> Check:
    """Several checks as one gate: every value printed, all must hold."""
    return Check(all(c.ok for c in checks), [line for c in checks for line in c.measured])


def _head_install_ready(ctx: Context) -> Check:
    """Before installing: a record to write to, the PR open AT the cut commit
    (not at something pushed after it), and its archive served."""
    pr = _release_pr(ctx)
    head = pr.get("headRefOid", "") if pr else ""
    cut = ctx.git("rev-parse", "HEAD")
    return _all(
        Check(
            bool(ctx.tracking),
            [f"tracking issue: {'#' + str(ctx.tracking) if ctx.tracking else 'none'}"],
        ),
        Check(
            bool(pr) and pr.get("state") == "OPEN" and head == cut,
            [
                f"release PR {'#' + str(pr['number']) if pr else 'none'} head {head[:12] or '-'} "
                f"is the cut commit {cut[:12]}: {head == cut}"
            ],
        ),
        _archive_served(_head_archive)(ctx),
    )


def _install(ctx: Context, url: str, note: str) -> None:
    formula = _formula(ctx, url, ctx.hash_url(url))
    if not release_check.names_canonical_owner(release_check.source_url_in_formula(formula)):
        ctx.notes[note] = "refused: the formula's url does not name the canonical owner"
        return
    if not release_check.names_canonical_homepage(release_check.homepage_in_formula(formula)):
        ctx.notes[note] = "refused: the formula's homepage does not name the canonical owner"
        return
    ran, ok, lines = ctx.install(formula, ctx.version)
    for line in lines[-6:]:
        print(f"      | {line}")
    ctx.notes[note] = "passed" if ran and ok else ("did not run" if not ran else "FAILED")
    if note == "candidate install":
        ctx.notes["candidate_formula"] = formula


def _git_blob_id(data: bytes) -> str:
    """The id git gives `data` as a blob. It IS SHA-1 over this header and the
    bytes, by git's own definition: an identity to compare with `ls-tree`,
    not a security hash, so `usedforsecurity=False`."""
    # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _counted(paths: list[str]) -> str:
    """A count with a sample: "3 differing" once read as three files when it
    was 489 of 680, because only the first three were printed (#158)."""
    return f"{len(paths)} {paths[:3]}" if paths else "none"


def _archive_is_tree(ctx: Context, tarball: Path, commit: str) -> tuple[bool, list[str]]:
    """Whether `tarball` holds exactly `commit`'s tree: every tracked file's git
    blob hash and mode, computed from the tar's own bytes, against
    `git ls-tree -r`, and no file the tree doesn't have (#90).

    The proof a local install rests on: the URL it came from is not evidence
    of what it holds. Measured on #90 (1945) against Forgejo's archive of
    a82e211: 699 of 699 files, 0 differing. Directories are the tar's own
    entries and carry nothing, so they are counted, not compared.

    **Every member sits under ONE top-level directory**, the archive's
    prefix, and is compared with its prefix stripped. Stripping a first
    component without checking it would let a second top-level directory
    holding an identical copy pass as "no extra file" (1d on #96); a file at
    the top level, with no prefix, would have been skipped outright. Both are
    refused, and so is a path the tar holds twice (which copy an extractor
    keeps is its choice, not the proof's). `ls-tree -z`, so a path git would
    quote is read as itself (1d)."""
    import tarfile

    listing = ctx.run(["git", "-C", str(ctx.root), "ls-tree", "-r", "-z", commit])
    tree: dict[str, tuple[str, str]] = {}
    for entry in (listing.stdout or "").split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and path:
            tree[path] = (parts[0], parts[2])
    got: dict[str, tuple[str, str]] = {}
    odd: list[str] = []
    prefixes: set[str] = set()
    twice: list[str] = []
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            for member in tar.getmembers():
                top, slash, path = member.name.partition("/")
                prefixes.add(top)
                path = path.rstrip("/")
                if member.isdir():
                    continue
                if not slash or not path:
                    odd.append(member.name)  # a non-directory outside any prefix
                    continue
                if path in got:
                    twice.append(path)
                if member.issym():
                    data, mode = member.linkname.encode(), "120000"
                elif member.isfile():
                    handle = tar.extractfile(member)
                    data = handle.read() if handle else b""
                    mode = "100755" if member.mode & 0o111 else "100644"
                else:
                    odd.append(member.name)
                    continue
                got[path] = (mode, _git_blob_id(data))
    except (OSError, tarfile.TarError) as error:
        return False, [f"the archive could not be read: {error}"]
    missing = sorted(set(tree) - set(got))
    extra = sorted(set(got) - set(tree))
    differ = sorted(p for p in set(tree) & set(got) if tree[p] != got[p])
    one_prefix = len(prefixes) == 1
    ok = bool(tree) and one_prefix and not (missing or extra or differ or odd or twice)
    return ok, [
        f"archive against {commit[:12]}'s tree: {len(got)} file(s) against {len(tree)} "
        f"(must be equal and > 0)",
        f"top-level directories {sorted(prefixes)[:3]} (must be exactly one)",
        f"missing {_counted(missing)}, extra {_counted(extra)}, "
        f"differing {_counted(differ)}, held twice {_counted(twice)}, "
        f"not a file or link under the prefix {_counted(odd)}",
    ]


def _forgejo_archive(ctx: Context, ref: str, commit: str) -> tuple[Path | None, str, list[str]]:
    """Forgejo's archive of `ref`, fetched by the host as the operator's bot,
    proved to be `commit`'s tree: (file, its sha256, measured lines). The
    file is None when either fails, so nothing unproved is installed."""
    dest = Path(tempfile.mkdtemp()) / "source.tar.gz"
    status = ctx.fetch_archive(f"/repos/{FORGEJO_REPO}/archive/{ref}.tar.gz", dest)
    lines = [f"Forgejo archive of {ref}: HTTP {status} (must be 200)"]
    if status != 200 or not dest.is_file():
        return None, "", lines
    ok, proof = _archive_is_tree(ctx, dest, commit)
    lines += proof
    if not ok:
        return None, "", lines
    return dest, hashlib.sha256(dest.read_bytes()).hexdigest(), lines


def _install_local(ctx: Context, ref: str, commit: str, note: str) -> None:
    """A local release's install (#90): the tarball the host proved, handed to
    the container as a read-only file, `url` pointing at it. The formula's
    sha256 is the host's hash of those bytes, and the container checks it
    again itself (Homebrew 4.6.20 only warns on a mismatched file: #90, 1945)."""
    source, digest, lines = _forgejo_archive(ctx, ref, commit)
    for line in lines:
        print(f"      {line}")
    if source is None:
        ctx.notes[note] = f"refused: the Forgejo archive of {ref} is not {commit[:12]}'s tree"
        return
    formula = _formula(ctx, f"file://{LOCAL_SOURCE}", digest)
    if not release_check.names_canonical_homepage(release_check.homepage_in_formula(formula)):
        ctx.notes[note] = "refused: the formula's homepage does not name the canonical owner"
        return
    ran, ok, out = ctx.install(formula, ctx.version, source)
    for line in out[-6:]:
        print(f"      | {line}")
    ctx.notes[note] = "passed" if ran and ok else ("did not run" if not ran else "FAILED")
    if note == "candidate install":
        ctx.notes["candidate_formula"] = formula


def _install_candidate_local(ctx: Context) -> None:
    """The second install of a local release: the TAG's Forgejo archive,
    proved to be the tree of main's merge commit, which the tag must name."""
    _install_local(ctx, ctx.tag, _released_commit(ctx), "candidate install")


def _head_install_ready_local(ctx: Context) -> Check:
    """As `_head_install_ready`, less the GitHub archive probe: a local
    release's archive is fetched and proved at install time. `--formula` is
    required, since without it the base formula is fetched from GitHub."""
    pr = _release_pr(ctx)
    head = pr.get("headRefOid", "") if pr else ""
    cut = ctx.git("rev-parse", "HEAD")
    return _all(
        Check(
            bool(ctx.tracking),
            [f"tracking issue: {'#' + str(ctx.tracking) if ctx.tracking else 'none'}"],
        ),
        Check(
            bool(pr) and pr.get("state") == "OPEN" and head == cut,
            [
                f"release PR {'#' + str(pr['number']) if pr else 'none'} head {head[:12] or '-'} "
                f"is the cut commit {cut[:12]}: {head == cut}"
            ],
        ),
        Check(
            ctx.formula_path is not None,
            [f"--formula: {ctx.formula_path or 'none (a local release needs one)'}"],
        ),
    )


#: A local release's record on the tracking issue (#90): the commit the
#: Forgejo tag names, its tree, and what waits for GitHub. The later public
#: run reads it, and refuses a tag whose tree has moved since.
LOCAL_LINE = re.compile(
    r"^Release-local: ([0-9a-f]{40}) release=(\S+) tree=([0-9a-f]{40}) deferred=(\S+)\s*$"
)


def _local_recorded(ctx: Context) -> Check:
    main = _released_commit(ctx)
    tree = ctx.git("rev-parse", f"{main}^{{tree}}") if main else ""
    found = [
        m
        for _, _, line in _tracking_records(ctx)
        if (m := LOCAL_LINE.match(line)) and m.group(2) == ctx.version
    ]
    ok = any(m.group(1) == main and m.group(3) == tree for m in found)
    return Check(
        ok,
        [
            f"local release record for {ctx.version} on #{ctx.tracking}: "
            f"{', '.join(m.group(1)[:12] for m in found) or 'none'} "
            f"(must name {main[:12] or '?'}, tree {tree[:12] or '?'})",
        ],
    )


def _record_local(ctx: Context) -> None:
    main = _released_commit(ctx)
    tree = ctx.git("rev-parse", f"{main}^{{tree}}")
    body = (
        f"**{ctx.tag} is released locally** by `scripts/release.py --publish local`: "
        f"Forgejo `main` is `{main}`, the tag `{ctx.tag}` on Forgejo names it, and both "
        "installs passed from Forgejo archives the host proved against the tree.\n\n"
        "**Deferred until GitHub is set up, and not done:** "
        + ", ".join(f"`{k}`" for k in DEFERRED_LOCAL)
        + ". The public release starts from this tag's tree; a later public run refuses "
        "if the tag has moved.\n\n"
        # 80's two conditions on #96 (1966).
        f"**Also waiting on the public release (#95):** the docs' install pins name "
        f"`{release_check.REPOSITORY}@{ctx.tag}`, which GitHub doesn't hold until then. "
        "Neither local install used them.\n\n"
        "**What the installs reached:** this script asked GitHub nothing. Homebrew inside "
        "the install container read GitHub and PyPI anonymously, as a user's install "
        "would.\n\n"
        f"Release-local: {main} release={ctx.version} tree={tree} "
        f"deferred={','.join(DEFERRED_LOCAL)}"
    )
    ctx.forgejo("POST", f"/repos/{FORGEJO_REPO}/issues/{ctx.tracking}/comments", {"body": body})


def _local_release_matches(ctx: Context) -> Check:
    """The later public run of a release first published locally (#90)
    publishes the SAME tree: if Forgejo holds this version's tag, its tree
    must be the cut's, and the one the local record names. Holds when there
    is no Forgejo tag (no local release to match)."""
    out = (
        ctx.run(
            ["git", "-C", str(ctx.root), "ls-remote", "--tags", ctx.forgejo_url, f"{ctx.tag}^{{}}"]
        ).stdout
        or ""
    ).split()
    target = out[0] if out else ""
    if not target:
        return Check(True, [f"no {ctx.tag} on Forgejo: no local release to match"])
    ctx.run(["git", "-C", str(ctx.root), "fetch", "-q", ctx.forgejo_url, f"refs/tags/{ctx.tag}"])
    tag_tree = ctx.git("rev-parse", f"{target}^{{tree}}")
    cut_tree = ctx.git("rev-parse", "HEAD^{tree}")
    recorded = [
        m.group(3)
        for _, _, line in _tracking_records(ctx)
        if (m := LOCAL_LINE.match(line)) and m.group(2) == ctx.version
    ]
    ok = bool(tag_tree) and tag_tree == cut_tree and bool(recorded) and recorded[-1] == tag_tree
    return Check(
        ok,
        [
            f"{ctx.tag} on Forgejo names {target[:12]}, tree {tag_tree[:12] or '?'}",
            f"the cut's tree {cut_tree[:12] or '?'} must equal it: {tag_tree == cut_tree}",
            f"the local release record's tree {(recorded[-1][:12] if recorded else 'none')} "
            f"must equal it too",
        ],
    )


def _archive_served(url_of: Callable[[Context], str]) -> Callable[[Context], Check]:
    def gate(ctx: Context) -> Check:
        url = url_of(ctx)
        status = ctx.status_of(url)
        return Check(status == 200, [f"{url} -> HTTP {status} (must be 200)"])

    return gate


def _tagged(ctx: Context) -> Check:
    remote = (
        ctx.run(
            ["git", "-C", str(ctx.root), "ls-remote", "--tags", _tag_remote(ctx), f"{ctx.tag}^{{}}"]
        ).stdout
        or ""
    ).split()
    target = remote[0] if remote else ""
    if _is_local(ctx):
        # A local release has no GitHub Release; that step is deferred (#90).
        main = _released_commit(ctx)
        return Check(
            bool(target) and target == main,
            [
                f"tag {ctx.tag} on Forgejo -> {target[:12] or '-'} (must be main's merge commit "
                f"{main[:12] or '?'})"
            ],
        )
    release = (
        ctx.run(
            [
                "gh",
                "release",
                "view",
                ctx.tag,
                "--repo",
                release_check.REPOSITORY,
                "--json",
                "tagName",
            ]
        ).returncode
        == 0
    )
    main = _released_commit(ctx)
    what = "the public commit P" if ctx.forge == "forgejo" else "main's merge commit"
    return Check(
        bool(target) and target == main and release,
        [
            f"tag {ctx.tag} -> {target[:12] or '-'} (must be {what} {main[:12] or '?'})",
            f"GitHub Release {ctx.tag}: {'exists' if release else 'absent'}",
        ],
    )


def _tag(ctx: Context) -> None:
    main = _released_commit(ctx)
    notes_file = Path(tempfile.mkdtemp()) / "notes.md"
    text = (ctx.root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(rf"^## {re.escape(ctx.version)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    notes_file.write_text(section.group(1).strip() if section else "", encoding="utf-8")
    ctx.run(
        ["git", "-C", str(ctx.root), "tag", "-a", ctx.tag, main, "-m", f"Release {ctx.version}"]
    )
    ctx.run(["git", "-C", str(ctx.root), "push", _tag_remote(ctx), f"refs/tags/{ctx.tag}"])
    if _is_local(ctx):
        return  # the GitHub Release is deferred with the rest of GitHub (#90)
    ctx.run(
        [
            "gh",
            "release",
            "create",
            ctx.tag,
            "--repo",
            release_check.REPOSITORY,
            "--title",
            ctx.tag,
            "--notes-file",
            str(notes_file),
            "--verify-tag",
        ]
    )


def _resources_match_lock(ctx: Context) -> Check:
    """The lock decides (CONTRIBUTING): every formula resource pinned as the lock pins it.

    **Checked before the cut is written, not after the tag** (#389).
    `_formula` rewrites only `url`, `sha256`, `homepage` and `version`, so
    every install this script runs uses the base formula's resource pins.
    Checked after the tag, a runtime-dependency change would have been
    installed against the previous release's pins, approved, and tagged
    before anything said so. It sits before `changelog`, so a mismatch
    refuses before the release PR exists (policyforge-ba on #419).

    What is read is the base formula, `--formula` if given, else the
    published one (`_base_formula`), or the candidate once one exists,
    which carries the same pins. The output names which."""
    formula = ctx.notes.get("candidate_formula") or _base_formula(ctx)
    source = (
        "the candidate formula"
        if ctx.notes.get("candidate_formula")
        else (f"--formula {ctx.formula_path}" if ctx.formula_path else "the published formula")
    )
    have = release_check.formula_resources(formula)
    pins = release_check.lock_pins((ctx.root / release_check.LOCK).read_text(encoding="utf-8"))
    mismatched = sorted(n for n in set(have) & set(pins) if have[n] != pins[n])
    unlocked = sorted(set(have) - set(pins))
    return Check(
        bool(have) and not mismatched and not unlocked,
        [
            f"formula resources {len(have)} (must be > 0), lock pins {len(pins)}",
            f"read from {source}",
            f"version mismatches with {release_check.LOCK}: {mismatched or 'none'}",
            f"in the formula and in no lock: {unlocked or 'none'}",
            *(
                []
                if not (mismatched or unlocked)
                else [
                    "regenerate the formula's resource stanzas from the lock before cutting: "
                    "the install would otherwise test pins nobody is releasing (#389). "
                    "Run `brew update-python-resources`, reconcile every resource to the "
                    "lock (CONTRIBUTING), and pass that file with --formula PATH"
                ]
            ),
        ],
    )


TAP_URL = f"https://github.com/{TAP_REPOSITORY}.git"


def _published(ctx: Context) -> Check:
    """Did the push land: the tap's main, asked of the server, holds the candidate.

    Read through the contents API at the tip `ls-remote` names, never
    raw.githubusercontent.com: in the 1.6.1 cut raw served the 1.6.0 file
    for about 300 seconds after a correct push, and this postcondition
    failed on it (#416). Raw is the route a user's `brew` takes, so it
    belongs to step 14, which asks that question."""
    formula = ctx.notes.get("candidate_formula", "")
    if not formula:
        return Check(False, ["no candidate formula has passed its install in this run"])
    lines = []
    refused = ctx.notes.get("publish", "")
    if refused:
        lines.append(f"publish: {refused}")
    tip = _remote_tip(ctx, TAP_URL, "main")
    if not tip:
        return Check(False, [*lines, f"{TAP_REPOSITORY} main: no answer from ls-remote"])
    lines.append(f"{TAP_REPOSITORY} main is {tip[:12]} (ls-remote)")
    pushed = ctx.notes.get("tap_sha", "")
    if pushed:
        lines.append(f"that is the commit this run pushed ({pushed[:12]}): {tip == pushed}")
    read = ctx.run(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.raw",
            f"repos/{TAP_REPOSITORY}/contents/{TAP_FORMULA_PATH}?ref={tip}",
        ]
    )
    if read.returncode != 0:
        return Check(False, [*lines, f"contents API at {tip[:12]}: exit {read.returncode}"])
    live = read.stdout or ""
    same = hashlib.sha256(live.encode()).hexdigest() == hashlib.sha256(formula.encode()).hexdigest()
    lines.append(f"formula at {tip[:12]} == the candidate that passed its install: {same}")
    return Check(same and not refused, lines)


def _publish(ctx: Context) -> None:
    """Clone the tap, commit the candidate, push; stop at the first failure.

    In the 1.6.1 cut `git commit` exited 128 (no identity on the machine),
    `git push` then said "Everything up-to-date" and exited 0, and nothing
    here read either status (#407). Every exit code is now read, the
    refusal is left in `notes` for the postcondition to print, and the
    commit takes its identity from this repository's git config, passed to
    that one process, rather than from whatever the machine has."""
    formula = ctx.notes["candidate_formula"]
    ctx.notes.pop("publish", None)
    name, email = ctx.git("config", "user.name"), ctx.git("config", "user.email")
    if not (name and email):
        ctx.notes["publish"] = (
            f"refused: no user.name/user.email in {ctx.root}'s git config to commit "
            "the tap as; nothing was cloned or pushed"
        )
        return
    work = Path(tempfile.mkdtemp())

    def ran(argv: list[str]) -> bool:
        done = ctx.run(argv)
        if done.returncode == 0:
            return True
        said = ((done.stderr or "").strip().splitlines() or ["(no stderr)"])[-1]
        ctx.notes["publish"] = (
            f"refused: `{' '.join(argv)}` exited {done.returncode} ({said}); nothing after it ran"
        )
        return False

    if not ran(["git", "clone", "-q", TAP_URL, str(work)]):
        return
    (work / TAP_FORMULA_PATH).write_text(formula, encoding="utf-8", newline="\n")
    identity = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    if not ran(["git", "-C", str(work), *identity, "commit", "-qam", f"policyforge {ctx.version}"]):
        return
    head = ctx.run(["git", "-C", str(work), "rev-parse", "HEAD"])
    ctx.notes["tap_sha"] = (head.stdout or "").strip() if head.returncode == 0 else ""
    ran(["git", "-C", str(work), "push", "-q", "origin", "HEAD:main"])


def _release_check_passes(ctx: Context) -> Check:
    code = ctx.run(_release_check_argv(ctx)).returncode
    return Check(code == 0, [f"release_check.py --version {ctx.version}: exit {code} (must be 0)"])


def _release_check_argv(ctx: Context) -> list[str]:
    # `--version`, not a positional: the 1.6.1 cut passed it positionally,
    # argparse exited 2 on the usage error, and the final check never ran
    # (#415). The test parses this argv with release_check's own parser.
    return [
        sys.executable,
        str(ctx.root / "scripts" / "release_check.py"),
        "--version",
        ctx.version,
    ]


# --- the two artefact gates (80's ruling on #348) ------------------------------
#
# The script cannot do the #226 re-measure or the post-zero review, but it can
# refuse to pass until each has left an ARTEFACT. Both are read from the
# release's tracking issue (`--tracking-issue N`), each as the LAST non-empty
# line of a comment -- the verdict-line rule, so quoting one in a discussion
# does not create one. Every session posts as the same GitHub account, so the
# handle in the line is the identity, as it is for `reviewer=`. `--execute`
# cannot satisfy either; only the comment can.

#: One per reviewer, posted after the milestone reached zero (CLAUDE.md,
#: "Closing a release train": product, research and quality each review).
REVIEW_LINE = re.compile(r"^Post-zero-review: (\S+) reviewer=policyforge-(\w+)\s*$")
POST_ZERO_REVIEWERS = ("80", "5b", "1d")

#: The #226 re-measure of every figure in the notes, naming the train SHA it
#: was done at. It must be the current train tip: a figure is a fact about a
#: moment, and only one taken after the last merge may ship.
MEASURE_LINE = re.compile(
    r"^Notes-measured-SHA: ([0-9a-f]{40}) release=(\S+) reviewer=policyforge-(\w+)\s*$"
)


#: The GitHub logins whose comments on the tracking issue count as records
#: (80's ruling on #383): the account every session posts as, and the user's.
#: By LOGIN, an identity, not by `author_association`, which is a role any
#: future collaborator would hold. The repository is public: without this,
#: a stranger's later "Release-install: <head> release=X result=passed"
#: overrode a real FAILED and opened the approval wait and the tag
#: (policyforge-ba and policyforge-b5 on #383, separately).
RECORD_AUTHORS = frozenset({"rdazzlebot", "rdazzleman"})

#: The same rule on Forgejo, where each session posts as its own bot: the
#: team's six and the user's own login, each measured to exist there (#7).
#: `rdazzlebot` has no account on Forgejo, so it is not listed.
FORGEJO_RECORD_AUTHORS = frozenset(
    {"rdazzleman", *(f"policyforge-{h}" for h in ("80", "1d", "9b", "ba", "b5", "5b"))}
)


def _tracking_records(
    ctx: Context, only: frozenset[str] | None = None
) -> list[tuple[str, int, str]]:
    """(created_at, position, last non-empty line) for every comment on the
    tracking issue by a `RECORD_AUTHORS` login, in the order GitHub lists them.
    `only` narrows the logins further (#242: an acknowledgement counts only
    from the user's own login), and leaves the ignored-records note alone.

    `position` breaks a same-second tie by comment order, never by the line's
    text, which put "passed" after "FAILED" (ba on #383). Comments by anyone
    else are not records: they are ignored, and kept in `ctx.notes` so each
    gate can say it ignored them (`_ignored`)."""
    if not ctx.tracking:
        return []
    if ctx.forge == "forgejo":
        # issues/N/comments returns every plain comment in one answer; it
        # ignores `limit` and `page` (measured on #7), so it is read once.
        status, body, _ = ctx.forgejo(
            "GET", f"/repos/{FORGEJO_REPO}/issues/{ctx.tracking}/comments", None
        )
        comments = body if status == 200 and isinstance(body, list) else []
        out = "\n".join(
            json.dumps([c.get("created_at"), c.get("body"), (c.get("user") or {}).get("login")])
            for c in comments
        )
        authors = FORGEJO_RECORD_AUTHORS if only is None else FORGEJO_RECORD_AUTHORS & only
    else:
        out = ctx.run(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{release_check.REPOSITORY}/issues/{ctx.tracking}/comments",
                "--jq",
                ".[] | [.created_at, .body, .user.login] | @json",
            ]
        ).stdout
        authors = RECORD_AUTHORS if only is None else RECORD_AUTHORS & only
    records: list[tuple[str, int, str]] = []
    ignored: list[list[str]] = []
    for position, line in enumerate((out or "").splitlines()):
        try:
            created, body, login = json.loads(line)
        except ValueError:
            continue
        lines = [x.strip() for x in (body or "").splitlines() if x.strip()]
        if not lines:
            continue
        if login in authors:
            records.append((created, position, lines[-1]))
        else:
            ignored.append([str(login), lines[-1]])
    if only is None:
        ctx.notes["ignored records"] = json.dumps(ignored)
    return records


def _ignored(ctx: Context, pattern: re.Pattern[str]) -> list[str]:
    """A measured line for each record-shaped line `pattern` matches that was
    posted by someone outside `RECORD_AUTHORS`: said, not silently dropped."""
    ignored = json.loads(ctx.notes.get("ignored records") or "[]")
    return [
        f"IGNORED a record by {login} (not in {sorted(RECORD_AUTHORS)}): {line[:80]}"
        for login, line in ignored
        if pattern.match(line)
    ]


def _milestone_zero_at(ctx: Context) -> str:
    """When the milestone reached zero: the latest `closed_at` among its issues."""
    if ctx.forge == "forgejo":
        milestone, _ = _fj_milestone(ctx)
        if milestone is None:
            return ""
        closed = _fj_list(
            ctx,
            f"/repos/{FORGEJO_REPO}/issues?state=closed&type=issues&milestones={milestone['id']}",
        )
        stamps = [i["closed_at"] for i in closed or [] if i.get("closed_at")]
        return max(stamps) if stamps else ""
    repo = release_check.REPOSITORY
    milestones = ctx.run(["gh", "api", f"repos/{repo}/milestones?state=all&per_page=100"]).stdout
    milestone, _ = pick_milestone(json.loads(milestones or "[]"), ctx.version)
    if milestone is None:
        return ""
    number = milestone["number"]
    out = ctx.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/issues?milestone={number}&state=closed&per_page=100",
            "--jq",
            ".[] | select(.pull_request|not) | .closed_at",
        ]
    ).stdout
    stamps = [s.strip() for s in (out or "").splitlines() if s.strip()]
    return max(stamps) if stamps else ""


def _post_zero_reviewed(ctx: Context) -> Check:
    if not ctx.tracking:
        return Check(False, ["no --tracking-issue given, so there is no record to read"])
    zero_at = _milestone_zero_at(ctx)
    after = {
        match.group(2)
        for created, _position, line in _tracking_records(ctx)
        if (match := REVIEW_LINE.match(line))
        and match.group(1) == ctx.version
        and zero_at
        and created > zero_at
    }
    missing = [h for h in POST_ZERO_REVIEWERS if h not in after]
    return Check(
        bool(zero_at) and not missing,
        [
            f"milestone {ctx.version} reached zero at {zero_at or '?'}",
            f"post-zero reviews on #{ctx.tracking} after it: {sorted(after) or 'none'}",
            f"missing (each posts `Post-zero-review: {ctx.version} reviewer=policyforge-<handle>` "
            f"as a comment's last line): {missing or 'none'}",
            *_ignored(ctx, REVIEW_LINE),
        ],
    )


def _notes_measured(ctx: Context) -> Check:
    if not ctx.tracking:
        return Check(False, ["no --tracking-issue given, so there is no record to read"])

    branches = _train_branches(ctx)
    valid_tips, refused = _train_tips(ctx, branches)
    if refused:
        return refused

    train = next(iter(valid_tips.keys())) if valid_tips else branches[0]
    tip = valid_tips.get(train, "")

    measured = [
        (created, position, match.group(1))
        for created, position, line in _tracking_records(ctx)
        if (match := MEASURE_LINE.match(line)) and match.group(2) == ctx.version
    ]
    latest = max(measured)[2] if measured else ""
    return Check(
        bool(tip) and latest == tip,
        [
            f"train tip origin/{train} ON THE SERVER: {tip[:12] or '?'}",
            f"latest `Notes-measured-SHA:` for {ctx.version} on #{ctx.tracking}: "
            f"{latest[:12] or 'none'} "
            "(must be the tip: a measurement before the last merge may be stale)",
            *_ignored(ctx, MEASURE_LINE),
        ],
    )


def steps(forge: str = "github", publish: str = PUBLISH_PUBLIC) -> list[Step]:
    """The cut, in order. The order is the point: nothing here is optional.

    On the Forgejo path (#33) three steps join it: the public commit P is
    built and guarded, proposed on a named GitHub branch before the approval
    install (which installs P), and fast-forwarded onto GitHub main after the
    user's approval. The tag then names P.

    A LOCAL release (#90) is that plan with every GitHub step deferred and
    the installs and the tag moved to Forgejo (`_localised`)."""
    plan = _public_steps(forge)
    if publish != PUBLISH_LOCAL:
        return plan
    if forge != "forgejo":
        raise ValueError("a local release is a Forgejo release: --forge forgejo")
    return _localised(plan)


def _localised(plan: list[Step]) -> list[Step]:
    """The public Forgejo plan, made local (#90): each DEFERRED_LOCAL step
    becomes a DEFER that says what waits for GitHub; head-install, tag and
    candidate-install read and write Forgejo instead; and a final step records
    the local release and what it deferred on the tracking issue. Every other
    step is the public plan's own, unchanged."""
    local = {
        "head-install": Step(
            "head-install",
            "install the release PR's head from its Forgejo archive, before approval",
            ACT,
            _head_install_ready_local,
            _head_installed,
            _install_head,
            outward=True,
            would=(
                "fetch the head's archive from Forgejo, prove it is the cut's tree, install it "
                "with Homebrew in a container from that file, and post the result on the "
                "tracking issue"
            ),
        ),
        "tag": Step(
            "tag",
            "tag main's merge commit on Forgejo",
            ACT,
            lambda c: _all(_head_installed(c), _no_tag(c)),
            _tagged,
            _tag,
            outward=True,
            would="create the annotated tag on main's merge commit and push it to Forgejo",
        ),
        "candidate-install": Step(
            "candidate-install",
            "install the tag's Forgejo archive, the second install",
            ACT,
            _tagged,
            lambda c: _install_check(c, "", "candidate install"),
            _install_candidate_local,
            would=(
                "fetch the tag's archive from Forgejo, prove it is main's tree, and install it "
                "with Homebrew in a container from that file"
            ),
        ),
    }
    out = []
    for step in plan:
        if step.key in DEFERRED_LOCAL:
            out.append(
                Step(
                    step.key,
                    step.title,
                    DEFER,
                    _deferred,
                    _deferred,
                    would=f"until GitHub is set up (#90): {step.title}",
                )
            )
        elif step.key in local:
            out.append(local[step.key])
        elif step.key == "main":
            out.append(
                Step(
                    step.key,
                    step.title,
                    step.kind,
                    step.gate,
                    step.post,
                    would="the user's approval and merge of the release PR on Forgejo",
                )
            )
        else:
            out.append(step)
    out.append(
        Step(
            "local-record",
            "record the local release and what it deferred on the tracking issue",
            ACT,
            lambda c: _install_check(c, "", "candidate install"),
            _local_recorded,
            _record_local,
            outward=True,
            would="post the Release-local record naming the tag's commit, its tree and the "
            "deferred steps",
        )
    )
    return out


def _deferred(ctx: Context) -> Check:
    return Check(False, ["deferred: never evaluated"])


def _public_steps(forge: str) -> list[Step]:
    plan = _base_steps()
    if forge != "forgejo":
        return plan
    keys = [s.key for s in plan]
    public = [
        Step(
            "public-build",
            "build the public commit P from the cut, and 1d's guard passes it",
            CHECK,
            # A release first published locally goes public from the same
            # tree, or not at all (#90). The cadence refuses a patch first,
            # before this asks Forgejo anything (`_refused_first`).
            _refused_first(lambda c: _all(_local_release_matches(c), _public_built(c))),
            _public_built,
        ),
        Step(
            "public-candidate",
            "propose P on GitHub's release-candidate branch, before the approval install",
            ACT,
            _candidate_ready,
            _candidate_pushed,
            _push_candidate,
            outward=True,
            would=(
                "push P (and only P's new objects) to GitHub as refs/heads/release-candidate/X.Y.Z"
            ),
        ),
    ]
    at = keys.index("head-install")
    plan[at:at] = public
    main_at = [s.key for s in plan].index("main") + 1
    plan[main_at:main_at] = [
        Step(
            "public-main",
            "fast-forward GitHub main to P, after the user's approval",
            ACT,
            _public_main_ready,
            _public_main_done,
            _push_public_main,
            outward=True,
            would=(
                "push P to GitHub main as a fast-forward from G, then delete the candidate branch"
            ),
        )
    ]
    tag = next(s for s in plan if s.key == "tag")
    tag.gate = _refused_first(lambda c: _all(_public_main_done(c), _head_installed(c), _no_tag(c)))
    tag.title = "tag the public commit P and publish the GitHub Release"
    main = next(s for s in plan if s.key == "main")
    main.would = "the user's approval and merge of the release PR on Forgejo"
    return plan


def _base_steps() -> list[Step]:
    return [
        Step("train-final", "the train is the release", CHECK, _milestone_open, _milestone_open),
        Step(
            "clean-tree",
            "standing on the pushed train, nothing uncommitted",
            CHECK,
            _train_tree,
            _train_tree,
        ),
        Step(
            "post-zero-review",
            "80, 5b and 1d each reviewed after the milestone reached zero",
            CHECK,
            _post_zero_reviewed,
            _post_zero_reviewed,
        ),
        Step(
            "notes-measured",
            "the #226 re-measure names the current train tip",
            CHECK,
            _notes_measured,
            _notes_measured,
        ),
        Step(
            "resources",
            "the formula's resources are the lock's, before anything installs with them",
            CHECK,
            _resources_match_lock,
            _resources_match_lock,
        ),
        Step(
            "changelog",
            "assemble changelog.d/ into `## X.Y.Z`",
            ACT,
            lambda c: _all(_fragments_ready(c), _rehearsed(c)),
            _changelog_cut,
            _assemble,
            would="assemble the fragments into CHANGELOG.md and remove them",
            commits_to="release-pr",
        ),
        Step(
            "version",
            "bump pyproject and __version__ together",
            ACT,
            _bump_ready,
            _bumped,
            _bump,
            would="set both version strings",
            commits_to="release-pr",
        ),
        Step(
            "install-pins",
            "point every install pin at this release (#413)",
            ACT,
            _pins_ready,
            _pins_current,
            _repin,
            would="rewrite each `<owner>/policyforge@vX.Y.Z` in the docs to this release",
            commits_to="release-pr",
        ),
        Step(
            "release-pr",
            "the cut, as a PR against main",
            ACT,
            _pr_ready,
            _pr_open,
            _open_pr,
            outward=True,
            would="commit, push the cut branch and open a PR against main",
        ),
        Step(
            "head-install",
            "install the release PR's head in a clean container, before approval",
            ACT,
            _head_install_ready,
            _head_installed,
            _install_head,
            outward=True,
            would=(
                "build and install the release PR head's archive with Homebrew in a "
                "container, and post the result on the tracking issue"
            ),
        ),
        Step(
            "head-pythons",
            "the full suite on Python 3.10, 3.11 and 3.13, before approval",
            ACT,
            _head_installed,
            _pythons_tested,
            _run_pythons,
            outward=True,
            would=(
                "run the full suite on the approval commit in python:3.10, 3.11 and 3.13-slim "
                "containers, each a fresh unhashed install (run this invocation through the "
                "machine's heavy queue), and post one record per version on the tracking issue"
            ),
        ),
        Step(
            "main",
            "the user merges the release PR, with the install result in hand",
            WAIT,
            lambda c: _all(_pr_still_open(c), _head_installed(c), _pythons_tested(c)),
            _main_merged,
            would="the user's approval and merge of the release PR in GitHub",
        ),
        Step(
            "tag",
            "tag main's merge commit and publish the GitHub Release",
            ACT,
            lambda c: _all(_head_installed(c), _no_tag(c)),
            _tagged,
            _tag,
            outward=True,
            would="create and push the tag, and publish the GitHub Release",
        ),
        Step(
            "candidate-install",
            "install the candidate formula, before publishing it",
            ACT,
            _archive_served(_tag_archive),
            lambda c: _install_check(c, _tag_archive(c), "candidate install"),
            lambda c: _install(c, _tag_archive(c), "candidate install"),
            would="build and install the tag's formula with Homebrew in a container",
        ),
        Step(
            "publish",
            "publish the formula to the tap",
            ACT,
            lambda c: Check(
                bool(c.notes.get("candidate_formula")), ["a candidate formula passed its install"]
            ),
            _published,
            _publish,
            outward=True,
            would=f"push the formula to {TAP_REPOSITORY}",
        ),
        Step(
            "release-check",
            "what a user installs is what was released",
            CHECK,
            _release_check_passes,
            _release_check_passes,
        ),
    ]


# --- real side effects -------------------------------------------------------


def _git_env() -> dict[str, str] | None:
    """The environment for git, with the caller's Forgejo token as an HTTP
    header scoped to the Forgejo host, or None to inherit unchanged.

    Through GIT_CONFIG_* rather than `-c` on the command line, so the token
    is in no argv: `_open_pr` prints part of a failed argv, and a process
    listing shows all of it. Scoped by URL prefix, so git sends it only to
    Forgejo, under both names the remote may carry (`localhost`, 127.0.0.1)."""
    token = os.environ.get("FORGEJO_TOKEN", "").strip()
    if not token:
        return None
    env = dict(os.environ)
    count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    for host in sorted({f"{FORGEJO_URL}/", "http://localhost:3000/"}):
        env[f"GIT_CONFIG_KEY_{count}"] = f"http.{host}.extraHeader"
        env[f"GIT_CONFIG_VALUE_{count}"] = f"Authorization: token {token}"
        # For Forgejo, the header is the ONLY credential (ba on #124): an empty
        # helper resets the helper list for this URL prefix. Without it, a
        # rejected token made git ask the configured helper for a stored Forgejo
        # credential, then tell it to ERASE that credential (measured: a logging
        # stand-in helper behind a 401 was asked [get, erase]). Scoped to the
        # Forgejo prefixes (1d on #124): GitHub keeps the operator's own helper.
        env[f"GIT_CONFIG_KEY_{count + 1}"] = f"credential.{host}.helper"
        env[f"GIT_CONFIG_VALUE_{count + 1}"] = ""
        count += 2
    env["GIT_CONFIG_COUNT"] = str(count)
    # A rejected token must fail, not open Git Credential Manager and wait
    # for a person who isn't there (ba on #28; fj does the same).
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return env


#: The git subcommands that talk to a server, and so the only ones given the
#: token. `commit` runs this repository's hooks, which have no use for it.
_GIT_NETWORK = frozenset({"push", "fetch", "ls-remote", "clone", "pull"})


def _git_subcommand(argv: list[str]) -> str:
    """`commit` for `git -C <root> -c k=v commit -am ...`; "" if not git."""
    if argv[:1] != ["git"]:
        return ""
    rest = iter(argv[1:])
    for arg in rest:
        if arg in ("-C", "-c"):
            next(rest, None)
        elif not arg.startswith("-"):
            return arg
    return ""


def _real_forgejo(
    method: str, path: str, data: object, *, text: bool = False
) -> tuple[int, object, dict]:
    """Forgejo's API as the caller's own bot (FORGEJO_TOKEN): (status, JSON, headers).
    With `text=True` the body is returned decoded, not parsed: an Actions job
    log is plain text (publish_public's `ci-read`, #326)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    token = os.environ.get("FORGEJO_TOKEN", "").strip()
    # http(s) only, refused in code rather than only suppressed: urllib also
    # opens `file://`, and FORGEJO_URL comes from the environment. The worst
    # case left is the caller pointing their OWN token at a host they chose.
    if not token or urllib.parse.urlsplit(FORGEJO_URL).scheme not in ("http", "https"):
        return 0, None, {}
    request = urllib.request.Request(
        f"{FORGEJO_URL}/api/v1{path}",
        data=None if data is None else json.dumps(data).encode("utf-8"),
        method=method,
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310  # nosemgrep
            raw = response.read()
            if text:
                return (
                    response.status,
                    raw.decode("utf-8", errors="replace"),
                    dict(response.headers),
                )
            return response.status, json.loads(raw) if raw else None, dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, None, {}
    except (OSError, ValueError):
        return 0, None, {}


def _real_fetch_archive(path: str, dest: Path) -> int:
    """A Forgejo API path (an archive) saved to `dest`, as the caller's own
    bot: the HTTP status, 0 when it could not be asked. The same refusals as
    `_real_forgejo`: http(s) only, and only with a token. The token goes in a
    header to FORGEJO_URL and nowhere else: not into `dest`, not into a
    container (#90)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    token = os.environ.get("FORGEJO_TOKEN", "").strip()
    if not token or urllib.parse.urlsplit(FORGEJO_URL).scheme not in ("http", "https"):
        return 0
    request = urllib.request.Request(
        f"{FORGEJO_URL}/api/v1{path}", headers={"Authorization": f"token {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:  # nosec B310  # nosemgrep
            dest.write_bytes(response.read())
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return 0


def userinfo_refusal(url: str) -> str | None:
    """Why `url` is refused, naming its host, if it carries credentials
    (`user:token@host`), or None. The credential itself is never in the
    message (80's ruling (b) on #124): a release step must not depend on a
    credential that isn't the operator's, and one in a URL is a credential
    nobody chose for this run."""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme and parsed.netloc and (parsed.username or parsed.password):
        return (
            f"refused: a git URL with embedded credentials, for host {parsed.hostname}. "
            "Use the repository's plain URL; your own token goes through FORGEJO_TOKEN (#124)"
        )
    return None


def _redacted(arg: str) -> str:
    """`arg` with any userinfo replaced, so a refused argv can be printed."""
    if userinfo_refusal(arg) is None:
        return arg
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(arg)
    host = parsed.hostname or ""
    netloc = f"<credentials removed>@{host}" + (f":{parsed.port}" if parsed.port else "")
    return urlunsplit(parsed._replace(netloc=netloc))


def _real_run(argv: list[str]) -> subprocess.CompletedProcess:
    if _git_subcommand(argv) in _GIT_NETWORK:
        for arg in argv:
            if why := userinfo_refusal(arg):
                return subprocess.CompletedProcess([_redacted(a) for a in argv], 2, "", why)
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            env=_git_env() if _git_subcommand(argv) in _GIT_NETWORK else None,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(argv, 127, "", str(error))


def _real_status(url: str) -> int:
    import urllib.error
    import urllib.request

    # https only, refused in code rather than only suppressed: urllib also
    # opens `file://`, and every URL here is a GitHub archive this script
    # built. The suppression sits on the offending line, as release_check's
    # does, because semgrep honours `nosemgrep` only there.
    if not url.startswith("https://"):
        return 0
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310  # nosemgrep
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return 0


def _install_script(version: str, source_sha: str = "") -> str:
    """The container's `&&` chain, ending with the installed package's version.

    **The version is the one check that reads the artefact itself**
    (policyforge-ba on #348): an install of the wrong commit passes every
    other line here. When this was written `policyforge --version` didn't
    exist (it does since #219, printing the installed metadata), so it asks
    the formula's virtualenv for `policyforge.__version__`, the string step 6
    bumps, which is the artefact this check is about. `test "$(...)" = X`
    fails on a wrong version and on no output alike. PROBE MEASURED on #348
    against the published 1.6.0 formula in this image, on its Homebrew
    4.6.20: it read 1.6.0, the arm expecting 9.9.9 failed. The chain now
    updates Homebrew first (#391), so it measures the Homebrew a user
    installs with, not the one the image happens to ship.

    **And right after the install, `release_check.version_step`** (#232):
    `policyforge --version` must print exactly this version with nothing on
    stderr, so a skew between the installed metadata and that same
    `__version__` is caught before the user approves the main merge, not
    after the publish. It is the step `release_check` runs, not a retyped
    copy. For a release older than 1.8.0 the chain echoes
    `release_check.VERSION_NOT_RUN` instead, never a silent gap.
    """
    step = release_check.version_step(version)
    version_check = step[1] if step else f"echo {shlex.quote(release_check.VERSION_NOT_RUN)}"
    python = '"$(brew --prefix local/candidate/policyforge)/libexec/bin/python"'
    read = "import policyforge, sys; sys.stdout.write(policyforge.__version__)"
    # A local release's tarball (#90), checked by the container itself before
    # Homebrew sees it: Homebrew 4.6.20 only WARNS on a `file://` checksum
    # mismatch and installs anyway (measured on #90, 1945).
    source = [f'echo "{source_sha}  {LOCAL_SOURCE}" | sha256sum -c -'] if source_sha else []
    return " && ".join(
        [
            *source,
            # The Homebrew a user has, not the image's: `homebrew/brew:latest`
            # still ships 4.6.20, and whether a run auto-updated decided whether
            # a formula with no version installed or was refused (#391: 1d's
            # run stayed on 4.6.20 and passed, 9b's updated to 7.0.6 and failed).
            "brew update --quiet",
            'echo "HOMEBREW: $(brew --version | head -1)"',
            "brew tap-new --no-git local/candidate",
            "cp /candidate/policyforge.rb "
            '"$(brew --repository)/Library/Taps/local/homebrew-candidate/Formula/"',
            "brew install --build-from-source local/candidate/policyforge",
            version_check,
            "brew test local/candidate/policyforge",
            "brew audit --strict local/candidate/policyforge",
            "mkdir -p /tmp/pf",
            # release_check's pinned smoke test, not retyped: `frameworks` in an
            # empty directory exits 1 by design, so `init` must come first.
            *(
                command
                for name, command in release_check.INSTALL_STEPS
                if name in ("init", "frameworks")
            ),
            *SEAM_CHECKS,
            f'test "$({python} -c \'{read}\')" = "{version}"',
        ]
    )


#: The public tool without the private parsers (#32, #33): each licensed ETL
#: command, run with no arguments, exits NON-ZERO, names `generate-parser`,
#: and prints no traceback; `generate-parser` itself is there. This is the
#: plugin seam's contract, checked in the install every approval rests on:
#: an install of a tree still carrying the parsers could not see it broken.
#: (command, its real arguments, whether its message must name
#: `generate-parser`). Real arguments, in a git directory ignoring
#: `local_content/`, so the licensed-write guard passes and the PLUGIN
#: lookup is what answers: with no arguments Click refuses the missing
#: option first and the seam is never reached (measured on #36; this PR's
#: first version made exactly that no-argument check). `generate-parser`
#: has no SOC 2 path, so SOC 2's message does not point to it (#36).
SEAM_COMMANDS = (
    ("etl-hitrust", "--export seam.csv --out local_content/hitrust/controls.json", True),
    ("etl-govramp", "--export seam.csv --out local_content/govramp/controls.json", True),
    ("etl-soc2-tsc", "--criteria seam.txt --out local_content/soc2-tsc/controls.json", False),
)
#: What the public command prints when no plugin is installed, as grep reads
#: it. The words around it are `plugins.py`'s to change: #50 renamed the
#: "licensed-parsers plugin" to the "licensed-framework plugin", and a check
#: grepping the old name would have refused every install at the cut. A test
#: runs the REAL command with no plugin and requires this in its output, so
#: the two cannot drift apart silently again (9b, reviewing #50).
SEAM_REFUSAL = "needs the licensed-"
SEAM_CHECKS = (
    "cd /tmp/pf && git init -q . && printf 'local_content/\\n' > .gitignore "
    "&& printf 'a,b\\n1,2\\n' > seam.csv && printf 'CC1.1 x\\n' > seam.txt",
    *(
        f"cd /tmp/pf && {{ policyforge {command} {args} > /tmp/seam.out 2>&1; test $? -ne 0; }} "
        f"&& grep -q '{SEAM_REFUSAL}' /tmp/seam.out "
        "&& ! grep -q Traceback /tmp/seam.out "
        + ("&& grep -q generate-parser /tmp/seam.out " if names_generator else "")
        + f"&& test ! -e {args.split('--out ')[1]}"
        for command, args, names_generator in SEAM_COMMANDS
    ),
    "policyforge generate-parser --help > /dev/null",
)


def _docker_argv(work: Path, script: str) -> list[str]:
    """The container run: the work directory read-only at /candidate and the
    chain, nothing else. No `-e`, `--env` or `--env-file`, so no host variable
    (the operator's FORGEJO_TOKEN included) reaches the container (80's
    condition on #90); `test_release_local` inspects this argv for it."""
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{work}:/candidate:ro",
        CONTAINER_IMAGE,
        "bash",
        "-lc",
        script,
    ]


def _real_install(
    formula: str, version: str, source: Path | None = None
) -> tuple[bool, bool, list[str]]:
    """Install a local formula in a clean container through a throwaway tap.

    **Measured on #255, not assumed:** in the `homebrew/brew` image
    (Homebrew 4.6.20), `brew install --formula /path/policyforge.rb` is
    refused with advice to `brew tap-new`, while the same file copied into a
    tap made with `brew tap-new --no-git local/candidate` resolves. So the
    formula goes into that throwaway tap. Same `&&` chain and the same
    could-it-start probe as `release_check.run_install_check`.
    """
    import shutil

    if shutil.which("docker") is None:
        return False, False, ["docker is not on PATH"]
    probe = _real_run(["docker", "run", "--rm", CONTAINER_IMAGE, "true"])
    if probe.returncode != 0:
        return False, False, [f"could not start {CONTAINER_IMAGE}"]
    work = Path(tempfile.mkdtemp())
    (work / "policyforge.rb").write_text(formula, encoding="utf-8", newline="\n")
    digest = ""
    if source is not None:
        # The bytes the host proved, copied beside the formula (#90).
        data = source.read_bytes()
        (work / Path(LOCAL_SOURCE).name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
    script = _install_script(version, digest)
    proc = _real_run(_docker_argv(work, script))
    lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-12:]
    lines.append(f"docker exit status: {proc.returncode}")
    return True, proc.returncode == 0, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="the version to cut, e.g. 1.6.1")
    parser.add_argument("--execute", action="store_true", help="act; without it, a dry run")
    parser.add_argument(
        "--tracking-issue",
        type=int,
        help="the issue holding the post-zero reviews and the notes re-measure",
    )
    parser.add_argument(
        "--formula",
        type=Path,
        help="the regenerated, lock-reconciled formula to cut from (default: the published one)",
    )
    parser.add_argument(
        "--forge",
        choices=("forgejo", "github"),
        default="forgejo",
        help=(
            "where the team-side steps read: the milestone, the train, the tracking records, "
            "the release PR and main (default: forgejo). The tag archive, the tap and the "
            "published formula are GitHub either way."
        ),
    )
    parser.add_argument(
        "--publish",
        choices=(PUBLISH_PUBLIC, PUBLISH_LOCAL),
        default=PUBLISH_PUBLIC,
        help=(
            "public (default): Forgejo, then GitHub. local: Forgejo only, tagged and installed "
            "from Forgejo, with every GitHub step deferred and recorded (#90). Needs "
            "--forge forgejo, --formula and --tracking-issue."
        ),
    )
    args = parser.parse_args(argv)
    if args.formula is not None and not args.formula.is_file():
        parser.error(f"--formula {args.formula}: no such file")
    if args.publish == PUBLISH_LOCAL:
        # Each refused here rather than at the step: without --formula the
        # base formula is fetched from GitHub, which a local release never asks.
        missing = [
            flag
            for flag, given in (
                ("--forge forgejo", args.forge == "forgejo"),
                ("--formula", args.formula is not None),
                ("--tracking-issue", args.tracking_issue is not None),
            )
            if not given
        ]
        if missing:
            parser.error(f"--publish local needs {', '.join(missing)}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"release: {args.version!r} is not X.Y.Z")
        return 2
    ctx = Context(
        version=args.version,
        run=_real_run,
        status_of=_real_status,
        hash_url=release_check.hash_of,
        install=_real_install,
        run_python=_real_run_python,
        ci_legs=_real_ci_legs,
        tracking=args.tracking_issue,
        formula_path=args.formula,
        forge=args.forge,
        forgejo=_real_forgejo,
        publish=args.publish,
        fetch_archive=_real_fetch_archive,
        rehearse=_real_rehearse,
    )
    if args.forge == "forgejo" and not os.environ.get("FORGEJO_TOKEN", "").strip():
        print("release: --forge forgejo needs FORGEJO_TOKEN (your own bot's token)")
        return 2
    if why := userinfo_refusal(ctx.forgejo_url):
        print(f"release: FORGEJO_URL is {why}")
        return 2
    print(f"release {ctx.tag}: {'EXECUTE' if args.execute else 'DRY RUN (nothing will change)'}")
    plan = steps(args.forge, args.publish)
    if args.publish == PUBLISH_LOCAL:
        print(
            "LOCAL release (#90): Forgejo only. Deferred until GitHub is set up: "
            + ", ".join(s.key for s in plan if s.kind == DEFER)
        )
    if not args.execute:
        # The whole order first: a dry run stops at the first step that would
        # act, so without this it would never show what comes after (#381).
        print("\nThe cut, in order:")
        for index, step in enumerate(plan, 1):
            kind = step.kind + (", outward" if step.outward else "")
            print(f"  {index:>2}. {step.key}: {step.title} [{kind}]")
    return run_steps(plan, ctx, execute=args.execute)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
