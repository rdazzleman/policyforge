"""`scripts/publish_public.py`: a local release published through the
release-only public repo (#115; 80's rulings on 9b's seven points, 2286).

A stand-in world: a private bare repo with the tagged local release and its
`Release-local` record, an empty bare `policyforge-public`, and a bare repo
standing in for GitHub that only the "mirror" writes (the injected sleep).
1d's read list (2372) is the spec: no push before the recorded approval; P's
tree EQUALS the tag's and a licensed path is refused; the install is from a
host-built archive; the GitHub poll times out loudly; GitHub's archive is
tree-proved before its sha is recorded; no token anywhere; fast-forward only.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish_public as pp
import release
from release import Check

VERSION = "9.9.0"
#: Every job a candidate's GitHub run must pass, written out by hand from the
#: issues that added them (#165, #326, #380, #381): the other side of CI_REQUIRED.
PLATFORM_JOBS = (
    "scan-and-test",
    "pytest (macos-latest, 3.12)",
    "pytest (windows-latest, 3.12)",
    "pytest (ubuntu-latest, 3.10)",
    "pytest (ubuntu-latest, 3.11)",
    "pytest (ubuntu-latest, 3.12)",
    "pytest (ubuntu-latest, 3.13)",
    "pytest (ubuntu-latest, 3.14)",
    "pytest (ubuntu-latest, 3.12, unpinned)",
    "brew install (macos-latest)",
)
IDENTITY = "PolicyForge Release <release@example.invalid>"
SENTINEL = "fj-token-sentinel-115"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
    "GIT_AUTHOR_DATE": "2026-10-02T12:00:00+0000",
    "GIT_COMMITTER_DATE": "2026-10-02T12:00:00+0000",
}


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        check=True,
    ).stdout.strip()


class Forgejo:
    """Issue comments by login; POSTs are 9b's."""

    def __init__(self):
        self.comments: dict[int, list[dict]] = {}

    def add(self, issue: int, login: str, body: str) -> None:
        self.comments.setdefault(issue, []).append({"user": {"login": login}, "body": body})

    def __call__(self, method, path, data):
        issue = int(path.split("/issues/")[1].split("/")[0])
        if method == "POST":
            self.add(issue, "policyforge-9b", data["body"])
            return 201, {}, {}
        return 200, list(self.comments.get(issue, [])), {}


@pytest.fixture
def world(tmp_path, monkeypatch, request):
    """The local release of 9.9.0, done: a private repo with tag v9.9.0 and
    its record; an empty public repo; a 'GitHub' only the mirror writes.
    An indirect parameter, a {path: bytes} dict, adds files to the release."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    _git(src, "config", "core.autocrlf", "false")
    (src / "src").mkdir()
    (src / "src" / "tool.py").write_bytes(b"print('tool')\n")
    (src / "README.md").write_bytes(b"# tool\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "earlier history the public never sees")
    (src / "src" / "tool.py").write_bytes(b"print('tool 9.9.0')\n")
    for name, data in getattr(request, "param", {}).items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "Release 9.9.0")
    _git(src, "tag", "-a", "v9.9.0", "-m", "Release 9.9.0")
    private = tmp_path / "private.git"
    _git(tmp_path, "clone", "-q", "--bare", str(src), str(private))
    root = tmp_path / "root"
    # A plain clone: its remote is `origin`, and there is no `forgejo` remote.
    # The tooling reads the private repo by URL (`forgejo_url`, 80's ruling (b)
    # on #124), so every test here is also that case.
    _git(tmp_path, "clone", "-q", str(private), str(root))
    # The hostile setting, on purpose (#158): Git for Windows sets core.autocrlf=true in
    # its SYSTEM config, so the host-built archive must not depend on it. With
    # `-c core.autocrlf=false` removed from `_install_public`, 17 tests in this file
    # fail (1d measured it on #158); that is the guard, not a dedicated test.
    _git(root, "config", "core.autocrlf", "true")
    public = tmp_path / "public.git"
    github = tmp_path / "github.git"
    ci = tmp_path / "ci.git"
    for bare in (public, github, ci):
        _git(tmp_path, "init", "-q", "--bare", str(bare))
        # A Windows git's own `archive` applies core.autocrlf; the tree proof
        # rightly refuses CRLF bytes (it caught this harness's first version).
        _git(bare, "config", "core.autocrlf", "false")
    # The tap on Forgejo, seeded by the user from the old tap, and GitHub's,
    # which only its mirror writes.
    tap, github_tap = tmp_path / "tap.git", tmp_path / "github-tap.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "Formula").mkdir()
    (seed / "Formula" / "policyforge.rb").write_bytes(b"class Policyforge < Formula\nend\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "the old tap")
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(tap))
    _git(tmp_path, "init", "-q", "--bare", str(github_tap))
    commit = _git(root, "rev-parse", "v9.9.0^{commit}")
    tree = _git(root, "rev-parse", "v9.9.0^{tree}")
    fake = Forgejo()
    fake.add(
        100,
        "policyforge-9b",
        f"local\n\nRelease-local: {commit} release={VERSION} tree={tree} deferred=x",
    )
    formula = tmp_path / "formula.rb"
    formula.write_text(
        'class Policyforge < Formula\n  homepage "https://x.invalid"\n'
        '  url "https://x.invalid/a.tar.gz"\n  sha256 "' + "0" * 64 + '"\nend\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        release_check_mod(), "names_canonical_homepage", lambda h: True, raising=False
    )
    calls: list[list[str]] = []
    installs: list = []
    #: release_check.py reads GitHub for real; here its exit code is the stand-in.
    release_check_exit = [0]

    def run(argv):
        calls.append(list(argv))
        if any(str(a).endswith("release_check.py") for a in argv):
            return subprocess.CompletedProcess(argv, release_check_exit[0], "", "")
        return release._real_run(argv)

    def install(formula_text, version, source=None):
        installs.append((formula_text, source.read_bytes() if source else b""))
        return True, True, ["installed"]

    #: Every clone the shape test was handed (#322); the stand-in passes.
    #: GitHub's run on the policyforge-ci candidate, as ops ci-read reports it (#326):
    #: "passed", a failing job's name, "absent" (no run yet) or "running".
    ci_outcome = ["passed"]
    ci_reads: list[str] = []

    def read_ci(ctx):
        tip = subprocess.run(
            [
                "git",
                "-C",
                str(ci),
                "rev-parse",
                "-q",
                "--verify",
                f"refs/heads/release/{ctx.version}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()
        ci_reads.append(tip)
        outcome = ci_outcome[0]
        if not tip or outcome == "absent":
            return "# rdazzleman/policyforge-ci: the latest 0 run(s)" + chr(10)
        status = (
            "in_progress/None"
            if outcome == "running"
            else ("completed/success" if outcome == "passed" else "completed/failure")
        )
        jobs = list(PLATFORM_JOBS)
        marks = [
            ("FAIL" if job == outcome else ("in_progress" if outcome == "running" else "success"))
            for job in jobs
        ]
        lines = [
            f"2026-10-06T00:00:00.0000000Z ## ci #7  release/{ctx.version} {tip[:8]}  {status}  t"
        ]
        lines += [
            f"2026-10-06T00:00:00.0000000Z - {m}: {j}" for m, j in zip(marks, jobs, strict=True)
        ]
        lines += ["2026-10-06T00:00:00.0000000Z - skipped: changelog", ""]
        return chr(10).join(lines)

    shapes: list[Path] = []

    def test_shape(clone):
        shapes.append(clone)
        return True, True, ["shape: fake"]

    #: Every question the macOS wheel check was asked (#325), and its answer.
    macos: list[tuple[str, str, str]] = []
    macos_answer = [(0, ["macOS: fake"])]

    def macos_wheels(lock, formula_text, pyproject):
        macos.append((lock, formula_text, pyproject))
        return macos_answer[0]

    def mirror(_seconds):
        """The push mirrors, as Forgejo runs them: every ref of each repo."""
        for repo, mirror_of in ((public, github), (tap, github_tap)):
            if _git(repo, "for-each-ref"):
                _git(repo, "push", "-q", "--mirror", str(mirror_of))

    def fetch(url, dest):
        tag = url.rsplit("/", 1)[-1].removesuffix(".tar.gz")
        _git(
            github,
            "archive",
            "--format=tar.gz",
            f"--prefix=policyforge-{tag[1:]}/",
            "-o",
            str(dest),
            tag,
        )
        return 200

    def hash_url(url):
        dest = tmp_path / "hashed.tgz"
        fetch(url, dest)
        return hashlib.sha256(dest.read_bytes()).hexdigest()

    signed_out: list[list[str]] = []
    monkeypatch.setenv("FORGEJO_TOKEN", SENTINEL)
    monkeypatch.setattr(pp, "_guard", lambda ctx, public_sha, parent: Check(True, ["guard: fake"]))
    ctx = pp.PublicContext(
        version=VERSION,
        root=root,
        run=run,
        install=install,
        tracking=200,
        formula_path=formula,
        forge="forgejo",
        forgejo=fake,
        forgejo_url=str(private),
        local_issue=100,
        identity=IDENTITY,
        public_url=str(public),
        github_url=str(github),
        tap_url=str(tap),
        github_tap_url=str(github_tap),
        hash_url=hash_url,
        fetch_anonymous=fetch,
        run_signed_out=lambda argv: (
            calls.append(list(argv)),
            signed_out.append(list(argv)),
            pp._real_run_signed_out(argv),
        )[2],
        test_shape=test_shape,
        ci_url=str(ci),
        read_ci=read_ci,
        ci_timeout=600,
        ci_poll_every=180,
        macos_wheels=macos_wheels,
        mirror_timeout=60,
        poll_every=30,
        sleep=mirror,
        confirm=lambda prompt: prompt.split("'")[1],
    )
    return {
        "ctx": ctx,
        "fake": fake,
        "public": public,
        "github": github,
        "root": root,
        "commit": commit,
        "tree": tree,
        "calls": calls,
        "signed_out": signed_out,
        "installs": installs,
        "shapes": shapes,
        "ci": ci,
        "ci_outcome": ci_outcome,
        "ci_reads": ci_reads,
        "macos": macos,
        "macos_answer": macos_answer,
        "mirror": mirror,
        "tap": tap,
        "github_tap": github_tap,
        "release_check_exit": release_check_exit,
    }


def release_check_mod():
    import release_check

    return release_check


def _run(ctx) -> tuple[int, str]:
    said: list[str] = []
    code = release.run_steps(pp.steps(), ctx, execute=True, out=said.append)
    return code, "\n".join(said)


def _refs(bare: Path) -> str:
    return _git(bare, "for-each-ref", "--format=%(refname) %(objectname)")


def _approve(w, sha, login="rdazzleman"):
    w["fake"].add(200, login, f"approved\n\nPublish-approved: {sha} release={VERSION}")


# --- the whole flow -------------------------------------------------------------------


def test_the_flow_waits_for_approval_then_publishes_p_and_its_tag(world):
    w = world
    code, said = _run(w["ctx"])
    assert code == 5, said
    public_sha = w["ctx"].notes["public_sha"]
    assert _refs(w["public"]) == "", "nothing pushed before the approval"
    bodies = [c["body"] for c in w["fake"].comments[200]]
    assert "Public-install: " + public_sha in bodies[-3]
    assert f"Public-shape: {public_sha} release={VERSION} result=passed" in bodies[-2]
    assert "Candidate-ci: " in bodies[-1] and bodies[-1].rstrip().endswith("result=passed")
    _approve(w, public_sha)
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert _git(w["public"], "rev-parse", "refs/heads/main") == public_sha
    assert _git(w["public"], "rev-parse", "refs/tags/v9.9.0^{}") == public_sha
    assert _git(w["github"], "rev-parse", "refs/heads/main") == public_sha
    bodies = [c["body"] for c in w["fake"].comments[200]]
    assert any("Public-published: " + public_sha in body for body in bodies)
    assert "Candidate-install: " + public_sha in bodies[-1], "the candidate install is recorded"


def test_p_is_the_tags_tree_with_no_private_history(world):
    w = world
    w["ctx"].notes.update({"tag_commit": w["commit"], "tag_tree": w["tree"]})
    public_sha, _ = pp.build_public(w["ctx"])
    assert _git(w["root"], "rev-parse", f"{public_sha}^{{tree}}") == w["tree"]
    assert _git(w["root"], "rev-list", "--count", public_sha) == "1", "a root commit"
    again, _ = pp.build_public(w["ctx"])
    assert again == public_sha, "deterministic: the approved P is the pushed P"


def test_the_public_tag_is_a_new_object_naming_p_not_the_private_tag(world):
    w = world
    _run(w["ctx"])
    public_sha = w["ctx"].notes["public_sha"]
    _approve(w, public_sha)
    w["ctx"].notes.clear()
    assert _run(w["ctx"])[0] == 0
    public_tag = _git(w["public"], "rev-parse", "refs/tags/v9.9.0")
    private_tag = _git(w["root"], "rev-parse", "refs/tags/v9.9.0")
    assert public_tag != private_tag
    assert _git(w["root"], "rev-parse", f"{private_tag}^{{commit}}") == w["commit"]


# --- must-fail arms -----------------------------------------------------------------------


@pytest.mark.parametrize("login,which", [("policyforge-80", "right"), ("rdazzleman", "wrong")])
def test_only_the_users_approval_of_this_exact_p_releases_the_push(world, login, which):
    w = world
    assert _run(w["ctx"])[0] == 5
    sha = w["ctx"].notes["public_sha"] if which == "right" else "e" * 40
    _approve(w, sha, login)
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 5, said
    assert _refs(w["public"]) == "", "still nothing pushed"


def test_a_licensed_path_in_the_tag_is_a_refusal_not_a_strip(world, monkeypatch):
    w = world
    monkeypatch.setattr(release, "LICENSED_PATHS", ("src/tool.py",))
    code, said = _run(w["ctx"])
    assert code == 12, said  # public-build is step 2
    assert "licensed path" in said and "never a strip" in said
    assert _refs(w["public"]) == ""


def test_a_local_record_naming_another_tree_refuses(world):
    w = world
    w["fake"].comments[100][-1]["body"] = (
        f"x\n\nRelease-local: {w['commit']} release={VERSION} tree={'f' * 40} deferred=x"
    )
    code, said = _run(w["ctx"])
    assert code == 11, said


def test_a_bad_identity_refuses(world):
    w = world
    w["ctx"].identity = "just a name"
    code, said = _run(w["ctx"])
    assert code == 12 and "Name <email>" in said, said


def test_the_next_release_fast_forwards_from_the_public_tip(world):
    w = world
    _run(w["ctx"])
    first = w["ctx"].notes["public_sha"]
    _approve(w, first)
    w["ctx"].notes.clear()
    assert _run(w["ctx"])[0] == 0
    # 9.10.0, released locally on top of the private history.
    src = w["root"]
    (src / "README.md").write_bytes(b"# tool 9.10\n")
    _git(src, "commit", "-qam", "Release 9.10.0")
    _git(src, "tag", "-a", "v9.10.0", "-m", "Release 9.10.0")
    _git(
        src, "push", "-q", "origin", "HEAD:refs/heads/main", "refs/tags/v9.10.0"
    )  # the private repo
    commit, tree = _git(src, "rev-parse", "HEAD"), _git(src, "rev-parse", "HEAD^{tree}")
    w["fake"].add(
        100, "policyforge-9b", f"x\n\nRelease-local: {commit} release=9.10.0 tree={tree} deferred=x"
    )
    w["ctx"].version = "9.10.0"
    w["ctx"].notes.clear()
    assert _run(w["ctx"])[0] == 5
    second = w["ctx"].notes["public_sha"]
    assert _git(w["root"], "rev-parse", f"{second}^") == first, "P's parent is the public tip"
    w["fake"].add(200, "rdazzleman", f"ok\n\nPublish-approved: {second} release=9.10.0")
    w["ctx"].notes.clear()
    assert _run(w["ctx"])[0] == 0
    assert _git(w["public"], "rev-parse", "refs/heads/main") == second


def test_a_public_main_that_moved_is_never_forced(world, tmp_path):
    w = world
    _run(w["ctx"])
    public_sha = w["ctx"].notes["public_sha"]
    _approve(w, public_sha)
    # An unrelated commit lands on public main AFTER P was built and approved,
    # at the moment of the push: so the push itself is a non-fast-forward.
    # (Landing it before the run would only rebuild P on top of it, and the
    # approval would stop the run before any push: the push line never ran,
    # which is how this test's first version let a forced push survive.)
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    (other / "x").write_bytes(b"x\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "stray")
    real = w["ctx"].run
    pushed: list[list[str]] = []

    def run(argv):
        if "push" in argv and any("refs/heads/main" in a for a in argv):
            _git(other, "push", "-q", str(w["public"]), "main:refs/heads/main")
            pushed.append(list(argv))
        return real(argv)

    w["ctx"].run = run
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    stray = _git(other, "rev-parse", "HEAD")
    assert pushed, "the premise: the push to public main ran"
    assert code == 48, said  # public-main (step 8): its postcondition fails
    assert _git(w["public"], "rev-parse", "refs/heads/main") == stray, "never overwritten"


def test_an_unwanted_branch_on_github_stops_the_wait_and_is_named(world, capsys):
    """#164 item 3: a Dependabot branch on GitHub fails github-mirrored at once,
    naming the branch, and doesn't sit out the mirror timeout first, which
    would end in "MIRROR TIMEOUT" and blame the mirror for the wrong cause."""
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    _git(w["root"], "push", "-q", str(w["github"]), "HEAD:refs/heads/dependabot/pip/pip-1")
    slept: list[float] = []
    w["ctx"].sleep = slept.append
    code, said = _run(w["ctx"])
    out = capsys.readouterr().out
    assert code == 50, said  # github-mirrored is step 10: its postcondition fails
    assert "UNWANTED REFS ON GITHUB" in out and "refs/heads/dependabot/pip/pip-1" in out, out
    assert "MIRROR TIMEOUT" not in out, out
    assert slept == [], "the wait stopped without sleeping"


#: The refs rdazzleman/policyforge served signed out on 2026-10-03 (ba measured
#: them for #164), with stand-in shas: what the classifier has to get right.
_SEEN_ON_GITHUB = [
    "HEAD",
    "refs/heads/dependabot/github_actions/codeql-action-9d88a12d2b",
    "refs/heads/dependabot/pip/pip-b9721b17f2",
    "refs/heads/dependabot/pip/pip-bf70dc6093",
    "refs/heads/dependabot/pip/pip-c0be3156d1",
    "refs/heads/dependabot/pip/pip-c2466b921b",
    "refs/heads/dependabot/pip/pip-f4e065ec13",
    "refs/heads/dependabot/pip/requirements/semgrep/semgrep-1.178.0",
    "refs/heads/main",
    *[f"refs/pull/{n}/head" for n in range(1, 8)],
    "refs/tags/v1.7.0",
    "refs/tags/v1.7.0^{}",
]


def _served(names, main="a" * 40, head=None):
    refs = dict.fromkeys(names, "b" * 40)
    if "refs/heads/main" in refs:
        refs["refs/heads/main"] = main
    if "HEAD" in refs:
        refs["HEAD"] = main if head is None else head
    return refs


def test_the_refs_github_served_classify_as_seven_branches_refused_seven_prs_counted():
    """80's ruling (2942), on the real set: the Dependabot branches are refused,
    the PR refs counted, and every ref lands in exactly one class."""
    refs = _served(_SEEN_ON_GITHUB)
    unwanted, pull = pp._github_refs_unwanted(refs)
    assert unwanted == sorted(r for r in _SEEN_ON_GITHUB if r.startswith("refs/heads/dependabot/"))
    assert pull == [f"refs/pull/{n}/head" for n in range(1, 8)]
    allowed = {"HEAD", "refs/heads/main", "refs/tags/v1.7.0", "refs/tags/v1.7.0^{}"}
    assert len(unwanted) + len(pull) + len(allowed) == len(refs), "a ref dropped out"


def test_pull_refs_alone_pass_and_are_counted_on_the_line():
    """A PR ref outlives its PR and the owner can't delete it: accepted, counted
    and listed (2942). head and merge both count."""
    names = ["HEAD", "refs/heads/main", "refs/tags/v9.9.0", "refs/tags/v9.9.0^{}"]
    names += ["refs/pull/1/head", "refs/pull/1/merge", "refs/pull/12/head"]
    refs = _served(names)
    assert pp._github_refs_unwanted(refs) == (
        [],
        ["refs/pull/1/head", "refs/pull/1/merge", "refs/pull/12/head"],
    )


@pytest.mark.parametrize(
    "ref",
    [
        "refs/heads/gh-pages",
        "refs/tags/latest",
        "refs/tags/v1.7",
        "refs/tags/v1.7.0-rc1",
        "refs/tags/1.7.0",
        "refs/notes/x",
        "refs/pull/abc/head",
        "refs/pull/1/files",
        "refs/pull/1/head/extra",
    ],
)
def test_any_other_branch_tag_pull_shape_or_namespace_is_refused_by_name(ref):
    refs = _served(["HEAD", "refs/heads/main", "refs/tags/v9.9.0", ref])
    assert pp._github_refs_unwanted(refs) == ([ref], [])


def test_head_passes_only_at_mains_commit():
    base = ["HEAD", "refs/heads/main"]
    assert pp._github_refs_unwanted(_served(base))[0] == []
    assert pp._github_refs_unwanted(_served(base, head="c" * 40))[0] == ["HEAD"]
    assert pp._github_refs_unwanted(_served(["HEAD"]))[0] == ["HEAD"], "no main to equal"


def test_the_check_line_counts_both_classes(world):
    w = world
    w["ctx"].notes["public_sha"] = "a" * 40
    names = ["HEAD", "refs/heads/main", "refs/heads/stray", "refs/pull/3/head"]
    names += [f"refs/tags/v{VERSION}"]
    check = pp._mirror_check(w["ctx"], _served(names))
    assert not check.ok
    assert "unwanted 1 ['refs/heads/stray']" in check.measured[1], check.measured
    assert "pull refs 1 ['pull/3/head']" in check.measured[1], check.measured


def test_one_unwanted_ref_fails_a_mirror_that_is_otherwise_right(world):
    """main and the tag both P, one stray branch: refused. Without the stray,
    the same refs pass, so the refusal is the stray's."""
    w = world
    p = "a" * 40
    w["ctx"].notes["public_sha"] = p
    right = _served(["HEAD", "refs/heads/main", f"refs/tags/v{VERSION}"], main=p)
    right[f"refs/tags/v{VERSION}"] = p
    assert pp._mirror_check(w["ctx"], right).ok, pp._mirror_check(w["ctx"], right).measured
    stray = {**right, "refs/heads/dependabot/pip/x": "b" * 40}
    assert not pp._mirror_check(w["ctx"], stray).ok


@pytest.mark.parametrize(
    "extra",
    ["refs/heads/dependabot/pip/x", "refs/tags/nightly", "refs/notes/x"],
)
def test_an_extra_ref_in_place_of_the_tag_is_refused_by_name_not_count(world, extra):
    """#324: GitHub holds exactly as many refs as a right mirror, but one is a
    stray and the release tag is missing. A check that counts refs passes it;
    this one reads each by name, so both are said on the line."""
    w = world
    p = "a" * 40
    w["ctx"].notes["public_sha"] = p
    right = _served(["HEAD", "refs/heads/main", f"refs/tags/v{VERSION}"], main=p)
    right[f"refs/tags/v{VERSION}"] = p
    swapped = {**right, extra: "b" * 40}
    del swapped[f"refs/tags/v{VERSION}"]
    assert len(swapped) == len(right), "the same count as a right mirror"
    check = pp._mirror_check(w["ctx"], swapped)
    assert not check.ok
    assert f"v{VERSION} -> absent" in check.measured[0], check.measured
    assert extra in check.measured[1], check.measured


@pytest.mark.parametrize("ref", ["refs/heads/main", f"refs/tags/v{VERSION}"])
@pytest.mark.parametrize("how", ["absent", "elsewhere"])
def test_main_or_the_tag_missing_or_not_p_is_refused_with_nothing_stray(world, ref, how):
    """With no stray ref at all, main and the release tag are each still read
    by name: either missing, or naming another commit, refuses on its own
    (#324)."""
    w = world
    p = "a" * 40
    w["ctx"].notes["public_sha"] = p
    refs = {"refs/heads/main": p, f"refs/tags/v{VERSION}": p}
    assert pp._mirror_check(w["ctx"], refs).ok, "the right set passes"
    if how == "absent":
        del refs[ref]
    else:
        refs[ref] = "c" * 40
    check = pp._mirror_check(w["ctx"], refs)
    assert not check.ok, check.measured
    assert "unwanted 0" in check.measured[1], "the refusal is the tag's, not a stray's"


def test_github_not_answering_is_not_read_as_holding_nothing(world):
    """A failed ls-remote is None, never {}: "no refs" would read as nothing
    unwanted, and the line must say nothing was read."""
    w = world
    w["ctx"].run_signed_out = lambda argv: subprocess.CompletedProcess(argv, 128, "", "fatal")
    assert pp._signed_out_all_refs(w["ctx"]) is None
    check = pp._mirrored_now(w["ctx"])
    assert not check.ok and "didn't answer" in check.measured[0], check.measured


def test_a_mirror_that_never_arrives_times_out_loudly(world, capsys):
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    w["ctx"].sleep = lambda seconds: None  # the mirror never runs
    code, said = _run(w["ctx"])
    assert code == 50, said  # github-mirrored is step 10: its postcondition fails
    assert "MIRROR TIMEOUT" in capsys.readouterr().out


def _tamper(src: Path, dest: Path) -> Path:
    with tarfile.open(src, "r:gz") as tar:
        members = [(m, tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()]
    with tarfile.open(dest, "w:gz") as out:
        for m, data in members:
            if data is not None and m.name.endswith("tool.py"):
                data = b"print('not what was approved')\n"
            if data is not None:
                m.size = len(data)
                out.addfile(m, io.BytesIO(data))
            else:
                out.addfile(m)
    return dest


def test_an_archive_github_serves_that_is_not_p_is_not_recorded(world, tmp_path):
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    honest = w["ctx"].fetch_anonymous

    def tampered(url, dest):
        status = honest(url, tmp_path / "good.tgz")
        _tamper(tmp_path / "good.tgz", dest)
        return status

    w["ctx"].fetch_anonymous = tampered
    code, said = _run(w["ctx"])
    assert code == 51, said  # github-archive is step 11
    assert not any("Public-published" in c["body"] for c in w["fake"].comments[200])


def test_no_token_in_any_argument(world):
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    _run(w["ctx"])
    assert w["calls"], "the premise: git ran"
    assert not any(SENTINEL in " ".join(c) for c in w["calls"])


def test_github_is_read_with_no_credential_helper(world):
    w = world
    w["ctx"].notes["public_sha"] = "a" * 40
    pp._mirrored_now(w["ctx"])
    reads = [c for c in w["calls"] if "ls-remote" in c and str(w["github"]) in c]
    assert reads and all("credential.helper=" in c for c in reads)


def test_the_install_is_from_a_host_built_archive_of_p(world):
    w = world
    _run(w["ctx"])
    public_sha = w["ctx"].notes["public_sha"]
    ((formula, data),) = w["installs"]
    assert f'url "file://{release.LOCAL_SOURCE}"' in formula
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
    prefix = f"policyforge-{VERSION}"
    assert all(n == prefix or n.startswith(prefix + "/") for n in names), names
    assert _refs(w["public"]) == "", "P was on no server when it was installed"
    assert public_sha


def test_the_guard_runs_with_no_base_for_a_first_release(world, monkeypatch):
    w = world
    monkeypatch.undo()
    seen: list[list[str]] = []
    w["ctx"].run = lambda argv: (
        seen.append(list(argv)),
        subprocess.CompletedProcess(argv, 0, "", ""),
    )[1]
    w["ctx"].notes["tag_commit"] = "c" * 40
    pp._guard(w["ctx"], "a" * 40, "")
    pp._guard(w["ctx"], "a" * 40, "b" * 40)
    first, later = seen
    assert "--base" not in first and first[first.index("--source") + 1] == "c" * 40
    assert later[later.index("--base") + 1] == "b" * 40


# --- signed out means no credential can be found (1d on #121) ------------------------


class _Asks401(BaseHTTPRequestHandler):
    def do_GET(self):  # GitHub answering a private repo: authenticate, please
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="GitHub"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def _askpass_leaks(tmp_path, monkeypatch, runner) -> bool:
    """Whether `runner` lets git call a GIT_ASKPASS the caller's environment
    sets, against a server that asks for credentials."""
    marker = tmp_path / "asked"
    script = tmp_path / "askpass.cmd" if os.name == "nt" else tmp_path / "askpass.sh"
    if os.name == "nt":
        script.write_text(f'@echo off\r\necho asked>>"{marker}"\r\necho x\r\n', encoding="utf-8")
    else:
        script.write_text(f'#!/bin/sh\necho asked >> "{marker}"\necho x\n', encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("GIT_ASKPASS", str(script))
    for var in ("HOME", "USERPROFILE"):  # a credential manager writes there, not in ~
        monkeypatch.setenv(var, str(tmp_path))
    # Never the user's ~/.gitconfig: a credential manager this reaches records
    # its provider in the GLOBAL config (1d on #121; 9b's arms wrote five).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.gitconfig"))
    server = HTTPServer(("127.0.0.1", 0), _Asks401)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = runner(["git", "ls-remote", f"http://127.0.0.1:{server.server_port}/private.git"])
    finally:
        server.shutdown()
    # A read that HANGS has also reached something that asks a person: with
    # the caller's environment inherited, git found this machine's Git
    # Credential Manager and waited on it (9b's arm, measured). Bounded, so
    # that shows as red rather than as a stuck run.
    hung = result.returncode == 127 and "timed out" in (result.stderr or "")
    return marker.exists() or hung


def test_a_signed_out_read_never_reaches_an_askpass(tmp_path, monkeypatch):
    """1d measured the first version calling GIT_ASKPASS for a username and a
    password: `-c core.askPass=` does not clear the variable."""
    assert not _askpass_leaks(
        tmp_path, monkeypatch, lambda argv: pp._real_run_signed_out(argv, timeout=30)
    )


def test_the_askpass_arm_can_see_a_leak(tmp_path, monkeypatch):
    """The control: the ordinary runner, with the same flags git would get,
    DOES call the askpass, so the test above isn't blind."""
    flags = ["-c", "credential.helper=", "-c", "core.askPass="]
    assert _askpass_leaks(
        tmp_path, monkeypatch, lambda argv: release._real_run([argv[0], *flags, *argv[1:]])
    )


def test_the_signed_out_env_drops_every_credential_route(monkeypatch):
    monkeypatch.setenv("GIT_ASKPASS", "a")
    monkeypatch.setenv("SSH_ASKPASS", "b")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.https://github.com/.extraHeader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Authorization: token " + SENTINEL)
    env = pp.signed_out_env()
    assert not {"GIT_ASKPASS", "SSH_ASKPASS"} & set(env)
    assert not any(
        k.startswith("GIT_CONFIG_") and k not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")
        for k in env
    )
    assert SENTINEL not in " ".join(env.values())
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert Path(env["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8") == ""
    home = Path(env["HOME"])
    assert env["USERPROFILE"] == env["XDG_CONFIG_HOME"] == str(home)
    assert [p.name for p in home.iterdir()] == ["gitconfig"]  # nothing to authenticate with
    assert "HOMEDRIVE" not in env and "HOMEPATH" not in env


class _RecordsAuth(BaseHTTPRequestHandler):
    """Asks for Basic auth, records every Authorization header it is sent, and
    answers 200 (an unusable body) once one arrives, so git stops there
    rather than going on to a credential helper."""

    seen: list = []
    leaked: list = []

    def do_GET(self):
        auth = self.headers.get("Authorization")
        type(self).seen.append(auth)
        type(self).leaked.append(self.headers.get("X-Leak"))
        self.send_response(200 if auth else 401)
        if not auth:
            self.send_header("WWW-Authenticate", 'Basic realm="GitHub"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def _netrc_sent(tmp_path, monkeypatch, runner) -> list:
    """The Authorization headers git sends when the caller's HOME holds a
    netrc entry for the server (ba on #121, 2399)."""
    home = tmp_path / "home"
    home.mkdir()
    entry = "machine 127.0.0.1 login netrcuser password NETRC-SECRET" + chr(10)
    for name in (".netrc", "_netrc"):
        (home / name).write_text(entry, encoding="utf-8")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    # Never the user's ~/.gitconfig: a credential manager this reaches records
    # its provider in the GLOBAL config (1d on #121; 9b's arms wrote five).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.gitconfig"))
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    _RecordsAuth.seen = []
    server = HTTPServer(("127.0.0.1", 0), _RecordsAuth)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runner(["git", "ls-remote", f"http://127.0.0.1:{server.server_port}/private.git"])
    finally:
        server.shutdown()
    return [a for a in _RecordsAuth.seen if a]


def test_a_signed_out_read_sends_no_netrc_credential(tmp_path, monkeypatch):
    flags = ["-c", "credential.helper=", "-c", "core.askPass="]
    sent = _netrc_sent(
        tmp_path,
        monkeypatch,
        lambda argv: pp._real_run_signed_out([argv[0], *flags, *argv[1:]], timeout=30),
    )
    assert sent == []


def test_the_netrc_arm_can_see_a_credential(tmp_path, monkeypatch):
    """The control: the ordinary runner, same flags, DOES send the netrc
    login, so the test above isn't blind."""
    flags = ["-c", "credential.helper=", "-c", "core.askPass="]
    sent = _netrc_sent(
        tmp_path, monkeypatch, lambda argv: release._real_run([argv[0], *flags, *argv[1:]])
    )
    assert sent and all(a.startswith("Basic ") for a in sent)


def _repo_header_sent(tmp_path, monkeypatch, runner, route: str) -> list:
    """The X-Leak headers git sends when a repository's own config carries
    `http.extraHeader` and the read runs inside it (route "cwd") or with
    GIT_DIR naming it (route "GIT_DIR") (ba on #121, 2405)."""
    repo = tmp_path / "checkout"
    release._real_run(["git", "init", "-q", str(repo)])
    release._real_run(["git", "-C", str(repo), "config", "http.extraHeader", "X-Leak: REPO-SECRET"])
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    # Never the user's ~/.gitconfig: a credential manager this reaches records
    # its provider in the GLOBAL config (1d on #121; 9b's arms wrote five).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.gitconfig"))
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    if route == "cwd":
        monkeypatch.chdir(repo)
    else:
        monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    _RecordsAuth.seen, _RecordsAuth.leaked = [], []
    server = HTTPServer(("127.0.0.1", 0), _RecordsAuth)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        runner(["git", "ls-remote", f"http://127.0.0.1:{server.server_port}/private.git"])
    finally:
        server.shutdown()
    return [h for h in _RecordsAuth.leaked if h]


@pytest.mark.parametrize("route", ["cwd", "GIT_DIR"])
def test_a_signed_out_read_ignores_the_repository_it_runs_in(tmp_path, monkeypatch, route):
    flags = ["-c", "credential.helper=", "-c", "core.askPass="]
    sent = _repo_header_sent(
        tmp_path,
        monkeypatch,
        lambda argv: pp._real_run_signed_out([argv[0], *flags, *argv[1:]], timeout=30),
        route,
    )
    assert sent == []


@pytest.mark.parametrize("route", ["cwd", "GIT_DIR"])
def test_the_repository_arm_can_see_a_header(tmp_path, monkeypatch, route):
    """The control: the ordinary runner, same flags, DOES send the repo's
    header by either route."""
    flags = ["-c", "credential.helper=", "-c", "core.askPass="]
    sent = _repo_header_sent(
        tmp_path,
        monkeypatch,
        lambda argv: release._real_run([argv[0], *flags, *argv[1:]]),
        route,
    )
    assert sent == ["REPO-SECRET"] * len(sent) and sent


# --- what counts as the approval (ba on #121) -------------------------------------------


def test_a_quoted_approval_line_does_not_approve(world):
    """Only a comment's LAST line is a record: quoting one is not giving one."""
    w = world
    _run(w["ctx"])
    sha = w["ctx"].notes["public_sha"]
    w["fake"].add(200, "rdazzleman", f"Publish-approved: {sha} release={VERSION}\n\nnot yet")
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 5, said
    assert _refs(w["public"]) == ""


def test_an_approval_for_another_version_does_not_approve(world):
    w = world
    _run(w["ctx"])
    sha = w["ctx"].notes["public_sha"]
    w["fake"].add(200, "rdazzleman", f"ok\n\nPublish-approved: {sha} release=9.9.1")
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 5, said
    assert _refs(w["public"]) == ""


# --- part B: the tap through its own Forgejo repo and mirror ------------------------


def _to_the_end(w) -> tuple[int, str]:
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    return _run(w["ctx"])


def test_the_tap_carries_the_formula_that_passed_and_the_mirror_carries_the_tap(world):
    w = world
    code, said = _to_the_end(w)
    assert code == 0, said
    tap_head = _git(w["tap"], "rev-parse", "refs/heads/main")
    formula = _git(w["tap"], "show", f"{tap_head}:Formula/policyforge.rb")
    sha = w["ctx"].notes["github_archive_sha256"]
    assert f'sha256 "{sha}"' in formula, "the proved archive's sha256"
    assert (
        f"https://github.com/{pp.release_check.REPOSITORY}/archive/refs/tags/v9.9.0.tar.gz"
        in formula
    )
    assert _git(w["github_tap"], "rev-parse", "refs/heads/main") == tap_head
    author = _git(w["tap"], "log", "-1", "--format=%an <%ae>", tap_head)
    assert author == IDENTITY
    assert _git(w["tap"], "rev-parse", f"{tap_head}^"), "a fast-forward on the old tap"
    assert any(str(a).endswith("release_check.py") for c in w["calls"] for a in c)


def test_every_read_of_either_github_repo_is_signed_out(world):
    """GitHub's public repo AND its tap are read through `run_signed_out`,
    never `ctx.run`, which carries the caller's environment (#121)."""
    w = world
    code, said = _to_the_end(w)
    assert code == 0, said
    github = (str(w["github"]), str(w["github_tap"]))
    reads = [c for c in w["calls"] if "ls-remote" in c and any(g in c for g in github)]
    assert any(str(w["github_tap"]) in c for c in reads), "the tap's mirror was read"
    assert sorted(map(tuple, reads)) == sorted(
        tuple(c) for c in w["signed_out"] if "ls-remote" in c
    )


def test_a_tap_read_is_signed_out_unless_it_is_the_forgejo_tap(world, tmp_path):
    """Signed out is the DEFAULT: any URL but the Forgejo tap, including one
    no caller passes today, is read through `run_signed_out` (ba on #122)."""
    w = world
    ctx = w["ctx"]
    elsewhere = str(tmp_path / "some-other-tap.git")
    for url in (ctx.github_tap_url, elsewhere):
        pp._tap_tip(ctx, url)
        assert url in w["signed_out"][-1], url
    before = len(w["signed_out"])
    pp._tap_tip(ctx, ctx.tap_url)
    assert len(w["signed_out"]) == before, "the Forgejo tap is read with the token"
    assert ctx.tap_url in w["calls"][-1]


def test_an_archive_github_re_encoded_after_the_proof_is_not_installed(world):
    w = world
    w["ctx"].hash_url = lambda url: "9" * 64
    code, said = _to_the_end(w)
    assert code == 22, said  # candidate-install (step 12): its gate refuses
    assert len(w["installs"]) == 1, "only the pre-approval install of P ran"


def test_a_tap_that_moved_is_never_forced(world, tmp_path):
    w = world
    other = tmp_path / "stray-tap"
    _git(tmp_path, "clone", "-q", str(w["tap"]), str(other))
    (other / "x").write_bytes(b"x\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "stray")
    real = w["ctx"].run
    pushed: list = []

    def run(argv):
        if "push" in argv and str(w["tap"]) in argv:
            _git(other, "push", "-q", "origin", "HEAD:refs/heads/main")
            pushed.append(argv)
        return real(argv)

    w["ctx"].run = run
    code, said = _to_the_end(w)
    assert pushed, "the premise: the tap push ran"
    assert code == 53, said  # tap-commit (step 13): its postcondition fails
    assert _git(w["tap"], "rev-parse", "refs/heads/main") == _git(other, "rev-parse", "HEAD")


def test_a_tap_mirror_that_never_arrives_times_out_loudly(world, capsys):
    w = world
    calls = [0]

    def public_only(_seconds):
        calls[0] += 1
        _git(w["public"], "push", "-q", "--mirror", str(w["github"]))

    w["ctx"].sleep = public_only
    code, said = _to_the_end(w)
    assert code == 54, said  # tap-mirrored (step 14)
    assert "MIRROR TIMEOUT" in capsys.readouterr().out


def test_a_release_check_that_fails_fails_the_publish(world):
    w = world
    w["release_check_exit"][0] = 1
    code, said = _to_the_end(w)
    assert code == 25, said  # release-check (step 15)


# ---- the candidate install is recorded, so a resumed run doesn't repeat it (9b on #124) ----


def _candidate_installs(w) -> int:
    """Installs of the formula naming GitHub's tag archive (step 3's names a local file)."""
    return sum(1 for formula, _ in w["installs"] if "/archive/refs/tags/" in formula)


def test_a_resumed_run_holds_the_candidate_install_from_its_record(world):
    """The ~40-minute install runs once. A re-run holds it from the record,
    and rebuilds the same formula: the tap, which holds only the formula that
    passed, isn't committed again."""
    w = world
    code, said = _to_the_end(w)
    assert code == 0, said
    assert _candidate_installs(w) == 1
    tap = _git(w["tap"], "rev-parse", "refs/heads/main")
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert _candidate_installs(w) == 1, "held from the Candidate-install record"
    assert "Candidate-install record on #200" in said
    assert _git(w["tap"], "rev-parse", "refs/heads/main") == tap, (
        "the same formula, not recommitted"
    )


def test_a_changed_formula_base_installs_the_candidate_again(world):
    w = world
    assert _to_the_end(w)[0] == 0
    base = w["ctx"].formula_path
    base.write_text(
        base.read_text(encoding="utf-8") + "# edited by the operator\n", encoding="utf-8"
    )
    w["ctx"].notes.clear()
    _run(w["ctx"])
    assert _candidate_installs(w) == 2, "the record names another formula"


def test_a_candidate_record_from_another_login_does_not_count(world):
    w = world
    assert _to_the_end(w)[0] == 0
    for comment in w["fake"].comments[200]:
        if "Candidate-install: " in comment["body"]:
            comment["user"]["login"] = "someone-else"
    w["ctx"].notes.clear()
    _run(w["ctx"])
    assert _candidate_installs(w) == 2


def test_a_run_interrupted_after_the_candidate_install_resumes_it(world):
    """The real resume (#124): stopped at the tap's confirmation, after P was
    pushed and the candidate installed. Re-run, it finds the pushed P is this
    release's P (not a second commit on top, waiting for a second approval)
    and holds the install from its record, so it finishes."""
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    typed = w["ctx"].confirm
    w["ctx"].confirm = lambda prompt: "no" if "tap-commit" in prompt else typed(prompt)
    code, said = _run(w["ctx"])
    assert code == 6, said
    pushed = _git(w["public"], "rev-parse", "refs/heads/main")
    assert _candidate_installs(w) == 1
    w["ctx"].confirm = typed
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert "already this release's P" in said
    assert _git(w["public"], "rev-parse", "refs/heads/main") == pushed, "no second public commit"
    assert _candidate_installs(w) == 1, "the install held from its record"
    assert "Formula/policyforge.rb" in _git(w["tap"], "show", "--name-only", "refs/heads/main")


@pytest.mark.parametrize(
    "stop_at",
    ["public-tag", "github-archive"],
    ids=["main-pushed-tag-not", "tag-pushed"],
)
def test_a_run_interrupted_after_p_was_pushed_resumes_on_the_same_p(world, stop_at):
    """9b on #124: a re-run after main was pushed but not the tag, and one after
    the tag too. Both find main IS this release's P and finish on it."""
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    typed = w["ctx"].confirm
    w["ctx"].confirm = lambda prompt: "no" if f"'{stop_at}'" in prompt else typed(prompt)
    code, said = _run(w["ctx"])
    assert code == 6, said
    pushed = _git(w["public"], "rev-parse", "refs/heads/main")
    w["ctx"].confirm = typed
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert _git(w["public"], "rev-parse", "refs/heads/main") == pushed, "no second public commit"
    assert _git(w["public"], "rev-parse", "refs/tags/v9.9.0^{}") == pushed


def test_a_resumed_p_is_guarded_from_its_own_parent_not_from_itself(world, monkeypatch):
    """When main IS P, the guard's range from the tip would be empty, and the
    real guard refuses an empty range (NothingToCheck). So its base is P's
    own parent (the world's stand-in guard passes anything, so this records
    what it was asked)."""
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    typed = w["ctx"].confirm
    w["ctx"].confirm = lambda prompt: "no" if "'public-tag'" in prompt else typed(prompt)
    _run(w["ctx"])
    pushed = _git(w["public"], "rev-parse", "refs/heads/main")
    asked = []
    monkeypatch.setattr(
        pp, "_guard", lambda ctx, public, parent: asked.append((public, parent)) or Check(True, [])
    )
    w["ctx"].confirm = typed
    w["ctx"].notes.clear()
    assert _run(w["ctx"])[0] == 0
    parent = _git(w["public"], "rev-list", "--parents", "-n", "1", pushed).split()[1:]
    assert asked and asked[0] == (pushed, parent[0] if parent else "")
    assert asked[0][0] != asked[0][1]


# --- the public shape, tested before approval (#322) -----------------------------------

#: A test that reads the repository's own parent, as #163's did: it passes in any
#: checkout with history and fails where the public repo starts, one root commit.
READS_PARENT = b"""import subprocess
from pathlib import Path


def test_reads_its_own_parent():
    here = Path(__file__).resolve().parent.parent
    subprocess.run(
        ["git", "-C", str(here), "rev-parse", "--verify", "HEAD~1"], check=True, capture_output=True
    )
"""
#: A clean one, which also needs the shape's tag: an annotated vX.Y.Z on HEAD.
READS_TAG = b"""import subprocess
from pathlib import Path


def test_reads_its_release_tag():
    here = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        ["git", "-C", str(here), "describe", "--exact-match", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert out == "v9.9.0"
"""


def _pytest_in(clone: Path) -> tuple[bool, bool, list[str]]:
    """The shape runner, on the host: pytest in the clone it is handed, as
    `_real_test_shape` runs CI's pytest in its container."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(clone)],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return True, proc.returncode == 0, (proc.stdout or "").splitlines()


def _shape_records(w) -> list[str]:
    return [
        line
        for c in w["fake"].comments.get(200, [])
        for line in c["body"].splitlines()
        if line.startswith("Public-shape: ")
    ]


@pytest.mark.parametrize("world", [{"tests/test_history.py": READS_PARENT}], indirect=True)
def test_a_p_whose_test_reads_head_1_is_refused_before_approval(world):
    w = world
    w["ctx"].test_shape = _pytest_in
    code, said = _run(w["ctx"])
    public_sha = w["ctx"].notes["public_sha"]
    assert code == 44, said  # public-shape is step 4: its postcondition fails
    assert _shape_records(w) == [f"Public-shape: {public_sha} release={VERSION} result=FAILED"]
    assert "WAITING" not in said, "approval is never asked for"
    assert _refs(w["public"]) == ""


@pytest.mark.parametrize("world", [{"tests/test_tag.py": READS_TAG}], indirect=True)
def test_a_clean_p_passes_in_its_tree_alone_and_waits_for_approval(world):
    w = world
    w["ctx"].test_shape = _pytest_in
    code, said = _run(w["ctx"])
    public_sha = w["ctx"].notes["public_sha"]
    assert code == 5, said
    assert _shape_records(w) == [f"Public-shape: {public_sha} release={VERSION} result=passed"]


def test_the_clone_is_one_root_commit_of_ps_tree_with_only_its_tag(world):
    w = world
    before = _git(w["root"], "for-each-ref", "--format=%(refname) %(objectname)")
    code, said = _run(w["ctx"])
    assert code == 5, said
    (clone,) = w["shapes"]
    public_sha = w["ctx"].notes["public_sha"]
    assert _git(clone, "rev-list", "--count", "--all") == "1"
    assert _git(clone, "rev-parse", "HEAD^{tree}") == _git(
        w["root"], "rev-parse", f"{public_sha}^{{tree}}"
    )
    assert _git(clone, "tag") == "v9.9.0"
    assert _git(clone, "cat-file", "-t", "v9.9.0") == "tag", "annotated, as the public tag is"
    assert _git(clone, "rev-parse", "v9.9.0^{}") == _git(clone, "rev-parse", "HEAD")
    assert _git(clone, "branch", "--format=%(refname:short)") == "main"
    assert not (clone / ".git" / "objects" / "info" / "alternates").exists()
    assert (clone / "src" / "tool.py").read_bytes() == b"print('tool 9.9.0')\n", "LF on the host"
    after = _git(w["root"], "for-each-ref", "--format=%(refname) %(objectname)")
    assert after == before, "no ref was written in the checkout"


@pytest.mark.parametrize(
    "answer, result",
    [((True, False, ["1 failed"]), "FAILED"), ((False, False, ["no docker"]), "did-not-run")],
)
def test_a_shape_test_that_did_not_pass_stops_before_approval(world, answer, result):
    w = world
    w["ctx"].test_shape = lambda clone: answer
    code, said = _run(w["ctx"])
    public_sha = w["ctx"].notes["public_sha"]
    assert code == 44, said
    assert _shape_records(w) == [f"Public-shape: {public_sha} release={VERSION} result={result}"]
    assert "WAITING" not in said


def test_a_resumed_run_holds_the_shape_test_from_its_record(world):
    w = world
    code, said = _run(w["ctx"])
    assert code == 5, said
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert len(w["shapes"]) == 1, "the suite ran once, not again on the resume"


def test_a_shape_record_from_another_login_does_not_count(world):
    w = world
    _run(w["ctx"])  # builds P; the shape passes and is recorded by 9b
    public_sha = w["ctx"].notes["public_sha"]
    w["fake"].comments[200] = [
        c for c in w["fake"].comments[200] if "Public-shape" not in c["body"]
    ]
    w["fake"].add(
        200, "someone-else", f"x\n\nPublic-shape: {public_sha} release={VERSION} result=passed"
    )
    w["ctx"].notes.clear()
    w["ctx"].test_shape = lambda clone: (True, False, ["1 failed"])
    code, said = _run(w["ctx"])
    assert code == 44, said


@pytest.mark.parametrize("key", ["approval", "public-main"])
def test_approval_and_the_push_each_need_the_shape_record(world, key):
    """Belt and braces: the two gates after the shape step ask for its record
    themselves, so a plan that loses the step still can't push."""
    w = world
    _run(w["ctx"])
    public_sha = w["ctx"].notes["public_sha"]
    _approve(w, public_sha)
    w["fake"].comments[200] = [
        c for c in w["fake"].comments[200] if "Public-shape" not in c["body"]
    ]
    step = next(s for s in pp.steps() if s.key == key)
    gate = step.gate(w["ctx"])
    assert not gate.ok and any("Public-shape record" in line for line in gate.measured), gate


# --- macOS wheels before approval (#325) ---------------------------------------------

LOCK = (
    b"alpha==1.0 \\\n    --hash=sha256:" + b"a" * 64 + b"\n    # via policyforge (pyproject.toml)\n"
)
PYPROJECT = b'[project]\nname = "tool"\ndependencies = ["alpha"]\n'


def test_a_blocking_macos_miss_stops_the_run_before_approval(world):
    w = world
    w["macos_answer"][0] = (1, ["    MISSING beta==2.0: no wheel; pulled in by base"])
    code, said = _run(w["ctx"])
    assert code == 17, said  # approval (step 7): its gate refuses
    assert "MISSING beta==2.0" in said and "WAITING" not in said
    assert _refs(w["public"]) == "", "nothing pushed"


@pytest.mark.parametrize("key", ["approval", "public-main"])
def test_approval_and_the_push_each_need_macos_wheels(world, key):
    """Both gates ask it themselves, as they ask for the shape record."""
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes = {k: v for k, v in w["ctx"].notes.items() if not k.startswith("macos-wheels")}
    w["macos_answer"][0] = (2, ["macOS wheel readiness: COULDN'T TELL, so not a pass: offline"])
    gate = next(s for s in pp.steps() if s.key == key).gate(w["ctx"])
    assert not gate.ok and any("exit 2 (must be 0)" in line for line in gate.measured), gate


@pytest.mark.parametrize(
    "world", [{"requirements/runtime.txt": LOCK, "pyproject.toml": PYPROJECT}], indirect=True
)
def test_the_macos_check_reads_ps_own_lock_and_this_runs_formula_once(world):
    """P's bytes, not the checkout's, and `--formula`, the formula this run
    installs. An advisory (exit 0) passes and is printed. Asked once: the
    approval gate and the push gate share the answer."""
    w = world
    w["macos_answer"][0] = (0, ["    ADVISORY gamma==3.0: no wheel; pulled in by extra ext"])
    code, said = _run(w["ctx"])
    assert code == 5, said
    assert "ADVISORY gamma==3.0" in said
    _approve(w, w["ctx"].notes["public_sha"])
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert w["macos"] == [
        (
            LOCK.decode().strip(),
            w["ctx"].formula_path.read_text(encoding="utf-8"),
            PYPROJECT.decode().strip(),
        )
    ]


# --- the real runner: what it runs, and what it passes to docker ---------------------


def _ci_steps() -> dict[str, str]:
    import yaml

    ci = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
    job = yaml.safe_load(ci.read_text(encoding="utf-8"))["jobs"]["scan-and-test"]
    return {s.get("name", ""): s for s in job["steps"]}


@pytest.mark.github_config
def test_the_shape_script_runs_cis_own_two_test_steps():
    """Derived from ci.yml by its own parse, the other side of the check: if
    CI's pytest or skip_gate line changes, this script must follow it."""
    steps = _ci_steps()
    version, digest = pp._ci_gitleaks(Path(__file__).resolve().parent.parent)
    script = pp._shape_script(version, digest)
    for name in ("pytest", "expected skips only"):
        line = steps[name]["run"].strip().replace("$RUNNER_TEMP", "/tmp")
        assert line in script, (name, line)
    install = steps["Install project"]["run"].strip().splitlines()
    for line in install:
        assert line.strip() in script, line


@pytest.mark.github_config
def test_the_gitleaks_pins_are_read_from_the_trees_own_ci():
    env = _ci_steps()["gitleaks (secrets scan)"]["env"]
    pins = pp._ci_gitleaks(Path(__file__).resolve().parent.parent)
    assert pins == (str(env["GITLEAKS_VERSION"]), env["GITLEAKS_SHA256"])
    script = pp._shape_script(*pins)
    assert f"'{env['GITLEAKS_SHA256']}  /tmp/gitleaks.tgz' | sha256sum -c -" in script


def test_no_host_variable_reaches_the_shape_container(tmp_path):
    argv = pp._shape_docker_argv(tmp_path, "true")
    assert not {"-e", "--env", "--env-file"} & set(argv)
    assert not any(str(a).startswith(("-e=", "--env=")) for a in argv)
    assert f"{tmp_path}:/src:ro" in argv, "the clone is mounted read-only"
    assert pp.SHAPE_IMAGE == "python:3.12-slim"


@pytest.mark.github_config
@pytest.mark.parametrize(
    "code, ran, passed",
    [(0, True, True), (1, True, False), (pp.SHAPE_SETUP_FAILED, False, False), (125, False, False)],
)
def test_the_real_runner_reads_setup_failure_as_not_run(tmp_path, monkeypatch, code, ran, passed):
    import shutil

    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    ci = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
    (tmp_path / ".github" / "workflows" / "ci.yml").write_bytes(ci.read_bytes())
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    seen = []

    def run(argv):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, code, "out", "")

    monkeypatch.setattr(release, "_real_run", run)
    got = pp._real_test_shape(tmp_path)
    assert got[:2] == (ran, passed)
    assert got[2][-1] == f"docker exit status: {code}"
    assert seen and seen[0][0] == "docker"


def test_the_real_runner_does_not_run_without_cis_gitleaks_pins(tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(release, "_real_run", lambda argv: pytest.fail("ran docker"))
    ran, passed, lines = pp._real_test_shape(tmp_path)
    assert (ran, passed) == (False, False) and "GITLEAKS" in lines[-1]


# --- macOS, Windows and Linux on GitHub, before approval (#326) ---------------------------


def _ci_records(w) -> list[str]:
    return [
        line
        for c in w["fake"].comments.get(200, [])
        for line in c["body"].splitlines()
        if line.startswith("Candidate-ci: ")
    ]


def _ci_branch(w) -> str:
    """policyforge-ci's release branch, or "" while there is none."""
    return (
        _refs(w["ci"]).partition(f"refs/heads/release/{VERSION} ")[2].split()[:1][0]
        if (f"refs/heads/release/{VERSION} " in _refs(w["ci"]))
        else ""
    )


def test_a_passed_platform_run_lets_approval_be_asked(world):
    w = world
    code, said = _run(w["ctx"])
    assert code == 5, said
    tip = _ci_branch(w)
    p_tree = _git(w["root"], "rev-parse", f"{w['ctx'].notes['public_sha']}^{{tree}}")
    assert _git(w["ci"], "rev-parse", f"{tip}^{{tree}}") == p_tree, "the candidate holds P's tree"
    assert _git(w["ci"], "rev-list", "--count", tip) == "1", "a new branch gets a root commit"
    assert _ci_records(w) == [f"Candidate-ci: {tip} release={VERSION} tree={p_tree} result=passed"]
    assert _refs(w["public"]) == "", "nothing public before the approval"


@pytest.mark.parametrize("job", PLATFORM_JOBS)
def test_a_red_platform_job_blocks_approval(world, job):
    w = world
    w["ci_outcome"][0] = job
    code, said = _run(w["ctx"])
    assert code == 46, said  # ci-result is step 6: its postcondition fails
    assert _ci_records(w)[-1].endswith("result=FAILED"), _ci_records(w)
    assert "WAITING" not in said, "approval is never asked for"
    assert _refs(w["public"]) == ""


@pytest.mark.parametrize("outcome", ["absent", "running"])
def test_a_run_that_never_completes_is_not_run_never_a_pass(world, outcome):
    w = world
    w["ci_outcome"][0] = outcome
    code, said = _run(w["ctx"])
    assert code == 46, said
    assert _ci_records(w)[-1].endswith("result=did-not-run"), _ci_records(w)
    assert "NOT RUN" in w["fake"].comments[200][-1]["body"], "the record says why"
    assert len(w["ci_reads"]) == 600 // 180 + 1, "it read until the wait ran out"
    assert _refs(w["public"]) == ""


def test_a_publish_guard_refusal_stops_the_candidate_push(world, monkeypatch, capsys):
    """The world's guard passes P; this one refuses any OTHER commit, the candidate."""
    w = world

    def guard(ctx, candidate, parent):
        # P is guarded while it's built, before its note exists; the candidate after.
        is_p = not ctx.notes.get("public_sha")
        return Check(is_p, [f"guard: {'P passes' if is_p else 'REFUSES the candidate'}"])

    monkeypatch.setattr(pp, "_guard", guard)
    code, said = _run(w["ctx"])
    assert code == 45, said  # ci-candidate is step 5: its postcondition fails
    assert "REFUSES the candidate" in capsys.readouterr().out
    assert _ci_branch(w) == "", "no candidate reached policyforge-ci"
    assert w["ci_reads"] == [] and _ci_records(w) == []


def test_an_existing_branch_only_fast_forwards(world):
    """A candidate already on release/X.Y.Z (80's hand-pushed 1.8.0) becomes
    the parent, never a sibling the push would have to force over."""
    w = world
    seed = w["root"].parent / "ci-seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "old.txt").write_bytes(b"an earlier candidate\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "an earlier candidate")
    _git(seed, "push", "-q", str(w["ci"]), f"HEAD:refs/heads/release/{VERSION}")
    old = _ci_branch(w)
    code, said = _run(w["ctx"])
    assert code == 5, said
    new = _ci_branch(w)
    assert new != old and _git(w["ci"], "rev-parse", f"{new}^") == old


def test_a_branch_already_holding_ps_tree_is_used_as_it_is(world):
    w = world
    code, said = _run(w["ctx"])
    assert code == 5, said
    tip = _ci_branch(w)
    w["fake"].comments[200] = [
        c for c in w["fake"].comments[200] if "Candidate-ci" not in c["body"]
    ]
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 5, said
    assert _ci_branch(w) == tip, "nothing new pushed"
    assert _ci_records(w) == [_ci_records(w)[0]] and tip in _ci_records(w)[0]


def test_a_resumed_run_holds_the_platform_result_from_its_record(world):
    w = world
    code, said = _run(w["ctx"])
    assert code == 5, said
    reads = len(w["ci_reads"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert len(w["ci_reads"]) == reads, "GitHub wasn't read again on the resume"


def test_a_platform_record_from_another_login_does_not_count(world):
    w = world
    w["ci_outcome"][0] = "pytest (macos-latest, 3.12)"
    _run(w["ctx"])
    tip, tree = _ci_branch(w), w["ctx"].notes["tag_tree"]
    w["fake"].add(
        200, "someone-else", f"x\n\nCandidate-ci: {tip} release={VERSION} tree={tree} result=passed"
    )
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 46, said


@pytest.mark.parametrize("key", ["approval", "public-main"])
def test_approval_and_the_push_each_need_the_platform_record(world, key):
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["fake"].comments[200] = [
        c for c in w["fake"].comments[200] if "Candidate-ci" not in c["body"]
    ]
    gate = next(s for s in pp.steps() if s.key == key).gate(w["ctx"])
    assert not gate.ok and any("Candidate-ci record" in line for line in gate.measured), gate


# --- reading ci-read's summary, in the shape its job log really has -----------------------

_STAMP = "2026-10-07T00:41:51.9964245Z "
_SUMMARY = "\n".join(
    _STAMP + line
    for line in [
        "# rdazzleman/policyforge-ci: the latest 3 run(s)",
        "",
        "## ci #3  release/1.8.0 aaaaaaaa  in_progress/None  2026-10-06T22:35:54Z",
        "- in_progress: scan-and-test",
        "",
        "## ci #2  release/1.8.0 c812b255  completed/success  2026-10-04T22:35:54Z",
        "- success: pytest (macos-latest, 3.12)",
        "- success: scan-and-test",
        "- success: pytest (windows-latest, 3.12)",
        "- success: pytest (ubuntu-latest, 3.10)",
        "- success: pytest (ubuntu-latest, 3.11)",
        "- success: pytest (ubuntu-latest, 3.12)",
        "- success: pytest (ubuntu-latest, 3.13)",
        "- success: pytest (ubuntu-latest, 3.14)",
        "- success: pytest (ubuntu-latest, 3.12, unpinned)",
        "- success: brew install (macos-latest)",
        "- skipped: changelog",
        "",
        "## ci #1  release/1.7.0 05da4bb2  completed/failure  2026-10-04T02:10:49Z",
        "- FAIL: scan-and-test",
        "  failed step(s): pytest",
        "  ```",
        "  - FAIL: not a job line, inside an excerpt",
        "  ```",
        "- FAIL: pytest (macos-latest, 3.12)",
        "- success: pytest (windows-latest, 3.12)",
        "",
        "failed jobs: 2",
    ]
)


@pytest.mark.parametrize(
    "branch, sha, want",
    [
        ("release/1.8.0", "c812b255" + "0" * 32, "passed"),
        ("release/1.7.0", "05da4bb2" + "0" * 32, "FAILED"),
        ("release/1.8.0", "aaaaaaaa" + "0" * 32, None),  # still running
        ("release/1.8.0", "deadbeef" + "0" * 32, None),  # no run at this commit
        ("release/1.9.0", "c812b255" + "0" * 32, None),  # the right sha, another branch
    ],
)
def test_ci_reads_summary_is_read_by_branch_and_commit(branch, sha, want):
    verdict, _ = pp.ci_verdict(pp.ci_run_for(_SUMMARY, branch, sha))
    assert verdict == want


def test_the_ci_workflow_is_chosen_among_the_runs_one_push_starts():
    """The real log (ops job 4717): one push starts `macos-wheels`, `ci` and
    `single-commit export` at the same sha, printed newest first, and the
    first of them is a skipped run with one job. Reading it as the candidate's
    result read FAILED on a green candidate (b5 and 1d on PR 423)."""
    summary = "\n".join(
        [
            "## macos-wheels #4  release/9.9.0 abcdef12  completed/skipped  t",
            "- skipped: macOS wheel readiness",
            "## ci #6  release/9.9.0 abcdef12  completed/success  t",
            *(f"- success: {j}" for j in PLATFORM_JOBS),
            "## single-commit export #4  release/9.9.0 abcdef12  completed/success  t",
            "- success: pytest-in-an-export",
        ]
    )
    sha = "abcdef12" + "0" * 32
    verdict, _ = pp.ci_verdict(pp.ci_run_for(summary, "release/9.9.0", sha))
    assert verdict == "passed"
    other = pp.ci_run_for(summary, "release/9.9.0", sha, workflow="macos-wheels")
    assert other["jobs"] == [("skipped", "macOS wheel readiness")]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "None"])
def test_a_run_that_didnt_conclude_success_is_not_a_pass_with_every_job_green(conclusion):
    """1d's shapes on PR 362: the run's own conclusion decides too."""
    jobs = PLATFORM_JOBS
    summary = f"## ci #9  release/9.9.0 abcdef12  completed/{conclusion}  t\n" + "\n".join(
        f"- success: {j}" for j in jobs
    )
    verdict, lines = pp.ci_verdict(pp.ci_run_for(summary, "release/9.9.0", "abcdef12" + "0" * 32))
    assert verdict == "FAILED", lines
    assert any(f"run conclusion {conclusion}" in line for line in lines), lines


@pytest.mark.parametrize(
    "jobs",
    [
        ["success: scan-and-test", "success: pytest (windows-latest, 3.12)"],  # no macOS job at all
        [
            "success: scan-and-test",
            "skipped: pytest (macos-latest, 3.12)",
            "success: pytest (windows-latest, 3.12)",
        ],
        [  # every platform job green, but another job failed: still not a pass
            *(f"success: {job}" for job in PLATFORM_JOBS),
            "FAIL: prompt-ledger",
        ],
        # The three platform jobs from before #380, and none of its legs: an old
        # ci.yml on the candidate would read as a pass without them required.
        [
            "success: scan-and-test",
            "success: pytest (macos-latest, 3.12)",
            "success: pytest (windows-latest, 3.12)",
        ],
        # Every leg but one: a Python that never ran is missing, not absorbed.
        [f"success: {job}" for job in PLATFORM_JOBS if job != "pytest (ubuntu-latest, 3.10)"],
        # The unpinned resolve skipped, every hashed leg green.
        [
            *(f"success: {job}" for job in PLATFORM_JOBS if "unpinned" not in job),
            "skipped: pytest (ubuntu-latest, 3.12, unpinned)",
        ],
        # The Mac's brew install missing, or skipped (#381): a candidate that was
        # never installed from source on a Mac is not a pass.
        [f"success: {job}" for job in PLATFORM_JOBS if "brew" not in job],
        [
            *(f"success: {job}" for job in PLATFORM_JOBS if "brew" not in job),
            "skipped: brew install (macos-latest)",
        ],
    ],
)
def test_a_missing_or_skipped_platform_job_is_not_a_pass(jobs):
    summary = "## ci #9  release/9.9.0 abcdef12  completed/success  t\n" + "\n".join(
        f"- {j}" for j in jobs
    )
    verdict, lines = pp.ci_verdict(pp.ci_run_for(summary, "release/9.9.0", "abcdef12" + "0" * 32))
    assert verdict == "FAILED", lines


def test_the_real_reader_returns_nothing_when_ci_read_cant_be_dispatched(world):
    w = world
    calls = []

    def forgejo(method, path, data):
        calls.append((method, path))
        return 404, None, {}

    w["ctx"].forgejo = forgejo
    assert pp._real_read_ci(w["ctx"]) is None
    assert calls == [("POST", "/repos/policyforge/ops/actions/workflows/ci-read.yml/dispatches")]


def test_the_real_reader_returns_ci_reads_job_log(world, monkeypatch):
    w = world
    seen = []

    def forgejo(method, path, data):
        seen.append((method, path, data))
        if method == "POST":
            return 201, {"id": 77, "run_number": 5}, {}
        if path.endswith("/runs/77"):
            return 200, {"id": 77, "status": "success"}, {}
        if path.endswith("/runs/77/jobs"):
            return 200, [{"id": 900, "name": "read"}], {}
        return 404, None, {}

    def text_get(method, path, data, *, text=False):
        assert (method, path, text) == ("GET", "/repos/policyforge/ops/actions/jobs/900/logs", True)
        return 200, _SUMMARY, {}

    w["ctx"].forgejo = forgejo
    monkeypatch.setattr(release, "_real_forgejo", text_get)
    assert pp._real_read_ci(w["ctx"]) == _SUMMARY
    assert seen[0][2]["return_run_info"] is True and seen[0][2]["ref"] == "main"


def test_every_job_a_candidate_must_pass_is_required_and_nothing_else_is():
    """CI_REQUIRED against the hand-written list: each listed job matches
    exactly one rule, and each rule matches exactly one listed job."""
    for job in PLATFORM_JOBS:
        hits = [r.pattern for r in pp.CI_REQUIRED if r.fullmatch(job)]
        assert len(hits) == 1, (job, hits)
    for rule in pp.CI_REQUIRED:
        hits = [job for job in PLATFORM_JOBS if rule.fullmatch(job)]
        assert len(hits) == 1, (rule.pattern, hits)


#: The two guards that keep a job to GitHub's runners: any github.com repo
#: (#165's platform legs), or the private test repo alone (#380).
GITHUB_ONLY = (
    "github.server_url == 'https://github.com'",
    "endsWith(github.repository, '/policyforge-ci')",
)


@pytest.mark.github_config
def test_every_platform_leg_ci_yml_defines_is_one_a_candidate_must_pass():
    """From ci.yml's own parse, the other side again: every job that runs only
    on GitHub, with its matrix expanded into the names GitHub shows, is in the
    hand-written list. A leg added to ci.yml and not to the publish check fails
    here."""
    import itertools

    import yaml

    ci = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
    jobs = yaml.safe_load(ci.read_text(encoding="utf-8"))["jobs"]
    names = []
    for key, job in jobs.items():
        guard = str(job.get("if", ""))
        if not any(only in guard for only in GITHUB_ONLY):
            continue
        matrix = job.get("strategy", {}).get("matrix", {})
        axes = {k: v for k, v in matrix.items() if isinstance(v, list)}
        for values in itertools.product(*axes.values()) or [()]:
            name = str(job.get("name", key))
            for axis, value in zip(axes, values, strict=True):
                name = name.replace("${{ matrix." + axis + " }}", str(value))
            names.append(name)
    assert names, "no GitHub-only job found in ci.yml"
    assert sorted(set(names) - set(PLATFORM_JOBS)) == [], "legs the publish check doesn't require"
    assert len(names) == len(set(names)), names


# --- P leaves out what GitHub would act on (161) --------------------------------------

#: Written by hand from 161 and its ruling (the public repo runs nothing), not
#: from `PUBLIC_DROPPED`: the paths a release holds, and which reach P.
_GITHUB_FILES = {
    ".github/dependabot.yml": b"version: 2\n",
    ".github/workflows/ci.yml": b"on: push\n",
    ".github/workflows/nested/x.yml": b"on: push\n",
    ".github/PULL_REQUEST_TEMPLATE.md": b"# template\n",
    ".github/dependabot.yml.example": b"kept: it only shares the name's start\n",
    ".github/dependabot.yaml": b"version: 2\n",
    "docs/.github/workflows/ci.yml": b"not GitHub's: a path under another folder\n",
    "workflows/ci.yml": b"not GitHub's either\n",
}


def _p_paths(w) -> set[str]:
    w["ctx"].notes.update({"tag_commit": w["commit"], "tag_tree": w["tree"]})
    public_sha, lines = pp.build_public(w["ctx"])
    assert public_sha, lines
    listed = _git(w["root"], "ls-tree", "-r", "--name-only", public_sha)
    return set(listed.split("\n")), public_sha


@pytest.mark.parametrize("world", [_GITHUB_FILES], indirect=True)
def test_p_leaves_out_dependabot_and_the_workflows_and_keeps_the_rest(world):
    paths, public_sha = _p_paths(world)
    assert ".github/dependabot.yml" not in paths
    assert ".github/dependabot.yaml" not in paths
    assert not [p for p in paths if p.startswith(".github/workflows/")]
    for kept in (
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/dependabot.yml.example",
        "docs/.github/workflows/ci.yml",
        "workflows/ci.yml",
        "src/tool.py",
        "README.md",
    ):
        assert kept in paths, kept
    again, _ = pp.build_public(world["ctx"])
    assert again == public_sha, "deterministic: the approved P is the pushed P"


@pytest.mark.parametrize(
    "world",
    [{".github/dependabot.yml": b"version: 2\n", ".github/workflows/ci.yml": b"on: push\n"}],
    indirect=True,
)
def test_a_github_folder_left_empty_is_not_in_p(world):
    paths, _ = _p_paths(world)
    assert not [p for p in paths if p.startswith(".github")]


@pytest.mark.parametrize("world", [_GITHUB_FILES], indirect=True)
def test_the_check_reads_the_difference_and_names_only_the_dropped_paths(world):
    w = world
    w["ctx"].notes.update({"tag_commit": w["commit"], "tag_tree": w["tree"]})
    public_sha, _ = pp.build_public(w["ctx"])
    public_tree = _git(w["root"], "rev-parse", f"{public_sha}^{{tree}}")
    dropped, other = pp._tree_difference(w["ctx"], w["tree"], public_tree)
    assert sorted(dropped) == [
        "D .github/dependabot.yaml",
        "D .github/dependabot.yml",
        "D .github/workflows/ci.yml",
        "D .github/workflows/nested/x.yml",
    ]
    assert other == []
    # Any other difference is named: the release tree against P's parent tree,
    # which lacks this release's change to src/tool.py.
    _, other = pp._tree_difference(
        w["ctx"], w["tree"], _git(w["root"], "rev-parse", "HEAD~1^{tree}")
    )
    assert "M src/tool.py" in other


@pytest.mark.parametrize("world", [_GITHUB_FILES], indirect=True)
def test_the_flow_publishes_p_without_them_and_the_candidate_with_them(world):
    w = world
    code, said = _run(w["ctx"])
    assert code == 5, said
    assert "4 path(s) dropped; other differences 0" in said
    public_sha = w["ctx"].notes["public_sha"]
    public_paths = _git(w["root"], "ls-tree", "-r", "--name-only", public_sha).splitlines()
    assert ".github/workflows/ci.yml" not in public_paths
    assert "docs/.github/workflows/ci.yml" in public_paths
    candidate = _git(w["ci"], "rev-parse", f"refs/heads/release/{VERSION}")
    assert _git(w["ci"], "rev-parse", f"{candidate}^{{tree}}") == w["tree"], (
        "the CI candidate keeps the workflows: they are what runs there"
    )


@pytest.mark.parametrize("world", [_GITHUB_FILES], indirect=True)
def test_a_deletion_outside_the_dropped_paths_is_another_difference(world):
    """b5 on PR 459: a P that lacks more than `PUBLIC_DROPPED` (a builder that
    drops too much) is named, not counted as dropped."""
    w = world
    w["ctx"].notes.update({"tag_commit": w["commit"], "tag_tree": w["tree"]})
    public_sha, _ = pp.build_public(w["ctx"])
    without_readme = _git(w["root"], "rev-parse", f"{public_sha}^{{tree}}")
    # P's tree less README.md, written with git's own tools.
    entries = [
        e
        for e in _git(w["root"], "ls-tree", without_readme).splitlines()
        if not e.endswith("\tREADME.md")
    ]
    short = subprocess.run(
        ["git", "-C", str(w["root"]), "mktree"],
        input="\n".join(entries) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    dropped, other = pp._tree_difference(w["ctx"], w["tree"], short)
    assert "D README.md" in other
    assert "D README.md" not in dropped


@pytest.mark.parametrize("world", [_GITHUB_FILES], indirect=True)
def test_a_p_that_drops_too_much_stops_the_run_and_names_the_path(world, monkeypatch):
    """The check, not only the builder: with the builder made to drop README.md
    as well, the run refuses at the public build and names it."""
    w = world
    real = pp._tree_without_dropped

    def over_dropping(ctx, tree, prefix=""):
        with monkeypatch.context() as m:
            m.setattr(pp, "PUBLIC_DROPPED", (*pp.PUBLIC_DROPPED, "README.md"))
            return real(ctx, tree, prefix)

    monkeypatch.setattr(pp, "_tree_without_dropped", over_dropping)
    code, said = _run(w["ctx"])
    assert code == 12, said  # public-build is step 2
    assert "D README.md" in said
    assert _refs(w["public"]) == ""
