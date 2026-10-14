#!/usr/bin/env python3
"""Publish a locally released version to the public repository (#115).

WHY. A release is cut on the private Forgejo first (`release.py --publish
local`, #90). The public sees only RELEASE COMMITS: a separate Forgejo repo,
`policyforge/policyforge-public`, holds one commit P per minor release and its
tag, and a Forgejo push mirror (the user's token, entered by the user) carries
that whole repo to github.com. The private repo is never mirrored: its history
carries the licensed parsers (#32). The user's design and words are on #115;
80's rulings on 9b's seven points are at 2286.

THE CONTRACT (`release.py`'s engine, the same gates/postconditions/keys):
- **P's tree is the local tag's tree without `PUBLIC_DROPPED`** (GitHub's
  Dependabot config and workflows: the public repo runs nothing, 161), and
  nothing else differs, measured. A licensed path in it is a refusal, never a
  strip: a release whose tree carries one is the finding.
- **P is built and installed HERE, and nothing is pushed before the user's
  recorded approval:** a `Publish-approved: <P> release=X.Y.Z` line, last in
  a comment by `rdazzleman` on the tracking issue, naming the P this run
  builds. P is deterministic (the tag commit's date, a given identity, no
  clock), so the approved P is the pushed P across the exit at the wait.
- **Fast-forward only.** P's parent is `policyforge-public`'s main (none for
  the first release, a root commit). Pushes are plain: git refuses a
  non-fast-forward, and nothing here forces.
- **Tags never cross repos.** The public `vX.Y.Z` is a new tag object naming
  P (`git mktag`, deterministic); the private `vX.Y.Z` names the local commit.
- **GitHub is read signed out only**, and only after the push: the mirror is
  asynchronous, so the poll has a timeout and fails loudly. GitHub's served
  tag archive is tree-proved against P before its sha256 is recorded for the
  formula. No credential helper and no prompt is allowed on those reads.
- **No token** in any argument, record or container: Forgejo pushes take the
  operator's token as an HTTP header in git's environment (`release._git_env`).
- **P is tested in the public SHAPE before approval** (#322, from 164 item 1).
  The public repo starts as one root commit with only its tag, a shape no
  team check ran in, and 1.7.0's CI failed there on every leg (#163: a test
  read `HEAD~1`). So, after the install, CI's own `pytest` and `skip_gate.py`
  run on Linux in a fresh clone of P's tree ALONE, as one root commit with
  only its tag. Anything but a pass stops the run before approval is asked.
- **And on macOS, Windows and Linux before approval** (#326, from #165 Part B).
  A candidate commit with the release tree (P's, plus the workflows that run
  there) goes to `policyforge-ci`'s release/X.Y.Z
  (guarded by `publish_guard` first, fast-forward only), its mirror carries it
  to the private GitHub repo, and GitHub's run is read through ops' ci-read,
  the only holder of the user's read token. Approval needs every platform job
  passed; a run that never appears is NOT RUN, never a pass.

**The tap, the same way (#115, part B).** The formula is installed in a
clean container from GitHub's own tag archive first (its sha256 re-read and
equal to the proved one), then committed to the Forgejo repo
`policyforge/homebrew-tap` (fast-forward only), whose own push mirror (the
user's second token) carries it to GitHub. The script reads GitHub's tap
signed out until it shows that commit, and ends with `release_check.py`:
what a user installs, read signed out from GitHub, is what was released.

Usage:

    python scripts/publish_public.py 1.7.0 --tracking-issue N --local-issue 100 \
        --identity "Name <email>" --formula PATH --ops OPS  # dry run
    ... --execute                                          # act, step by step
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import release  # noqa: E402
import release_check  # noqa: E402
from release import ACT, CHECK, WAIT, Check, Context, Step  # noqa: E402

#: The public release repo on Forgejo, and where its mirror lands.
PUBLIC_REPO = "policyforge/policyforge-public"
PUBLIC_URL = f"{release.FORGEJO_URL}/{PUBLIC_REPO}.git"
GITHUB_PUBLIC_URL = f"https://github.com/{release_check.REPOSITORY}.git"
#: The tap, the same way: a Forgejo repo the user seeds, mirrored to GitHub.
TAP_REPO = "policyforge/homebrew-tap"
TAP_PUBLIC_URL = f"{release.FORGEJO_URL}/{TAP_REPO}.git"
GITHUB_TAP_URL = f"https://github.com/{release_check.OWNER}/homebrew-tap.git"
#: What P leaves out of the release tree (161; the user's decision, relayed by
#: 80 on 161: "the public repo runs NOTHING"). GitHub acts on these when it
#: holds them: Dependabot opened branches and PRs on the public repo, and its
#: workflows ran (161). The team still uses them on Forgejo and on the private
#: CI repo, so they stay in the release tree and in the CI candidate; only P
#: drops them. A path ending in `/` is a folder, dropped whole.
PUBLIC_DROPPED = (".github/dependabot.yml", ".github/dependabot.yaml", ".github/workflows/")
#: The private test repo (#165 Part B, #326): a candidate with the release tree goes to
#: its release/X.Y.Z, whose push mirror (the user's token) carries it to the
#: private rdazzleman/policyforge-ci, where GitHub runs Linux, macOS and Windows.
CI_REPO = release.CI_REPO
CI_URL = release.CI_URL
#: GitHub's results are read ONLY through ops' ci-read workflow, which alone
#: holds the user's read-only token (POLICYFORGE_CI_READ). This run dispatches
#: it and reads its job log; it never sees the token.
OPS_REPO = release.OPS_REPO
CI_READ_WORKFLOW = release.CI_READ_WORKFLOW
#: The platform jobs that must each succeed on the candidate (ci.yml's names).
CI_REQUIRED = (
    re.compile(r"scan-and-test"),
    re.compile(r"pytest \(macos-[^)]*\)"),
    re.compile(r"pytest \(windows-[^)]*\)"),
    # Every Python `requires-python` claims, one leg each, so a leg that never
    # ran is missing rather than absorbed by its neighbours (#380).
    re.compile(r"pytest \(ubuntu-latest, 3\.10\)"),
    re.compile(r"pytest \(ubuntu-latest, 3\.11\)"),
    re.compile(r"pytest \(ubuntu-latest, 3\.12\)"),
    re.compile(r"pytest \(ubuntu-latest, 3\.13\)"),
    re.compile(r"pytest \(ubuntu-latest, 3\.14\)"),
    # A fresh resolve with no lock: what `pip install` gives a user today.
    re.compile(r"pytest \(ubuntu-latest, 3\.12, unpinned\)"),
    # The candidate's formula installed from source on a real Mac (#381).
    re.compile(r"brew install \(macos-latest\)"),
)

#: The only login whose line approves a publish: the user's own account.
APPROVER = "rdazzleman"

APPROVAL_LINE = re.compile(r"^Publish-approved: ([0-9a-f]{40}) release=(\S+)\s*$")
INSTALL_LINE = re.compile(
    r"^Public-install: ([0-9a-f]{40}) release=(\S+) result=(passed|FAILED|did-not-run|refused)\s*$"
)
PUBLISHED_LINE = re.compile(
    r"^Public-published: ([0-9a-f]{40}) release=(\S+) archive-sha256=([0-9a-f]{64})\s*$"
)
#: The candidate install, recorded (9b on #124): a resumed run holds from it
#: instead of repeating a ~40-minute container install, as step 3 does from
#: `Public-install`. It names the archive AND the formula installed, so a
#: re-encoded archive or a changed `--formula` base installs again.
#: P tested in the public shape (#322), recorded so a resumed run holds from it
#: instead of repeating the suite, as step 3 does from `Public-install`.
SHAPE_LINE = re.compile(
    r"^Public-shape: ([0-9a-f]{40}) release=(\S+) result=(passed|FAILED|did-not-run|refused)\s*$"
)
#: CI's pytest leg: `scan-and-test` runs on Linux with Python 3.12.
SHAPE_IMAGE = "python:3.12-slim"
#: GitHub's three-platform result on the candidate (#326), keyed by the TREE
#: tested (the release tree), so a resumed run holds from it, as from `Public-install`.
CI_LINE = re.compile(
    r"^Candidate-ci: ([0-9a-f]{40}) release=(\S+) tree=([0-9a-f]{40}) "
    r"result=(passed|FAILED|did-not-run|refused)\s*$"
)
CANDIDATE_LINE = re.compile(
    r"^Candidate-install: ([0-9a-f]{40}) release=(\S+) archive-sha256=([0-9a-f]{64}) "
    r"formula-sha256=([0-9a-f]{64}) result=(passed|FAILED|did-not-run|refused)\s*$"
)


@dataclass
class PublicContext(Context):
    """`release.Context`, plus what publishing needs. Every outside effect is
    injectable, as in `release.py`, so tests run against stand-ins."""

    #: The private repo's issue holding the `Release-local` record.
    local_issue: int | None = None
    #: The identity P and its tag carry: "Name <email>", the user's choice.
    identity: str = ""
    public_url: str = PUBLIC_URL
    github_url: str = GITHUB_PUBLIC_URL
    tap_url: str = TAP_PUBLIC_URL
    github_tap_url: str = GITHUB_TAP_URL
    #: Fetches a URL anonymously to a file: (url, dest) -> HTTP status.
    fetch_anonymous: Callable[[str, Path], int] = None  # type: ignore[assignment]
    #: Runs a git command that must reach GitHub WITHOUT credentials (#121, 1d).
    run_signed_out: Callable[[list[str]], subprocess.CompletedProcess] = None  # type: ignore[assignment]
    #: Runs CI's pytest and skip_gate in a clone: (clone) -> (ran, passed, lines).
    test_shape: Callable[[Path], tuple[bool, bool, list[str]]] = None  # type: ignore[assignment]
    ci_url: str = CI_URL
    #: One read of GitHub's results through ops' ci-read: (ctx) -> its summary
    #: text, or None when it couldn't be read this time.
    read_ci: Callable[[PublicContext], str | None] = None  # type: ignore[assignment]
    #: How long to wait for GitHub's run on the candidate, and between reads.
    ci_timeout: float = 3600.0
    ci_poll_every: float = 180.0
    #: The macOS wheel check (#325): (lock, formula, pyproject) texts -> (exit, lines).
    macos_wheels: Callable[[str, str, str], tuple[int, list[str]]] = None  # type: ignore[assignment]
    #: How long to wait for the mirror, and the pause between looks.
    mirror_timeout: float = 1200.0
    poll_every: float = 30.0
    sleep: Callable[[float], None] = time.sleep

    #: A clone of `policyforge/ops`: its inventory is checked first (#194).
    ops_root: Path | None = None

    #: owner/name on GitHub: what a user's formula url names (canonical owner).
    github_repo: str = release_check.REPOSITORY

    def github_archive(self, tag: str) -> str:
        return f"https://github.com/{self.github_repo}/archive/refs/tags/{tag}.tar.gz"


# --- reading records --------------------------------------------------------


def _comments(ctx: PublicContext, issue: int | None) -> list[tuple[str, str]]:
    """(login, last non-empty line) for every comment on `issue`, in order."""
    if not issue:
        return []
    status, body, _ = ctx.forgejo(
        "GET", f"/repos/{release.FORGEJO_REPO}/issues/{issue}/comments", None
    )
    out = []
    for c in body if status == 200 and isinstance(body, list) else []:
        lines = [x.strip() for x in (c.get("body") or "").splitlines() if x.strip()]
        if lines:
            out.append(((c.get("user") or {}).get("login", ""), lines[-1]))
    return out


def _post(ctx: PublicContext, body: str) -> bool:
    status, _, _ = ctx.forgejo(
        "POST", f"/repos/{release.FORGEJO_REPO}/issues/{ctx.tracking}/comments", {"body": body}
    )
    return status == 201


# --- the local release this publishes ----------------------------------------


def _tag_commit(ctx: PublicContext) -> str:
    """The private repo's tag's commit, asked of the server."""
    out = (
        ctx.run(
            [
                "git",
                "-C",
                str(ctx.root),
                "ls-remote",
                "--tags",
                ctx.forgejo_url,
                f"{ctx.tag}^{{}}",
            ]
        ).stdout
        or ""
    ).split()
    return out[0] if out else ""


def _local_released(ctx: PublicContext) -> Check:
    """The version was released locally: the private tag exists, and the
    `Release-local` record names the tag's commit and its tree."""
    commit = _tag_commit(ctx)
    if commit:
        ctx.run(
            [
                "git",
                "-C",
                str(ctx.root),
                "fetch",
                "-q",
                ctx.forgejo_url,
                f"refs/tags/{ctx.tag}",
            ]
        )
    tree = ctx.git("rev-parse", f"{commit}^{{tree}}") if commit else ""
    # Only the release tooling's authors write a record (9b on #124): the
    # same allow-list as every other record gate here.
    records = [
        m
        for login, line in _comments(ctx, ctx.local_issue)
        if login in release.FORGEJO_RECORD_AUTHORS
        and (m := release.LOCAL_LINE.match(line))
        and m.group(2) == ctx.version
    ]
    named = bool(records) and records[-1].group(1) == commit and records[-1].group(3) == tree
    if named:
        ctx.notes["tag_commit"], ctx.notes["tag_tree"] = commit, tree
    said = f"{records[-1].group(1)[:12]} tree {records[-1].group(3)[:12]}" if records else "none"
    return Check(
        bool(commit) and named,
        [
            f"private {ctx.tag} -> {commit[:12] or 'absent'}, tree {tree[:12] or '-'}",
            f"Release-local record on #{ctx.local_issue}: {said} "
            "(must name the same commit and tree)",
        ],
    )


# --- P ---------------------------------------------------------------------------


def _public_tip(ctx: PublicContext) -> str:
    """`policyforge-public`'s main, asked of the server ("" when it has none)."""
    out = (
        ctx.run(["git", "-C", str(ctx.root), "ls-remote", ctx.public_url, "refs/heads/main"]).stdout
        or ""
    ).split()
    return out[0] if out else ""


def _licensed_in_tree(ctx: PublicContext, commit: str) -> list[str]:
    listing = ctx.run(["git", "-C", str(ctx.root), "ls-tree", "-r", "-z", "--name-only", commit])
    tracked = [p for p in (listing.stdout or "").split("\0") if p]
    hits = []
    for licensed in release.LICENSED_PATHS:
        stem = licensed.rstrip("/")
        hits += [p for p in tracked if p == stem or p.startswith(stem + "/")]
    return sorted(set(hits))


def _dropped(path: str) -> bool:
    return any(path == d or (d.endswith("/") and path.startswith(d)) for d in PUBLIC_DROPPED)


def _tree_without_dropped(ctx: PublicContext, tree: str, prefix: str = "") -> str:
    """`tree` with every `PUBLIC_DROPPED` path taken out, written to the object
    store; "" for a tree left empty. Built from `ls-tree` entries as raw tree
    bytes (`hash-object -t tree`), in git's own order, so the result is
    deterministic and every other entry keeps its object id."""
    raw = b""
    for entry in (ctx.git("ls-tree", "-z", tree) + "\0").split("\0"):
        if not entry.strip():
            continue
        meta, name = entry.split("\t", 1)
        mode, kind, sha = meta.split()
        path = prefix + name + ("/" if kind == "tree" else "")
        if _dropped(path):
            continue
        if kind == "tree" and any(d.startswith(path) for d in PUBLIC_DROPPED):
            sha = _tree_without_dropped(ctx, sha, path)
            if not sha:
                continue
        raw += f"{mode.lstrip('0') if kind == 'tree' else mode} {name}".encode() + b"\0"
        raw += bytes.fromhex(sha)
    if not raw:
        return ""
    out = Path(tempfile.mkdtemp()) / "tree"
    out.write_bytes(raw)
    return ctx.git("hash-object", "-t", "tree", "-w", str(out))


def _tree_difference(
    ctx: PublicContext, tree: str, public_tree: str
) -> tuple[list[str], list[str]]:
    """(dropped, other): the paths that differ between the release tree and P's,
    split into deletions under `PUBLIC_DROPPED` and anything else. P is right
    only when `other` is empty."""
    listing = ctx.git("diff-tree", "-r", "-z", "--no-renames", "--name-status", tree, public_tree)
    fields = [f for f in listing.split("\0") if f]
    dropped, other = [], []
    for status, path in zip(fields[::2], fields[1::2], strict=False):
        (dropped if status == "D" and _dropped(path) else other).append(f"{status} {path}")
    return dropped, other


def _identity_line(ctx: PublicContext, epoch: str, offset: str) -> str:
    return f"{ctx.identity} {epoch} {offset}"


def build_public(ctx: PublicContext) -> tuple[str, list[str]]:
    """P, deterministically: the tag's tree without `PUBLIC_DROPPED` (161), the
    public tip as parent (none for a first release), the given identity, and
    the tag commit's date."""
    commit, release_tree = ctx.notes.get("tag_commit", ""), ctx.notes.get("tag_tree", "")
    if not (commit and release_tree):
        return "", ["no local release to publish (local-release did not hold)"]
    licensed = _licensed_in_tree(ctx, commit)
    if licensed:
        return "", [
            f"REFUSED: the {ctx.tag} tree carries licensed path(s): {', '.join(licensed[:5])}. "
            "A licensed path is a refusal, never a strip (80, #115 2286)"
        ]
    if not re.fullmatch(r"[^<>]+ <[^<>@\s]+@[^<>\s]+>", ctx.identity or ""):
        return "", [f"identity {ctx.identity!r} is not 'Name <email>' (the user's choice, #115)"]
    parent = _public_tip(ctx)
    if parent:
        ctx.run(["git", "-C", str(ctx.root), "fetch", "-q", ctx.public_url, "refs/heads/main"])
        if ctx.git("rev-parse", "FETCH_HEAD") != parent:
            return "", [f"public main {parent[:12]} could not be fetched as ls-remote named it"]
    when = ctx.git("show", "-s", "--format=%ct %cI", commit).split()
    if len(when) != 2:
        return "", [f"cannot read {commit[:12]}'s date"]
    epoch, iso = when
    offset = "+0000" if iso.endswith("Z") else iso[-6:].replace(":", "")
    who = _identity_line(ctx, epoch, offset)
    tree = _tree_without_dropped(ctx, release_tree)
    if not tree:
        return "", [f"{ctx.tag}'s tree without {', '.join(PUBLIC_DROPPED)} is empty"]
    ctx.notes["public_tree"] = tree

    def build(on: str) -> str:
        body = (
            f"tree {tree}\n"
            + (f"parent {on}\n" if on else "")
            + (f"author {who}\ncommitter {who}\n\nRelease {ctx.version}\n")
        )
        raw = Path(tempfile.mkdtemp()) / "commit"
        raw.write_bytes(body.encode("utf-8"))
        return ctx.git("hash-object", "-t", "commit", "-w", str(raw))

    # A re-run after P was pushed (#124): the tip may already BE this
    # release's P. Then P is the tip, never a second commit on top of it
    # waiting for a second approval. Decided by P's own determinism: rebuilt
    # on the tip's parent (or as a root), it reproduces the tip byte for byte.
    if parent:
        above = ctx.git("rev-list", "--parents", "-n", "1", parent).split()[1:]
        if len(above) <= 1 and build(above[0] if above else "") == parent:
            ctx.notes["public_parent"] = above[0] if above else ""
            return parent, [
                f"P {parent[:12]}: public main is already this release's P (rebuilt byte for "
                "byte on its own parent), so this run resumes it"
            ]
    public = build(parent)
    ctx.notes["public_parent"] = parent
    return public, [
        f"P {public[:12] or '-'}: tree {tree[:12]} ({ctx.tag}'s without "
        f"{', '.join(PUBLIC_DROPPED)}), "
        f"parent {parent[:12] if parent else 'none (the first public release: a root commit)'}"
    ]


def _guard(ctx: PublicContext, public: str, parent: str) -> Check:
    """The publish guard on P, by exit code (#35): its history source is the
    tag commit, whose ancestry carries the licensed parsers."""
    script = ctx.root / "scripts" / "publish_guard.py"
    argv = [
        sys.executable,
        str(script),
        "--candidate",
        public,
        "--source",
        ctx.notes.get("tag_commit", ""),
        "--root",
        str(ctx.root),
    ]
    if parent:
        argv[4:4] = ["--base", parent]
    code = ctx.run(argv).returncode
    return Check(code == 0, [f"publish_guard.py on P {public[:12]}: exit {code} (must be 0)"])


def _public_built(ctx: PublicContext) -> Check:
    """P builds, its tree equals the tag's, and the guard passes it. Re-derived
    every run: determinism makes it the same P the user approved."""
    allowed = release._public_release_allowed(ctx)
    if not allowed.ok:
        return allowed
    public, lines = build_public(ctx)
    if not public:
        return Check(False, lines)
    # P's tree is the release tree less `PUBLIC_DROPPED`, and nothing else
    # differs (161): measured as the trees' difference, not assumed from how
    # P was built.
    public_tree = ctx.git("rev-parse", f"{public}^{{tree}}")
    dropped, other = _tree_difference(ctx, ctx.notes["tag_tree"], public_tree)
    equal = public_tree == ctx.notes.get("public_tree") and not other
    lines.append(
        f"P's tree is {ctx.tag}'s without {', '.join(PUBLIC_DROPPED)}: {equal} "
        f"({len(dropped)} path(s) dropped; other differences {len(other)} {other[:5] or ''}, "
        "must be 0)"
    )
    # P's own parent, not the tip: when the tip IS P (a resumed run), the
    # range from the tip would be empty and the guard would see nothing.
    guard = _guard(ctx, public, ctx.notes.get("public_parent", ""))
    ok = equal and guard.ok
    if ok:
        ctx.notes["public_sha"] = public
    return Check(ok, [*lines, *guard.measured])


# --- the install before approval ---------------------------------------------------


def _latest(ctx: PublicContext, pattern: re.Pattern[str], authors=None) -> re.Match[str] | None:
    found = [
        m
        for login, line in _comments(ctx, ctx.tracking)
        if (authors is None or login in authors)
        and (m := pattern.match(line))
        and m.group(2) == ctx.version
    ]
    return found[-1] if found else None


def _installed(ctx: PublicContext) -> Check:
    record = _latest(ctx, INSTALL_LINE, release.FORGEJO_RECORD_AUTHORS)
    public = ctx.notes.get("public_sha", "")
    ok = bool(record) and record.group(1) == public and record.group(3) == "passed"
    return Check(
        ok,
        [
            f"Public-install record on #{ctx.tracking}: "
            f"{(record.group(1)[:12] + ' ' + record.group(3)) if record else 'none'} "
            f"(must be P {public[:12] or '?'} passed)"
        ],
    )


def _install_public(ctx: PublicContext) -> None:
    """Install P from an archive the host builds from P itself (`git archive`),
    tree-proved first and handed to the container as a file (#90's pattern).
    P is on no server yet, and nothing here pushes it."""
    public = ctx.notes["public_sha"]
    dest = Path(tempfile.mkdtemp()) / "source.tar.gz"
    ctx.run(
        [
            "git",
            "-C",
            str(ctx.root),
            "-c",
            "core.autocrlf=false",
            # A path `.gitattributes` marks `text` is still converted to the
            # native core.eol without this (b5 on #206; the class guard is
            # tests/test_archive_keeps_stored_bytes.py).
            "-c",
            "core.eol=lf",
            "archive",
            "--format=tar.gz",
            f"--prefix=policyforge-{ctx.version}/",
            "-o",
            str(dest),
            public,
        ]
    )
    ok, proof = release._archive_is_tree(ctx, dest, public)
    for line in proof:
        print(f"      {line}")
    if ok:
        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        formula = release._formula(ctx, f"file://{release.LOCAL_SOURCE}", digest)
        ran, passed, out = ctx.install(formula, ctx.version, dest)
        for line in out[-6:]:
            print(f"      | {line}")
        result = "passed" if ran and passed else ("did-not-run" if not ran else "FAILED")
    else:
        result = "refused"
    _post(
        ctx,
        (
            f"Container install of the {ctx.version} public commit P `{public}`, from an "
            f"archive 9b's host built from P and proved against P's tree: **{result}**. "
            "P is on no server.\n\n"
            f"Public-install: {public} release={ctx.version} result={result}"
        ),
    )


# --- the public shape, tested before approval (#322) ---------------------------------


def _shape_tested(ctx: PublicContext) -> Check:
    record = _latest(ctx, SHAPE_LINE, release.FORGEJO_RECORD_AUTHORS)
    public = ctx.notes.get("public_sha", "")
    ok = bool(record) and record.group(1) == public and record.group(3) == "passed"
    return Check(
        ok,
        [
            f"Public-shape record on #{ctx.tracking}: "
            f"{(record.group(1)[:12] + ' ' + record.group(3)) if record else 'none'} "
            f"(must be P {public[:12] or '?'} passed)"
        ],
    )


def _clone_alone(ctx: PublicContext, public: str) -> tuple[Path | None, list[str]]:
    """A fresh clone holding P's tree as ONE root commit, with only its tag.

    That is the public repo as GitHub first served 1.7.0, and as a user's
    clone of a first release sees it. P itself can't be cloned alone: from
    the second release on it has the previous P as its parent. So a staging
    repo borrows the objects (`alternates`, nothing copied, no ref written in
    the checkout), makes the root commit and an annotated tag, and a
    `--no-local` clone takes only what those two reach. The clone is then
    PROVED, not assumed: one commit, P's tree, the tag on it, no `HEAD~1`,
    and no alternates of its own. Anything else is `None`, and a refusal.
    """
    name, email = re.fullmatch(r"([^<>]+) <([^<>]+)>", ctx.identity).groups()
    who = ["-c", f"user.name={name.strip()}", "-c", f"user.email={email}"]
    tree = ctx.git("rev-parse", f"{public}^{{tree}}")
    common = Path(ctx.git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    base = Path(tempfile.mkdtemp())
    staging, clone = base / "staging.git", base / "clone"

    def git(where: Path, *args: str) -> str:
        return (ctx.run(["git", *who, "-C", str(where), *args]).stdout or "").strip()

    ctx.run(["git", "init", "-q", "--bare", "-b", "main", str(staging)])
    # newline="\n": a CRLF here makes the path end in "\r" on Windows, and git
    # then finds no object through it (measured while building this).
    (staging / "objects" / "info" / "alternates").write_text(
        (common / "objects").as_posix() + "\n", encoding="utf-8", newline="\n"
    )
    made = ctx.run(
        ["git", *who, "-C", str(staging), "commit-tree", tree, "-m", f"policyforge {ctx.version}"]
    )
    root = (made.stdout or "").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", root):
        why = (made.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return None, [f"could not make a root commit of P's tree {tree[:12]}: {why[0]}"]
    git(staging, "update-ref", "refs/heads/main", root)
    git(staging, "tag", "-a", ctx.tag, "-m", f"policyforge {ctx.version}", root)
    # LF on the host too: the fake runners read this checkout's files.
    ctx.run(
        [
            "git",
            "clone",
            "-q",
            "--no-local",
            "-c",
            "core.autocrlf=false",
            str(staging),
            str(clone),
        ]
    )
    commits = git(clone, "rev-list", "--count", "--all")
    head_tree = git(clone, "rev-parse", "HEAD^{tree}")
    tagged = git(clone, "rev-parse", f"{ctx.tag}^{{}}")
    parent = ctx.run(["git", "-C", str(clone), "rev-parse", "-q", "--verify", "HEAD~1"])
    borrowed = (clone / ".git" / "objects" / "info" / "alternates").exists()
    proof = [
        f"clone alone: {commits or '?'} commit(s), tree {head_tree[:12] or '-'} "
        f"(P's {tree[:12]}), {ctx.tag} -> {tagged[:12] or 'absent'}, "
        f"HEAD~1 {'resolves' if parent.returncode == 0 else 'absent'}, "
        f"alternates {'present' if borrowed else 'none'}"
    ]
    alone = (
        commits == "1"
        and head_tree == tree
        and tagged == git(clone, "rev-parse", "HEAD")
        and parent.returncode != 0
        and not borrowed
    )
    return (clone if alone else None), proof


def _test_public_shape(ctx: PublicContext) -> None:
    """CI's pytest and skip_gate in P's tree ALONE, and the result recorded.
    P is on no server, and nothing here pushes it."""
    public = ctx.notes["public_sha"]
    clone, proof = _clone_alone(ctx, public)
    for line in proof:
        print(f"      {line}")
    if clone is None:
        result = "refused"
    else:
        ran, passed, out = ctx.test_shape(clone)
        for line in out[-8:]:
            print(f"      | {line}")
        result = "passed" if ran and passed else ("did-not-run" if not ran else "FAILED")
    _post(
        ctx,
        (
            f"CI's `pytest` and `skip_gate.py`, run in a clone of the {ctx.version} public "
            f"commit P `{public}`'s tree ALONE (one root commit with only its tag, the shape "
            f"GitHub first holds): **{result}**. P is on no server.\n\n"
            f"Public-shape: {public} release={ctx.version} result={result}"
        ),
    )


def _ci_gitleaks(clone: Path) -> tuple[str, str] | None:
    """The gitleaks version and sha256 CI pins, read from the clone's own
    workflow, so the shape test installs what that tree's CI installs."""
    try:
        text = (clone / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    except OSError:
        return None
    version = re.search(r'GITLEAKS_VERSION:\s*"([0-9][0-9.]*)"', text)
    digest = re.search(r'GITLEAKS_SHA256:\s*"([0-9a-f]{64})"', text)
    return (version.group(1), digest.group(1)) if version and digest else None


#: The exit code the shape script gives when it never reached the tests.
SHAPE_SETUP_FAILED = 3


def _shape_script(gitleaks_version: str, gitleaks_sha256: str) -> str:
    """CI's `scan-and-test` install, then its two test steps, in a clone of
    the read-only clone (so git owns it). Setup failing is not a test result:
    it exits SHAPE_SETUP_FAILED, read as did-not-run. Either test step
    failing exits 1."""
    url = (
        "https://github.com/gitleaks/gitleaks/releases/download/"
        f"v{gitleaks_version}/gitleaks_{gitleaks_version}_linux_x64.tar.gz"
    )
    setup = SHAPE_SETUP_FAILED
    return "\n".join(
        [
            "set -u",
            # One stream, in order: apt's and pip's warnings on stderr otherwise
            # landed after pytest's summary and pushed it out of the tail.
            "exec 2>&1",
            "export DEBIAN_FRONTEND=noninteractive PIP_ROOT_USER_ACTION=ignore",
            f'fail() {{ echo "public-shape: setup failed: $1"; exit {setup}; }}',
            "apt-get update -qq > /dev/null && apt-get install -y -qq --no-install-recommends "
            "git curl ca-certificates > /dev/null || fail apt",
            "git config --global --add safe.directory '*'",
            "git clone -q /src /work || fail clone",
            "cd /work",
            f"curl -sSfL -o /tmp/gitleaks.tgz '{url}' || fail gitleaks-download",
            f"echo '{gitleaks_sha256}  /tmp/gitleaks.tgz' | sha256sum -c - > /dev/null "
            "|| fail gitleaks-sha256",
            "tar -xzf /tmp/gitleaks.tgz -C /usr/local/bin gitleaks || fail gitleaks-unpack",
            # CI's install lines, verbatim (a test holds them to ci.yml).
            "pip install --require-hashes --only-binary :all: -r requirements/ci.txt "
            "> /tmp/pip.log 2>&1 || { tail -5 /tmp/pip.log; fail pip; }",
            "pip install --require-hashes --only-binary :all: -r requirements/build.txt "
            "> /tmp/pip-build.log 2>&1 || { tail -5 /tmp/pip-build.log; fail pip-build; }",
            "pip install --no-deps --no-build-isolation -e . > /tmp/pip-project.log 2>&1 "
            "|| fail pip-project",
            'echo "public-shape: $(git rev-list --count --all) commit(s); tags: $(git tag)"',
            # CI's own two steps, as `scan-and-test` spells them.
            'pytest -q -rs -n auto --junitxml="/tmp/pytest.xml"; t=$?',
            'python scripts/skip_gate.py "/tmp/pytest.xml"; s=$?',
            'echo "public-shape: pytest exit $t, skip_gate exit $s"',
            '[ "$t" = 0 ] && [ "$s" = 0 ] || exit 1',
        ]
    )


def _shape_docker_argv(clone: Path, script: str) -> list[str]:
    """The clone read-only at /src and the script, nothing else: no `-e`,
    `--env` or `--env-file`, so no host variable (FORGEJO_TOKEN included)
    reaches the container, as `release._docker_argv`."""
    return ["docker", "run", "--rm", "-v", f"{clone}:/src:ro", SHAPE_IMAGE, "bash", "-c", script]


def _real_test_shape(clone: Path) -> tuple[bool, bool, list[str]]:
    import shutil

    if shutil.which("docker") is None:
        return False, False, ["docker is not on PATH"]
    pins = _ci_gitleaks(clone)
    if pins is None:
        return False, False, ["the clone's ci.yml names no GITLEAKS_VERSION and GITLEAKS_SHA256"]
    proc = release._real_run(_shape_docker_argv(clone, _shape_script(*pins)))
    lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-15:]
    lines.append(f"docker exit status: {proc.returncode}")
    if proc.returncode not in (0, 1):
        return False, False, lines
    return True, proc.returncode == 0, lines


# --- macOS, Windows and Linux on GitHub, before approval (#326) ---------------------


def _ci_ref(ctx: PublicContext) -> str:
    return f"refs/heads/release/{ctx.version}"


def _ci_tip(ctx: PublicContext) -> str:
    """policyforge-ci's release/X.Y.Z, asked of the server ("" when absent)."""
    out = (_git_net(ctx, "ls-remote", ctx.ci_url, _ci_ref(ctx)).stdout or "").split()
    return out[0] if out else ""


def build_ci_candidate(ctx: PublicContext) -> tuple[str, str, list[str]]:
    """(candidate, its parent, lines): a commit holding the release tree (P's
    plus `PUBLIC_DROPPED`, whose workflows are what run there) for
    policyforge-ci's release/X.Y.Z. Its parent is that branch's tip when the
    branch exists, so the branch only fast-forwards (80's hand-pushed 1.8.0
    candidate is one); otherwise it's a root commit, the public shape. The
    identity and date are P's, so a re-run builds the same commit. When the tip
    already holds the release tree, the tip IS the candidate and nothing is built."""
    commit, tree = ctx.notes.get("tag_commit", ""), ctx.notes.get("tag_tree", "")
    if not (commit and tree):
        return "", "", ["no local release to test (local-release did not hold)"]
    tip = _ci_tip(ctx)
    if tip:
        _git_net(ctx, "fetch", "-q", ctx.ci_url, _ci_ref(ctx))
        if ctx.git("rev-parse", "FETCH_HEAD") != tip:
            return "", "", [f"fetched policyforge-ci release/{ctx.version} isn't {tip[:12]}"]
        if ctx.git("rev-parse", f"{tip}^{{tree}}") == tree:
            return (
                tip,
                "",
                [
                    f"policyforge-ci release/{ctx.version} at {tip[:12]} already holds the "
                    "release tree"
                ],
            )
    when = ctx.git("show", "-s", "--format=%ct %cI", commit).split()
    if len(when) != 2:
        return "", "", [f"cannot read {commit[:12]}'s date"]
    epoch, iso = when
    offset = "+0000" if iso.endswith("Z") else iso[-6:].replace(":", "")
    who = _identity_line(ctx, epoch, offset)
    body = (
        f"tree {tree}\n"
        + (f"parent {tip}\n" if tip else "")
        + f"author {who}\ncommitter {who}\n\n"
        + f"Candidate {ctx.version}: the release tree, for the platform tests\n"
    )
    raw = Path(tempfile.mkdtemp()) / "commit"
    raw.write_bytes(body.encode("utf-8"))
    candidate = ctx.git("hash-object", "-t", "commit", "-w", str(raw))
    return (
        candidate,
        tip,
        [
            f"candidate {candidate[:12] or '-'}: tree {tree[:12]} (the release tree), parent "
            f"{tip[:12] if tip else 'none (a root commit)'}"
        ],
    )


def _ci_candidate_pushed(ctx: PublicContext) -> Check:
    """policyforge-ci's release/X.Y.Z holds the release tree."""
    tip, tree = _ci_tip(ctx), ctx.notes.get("tag_tree", "")
    held = ""
    if tip:
        _git_net(ctx, "fetch", "-q", ctx.ci_url, _ci_ref(ctx))
        held = ctx.git("rev-parse", f"{tip}^{{tree}}")
    ok = bool(tip) and bool(tree) and held == tree
    if ok:
        ctx.notes["ci_candidate"] = tip
    return Check(
        ok,
        [
            f"policyforge-ci release/{ctx.version} -> {tip[:12] or 'absent'}, tree "
            f"{held[:12] or '-'} (must be the release tree {tree[:12] or '?'})"
        ],
    )


def _push_ci_candidate(ctx: PublicContext) -> None:
    """Build the candidate, guard it, and push it, fast-forward only. A guard
    that doesn't pass stops here: nothing is pushed."""
    candidate, parent, lines = build_ci_candidate(ctx)
    for line in lines:
        print(f"      {line}")
    if not candidate or candidate == _ci_tip(ctx):
        return
    guard = _guard(ctx, candidate, parent)
    for line in guard.measured:
        print(f"      {line}")
    if not guard.ok:
        print("      REFUSED: the publish guard didn't pass the candidate; nothing was pushed")
        return
    done = _git_net(ctx, "push", ctx.ci_url, f"{candidate}:{_ci_ref(ctx)}")
    print(f"      push to policyforge-ci release/{ctx.version}: exit {done.returncode}")


#: The ci-read summary parser lives in `release.py`, which the cut step reads
#: the same summary with (#398); kept here under its old name.
ci_run_for = release.ci_run_for


def ci_verdict(run: dict | None) -> tuple[str | None, list[str]]:
    """("passed" or "FAILED", lines) for a COMPLETED run; (None, lines) while
    there's no run or it hasn't finished. The run must have concluded
    `success` and every required platform job must have succeeded: any
    other conclusion, a failed job, or a required one missing or skipped,
    is FAILED."""
    if run is None:
        return None, ["no GitHub run on the candidate yet"]
    lines = [f"GitHub run {run['status']}/{run['conclusion']}"] + [
        f"  {mark}: {name}" for mark, name in run["jobs"]
    ]
    if run["status"] != "completed":
        return None, lines
    failed = [name for mark, name in run["jobs"] if mark == "FAIL"]
    missing = [
        rule.pattern
        for rule in CI_REQUIRED
        if not any(rule.fullmatch(name) and mark == "success" for mark, name in run["jobs"])
    ]
    # The run's own conclusion too (1d on PR 362): a cancelled, timed-out or
    # failed run is not a pass, even with every required job green.
    if failed or missing or run["conclusion"] != "success":
        return "FAILED", [
            *lines,
            f"run conclusion {run['conclusion']} (must be success); failed: {failed or 'none'}; "
            f"required and not passed: {missing or 'none'}",
        ]
    return "passed", lines


def _ci_passed(ctx: PublicContext) -> Check:
    record = _latest(ctx, CI_LINE, release.FORGEJO_RECORD_AUTHORS)
    tree = ctx.notes.get("tag_tree", "")
    ok = bool(record) and bool(tree) and record.group(3) == tree and record.group(4) == "passed"
    said = f"{record[1][:12]} tree {record[3][:12]} {record[4]}" if record else "none"
    return Check(
        ok,
        [
            f"Candidate-ci record on #{ctx.tracking}: {said} "
            f"(must be the release tree {tree[:12] or '?'} passed)"
        ],
    )


def _await_ci(ctx: PublicContext) -> None:
    """Read GitHub's result on the candidate through ci-read until its run
    completes or the wait runs out, then record it. Running out is NOT RUN:
    a result nobody read is never a pass."""
    candidate = ctx.notes["ci_candidate"]
    branch = f"release/{ctx.version}"
    reads = max(1, int(ctx.ci_timeout // ctx.ci_poll_every) + 1)
    result, lines = "did-not-run", []
    for attempt in range(reads):
        summary = ctx.read_ci(ctx)
        if summary is None:
            lines = ["ci-read could not be read this time"]
        else:
            verdict, lines = ci_verdict(ci_run_for(summary, branch, candidate))
            if verdict:
                result = verdict
                break
        if attempt < reads - 1:
            ctx.sleep(ctx.ci_poll_every)
    else:
        lines.append(
            f"no completed GitHub run on the candidate within {ctx.ci_timeout:.0f} s: NOT RUN"
        )
    for line in lines:
        print(f"      {line}")
    shown = "\n".join(f"    {line}" for line in lines)
    _post(
        ctx,
        (
            f"GitHub's CI on the candidate `{candidate}` (the release tree, `{branch}` on "
            f"policyforge-ci), read through ops ci-read: **{result}**.\n\n{shown}\n\n"
            f"Candidate-ci: {candidate} release={ctx.version} "
            f"tree={ctx.notes.get('tag_tree', '')} result={result}"
        ),
    )


def _real_read_ci(ctx: PublicContext) -> str | None:
    """Dispatch ops' ci-read, wait for it, and return its job log: the
    summary it prints. None when any of that fails, which the caller reads
    as "not yet", never as a result."""
    return release.read_ci_summary(ctx.forgejo, ctx.sleep)


# --- macOS wheels, before approval (#325) ----------------------------------------------


def _macos_ready(ctx: PublicContext) -> Check:
    """Every runtime pin in P has a macOS wheel: P's own lock and pyproject,
    and the formula this run installs (`--formula`). arm64 must hold; an Intel
    miss is advisory, printed and counted (80's ruling on #325). Asked once
    per P in a run, because the approval gate and the push gate both read it
    and each answer is a round of PyPI calls. Not recorded on the issue: it's
    a property of P's lock and of PyPI, so a resumed run asks again."""
    public = ctx.notes.get("public_sha", "")
    if not public:
        return Check(False, ["macOS wheels: no P built yet"])
    key = f"macos-wheels {public}"
    if key not in ctx.notes:
        code, lines = ctx.macos_wheels(
            ctx.git("show", f"{public}:requirements/runtime.txt"),
            release._base_formula(ctx),
            ctx.git("show", f"{public}:pyproject.toml"),
        )
        ctx.notes[key] = "\n".join([str(code), *lines])
    code, *lines = ctx.notes[key].split("\n")
    return Check(
        code == "0",
        [f"macOS wheels for P {public[:12]}: exit {code} (must be 0)", *(f"  {x}" for x in lines)],
    )


# --- the user's approval ---------------------------------------------------------------


def _approved(ctx: PublicContext) -> Check:
    """Only the user's own account approves, and only the P built this run."""
    record = _latest(ctx, APPROVAL_LINE, {APPROVER})
    public = ctx.notes.get("public_sha", "")
    ok = bool(record) and bool(public) and record.group(1) == public
    return Check(
        ok,
        [
            f"Publish-approved by {APPROVER} on #{ctx.tracking}: "
            f"{record.group(1)[:12] if record else 'none'} (must be P {public[:12] or '?'})"
        ],
    )


# --- the push: fast-forward only, P and its tag only --------------------------------


def _git_net(ctx: PublicContext, *args: str) -> subprocess.CompletedProcess:
    return ctx.run(["git", "-C", str(ctx.root), *args])


def _public_main_done(ctx: PublicContext) -> Check:
    public, tip = ctx.notes.get("public_sha", ""), _public_tip(ctx)
    return Check(
        bool(public) and tip == public,
        [f"{PUBLIC_REPO} main -> {tip[:12] or 'none'} (must be P {public[:12] or '?'})"],
    )


def _push_main(ctx: PublicContext) -> None:
    """A plain push. P's parent is the public tip (or none at all), so this
    is a fast-forward or the first commit; git refuses anything else."""
    _git_net(ctx, "push", ctx.public_url, f"{ctx.notes['public_sha']}:refs/heads/main")


def _public_tag_object(ctx: PublicContext) -> str:
    """The public tag: a new annotated tag object naming P, deterministic."""
    public, commit = ctx.notes["public_sha"], ctx.notes["tag_commit"]
    when = ctx.git("show", "-s", "--format=%ct %cI", commit).split()
    epoch, iso = when
    offset = "+0000" if iso.endswith("Z") else iso[-6:].replace(":", "")
    body = (
        f"object {public}\ntype commit\ntag {ctx.tag}\n"
        f"tagger {_identity_line(ctx, epoch, offset)}\n\nRelease {ctx.version}\n"
    )
    raw = Path(tempfile.mkdtemp()) / "tag"
    raw.write_bytes(body.encode("utf-8"))
    return ctx.git("hash-object", "-t", "tag", "-w", str(raw))


def _public_tag_done(ctx: PublicContext) -> Check:
    public = ctx.notes.get("public_sha", "")
    out = (
        _git_net(ctx, "ls-remote", "--tags", ctx.public_url, f"{ctx.tag}^{{}}").stdout or ""
    ).split()
    there = out[0] if out else ""
    return Check(
        bool(public) and there == public,
        [f"{PUBLIC_REPO} {ctx.tag} -> {there[:12] or 'absent'} (must be P {public[:12] or '?'})"],
    )


def _push_tag(ctx: PublicContext) -> None:
    tag = _public_tag_object(ctx)
    _git_net(ctx, "push", ctx.public_url, f"{tag}:refs/tags/{ctx.tag}")


# --- GitHub, signed out, after the mirror ---------------------------------------------


def _signed_out_all_refs(ctx: PublicContext) -> dict[str, str] | None:
    """Every ref GitHub serves, asked with NO credentials: {ref: sha}, or None
    when `ls-remote` failed, which is not the same as GitHub holding nothing.

    The guarantee is `run_signed_out`'s environment (no home, no config, no
    helper, no repository: #121). The `-c` settings repeat two of its closures
    for a reader of this line; on their own they were measured NOT to be
    enough (#121), so nothing relies on them."""
    out = ctx.run_signed_out(
        ["git", "-c", "credential.helper=", "-c", "core.askPass=", "ls-remote", ctx.github_url]
    )
    if out.returncode != 0:
        return None
    refs: dict[str, str] = {}
    for line in (out.stdout or "").splitlines():
        sha, _, ref = line.partition("\t")
        if ref.strip() and re.fullmatch(r"[0-9a-f]{40}", sha.strip()):
            refs[ref.strip()] = sha.strip()
    return refs


#: What GitHub may serve beside a release (80's ruling on #164, 2942): the one
#: branch `main`, `HEAD` when it is `main`, and release tags with their peels.
_RELEASE_TAG = re.compile(r"refs/tags/v\d+\.\d+\.\d+(\^\{\})?")

#: A pull request's refs. GitHub creates one for every PR ever opened and keeps
#: it after the PR closes and its branch is gone, and an owner can't delete it,
#: so refusing on these would refuse every later release for one PR in the past
#: (ba measured seven on rdazzleman/policyforge, 2026-10-03, from Dependabot's
#: PRs), and a stranger's PR could hold a release. Counted and listed, never
#: dropped (2942). Any other shape under refs/pull/ is unwanted.
_PULL_REF = re.compile(r"refs/pull/\d+/(head|merge)")


def _github_refs_unwanted(refs: dict[str, str]) -> tuple[list[str], list[str]]:
    """(unwanted, pull). Every served ref lands in exactly one class: allowed,
    pull, or unwanted, so none drops out of the count. Unwanted is any branch
    but `main`, `HEAD` naming anything but `main`'s commit, any tag that isn't
    `vX.Y.Z`, a malformed pull ref, and any other namespace."""
    main = refs.get("refs/heads/main", "")
    unwanted, pull = [], []
    for ref in sorted(refs):
        if ref == "refs/heads/main" or _RELEASE_TAG.fullmatch(ref):
            continue
        if ref == "HEAD" and main and refs[ref] == main:
            continue
        (pull if _PULL_REF.fullmatch(ref) else unwanted).append(ref)
    return unwanted, pull


def _mirrored_now(ctx: PublicContext) -> Check:
    """GitHub, signed out, holds P as `main` and as the tag, and nothing else a
    reader would take for part of the release (#164, item 3)."""
    return _mirror_check(ctx, _signed_out_all_refs(ctx))


def _mirror_check(ctx: PublicContext, refs: dict[str, str] | None) -> Check:
    """`_mirrored_now` over refs already read, so a caller reads GitHub once."""
    public = ctx.notes.get("public_sha", "")
    if refs is None:
        return Check(False, ["GitHub (signed out) didn't answer `ls-remote`: nothing was read"])
    main = refs.get("refs/heads/main", "")
    tag = refs.get(f"refs/tags/{ctx.tag}^{{}}", refs.get(f"refs/tags/{ctx.tag}", ""))
    unwanted, pull = _github_refs_unwanted(refs)
    return Check(
        bool(public) and main == public and tag == public and not unwanted,
        [
            f"GitHub (signed out) main -> {main[:12] or 'absent'}, "
            f"{ctx.tag} -> {tag[:12] or 'absent'} (both must be P {public[:12] or '?'})",
            f"other refs on GitHub: unwanted {len(unwanted)} {unwanted or ''} (must be 0: only "
            f"main, HEAD at main and vX.Y.Z tags); pull refs {len(pull)} "
            f"{[r.removeprefix('refs/') for r in pull] or ''} (kept by GitHub, not refused)",
        ],
    )


def _await_mirror(ctx: PublicContext) -> None:
    """The mirror is asynchronous: look until both refs are P, or say loudly
    that the timeout passed. Nothing is pushed to GitHub from here.

    Unwanted refs stop the wait at once: waiting can't remove a branch, and
    timing out on them would blame the mirror for the wrong cause."""
    waited = 0.0
    while True:
        refs = _signed_out_all_refs(ctx)
        now = _mirror_check(ctx, refs)
        if refs is not None and _github_refs_unwanted(refs)[0]:
            print(
                f"    UNWANTED REFS ON GITHUB: {now.measured[1]}. Delete them on GitHub "
                "(signed in, as the owner); no session pushes there."
            )
            return
        if now.ok or waited >= ctx.mirror_timeout:
            if not now.ok:
                print(
                    f"    MIRROR TIMEOUT after {waited:.0f}s: {now.measured[0]}. Check the push "
                    "mirror's status in Forgejo's settings for policyforge-public."
                )
            return
        ctx.sleep(ctx.poll_every)
        waited += ctx.poll_every


# --- GitHub's served archive, proved before its sha is used ----------------------------


def _archive_recorded(ctx: PublicContext) -> Check:
    record = _latest(ctx, PUBLISHED_LINE, release.FORGEJO_RECORD_AUTHORS)
    public = ctx.notes.get("public_sha", "")
    ok = bool(record) and record.group(1) == public
    if ok:
        ctx.notes["github_archive_sha256"] = record.group(3)
    return Check(
        ok,
        [
            f"Public-published record on #{ctx.tracking}: "
            f"{(record.group(1)[:12] + ' sha256 ' + record.group(3)[:12]) if record else 'none'}"
        ],
    )


def _prove_archive(ctx: PublicContext) -> None:
    """GitHub's tag archive, fetched signed out, must BE P's tree before its
    sha256 goes anywhere: its bytes differ from any archive we built (its
    prefix, its gzip), so only the tree links it to what was approved."""
    public = ctx.notes["public_sha"]
    url = ctx.github_archive(ctx.tag)
    dest = Path(tempfile.mkdtemp()) / "github.tar.gz"
    status = ctx.fetch_anonymous(url, dest)
    print(f"      {url}: HTTP {status} (must be 200)")
    if status != 200 or not dest.is_file():
        return
    ok, proof = release._archive_is_tree(ctx, dest, public)
    for line in proof:
        print(f"      {line}")
    if not ok:
        print("      REFUSED: GitHub serves an archive that is not P's tree; nothing recorded")
        return
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    _post(
        ctx,
        (
            f"**{ctx.tag} is public:** `{PUBLIC_REPO}` main and `{ctx.tag}` name P `{public}`, the "
            f"mirror carried both to GitHub (read signed out), and GitHub's archive at the tag is "
            f"P's tree. Its sha256 is what the formula will say.\n\n"
            f"Public-published: {public} release={ctx.version} archive-sha256={digest}"
        ),
    )


# --- the topology preflight (#194) ------------------------------------------------------
#
# The rules for what goes where live in `policyforge/ops`'s `inventory.yaml`,
# and its check (`tools/inventory_check.py`) measures them. Before anything else
# runs, every repository this run writes to must HOLD there: drift, or a
# repository the check can't measure, refuses the run (80's ruling on #194).
# It runs in `main` before the plan, in a dry run too, and refuses with exit 3,
# so the steps' own numbers (and the exit codes built from them) don't move.
# Which repositories those are is derived from this run's own four URLs matched
# to the inventory's entries, never listed by hand, and a URL the inventory
# doesn't record refuses as well.


def _repo_key(url: str) -> tuple[str, str]:
    """("github" or "forgejo", "owner/name") for a URL or a local path, so
    localhost against 127.0.0.1, a trailing `.git` or slash, and case don't
    decide a match."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = (parsed.path if parsed.scheme else url).replace("\\", "/")
    name = "/".join(path.strip("/").removesuffix(".git").lower().split("/")[-2:])
    return ("github" if host.endswith("github.com") else "forgejo", name)


class _RunInstruments:
    """`inventory_check`'s instruments, made from this run's own: Forgejo as
    the operator's bot (`ctx.forgejo`, and `ctx.run`'s git with FORGEJO_TOKEN),
    GitHub only through `fetch_anonymous` and `run_signed_out`. Nothing here
    holds or passes a GitHub credential. The runner isn't read: no repository
    this run writes to depends on it, so its verdict says so and isn't required."""

    def __init__(self, ctx: PublicContext, unreadable: type[Exception]):
        self.ctx, self.Unreadable = ctx, unreadable

    def forgejo_repo(self, full: str) -> dict:
        status, body, _ = self.ctx.forgejo("GET", f"/repos/{full}", None)
        if status != 200 or not isinstance(body, dict):
            raise self.Unreadable(f"Forgejo answered {status} to the operator's bot")
        return body

    def forgejo_refs(self, url: str) -> list[str]:
        done = _git_net(self.ctx, "ls-remote", url)
        if done.returncode != 0:
            raise self.Unreadable("ls-remote failed as the operator's bot")
        return [line.split("\t")[1] for line in (done.stdout or "").splitlines() if "\t" in line]

    def forgejo_workflows(self, full: str, ref: str) -> list[str]:
        for folder in (".forgejo/workflows", ".github/workflows"):
            status, body, _ = self.ctx.forgejo(
                "GET", f"/repos/{full}/contents/{folder}?ref={ref}", None
            )
            if status == 200 and isinstance(body, list):
                return [f"{folder}/{item.get('name')}" for item in body]
        return []

    def _anonymous(self, url: str) -> tuple[int, bytes]:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "body"
            status = self.ctx.fetch_anonymous(url, dest)
            return status, dest.read_bytes() if dest.exists() else b""

    def github_repo(self, full: str) -> tuple[int, dict | None]:
        import json

        status, body = self._anonymous(f"https://api.github.com/repos/{full}")
        return status, json.loads(body) if status == 200 and body else None

    def github_refs(self, url: str) -> list[str]:
        out = self.ctx.run_signed_out(
            ["git", "-c", "credential.helper=", "-c", "core.askPass=", "ls-remote", url]
        )
        if out.returncode != 0:
            raise self.Unreadable("not readable signed out")
        return [line.split("\t")[1] for line in (out.stdout or "").splitlines() if "\t" in line]

    def github_text(self, url: str) -> tuple[int, str]:
        status, body = self._anonymous(url)
        return status, body.decode("utf-8", errors="replace")

    def http_status(self, url: str) -> int:
        return self._anonymous(url)[0]

    def runner_capacity(self) -> int:
        raise self.Unreadable("the publish preflight doesn't read the runner")


def _topology(ctx: PublicContext) -> Check:
    """Every repository this run writes to HOLDS in the ops inventory."""
    if ctx.ops_root is None:
        return Check(
            False,
            [
                "no ops checkout: pass --ops <a clone of policyforge/ops>, whose "
                "inventory.yaml the run's repositories are checked against first (#194)"
            ],
        )
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ops_inventory_check", ctx.ops_root / "tools" / "inventory_check.py"
        )
        tool = importlib.util.module_from_spec(spec)
        # Registered before it runs: its dataclasses look their module up here.
        sys.modules[spec.name] = tool
        spec.loader.exec_module(tool)
        inventory = tool.load(ctx.ops_root / "inventory.yaml")
        entries = {_repo_key(e["url"]): e["id"] for e in inventory["repositories"]}
    except Exception as exc:  # noqa: BLE001 - not knowing is a refusal
        return Check(False, [f"could not read the ops inventory or its check: {exc}"])
    ops_head = ctx.run(["git", "-C", str(ctx.ops_root), "rev-parse", "--short", "HEAD"]).stdout
    lines = [f"ops inventory at {(ops_head or '?').strip()}, {ctx.ops_root}"]
    writes = {
        _repo_key(url): url
        for url in (ctx.public_url, ctx.tap_url, ctx.github_url, ctx.github_tap_url, ctx.ci_url)
    }
    missing = [url for key, url in writes.items() if key not in entries]
    lines += [f"NOT IN THE INVENTORY  {url} (this run writes to it)" for url in missing]
    required = {entries[key] for key in writes if key in entries}
    try:
        verdicts = tool.check(inventory, _RunInstruments(ctx, tool.Unreadable))
    except Exception as exc:  # noqa: BLE001
        return Check(False, [*lines, f"the inventory check failed: {exc}"])
    held = [v for v in verdicts if v.repo in required]
    lines += [v.line() for v in held]
    others = [v for v in verdicts if v.repo not in required and v.kind != "HOLDS"]
    if others:
        lines.append(
            f"(not written by this run, so not required: {len(others)} not holding, "
            "e.g. " + ", ".join(f"{v.repo} {v.kind}" for v in others[:3]) + ")"
        )
    return Check(not missing and all(v.kind == "HOLDS" for v in held), lines)


# --- the plan ----------------------------------------------------------------------------


def steps() -> list[Step]:
    return [
        Step(
            "local-release",
            "the version was released locally, and its record agrees",
            CHECK,
            _local_released,
            _local_released,
        ),
        Step(
            "public-build",
            "build P: the tag's tree exactly, guarded, on no server",
            CHECK,
            _public_built,
            _public_built,
        ),
        Step(
            "public-install",
            "install P from a host-built archive, before approval",
            ACT,
            lambda c: _all_held(c, "public_sha"),
            _installed,
            _install_public,
            outward=True,
            would="install P in a clean container and post the result",
        ),
        Step(
            "public-shape",
            "CI's pytest and skip_gate pass in P's tree alone, before approval",
            ACT,
            _installed,
            _shape_tested,
            _test_public_shape,
            would="run CI's pytest and skip_gate on Linux in a clone of P's tree as one "
            "root commit with only its tag, and post the result",
        ),
        Step(
            "ci-candidate",
            f"a candidate with the release tree on {CI_REPO} (guarded, fast-forward only)",
            ACT,
            _shape_tested,
            _ci_candidate_pushed,
            _push_ci_candidate,
            outward=True,
            would=f"push a commit holding the release tree to {CI_REPO} release/<version>, "
            "no force; its mirror carries it to the private GitHub repo, where Actions runs",
        ),
        Step(
            "ci-result",
            "GitHub's Linux, macOS and Windows jobs pass on it, read through ops ci-read",
            ACT,
            _ci_candidate_pushed,
            _ci_passed,
            _await_ci,
            would="dispatch ops ci-read until GitHub's run on the candidate completes (or "
            "the wait runs out: NOT RUN), and post the result",
        ),
        Step(
            "approval",
            "the user approves this exact P",
            WAIT,
            lambda c: release._all(_installed(c), _shape_tested(c), _macos_ready(c), _ci_passed(c)),
            _approved,
            would=f"a comment by {APPROVER} on the tracking issue whose last line is "
            "`Publish-approved: <P> release=<version>`, once P's runtime pins have macOS wheels",
        ),
        Step(
            "public-main",
            f"put P on {PUBLIC_REPO} main (fast-forward only)",
            ACT,
            release._refused_first(
                lambda c: release._all(
                    _approved(c), _installed(c), _shape_tested(c), _macos_ready(c), _ci_passed(c)
                )
            ),
            _public_main_done,
            _push_main,
            outward=True,
            would=f"push P to {PUBLIC_REPO} main, no force",
        ),
        Step(
            "public-tag",
            f"tag P on {PUBLIC_REPO}",
            ACT,
            _public_main_done,
            _public_tag_done,
            _push_tag,
            outward=True,
            would=f"push a new annotated tag naming P to {PUBLIC_REPO}",
        ),
        Step(
            "github-mirrored",
            "the mirror carried main and the tag to GitHub (signed out)",
            ACT,
            _public_tag_done,
            _mirrored_now,
            _await_mirror,
            would="poll GitHub signed out until main and the tag are P, or time out loudly",
        ),
        Step(
            "github-archive",
            "GitHub's tag archive is P's tree; record its sha256",
            ACT,
            _mirrored_now,
            _archive_recorded,
            _prove_archive,
            outward=True,
            would="fetch GitHub's tag archive signed out, prove it, post its sha256",
        ),
        Step(
            "candidate-install",
            "install the formula from GitHub's own archive, before the tap",
            ACT,
            _candidate_ready,
            _candidate_installed,
            _candidate_install,
            would="install the formula pointing at GitHub's tag archive in a clean container",
        ),
        Step(
            "tap-commit",
            f"commit the formula to {TAP_REPO} (fast-forward only)",
            ACT,
            lambda c: release._install_check(c, "", "candidate install"),
            _tap_holds,
            _commit_tap,
            outward=True,
            would=f"commit Formula/policyforge.rb to {TAP_REPO} main and push it, no force",
        ),
        Step(
            "tap-mirrored",
            "the tap's mirror carried it to GitHub (signed out)",
            ACT,
            _tap_holds,
            _tap_mirrored_now,
            _await_tap_mirror,
            would="poll GitHub's tap signed out until main is the tap commit, or time out loudly",
        ),
        Step(
            "release-check",
            "what a user installs is what was released (signed out)",
            CHECK,
            release._release_check_passes,
            release._release_check_passes,
        ),
    ]


# --- the tap (part B): the formula through its own Forgejo repo and mirror -------------


def _candidate_ready(ctx: PublicContext) -> Check:
    """The archive the formula will name is the one proved: its sha256 now,
    fetched again, equals the recorded one (GitHub could re-encode it)."""
    recorded = ctx.notes.get("github_archive_sha256", "")
    url = ctx.github_archive(ctx.tag)
    now = ctx.hash_url(url) if recorded else ""
    return Check(
        bool(recorded) and now == recorded,
        [f"{url}: sha256 {now[:12] or '-'} (must be the proved {recorded[:12] or '?'})"],
    )


def _candidate_formula(ctx: PublicContext, archive_sha: str) -> str:
    """The formula the candidate install uses: GitHub's tag archive at the
    proved sha256, on the operator's base. Deterministic, so a resumed run
    rebuilds exactly the one a record names."""
    return release._formula(ctx, ctx.github_archive(ctx.tag), archive_sha)


def _candidate_installed(ctx: PublicContext) -> Check:
    """The candidate install passed: in this run, or by its record for this
    P, this archive and this formula (#124). On the record, the formula is
    rebuilt for the tap steps, which commit exactly the one installed."""
    if ctx.notes.get("candidate install"):
        return release._install_check(ctx, "", "candidate install")
    record = _latest(ctx, CANDIDATE_LINE, release.FORGEJO_RECORD_AUTHORS)
    public = ctx.notes.get("public_sha", "")
    archive = ctx.notes.get("github_archive_sha256", "")
    formula = _candidate_formula(ctx, archive) if archive else ""
    formula_sha = hashlib.sha256(formula.encode("utf-8")).hexdigest() if formula else ""
    ok = (
        bool(record)
        and record.group(1) == public
        and record.group(3) == archive
        and record.group(4) == formula_sha
        and record.group(5) == "passed"
    )
    if ok:
        ctx.notes["candidate install"] = "passed"
        ctx.notes["candidate_formula"] = formula
    said = (
        f"P {record.group(1)[:12]} archive {record.group(3)[:12]} "
        f"formula {record.group(4)[:12]} {record.group(5)}"
        if record
        else "none"
    )
    return Check(
        ok,
        [
            f"Candidate-install record on #{ctx.tracking}: {said} (must be P {public[:12] or '?'}"
            f" archive {archive[:12] or '?'} formula {formula_sha[:12] or '?'} passed)"
        ],
    )


def _candidate_install(ctx: PublicContext) -> None:
    """`release.py`'s own candidate install: the formula's url is GitHub's tag
    archive, as a user gets it, and the container fetches it. The result is
    recorded, so a resumed run needn't repeat it (#124)."""
    release._install(ctx, ctx.github_archive(ctx.tag), "candidate install")
    said = ctx.notes.get("candidate install", "")
    result = (
        "passed"
        if said == "passed"
        else "FAILED"
        if said == "FAILED"
        else "did-not-run"
        if said == "did not run"
        else "refused"
    )
    formula = ctx.notes.get("candidate_formula") or _candidate_formula(
        ctx, ctx.notes.get("github_archive_sha256", "")
    )
    _post(
        ctx,
        (
            f"Container install of the {ctx.version} formula from GitHub's own tag archive: "
            f"**{result}**.\n\n"
            f"Candidate-install: {ctx.notes.get('public_sha', '')} release={ctx.version} "
            f"archive-sha256={ctx.notes.get('github_archive_sha256', '')} "
            f"formula-sha256={hashlib.sha256(formula.encode('utf-8')).hexdigest()} result={result}"
        ),
    )


def _tap_tip(ctx: PublicContext, url: str) -> str:
    """The tap's main. ONLY the Forgejo tap (`ctx.tap_url`) is read with the
    bot's token (`ctx.run`); every other URL is read signed out, through
    `run_signed_out`, the same runner as the public repo's reads (#121). The
    safe side is the default, so a new caller can't forget it (ba on #122)."""
    if url != ctx.tap_url:
        argv = ["git", "-c", "credential.helper=", "-c", "core.askPass=", "ls-remote", url]
        out = ctx.run_signed_out([*argv, "refs/heads/main"]).stdout or ""
    else:
        out = ctx.run(["git", "ls-remote", url, "refs/heads/main"]).stdout or ""
    first = out.split()
    return first[0] if first and re.fullmatch(r"[0-9a-f]{40}", first[0]) else ""


def _tap_holds(ctx: PublicContext) -> Check:
    """The Forgejo tap's main carries exactly the formula that passed."""
    tip = _tap_tip(ctx, ctx.tap_url)
    formula = ctx.notes.get("candidate_formula", "")
    there = ""
    if tip:
        ctx.run(["git", "-C", str(ctx.root), "fetch", "-q", ctx.tap_url, "refs/heads/main"])
        there = ctx.git("show", f"{tip}:{release.TAP_FORMULA_PATH}")
    ok = bool(tip) and bool(formula) and there.strip() == formula.strip()
    if ok:
        ctx.notes["tap_sha"] = tip
    return Check(
        ok,
        [f"{TAP_REPO} main {tip[:12] or 'none'} carries the formula that passed: {ok}"],
    )


def _commit_tap(ctx: PublicContext) -> None:
    """Clone the tap, write the formula, commit as the release identity, and
    push. A plain push: git refuses a non-fast-forward, nothing forces."""
    work = Path(tempfile.mkdtemp()) / "tap"
    if ctx.run(["git", "clone", "-q", ctx.tap_url, str(work)]).returncode != 0:
        print(f"      could not clone {ctx.tap_url}")
        return
    path = work / release.TAP_FORMULA_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ctx.notes["candidate_formula"], encoding="utf-8", newline="\n")
    name, email = re.fullmatch(r"([^<>]+) <([^<>]+)>", ctx.identity).groups()
    who = ["-c", f"user.name={name.strip()}", "-c", f"user.email={email}"]
    ctx.run(["git", "-C", str(work), "add", release.TAP_FORMULA_PATH])
    ctx.run(["git", "-C", str(work), *who, "commit", "-q", "-m", f"policyforge {ctx.version}"])
    ctx.run(["git", "-C", str(work), "push", ctx.tap_url, "HEAD:refs/heads/main"])


def _tap_mirrored_now(ctx: PublicContext) -> Check:
    want = ctx.notes.get("tap_sha", "")
    there = _tap_tip(ctx, ctx.github_tap_url)
    return Check(
        bool(want) and there == want,
        [f"GitHub tap (signed out) main -> {there[:12] or 'absent'} (must be {want[:12] or '?'})"],
    )


def _await_tap_mirror(ctx: PublicContext) -> None:
    waited = 0.0
    while True:
        now = _tap_mirrored_now(ctx)
        if now.ok or waited >= ctx.mirror_timeout:
            if not now.ok:
                print(
                    f"    MIRROR TIMEOUT after {waited:.0f}s: {now.measured[0]}. Check the push "
                    f"mirror's status in Forgejo's settings for {TAP_REPO}."
                )
            return
        ctx.sleep(ctx.poll_every)
        waited += ctx.poll_every


def _all_held(ctx: PublicContext, note: str) -> Check:
    return Check(bool(ctx.notes.get(note)), [f"{note}: {ctx.notes.get(note, '')[:12] or 'none'}"])


# --- real side effects --------------------------------------------------------------------


#: Variables through which git would find a credential or a config that names
#: one. `-c core.askPass=` on the command line does NOT clear GIT_ASKPASS,
#: which git consults first: 1d measured a fake GIT_ASKPASS asked for a
#: username and a password by the first version of this read (#121, 2388).
_CREDENTIAL_ENV = (
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "SSH_ASKPASS_REQUIRE",
    "GIT_CONFIG_PARAMETERS",
    "HOMEDRIVE",
    "HOMEPATH",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
)


def signed_out_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """An environment that closes every credential route measured on #121:

    - no askpass of any kind (1d: an inherited `GIT_ASKPASS` answered a
      401 even with `-c core.askPass=`), and no `GIT_CONFIG_*` (which
      `_git_env` uses for the Forgejo token);
    - no system or global git config, where a credential helper is
      registered (Git for Windows puts its credential manager in the
      system config), and no prompt;
    - an EMPTY home: `HOME`, `USERPROFILE` and `XDG_CONFIG_HOME` point at a
      fresh directory (ba: git's curl sent a `.netrc`/`_netrc` login from
      the caller's home), which also hides `~/.git-credentials`;
    - no repository: `GIT_DIR` and its kin are dropped, the runner starts
      git in that empty home, and `GIT_CEILING_DIRECTORIES` stops git
      looking above it (ba: a repo-local `http.extraHeader` reached the
      server when the read ran inside a checkout).

    Not covered: a credential written into the URL itself (GitHub URLs here
    are constants), and a proxy that adds one. Everything else in the
    environment is kept, so git still runs."""
    env = {
        k: v
        for k, v in (os.environ if base is None else base).items()
        if k not in _CREDENTIAL_ENV and not k.startswith("GIT_CONFIG_")
    }
    home = Path(tempfile.mkdtemp(prefix="signed-out-home-"))
    empty = home / "gitconfig"
    empty.write_text("", encoding="utf-8")
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty),
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home),
            "GIT_CEILING_DIRECTORIES": str(home.parent),
        }
    )
    return env


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process AND its children. Killing git alone isn't enough: its
    helpers (git-remote-http, a credential manager) keep the output pipes
    open, and collecting the output then waits forever (9b measured it on
    #121, with a credential manager waiting for a person)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, 9)
    proc.kill()


def _real_run_signed_out(argv: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """Runs `argv` in `signed_out_env()`, bounded: a read that outlives
    `timeout` is killed with its whole tree and reported as exit 127. A URL
    carrying credentials is refused unrun, like `release._real_run` (#124)."""
    for arg in argv:
        if why := release.userinfo_refusal(arg):
            return subprocess.CompletedProcess([release._redacted(a) for a in argv], 2, "", why)
    try:
        env = signed_out_env()
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=env["HOME"],
            start_new_session=os.name != "nt",
        )
    except OSError as error:
        return subprocess.CompletedProcess(argv, 127, "", str(error))
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        out, _ = proc.communicate()
        return subprocess.CompletedProcess(argv, 127, out or "", f"timed out after {timeout}s")
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def _real_macos_wheels(lock: str, formula: str, pyproject: str) -> tuple[int, list[str]]:
    import macos_wheels

    return macos_wheels.check(lock, formula, pyproject, macos_wheels.pip_download())


def _real_fetch_anonymous(url: str, dest: Path) -> int:
    """https only, no credentials of any kind."""
    import urllib.error
    import urllib.request

    if not url.startswith("https://"):
        return 0
    try:
        with urllib.request.urlopen(url, timeout=300) as response:  # nosec B310  # nosemgrep
            dest.write_bytes(response.read())
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except OSError:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--tracking-issue",
        type=int,
        required=True,
        help="where the public publish's records and the approval go",
    )
    parser.add_argument(
        "--local-issue",
        type=int,
        required=True,
        help="the issue holding the version's Release-local record",
    )
    parser.add_argument("--identity", required=True, help="'Name <email>' for P and its tag")
    parser.add_argument(
        "--formula",
        type=Path,
        required=True,
        help="the formula the pre-approval install edits (as release.py)",
    )
    parser.add_argument(
        "--ops",
        type=Path,
        default=None,
        help="a clone of policyforge/ops: the run's repositories are checked against its "
        "inventory.yaml first, and the run refuses without it (#194)",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"publish_public: {args.version!r} is not X.Y.Z")
        return 2
    if not os.environ.get("FORGEJO_TOKEN", "").strip():
        print("publish_public: needs FORGEJO_TOKEN (your own bot's token)")
        return 2
    if why := release.userinfo_refusal(release.FORGEJO_REPO_URL):
        print(f"publish_public: FORGEJO_URL is {why}")
        return 2
    ctx = PublicContext(
        version=args.version,
        run=release._real_run,
        status_of=release._real_status,
        hash_url=release_check.hash_of,
        install=release._real_install,
        tracking=args.tracking_issue,
        formula_path=args.formula,
        forge="forgejo",
        forgejo=release._real_forgejo,
        local_issue=args.local_issue,
        identity=args.identity,
        fetch_anonymous=_real_fetch_anonymous,
        run_signed_out=_real_run_signed_out,
        test_shape=_real_test_shape,
        read_ci=_real_read_ci,
        macos_wheels=_real_macos_wheels,
        ops_root=args.ops,
    )
    topology = _topology(ctx)
    print("topology: every repository this run writes to holds its ops inventory rules (#194)")
    for line in topology.measured:
        print("    " + line.replace("\n", "\n    "))
    if not topology.ok:
        print("  REFUSED before any step: fix the drift, or the inventory, first")
        return 3
    plan = steps()
    print(f"publish {ctx.tag}: {'EXECUTE' if args.execute else 'DRY RUN (nothing will change)'}")
    for index, step in enumerate(plan, 1):
        kind = step.kind + (", outward" if step.outward else "")
        print(f"  {index}. {step.key}: {step.title} [{kind}]")
    return release.run_steps(plan, ctx, execute=args.execute)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
