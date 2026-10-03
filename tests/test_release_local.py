"""`scripts/release.py --publish local`: a release on Forgejo alone (#90).

The user, to 9b directly on 2026-09-29: 1.7 is published locally first.
Forgejo main and a Forgejo tag, both installs from Forgejo archives, and
every GitHub step deferred: said in the output, recorded on the tracking
issue, never skipped silently.

1d's read list (#90, 1843) is the spec: no github.com, measured on the wire
with a control that does hear GitHub; every GitHub step enumerated from the
code, with a new unhandled one failing; DEFERRED printed and recorded; both
installs from Forgejo archives, with the tree proof; the later public run
refusing a moved tag. 80's condition: the token never enters the container.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release
import release_check
from release import ACT, CHECK, DEFER, Context, Step

CUT = "c" * 40
MAIN = "d" * 40
SENTINEL = "fj-token-sentinel-4f1d"


# --- the plan -------------------------------------------------------------------


def _plan():
    return release.steps("forgejo", release.PUBLISH_LOCAL)


def test_the_local_plan_is_the_public_plan_with_github_deferred():
    public = [s.key for s in release.steps("forgejo")]
    local = _plan()
    assert [s.key for s in local] == [*public, "local-record"], "same order, one step added"
    kinds = {s.key: s.kind for s in local}
    assert {k for k, v in kinds.items() if v == DEFER} == set(release.DEFERRED_LOCAL)
    # Nothing deferred that a local release needs: the Forgejo cut, both
    # installs, the user's merge and the tag all run.
    for key in ("release-pr", "head-install", "main", "tag", "candidate-install"):
        assert kinds[key] != DEFER, key


def test_a_local_release_is_a_forgejo_release():
    with pytest.raises(ValueError):
        release.steps("github", release.PUBLISH_LOCAL)


def test_a_deferred_step_is_said_and_never_evaluated():
    def boom(ctx):
        raise AssertionError("a deferred step was evaluated")

    plan = [
        Step("before", "runs", CHECK, lambda c: release.Check(True), lambda c: release.Check(True)),
        Step(
            "gh", "a GitHub step", DEFER, boom, boom, act=boom, would="until GitHub: a GitHub step"
        ),
        Step("after", "runs", CHECK, lambda c: release.Check(True), lambda c: release.Check(True)),
    ]
    said: list[str] = []
    code = release.run_steps(plan, Context(version="9.9.9"), execute=True, out=said.append)
    assert code == 0
    text = "\n".join(said)
    assert "DEFERRED: until GitHub: a GitHub step" in text
    assert text.index("DEFERRED") < text.index("[3/3] after"), "and the run goes on past it"


def test_main_refuses_a_local_release_missing_what_keeps_it_off_github(capsys):
    for argv in (
        [
            "1.7.0",
            "--publish",
            "local",
            "--forge",
            "github",
            "--formula",
            __file__,
            "--tracking-issue",
            "1",
        ],
        ["1.7.0", "--publish", "local", "--tracking-issue", "1"],
        ["1.7.0", "--publish", "local", "--formula", __file__],
    ):
        with pytest.raises(SystemExit) as stop:
            release.main(argv)
        assert stop.value.code == 2, argv
    assert "--publish local needs" in capsys.readouterr().err


# --- every step, against recording fakes: which reach GitHub -------------------


def _touches_github(kind: str, what) -> bool:
    text = " ".join(map(str, what)) if isinstance(what, (list, tuple)) else str(what)
    if "github.com" in text or "githubusercontent.com" in text:
        return True
    if kind == "run" and list(what)[:1] == ["gh"]:
        return True
    # release_check.py reads the published formula and the tag's archive from
    # GitHub; its argv names neither, so running it counts by name. The wire
    # test below measures the traffic itself.
    return kind == "run" and any(str(a).endswith("release_check.py") for a in what)


class Recorder:
    """Every I/O callable a Context injects, recording what each was asked."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def run(self, argv):
        self.calls.append(("run", list(argv)))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def status_of(self, url):
        self.calls.append(("status", url))
        return 200

    def hash_url(self, url):
        self.calls.append(("hash", url))
        return "0" * 64

    def install(self, formula, version, source=None):
        self.calls.append(("install", formula))
        return True, True, []

    def forgejo(self, method, path, data):
        self.calls.append(("forgejo", path))
        return 200, [], {}

    def fetch(self, path, dest):
        self.calls.append(("fetch", path))
        return 404


FORMULA = (
    'class Policyforge < Formula\n  desc "x"\n  homepage "https://example.invalid"\n'
    '  url "https://example.invalid/a.tar.gz"\n  sha256 "' + "0" * 64 + '"\n\n'
    '  resource "click" do\n    url "https://example.invalid/click.tar.gz"\n'
    '    sha256 "' + "1" * 64 + '"\n  end\nend\n'
)


def _keep_the_real_changelog(monkeypatch) -> list:
    """`_assemble` runs `changelog_fragments.main`, which writes the checkout
    the script lives in, whatever `ctx.root` says: this test's first version
    rewrote a real CHANGELOG.md and deleted every fragment of its worktree
    (restored, nothing committed). It makes no network call (no urllib,
    subprocess or socket in it), so a stub hides nothing from the census."""
    called: list = []
    monkeypatch.setattr(release.changelog_fragments, "main", lambda argv: called.append(argv))
    return called


def _census(plan, tmp_path, monkeypatch, *, formula: bool) -> dict[str, list]:
    """Each step's gate, postcondition and action, run against recording
    fakes; {step: the GitHub touches it made}. A step that raises on the
    fakes still has everything it asked before raising recorded."""
    _keep_the_real_changelog(monkeypatch)
    monkeypatch.setattr(
        release_check,
        "fetch_formula",
        lambda url=release_check.FORMULA_URL: (rec.calls.append(("fetch_formula", url)), FORMULA)[
            1
        ],
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 9.9.0\n\nx\n", encoding="utf-8")
    base = tmp_path / "formula.rb"
    base.write_text(FORMULA, encoding="utf-8")
    rec = Recorder()
    ctx = Context(
        version="9.9.0",
        root=tmp_path,
        run=rec.run,
        status_of=rec.status_of,
        hash_url=rec.hash_url,
        install=rec.install,
        confirm=lambda prompt: prompt.split("'")[1],
        tracking=1,
        formula_path=base if formula else None,
        forge="forgejo",
        forgejo=rec.forgejo,
        publish=release.PUBLISH_PUBLIC,
        fetch_archive=rec.fetch,
    )
    ctx.publish = release.PUBLISH_LOCAL if any(s.kind == DEFER for s in plan) else "public"
    found: dict[str, list] = {}
    for step in plan:
        if step.kind == DEFER:
            continue
        ctx.notes.update(
            {
                "public_sha": "e" * 40,
                "main_sha": MAIN,
                "candidate_formula": FORMULA,
                "head install": "passed",
                "candidate install": "passed",
            }
        )
        if step.key == "resources":
            # It reads the candidate once one exists, else the BASE formula:
            # the base is the path taken before any candidate, so that one.
            ctx.notes.pop("candidate_formula")
        start = len(rec.calls)
        for part in (step.gate, step.post, step.act):
            if part is None:
                continue
            # The fakes answer nothing real; what each step asked is recorded.
            with contextlib.suppress(Exception):
                part(ctx)
        hits = [c for c in rec.calls[start:] if _touches_github(*c)]
        if hits:
            found[step.key] = hits
    return found


def test_the_census_hears_github_in_the_public_plan(tmp_path, monkeypatch):
    """The control: the same census, on the public plan without --formula,
    finds GitHub in every step the local plan defers or replaces, and in
    `resources` (the published formula). So its silence below means something."""
    found = _census(release.steps("forgejo"), tmp_path, monkeypatch, formula=False)
    for key in (*release.DEFERRED_LOCAL, "head-install", "tag", "candidate-install", "resources"):
        assert key in found, (key, sorted(found))


def test_no_step_of_a_local_release_reaches_github(tmp_path, monkeypatch):
    found = _census(_plan(), tmp_path, monkeypatch, formula=True)
    assert found == {}, found


def test_a_new_github_step_fails_the_local_census(tmp_path, monkeypatch):
    """Classified from the code, not from a list: a step added to the cut
    that reaches GitHub, and that nobody deferred, is caught."""
    original = release._base_steps

    def with_a_new_one():
        return [
            *original(),
            Step(
                "announce",
                "tell GitHub",
                ACT,
                lambda c: release.Check(True),
                lambda c: release.Check(False),
                lambda c: c.run(["git", "push", release.GITHUB_URL, "refs/heads/x"]),
            ),
        ]

    monkeypatch.setattr(release, "_base_steps", with_a_new_one)
    found = _census(_plan(), tmp_path, monkeypatch, formula=True)
    assert set(found) == {"announce"}, found


# --- the tree proof -----------------------------------------------------------------


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@x",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x",
        },
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    # A Windows git's own `archive` applies core.autocrlf and writes CRLF, which
    # the tree proof rightly refuses; Forgejo's archive is the blobs (#90, 1945).
    _git(root, "config", "core.autocrlf", "false")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_bytes(b"print('a')\n")
    (root / "README.md").write_bytes(b"# r\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c")
    return root, _git(root, "rev-parse", "HEAD")


def _archive(root, commit, dest) -> Path:
    _git(root, "archive", "--format=tar.gz", "--prefix=policyforge/", "-o", str(dest), commit)
    return dest


def _rewrite(src: Path, dest: Path, change) -> Path:
    """The tar at `src`, with `change(members) -> members` applied."""
    with tarfile.open(src, "r:gz") as tar:
        members = [(m, tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()]
    members = change(members)
    with tarfile.open(dest, "w:gz") as out:
        for member, data in members:
            if data is not None:
                member.size = len(data)
                out.addfile(member, io.BytesIO(data))
            else:
                out.addfile(member)
    return dest


def _real_ctx(root) -> Context:
    return Context(version="9.9.0", root=root, run=release._real_run)


def test_an_archive_of_the_commit_proves(repo, tmp_path):
    root, commit = repo
    ok, lines = release._archive_is_tree(
        _real_ctx(root), _archive(root, commit, tmp_path / "a.tgz"), commit
    )
    assert ok, lines


def _tampered(member_data):
    return [(m, b"print('b')\n" if m.name.endswith("a.py") else d) for m, d in member_data]


def _extra(member_data):
    extra = tarfile.TarInfo("policyforge/src/extra.py")
    return [*member_data, (extra, b"x\n")]


def _missing(member_data):
    return [(m, d) for m, d in member_data if not m.name.endswith("README.md")]


def _executable(member_data):
    for m, _ in member_data:
        if m.name.endswith("a.py"):
            m.mode = 0o755
    return member_data


def _copied(member_data, name):
    """An identical copy of src/a.py, filed under `name`."""
    source = next((m, d) for m, d in member_data if m.name.endswith("src/a.py"))
    twin = copy.copy(source[0])
    twin.name = name
    return [*member_data, (twin, source[1])]


def _second_prefix(member_data):
    """1d's case on #96: the same file under a SECOND top-level directory.
    Stripping the first component alone read it as src/a.py again."""
    return _copied(member_data, "other/src/a.py")


def _top_level_file(member_data):
    """A file with no prefix at all, which the first version skipped."""
    return _copied(member_data, "a.py")


def _moved_to_a_second_prefix(member_data):
    """src/a.py moved, bytes unchanged, under another top-level directory,
    with no copy left under the real prefix. Stripped, it matches the tree
    exactly: nothing missing, extra or doubled. Only the one-prefix check
    refuses it (measured: with that check off, every other case still fails)."""
    out = []
    for m, d in member_data:
        if m.name.endswith("src/a.py"):
            m = copy.copy(m)
            m.name = "other/src/a.py"
        out.append((m, d))
    return out


def _file_named_like_the_prefix(member_data):
    """A top-level FILE named exactly the prefix: the prefix set stays one,
    so only refusing a non-directory outside any prefix catches it."""
    ghost = tarfile.TarInfo("policyforge")
    return [*member_data, (ghost, b"x\n")]


def _held_twice(member_data):
    """The same path twice, identical bytes: which copy an extractor keeps
    is its choice, so the proof refuses the archive rather than guess."""
    return _copied(member_data, "policyforge/src/a.py")


@pytest.mark.parametrize(
    "change",
    [
        _tampered,
        _extra,
        _missing,
        _executable,
        _second_prefix,
        _moved_to_a_second_prefix,
        _top_level_file,
        _file_named_like_the_prefix,
        _held_twice,
    ],
)
def test_an_archive_that_is_not_the_commit_is_refused(repo, tmp_path, change):
    root, commit = repo
    good = _archive(root, commit, tmp_path / "a.tgz")
    bad = _rewrite(good, tmp_path / "b.tgz", change)
    ok, lines = release._archive_is_tree(_real_ctx(root), bad, commit)
    assert not ok, lines


def test_a_path_git_would_quote_proves(repo, tmp_path):
    """`ls-tree` without `-z` quotes a non-ASCII or oddly spelled path, which
    would read as a file the archive lacks. With `-z` it is itself (1d)."""
    root, _ = repo
    (root / "src" / "naïve x.py").write_bytes(b"x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "quoted")
    commit = _git(root, "rev-parse", "HEAD")
    quoted = _git(root, "-c", "core.quotepath=true", "ls-tree", "-r", commit)
    assert '"src/na' in quoted, "the premise: git quotes this path without -z"
    ok, lines = release._archive_is_tree(
        _real_ctx(root), _archive(root, commit, tmp_path / "a.tgz"), commit
    )
    assert ok, lines


def test_an_archive_of_another_commit_is_refused(repo, tmp_path):
    root, first = repo
    (root / "README.md").write_bytes(b"# changed\n")
    _git(root, "commit", "-qam", "d")
    ok, lines = release._archive_is_tree(
        _real_ctx(root), _archive(root, first, tmp_path / "a.tgz"), "HEAD"
    )
    assert not ok, lines


# --- the local installs -------------------------------------------------------------


def _install_ctx(root, archive: Path, installs: list, tmp_path) -> Context:
    base = tmp_path / "formula.rb"
    base.write_text(FORMULA, encoding="utf-8")

    def fetch(path, dest):
        shutil.copyfile(archive, dest)
        return 200

    def install(formula, version, source=None):
        installs.append((formula, version, source, source.read_bytes() if source else b""))
        return True, True, ["ok"]

    return Context(
        version="9.9.0",
        root=root,
        run=release._real_run,
        install=install,
        formula_path=base,
        forge="forgejo",
        publish=release.PUBLISH_LOCAL,
        fetch_archive=fetch,
    )


def test_a_local_install_installs_the_proved_bytes_from_a_file(repo, tmp_path):
    root, commit = repo
    archive = _archive(root, commit, tmp_path / "a.tgz")
    installs: list = []
    ctx = _install_ctx(root, archive, installs, tmp_path)
    release._install_local(ctx, commit, commit, "head install")
    assert ctx.notes["head install"] == "passed"
    ((formula, _version, _source, data),) = installs
    assert data == archive.read_bytes(), "the container gets the bytes the host proved"
    assert f'url "file://{release.LOCAL_SOURCE}"' in formula
    assert f'sha256 "{hashlib.sha256(data).hexdigest()}"' in formula
    assert 'version "9.9.0"' in formula, "a file url carries no version"
    assert "localhost" not in formula and "127.0.0.1" not in formula and "token" not in formula
    # 80's condition (#96, 1966): neither local install uses the docs' pins,
    # which name a GitHub tag that doesn't exist until the public release.
    pin = f"@v{ctx.version}"
    assert pin not in formula and pin not in release._install_script(ctx.version, "ab" * 32)


def test_a_local_install_of_an_unproved_archive_never_installs(repo, tmp_path):
    root, commit = repo
    bad = _rewrite(_archive(root, commit, tmp_path / "a.tgz"), tmp_path / "b.tgz", _tampered)
    installs: list = []
    ctx = _install_ctx(root, bad, installs, tmp_path)
    release._install_local(ctx, commit, commit, "head install")
    assert installs == [] and ctx.notes["head install"].startswith("refused")


def test_the_container_checks_the_tarball_itself_before_homebrew():
    """Homebrew 4.6.20 only warns on a `file://` checksum mismatch (#90, 1945)."""
    digest = "ab" * 32
    chain = release._install_script("9.9.0", digest).split(" && ")
    assert chain[0] == f'echo "{digest}  {release.LOCAL_SOURCE}" | sha256sum -c -'
    assert chain.index(chain[0]) < next(i for i, c in enumerate(chain) if "brew install" in c)
    assert "sha256sum" not in release._install_script("9.9.0"), "a public install has no file"


def test_the_token_never_reaches_the_container(tmp_path, monkeypatch):
    """80's condition: not as an env var, a mounted file or an argument."""
    monkeypatch.setenv("FORGEJO_TOKEN", SENTINEL)
    monkeypatch.setattr(shutil, "which", lambda name: "docker")
    runs: list[list[str]] = []
    monkeypatch.setattr(
        release,
        "_real_run",
        lambda argv: (runs.append(argv), subprocess.CompletedProcess(argv, 0, "", ""))[1],
    )
    source = tmp_path / "source.tar.gz"
    source.write_bytes(b"not a real archive")
    ran, ok, _ = release._real_install(FORMULA, "9.9.0", source)
    assert ran and ok
    run = runs[-1]
    assert SENTINEL not in " ".join(run)
    assert not {"-e", "--env", "--env-file"} & set(run), run
    assert not any(a.startswith(("--env=", "--env-file=", "-e=")) for a in run), run
    mount = run[run.index("-v") + 1].rsplit(":/candidate", 1)[0]
    for path in Path(mount).iterdir():
        assert SENTINEL.encode() not in path.read_bytes(), path


#: Opt-in, so it skips the SAME way everywhere: CI's job has no Docker and a
#: developer's gate may, and `skip_gate` matches one list exactly in both
#: (1d on #96: run 358 refused it as an unexpected skip). Listed in
#: tests/ci_expected_skips.txt. `test_the_token_never_reaches_the_container`
#: (the argv and the mounted files) is the check that runs everywhere.
DOCKER_OPT_IN = "POLICYFORGE_DOCKER_TESTS"


@pytest.mark.skipif(
    os.environ.get(DOCKER_OPT_IN) != "1",
    reason=f"opt-in: {DOCKER_OPT_IN}=1 runs it, with docker and the release image",
)
def test_the_token_is_not_in_a_real_containers_environment(tmp_path, monkeypatch):
    """The same argv the install uses, running `env` and reading the mount
    instead of installing: the operator's token is in the HOST's environment
    and nowhere in the container's. Opted into, it needs docker and the
    release image, and fails (never skips) without them."""
    monkeypatch.setenv("FORGEJO_TOKEN", SENTINEL)
    (tmp_path / "policyforge.rb").write_text(FORMULA, encoding="utf-8")
    argv = release._docker_argv(tmp_path, "env; cat /candidate/*")
    seen = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
    )
    assert seen.returncode == 0, seen.stderr
    assert "PATH=" in seen.stdout, "the premise: env ran in the container"
    assert SENTINEL not in seen.stdout + seen.stderr


# --- the tag, the record, and the later public run --------------------------------


def _fake_run(answers: dict):
    def run(argv):
        key = " ".join(argv[3:]) if argv[:2] == ["git", "-C"] else " ".join(argv)
        for prefix, out in answers.items():
            if key.startswith(prefix):
                return subprocess.CompletedProcess(argv, 0, out, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_the_local_tag_is_on_forgejo_and_names_main(tmp_path):
    calls: list = []
    run = _fake_run({"ls-remote --tags forgejo v9.9.0^{}": f"{MAIN}\trefs/tags/v9.9.0^{{}}\n"})
    ctx = Context(
        version="9.9.0",
        root=tmp_path,
        forge="forgejo",
        publish=release.PUBLISH_LOCAL,
        run=lambda argv: (calls.append(argv), run(argv))[1],
    )
    ctx.notes["main_sha"] = MAIN
    assert release._tagged(ctx).ok
    (tmp_path / "CHANGELOG.md").write_text("## 9.9.0\n\nx\n", encoding="utf-8")
    release._tag(ctx)
    assert ["git", "-C", str(tmp_path), "push", "forgejo", "refs/tags/v9.9.0"] in calls
    assert not any(c[:1] == ["gh"] for c in calls), "no GitHub Release"


def _records(line, login="policyforge-9b"):
    def forgejo(method, path, data):
        return (
            200,
            [{"created_at": "2026-09-30T00:00:00Z", "body": line, "user": {"login": login}}],
            {},
        )

    return forgejo


def _matching_ctx(tmp_path, tag_tree, cut_tree, record_tree):
    run = _fake_run(
        {
            "ls-remote --tags forgejo v1.7.0^{}": f"{MAIN}\trefs/tags/v1.7.0^{{}}\n",
            f"rev-parse {MAIN}^{{tree}}": tag_tree,
            "rev-parse HEAD^{tree}": cut_tree,
        }
    )
    line = (
        f"Release-local: {MAIN} release=1.7.0 tree={record_tree} "
        f"deferred={','.join(release.DEFERRED_LOCAL)}"
    )
    return Context(
        version="1.7.0", root=tmp_path, forge="forgejo", run=run, tracking=1, forgejo=_records(line)
    )


def test_the_later_public_run_takes_the_tree_it_released_locally(tmp_path):
    t = "1" * 40
    assert release._local_release_matches(_matching_ctx(tmp_path, t, t, t)).ok


@pytest.mark.parametrize("cut,record", [("2" * 40, "1" * 40), ("1" * 40, "2" * 40)])
def test_the_later_public_run_refuses_a_moved_tree(tmp_path, cut, record):
    check = release._local_release_matches(_matching_ctx(tmp_path, "1" * 40, cut, record))
    assert not check.ok, check.measured


def test_a_public_release_never_released_locally_is_unaffected(tmp_path):
    ctx = Context(version="1.7.0", root=tmp_path, forge="forgejo", run=_fake_run({}), tracking=1)
    check = release._local_release_matches(ctx)
    assert check.ok and "no local release" in check.measured[0]


def test_the_public_build_gate_carries_the_local_check():
    gate = next(s for s in release.steps("forgejo") if s.key == "public-build").gate
    ctx = Context(
        version="1.7.0",
        forge="forgejo",
        run=_fake_run(
            {
                "ls-remote --tags forgejo v1.7.0^{}": f"{MAIN}\trefs/tags/v1.7.0^{{}}\n",
                f"rev-parse {MAIN}^{{tree}}": "1" * 40,
                "rev-parse HEAD^{tree}": "2" * 40,
            }
        ),
        tracking=1,
        forgejo=_records("nothing"),
    )
    check = gate(ctx)
    assert not check.ok and any("must equal it: False" in m for m in check.measured), check.measured


def test_the_record_names_the_commit_the_tree_and_what_waits(tmp_path):
    posted: list = []
    run = _fake_run({f"rev-parse {MAIN}^{{tree}}": "7" * 40})
    ctx = Context(
        version="1.7.0",
        root=tmp_path,
        forge="forgejo",
        run=run,
        tracking=5,
        publish=release.PUBLISH_LOCAL,
        forgejo=lambda m, p, d: (posted.append((m, p, d)), (201, {}, {}))[1],
    )
    ctx.notes["main_sha"] = MAIN
    release._record_local(ctx)
    ((method, path, data),) = posted
    assert (method, path) == ("POST", f"/repos/{release.FORGEJO_REPO}/issues/5/comments")
    last = data["body"].rstrip().splitlines()[-1]
    match = release.LOCAL_LINE.match(last)
    assert match and match.group(1) == MAIN and match.group(3) == "7" * 40
    assert match.group(4).split(",") == list(release.DEFERRED_LOCAL)
    # 80's two conditions (#96, 1966): the pins wait on #95, and what the
    # container's Homebrew reached is said.
    body = data["body"]
    assert f"{release_check.REPOSITORY}@v1.7.0" in body and "#95" in body
    assert "GitHub and PyPI anonymously" in body
    ctx.forgejo = _records(last)
    assert release._local_recorded(ctx).ok


# --- on the wire: a recording proxy, with a control that hears GitHub ----------------


class _Proxy(BaseHTTPRequestHandler):
    """Records the host of every request made through it, and refuses all of them."""

    seen: list[str] = []

    def do_CONNECT(self):
        self.seen.append(self.path.split(":")[0])
        self.send_error(403)

    def do_GET(self):
        self.seen.append(self.path.split("/")[2] if "://" in self.path else self.path)
        self.send_error(403)

    do_HEAD = do_POST = do_GET

    def log_message(self, *args):
        pass


class _Forgejo(BaseHTTPRequestHandler):
    """A stand-in local Forgejo: every list is empty, every archive is 404."""

    hits: list[str] = []

    def _answer(self):
        self.hits.append(self.path)
        body = b"[]"
        self.send_response(200 if "/archive/" not in self.path else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_POST = do_HEAD = _answer

    def log_message(self, *args):
        pass


def _serve(handler) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _wire(plan, repo, tmp_path, monkeypatch, *, publish: str, formula: bool) -> list[str]:
    """Every step's gate, post and action with the REAL I/O (git, urllib,
    `gh` if present, release_check.py), a stand-in Forgejo on 127.0.0.1, and
    every other request sent through a recording proxy. The hosts it saw."""
    root, _ = repo
    _keep_the_real_changelog(monkeypatch)
    _Proxy.seen = []
    _Forgejo.hits = []
    proxy = _serve(_Proxy)
    forgejo = _serve(_Forgejo)
    bare = tmp_path / "forgejo.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    _git(root, "remote", "add", "forgejo", str(bare))
    url = f"http://127.0.0.1:{proxy.server_port}"
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("FORGEJO_TOKEN", SENTINEL)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setattr(release, "FORGEJO_URL", f"http://127.0.0.1:{forgejo.server_port}")
    base = tmp_path / "formula.rb"
    base.write_text(FORMULA, encoding="utf-8")
    ctx = Context(
        version="9.9.0",
        root=root,
        run=release._real_run,
        status_of=release._real_status,
        hash_url=release_check.hash_of,
        install=lambda formula, version, source=None: (True, True, []),
        confirm=lambda prompt: prompt.split("'")[1],
        tracking=1,
        formula_path=base if formula else None,
        forge="forgejo",
        forgejo=release._real_forgejo,
        publish=publish,
        fetch_archive=release._real_fetch_archive,
    )
    try:
        for step in plan:
            if step.kind == DEFER:
                continue
            ctx.notes.update(
                {
                    "public_sha": "e" * 40,
                    "main_sha": MAIN,
                    "head install": "passed",
                    "candidate install": "passed",
                }
            )
            for part in (step.gate, step.post, step.act):
                if part is None:
                    continue
                # The fakes answer nothing real; what each step asked is recorded.
                with contextlib.suppress(Exception):
                    part(ctx)
    finally:
        proxy.shutdown()
        forgejo.shutdown()
    return sorted(set(_Proxy.seen))


def _github(hosts):
    return [h for h in hosts if h.endswith(("github.com", "githubusercontent.com"))]


def test_the_wire_control_hears_github(repo, tmp_path, monkeypatch):
    """The public plan, through the same proxy: it does ask GitHub. Without
    this, the local test's silence could be a proxy nothing used."""
    hosts = _wire(
        release.steps("forgejo"),
        repo,
        tmp_path,
        monkeypatch,
        publish=release.PUBLISH_PUBLIC,
        formula=False,
    )
    assert _github(hosts), hosts


def test_a_local_release_asks_github_nothing_on_the_wire(repo, tmp_path, monkeypatch):
    hosts = _wire(_plan(), repo, tmp_path, monkeypatch, publish=release.PUBLISH_LOCAL, formula=True)
    assert _github(hosts) == [], hosts
    # The premise: the steps really asked Forgejo (through the real client),
    # and fetched an archive, so the proxy's silence is about GitHub alone.
    assert any("/api/v1/repos/" in h for h in _Forgejo.hits), _Forgejo.hits
    assert any("/archive/" in h for h in _Forgejo.hits), _Forgejo.hits


def test_the_harnesses_left_the_real_changelog_alone():
    """Run last in this file: the checkout's own fragments and CHANGELOG.md
    are as git has them, so no harness above wrote the real tree."""
    root = Path(release.__file__).resolve().parent.parent
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", "CHANGELOG.md", "changelog.d"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert status.returncode == 0 and status.stdout == "", status.stdout
