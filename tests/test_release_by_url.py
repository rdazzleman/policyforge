"""Release tooling reads and pushes the private repo BY URL, never through a
remote, and refuses a URL that carries a credential (80's ruling (b) on #124).

The shared clone's `forgejo` remote carried another account's token in its
URL (b5 measured that it had userinfo, without printing it), so every read
and push through it went to a URL holding a credential that wasn't the
operator's. These pin that the tooling no longer reads any remote, that a
remote holding a credential is never used, and that a URL with one is
refused naming its host, with the credential in nothing it returns.

Real git against scratch repositories; the "credential" is a marker string.
"""

from __future__ import annotations

import http.server
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publish_public as pp  # noqa: E402
import release  # noqa: E402

#: Stands in for a token in a URL; it must appear in nothing returned.
SECRET = "not-a-real-token-b5-124"
#: A URL holding it, at a port where nothing listens, so a use would fail too.
WITH_USERINFO = f"http://policyforge-ag:{SECRET}@127.0.0.1:9/policyforge/policyforge.git"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()  # fmt: skip


@pytest.fixture
def private(tmp_path) -> tuple[Path, Path, str]:
    """A private bare repo holding v9.9.0, and a plain clone of it (`origin`)."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    (src / "README.md").write_text("tool\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "Release 9.9.0")
    _git(src, "tag", "-a", "v9.9.0", "-m", "Release 9.9.0")
    bare = tmp_path / "private.git"
    _git(tmp_path, "clone", "-q", "--bare", str(src), str(bare))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(bare), str(clone))
    return bare, clone, _git(src, "rev-parse", "HEAD")


def _ctx(clone: Path, bare: Path, run) -> pp.PublicContext:
    return pp.PublicContext(version="9.9.0", root=clone, run=run, forgejo_url=str(bare))


# ---- no remote is read -------------------------------------------------------------


def test_a_clone_with_no_forgejo_remote_reads_the_private_tag(private):
    bare, clone, commit = private
    assert "forgejo" not in _git(clone, "remote").split(), "premise: no forgejo remote"
    assert pp._tag_commit(_ctx(clone, bare, release._real_run)) == commit


def test_a_remote_holding_a_credential_is_never_used(private):
    """The shared clone's shape: a `forgejo` remote whose URL holds a token.
    The tag is still read from the private repo's URL, and nothing the run
    asks git carries the credential. (The ruling's arm: fall back to the
    remote, and this fails.)"""
    bare, clone, commit = private
    _git(clone, "remote", "add", "forgejo", WITH_USERINFO)
    calls: list[list[str]] = []

    def run(argv):
        calls.append(list(argv))
        return release._real_run(argv)

    assert pp._tag_commit(_ctx(clone, bare, run)) == commit
    assert calls and not any(SECRET in " ".join(argv) for argv in calls), calls
    assert not any("forgejo" in argv for argv in calls), "no remote is named"


# ---- a URL with a credential is refused, and says so without it ------------------------


@pytest.mark.parametrize("command", ["ls-remote", "fetch", "push"])
def test_a_url_with_userinfo_is_refused_naming_only_its_host(tmp_path, command):
    done = release._real_run(["git", "-C", str(tmp_path), command, WITH_USERINFO, "main"])
    assert done.returncode == 2
    assert "embedded credentials" in done.stderr and "127.0.0.1" in done.stderr
    shown = done.stderr + done.stdout + " ".join(map(str, done.args))
    assert SECRET not in shown and "policyforge-ag" not in shown


def test_the_signed_out_runner_refuses_one_too():
    done = pp._real_run_signed_out(["git", "ls-remote", WITH_USERINFO])
    assert done.returncode == 2 and SECRET not in done.stderr + " ".join(done.args)


@pytest.mark.parametrize(
    ("url", "refused"),
    [
        ("http://127.0.0.1:3000/policyforge/policyforge.git", False),
        ("https://github.com/rdazzleman/policyforge.git", False),
        ("C:/tmp/private.git", False),
        ("/tmp/private.git", False),
        (WITH_USERINFO, True),
        ("https://token@github.com/rdazzleman/policyforge.git", True),
    ],
)
def test_which_urls_carry_userinfo(url, refused):
    assert (release.userinfo_refusal(url) is not None) is refused


def test_publish_public_refuses_a_forgejo_url_with_userinfo_before_anything(monkeypatch, capsys):
    monkeypatch.setenv("FORGEJO_TOKEN", "sentinel")
    monkeypatch.setattr(release, "FORGEJO_REPO_URL", WITH_USERINFO)
    monkeypatch.setattr(pp, "_topology", lambda ctx: pytest.fail("ran past the refusal"))
    code = pp.main(["9.9.0", "--tracking-issue", "1", "--local-issue", "1",
                    "--identity", "A <a@b>", "--formula", "f.rb"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 2 and "embedded credentials" in out and SECRET not in out


# ---- step 1 counts only the tooling's own records (9b on #124) ---------------------------


@pytest.mark.parametrize(("login", "counts"), [("policyforge-9b", True), ("someone-else", False)])
def test_a_release_local_record_counts_only_from_a_record_author(private, login, counts):
    bare, clone, commit = private
    tree = _git(clone, "rev-parse", "v9.9.0^{tree}")
    line = f"Release-local: {commit} release=9.9.0 tree={tree} deferred=x"

    def forgejo(method, path, data):
        return 200, [{"user": {"login": login}, "body": f"recorded\n\n{line}"}], {}

    ctx = _ctx(clone, bare, release._real_run)
    ctx.forgejo, ctx.local_issue = forgejo, 100
    assert pp._local_released(ctx).ok is counts


# ---- a rejected token never reaches the credential helper (ba on #124) -----------------


class _Unauthorized(http.server.BaseHTTPRequestHandler):
    """Answers every request 401, recording the Authorization it was sent."""

    seen: list[str] = []

    def do_GET(self):
        type(self).seen.append(self.headers.get("Authorization", ""))
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="forgejo"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def unauthorized():
    _Unauthorized.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Unauthorized)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _logging_helper(tmp_path: Path) -> tuple[Path, Path]:
    """A global git config whose credential helper hands out a stored
    credential and logs every action git asks of it (get, store, erase)."""
    log = tmp_path / "helper.log"
    helper = tmp_path / "helper.py"
    helper.write_text(
        "import sys\n"
        f"open({log.as_posix()!r}, 'a').write(sys.argv[-1] + chr(10))\n"
        "if sys.argv[-1] == 'get':\n"
        "    print('username=someone-else')\n"
        "    print('password=stored-for-someone-else')\n",
        encoding="utf-8",
    )
    config = tmp_path / "gitconfig"
    # A git config value: the shell command's own quotes escaped as \".
    quote = chr(92) + '"'
    command = f"!{quote}{Path(sys.executable).as_posix()}{quote} {quote}{helper.as_posix()}{quote}"
    config.write_text(f'[credential]\n\thelper = "{command}"\n', encoding="utf-8")
    return config, log


def test_a_rejected_token_never_asks_the_credential_helper(tmp_path, monkeypatch, unauthorized):
    """With the operator's token refused, git must not read a stored
    credential, nor erase it: the header is the only credential. The control
    (no tooling environment) shows this harness does see the helper asked."""
    config, log = _logging_helper(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.setenv("FORGEJO_TOKEN", "operator-token-sentinel")
    monkeypatch.setattr(release, "FORGEJO_URL", unauthorized)
    url = f"{unauthorized}/policyforge/policyforge.git"

    plain = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    subprocess.run(["git", "ls-remote", url], env=plain, capture_output=True, timeout=60)
    assert "get" in log.read_text(encoding="utf-8").split(), "premise: the helper can be asked"
    log.unlink()
    _Unauthorized.seen = []

    done = release._real_run(["git", "ls-remote", url])
    assert done.returncode != 0
    assert _Unauthorized.seen and set(_Unauthorized.seen) == {"token operator-token-sentinel"}
    assert not log.exists(), f"the helper was asked: {log.read_text(encoding='utf-8')}"


def _fill(env: dict, url: str) -> subprocess.CompletedProcess:
    """`git credential fill` for `url`: which credential git would use there."""
    return subprocess.run(
        ["git", "credential", "fill"],
        input=f"url={url}\n\n", env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )  # fmt: skip


def test_the_reset_is_forgejos_alone_github_keeps_its_helper(tmp_path, monkeypatch):
    """1d on #124: an unscoped reset left release.py's pushes to GitHub with
    no credential at all. Scoped, Forgejo's URLs never reach the helper, and
    GitHub's still do."""
    config, log = _logging_helper(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.setenv("FORGEJO_TOKEN", "operator-token-sentinel")
    env = release._git_env()

    github = _fill(env, "https://github.com/rdazzleman/policyforge.git")
    assert "username=someone-else" in github.stdout, github.stderr
    assert log.read_text(encoding="utf-8").split() == ["get"]
    log.unlink()

    for url in (release.FORGEJO_REPO_URL, "http://localhost:3000/policyforge/policyforge.git"):
        forgejo = _fill(env, url)
        assert forgejo.returncode != 0 and "someone-else" not in forgejo.stdout, url
    assert not log.exists(), f"the helper was asked for Forgejo: {log.read_text(encoding='utf-8')}"
