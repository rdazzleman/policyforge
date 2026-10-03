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
- **P's tree EQUALS the local tag's tree.** A licensed path in it is a
  refusal, never a strip: a release whose tree carries one is the finding.
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

**The tap, the same way (#115, part B).** The formula is installed in a
clean container from GitHub's own tag archive first (its sha256 re-read and
equal to the proved one), then committed to the Forgejo repo
`policyforge/homebrew-tap` (fast-forward only), whose own push mirror (the
user's second token) carries it to GitHub. The script reads GitHub's tap
signed out until it shows that commit, and ends with `release_check.py`:
what a user installs, read signed out from GitHub, is what was released.

Usage:

    python scripts/publish_public.py 1.7.0 --tracking-issue N --local-issue 100 \
        --identity "Name <email>" --formula PATH            # dry run
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

#: The only login whose line approves a publish: the user's own account.
APPROVER = "rdazzleman"

APPROVAL_LINE = re.compile(r"^Publish-approved: ([0-9a-f]{40}) release=(\S+)\s*$")
INSTALL_LINE = re.compile(
    r"^Public-install: ([0-9a-f]{40}) release=(\S+) result=(passed|FAILED|did-not-run|refused)\s*$"
)
PUBLISHED_LINE = re.compile(
    r"^Public-published: ([0-9a-f]{40}) release=(\S+) archive-sha256=([0-9a-f]{64})\s*$"
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
    #: How long to wait for the mirror, and the pause between looks.
    mirror_timeout: float = 1200.0
    poll_every: float = 30.0
    sleep: Callable[[float], None] = time.sleep

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
                release.FORGEJO_REMOTE,
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
                release.FORGEJO_REMOTE,
                f"refs/tags/{ctx.tag}",
            ]
        )
    tree = ctx.git("rev-parse", f"{commit}^{{tree}}") if commit else ""
    records = [
        m
        for _, line in _comments(ctx, ctx.local_issue)
        if (m := release.LOCAL_LINE.match(line)) and m.group(2) == ctx.version
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


def _identity_line(ctx: PublicContext, epoch: str, offset: str) -> str:
    return f"{ctx.identity} {epoch} {offset}"


def build_public(ctx: PublicContext) -> tuple[str, list[str]]:
    """P, deterministically: the tag's tree, the public tip as parent (none
    for a first release), the given identity, and the tag commit's date."""
    commit, tree = ctx.notes.get("tag_commit", ""), ctx.notes.get("tag_tree", "")
    if not (commit and tree):
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
    body = (
        f"tree {tree}\n"
        + (f"parent {parent}\n" if parent else "")
        + (f"author {who}\ncommitter {who}\n\nRelease {ctx.version}\n")
    )
    raw = Path(tempfile.mkdtemp()) / "commit"
    raw.write_bytes(body.encode("utf-8"))
    public = ctx.git("hash-object", "-t", "commit", "-w", str(raw))
    return public, [
        f"P {public[:12] or '-'}: tree {tree[:12]} (= {ctx.tag}'s), "
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
    equal = ctx.git("rev-parse", f"{public}^{{tree}}") == ctx.notes["tag_tree"]
    lines.append(f"P's tree equals {ctx.tag}'s tree: {equal}")
    guard = _guard(ctx, public, _public_tip(ctx))
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


def _signed_out(ctx: PublicContext, ref: str) -> str:
    """A ref on GitHub, asked with NO credentials: through `run_signed_out`,
    which gives git an environment that can't find any (1d on #121)."""
    out = (
        ctx.run_signed_out(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "core.askPass=",
                "ls-remote",
                ctx.github_url,
                ref,
            ]
        ).stdout
        or ""
    )
    first = out.split()
    return first[0] if first and re.fullmatch(r"[0-9a-f]{40}", first[0]) else ""


def _mirrored_now(ctx: PublicContext) -> Check:
    public = ctx.notes.get("public_sha", "")
    main, tag = _signed_out(ctx, "refs/heads/main"), _signed_out(ctx, f"refs/tags/{ctx.tag}^{{}}")
    return Check(
        bool(public) and main == public and tag == public,
        [
            f"GitHub (signed out) main -> {main[:12] or 'absent'}, "
            f"{ctx.tag} -> {tag[:12] or 'absent'} (both must be P {public[:12] or '?'})"
        ],
    )


def _await_mirror(ctx: PublicContext) -> None:
    """The mirror is asynchronous: look until both refs are P, or say loudly
    that the timeout passed. Nothing is pushed to GitHub from here."""
    waited = 0.0
    while True:
        now = _mirrored_now(ctx)
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
            "approval",
            "the user approves this exact P",
            WAIT,
            _installed,
            _approved,
            would=f"a comment by {APPROVER} on the tracking issue whose last line is "
            "`Publish-approved: <P> release=<version>`",
        ),
        Step(
            "public-main",
            f"put P on {PUBLIC_REPO} main (fast-forward only)",
            ACT,
            release._refused_first(lambda c: release._all(_approved(c), _installed(c))),
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
            lambda c: release._install_check(c, "", "candidate install"),
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


def _candidate_install(ctx: PublicContext) -> None:
    """`release.py`'s own candidate install: the formula's url is GitHub's tag
    archive, as a user gets it, and the container fetches it."""
    release._install(ctx, ctx.github_archive(ctx.tag), "candidate install")


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
    `timeout` is killed with its whole tree and reported as exit 127."""
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
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"publish_public: {args.version!r} is not X.Y.Z")
        return 2
    if not os.environ.get("FORGEJO_TOKEN", "").strip():
        print("publish_public: needs FORGEJO_TOKEN (your own bot's token)")
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
    )
    plan = steps()
    print(f"publish {ctx.tag}: {'EXECUTE' if args.execute else 'DRY RUN (nothing will change)'}")
    for index, step in enumerate(plan, 1):
        kind = step.kind + (", outward" if step.outward else "")
        print(f"  {index}. {step.key}: {step.title} [{kind}]")
    return release.run_steps(plan, ctx, execute=args.execute)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
