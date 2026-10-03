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
def world(tmp_path, monkeypatch):
    """The local release of 9.9.0, done: a private repo with tag v9.9.0 and
    its record; an empty public repo; a 'GitHub' only the mirror writes."""
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
    _git(src, "commit", "-qam", "Release 9.9.0")
    _git(src, "tag", "-a", "v9.9.0", "-m", "Release 9.9.0")
    private = tmp_path / "private.git"
    _git(tmp_path, "clone", "-q", "--bare", str(src), str(private))
    root = tmp_path / "root"
    _git(tmp_path, "clone", "-q", "-o", "forgejo", str(private), str(root))
    _git(root, "config", "core.autocrlf", "false")
    public = tmp_path / "public.git"
    github = tmp_path / "github.git"
    for bare in (public, github):
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

    def mirror(_seconds):
        """The push mirrors, as Forgejo runs them: every ref of each repo."""
        _git(public, "push", "-q", "--mirror", str(github))
        _git(tap, "push", "-q", "--mirror", str(github_tap))

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
    assert "Public-install: " + public_sha in w["fake"].comments[200][-1]["body"]
    _approve(w, public_sha)
    w["ctx"].notes.clear()
    code, said = _run(w["ctx"])
    assert code == 0, said
    assert _git(w["public"], "rev-parse", "refs/heads/main") == public_sha
    assert _git(w["public"], "rev-parse", "refs/tags/v9.9.0^{}") == public_sha
    assert _git(w["github"], "rev-parse", "refs/heads/main") == public_sha
    assert "Public-published: " + public_sha in w["fake"].comments[200][-1]["body"]


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
    _git(src, "push", "-q", "forgejo", "HEAD:refs/heads/main", "refs/tags/v9.10.0")
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
    assert code == 45, said  # public-main (step 5): its postcondition fails
    assert _git(w["public"], "rev-parse", "refs/heads/main") == stray, "never overwritten"


def test_a_mirror_that_never_arrives_times_out_loudly(world, capsys):
    w = world
    _run(w["ctx"])
    _approve(w, w["ctx"].notes["public_sha"])
    w["ctx"].notes.clear()
    w["ctx"].sleep = lambda seconds: None  # the mirror never runs
    code, said = _run(w["ctx"])
    assert code == 47, said  # github-mirrored is step 7: its postcondition fails
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
    assert code == 48, said  # github-archive is step 8
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
    assert code == 19, said  # candidate-install (step 9): its gate refuses
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
    assert code == 50, said  # tap-commit (step 10): its postcondition fails
    assert _git(w["tap"], "rev-parse", "refs/heads/main") == _git(other, "rev-parse", "HEAD")


def test_a_tap_mirror_that_never_arrives_times_out_loudly(world, capsys):
    w = world
    calls = [0]

    def public_only(_seconds):
        calls[0] += 1
        _git(w["public"], "push", "-q", "--mirror", str(w["github"]))

    w["ctx"].sleep = public_only
    code, said = _to_the_end(w)
    assert code == 51, said  # tap-mirrored (step 11)
    assert "MIRROR TIMEOUT" in capsys.readouterr().out


def test_a_release_check_that_fails_fails_the_publish(world):
    w = world
    w["release_check_exit"][0] = 1
    code, said = _to_the_end(w)
    assert code == 22, said  # release-check (step 12)
