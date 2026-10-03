"""Every src/ site that reads a child's output reads UTF-8 (#287).

Each site has a test with a byte that is not UTF-8 (0xFF, which cp1252 does
decode, so it tests the codec and not only the crash) and a twin with valid
non-ASCII UTF-8. **Real children throughout** (80, on #287): real git in a
temporary repository wherever git can be made to write the bytes, and where
it cannot -- no Windows path holds 0xFF -- a real Python child standing in
for git at the same call, with the call's own decoding arguments untouched.

Which kind each site is -- DATA (refuse a bad byte, loudly, in the caller)
or SHOWN (replace it) -- is 80's ruling on #287, and each test asserts that
kind: a data site raises `UndecodableOutput`; a shown site does not raise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from policyforge.child_output import UndecodableOutput, strict_text

BAD = b"Bad\xffName"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    return root


@pytest.fixture
def child_writes(monkeypatch):
    """Make the next `subprocess.run` a REAL Python child writing `payload`
    to stdout (and `err` to stderr), keeping every decoding argument the
    call site passed -- so the site's own decoding is what is tested."""
    real = subprocess.run

    def install(payload: bytes, err: bytes = b"", code: int = 0):
        script = (
            "import sys; "
            f"sys.stdout.buffer.write({payload!r}); "
            f"sys.stderr.buffer.write({err!r}); "
            f"sys.exit({code})"
        )

        def fake(argv, **kwargs):
            kwargs.pop("cwd", None)
            kwargs.pop("env", None)
            return real([sys.executable, "-c", script], **kwargs)

        monkeypatch.setattr(subprocess, "run", fake)

    return install


# ---- the helper ---------------------------------------------------------------


def test_strict_text_names_the_site_the_command_and_the_byte():
    with pytest.raises(UndecodableOutput) as caught:
        strict_text(BAD, site="drift (committed catalog)", argv=["git", "show", "HEAD:x", "extra"])
    message = str(caught.value)
    assert "drift (committed catalog)" in message
    assert "`git show HEAD:x`" in message
    assert "0xff at position 3" in message


def test_strict_text_reads_nothing_as_empty():
    assert strict_text(None, site="s", argv=["git"]) == ""
    assert strict_text(b"", site="s", argv=["git"]) == ""


def test_an_undecodable_output_is_not_a_subprocess_error():
    """The sites catch SubprocessError to mean "git could not answer" and
    return None. A bad byte in data must not be folded into that."""
    assert not issubclass(UndecodableOutput, subprocess.SubprocessError)


# ---- crosswalk._reviewer: DATA, recorded in the overlay -------------------


def _set_user_name(repo: Path, raw: bytes) -> None:
    config = repo / ".git" / "config"
    config.write_bytes(config.read_bytes().replace(b"name = T", b"name = " + raw))


def test_the_reviewer_name_is_recorded_as_written(repo, monkeypatch):
    from policyforge.cli.crosswalk import _reviewer

    _set_user_name(repo, "José".encode())
    monkeypatch.chdir(repo)
    assert _reviewer() == "José", "a cp1252 decode recorded 'JosÃ©' before #287"


def test_a_reviewer_name_that_is_not_utf8_is_refused(repo, monkeypatch):
    from policyforge.cli.crosswalk import _reviewer

    _set_user_name(repo, BAD)
    monkeypatch.chdir(repo)
    with pytest.raises(UndecodableOutput, match="reviewer name"):
        _reviewer()


# ---- drift.read_committed: DATA, parsed as a catalog --------------------------


def _commit(repo: Path, name: str, content: bytes) -> None:
    (repo / name).write_bytes(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "c")


def test_a_committed_catalog_reads_back_as_committed(repo, monkeypatch):
    from policyforge.frameworks.drift import read_committed

    _commit(repo, "controls.json", '["café"]'.encode())
    monkeypatch.chdir(repo)
    assert read_committed(Path("controls.json")) == '["café"]'


def test_a_committed_catalog_that_is_not_utf8_is_refused(repo, monkeypatch):
    """Before #287: 0x8F crashed with AttributeError on None, and 0xFF read
    as 'ÿ' into the catalog. Now it refuses, naming the site."""
    from policyforge.frameworks.drift import read_committed

    _commit(repo, "controls.json", b'["' + BAD + b'"]')
    monkeypatch.chdir(repo)
    with pytest.raises(UndecodableOutput, match="committed catalog"):
        read_committed(Path("controls.json"))


# ---- edit.tree.uncommitted: a DECISION -- may an edit overwrite the file? -----


def test_a_modified_page_with_a_non_ascii_name_is_uncommitted(repo):
    """**The headline of #287.** `git status -z` does not quote paths, so
    `café.md` came back as cp1252 mojibake, matched nothing, and a modified
    page was reported clean -- the check that stops an edit overwriting
    uncommitted work, silently off. `cafe.md` always worked."""
    from policyforge.edit.tree import uncommitted

    for name in ("cafe.md", "café.md"):
        page = repo / name
        page.write_text("one\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "c")
        page.write_text("two\n", encoding="utf-8")
        assert uncommitted([page], cwd=repo) == [page], f"{name} modified but reported clean"


def test_git_status_output_that_is_not_utf8_refuses_rather_than_answering_clean(repo, child_writes):
    from policyforge.edit.tree import uncommitted

    child_writes(BAD)
    with pytest.raises(UndecodableOutput, match="uncommitted-change check"):
        uncommitted([repo / "x.md"], cwd=repo)


# ---- edit.tree.display_path: SHOWN ---------------------------------------------


def test_display_path_is_relative_under_a_non_ascii_checkout(tmp_path):
    from policyforge.edit.tree import display_path

    root = tmp_path / "répo"
    root.mkdir()
    _git(root, "init", "-q")
    assert display_path(root / "p.md", cwd=root) == "p.md", "fell back to the absolute path"


def test_display_path_never_raises_on_a_bad_byte(tmp_path, child_writes):
    from policyforge.edit.tree import display_path

    child_writes(BAD + b"\n")
    assert isinstance(display_path(tmp_path / "p.md", cwd=tmp_path), str)


# ---- export.github_wiki._Git: SHOWN by default, DATA for log/symbolic-ref ----


def test_the_wiki_log_reads_a_non_ascii_author(repo):
    from policyforge.export.github_wiki import _Git

    (repo / "Page.md").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=José", "commit", "-qm", "publish")
    code, log = _Git(cwd=repo).data("log", "-1", "--format=%an", "--", "Page.md")
    assert (code, log.strip()) == (0, "José")


def test_wiki_data_output_that_is_not_utf8_is_refused(repo, child_writes):
    from policyforge.export.github_wiki import _Git

    child_writes(BAD)
    with pytest.raises(UndecodableOutput, match="GitHub wiki"):
        _Git(cwd=repo).data("log", "-1")


def test_wiki_messages_are_shown_with_the_bad_byte_replaced(repo, child_writes):
    from policyforge.export.github_wiki import _Git

    child_writes(b"", err=b"fatal: " + BAD, code=128)
    result = _Git(cwd=repo)("push", "origin")
    assert result.stderr == "fatal: Bad�Name"


# ---- ingest.parser_gate.trial_run: stdout DATA, stderr SHOWN -------------------


def _candidate(tmp_path: Path, body: str) -> tuple[Path, Path]:
    """A candidate loader whose body is `body`'s lines, one per line."""
    source = "import sys\n\n\ndef load_demo_export(path):\n" + "".join(
        f"    {line}\n" for line in body.splitlines()
    )
    parser = tmp_path / "candidate.py"
    parser.write_text(source, encoding="utf-8")
    sample = tmp_path / "sample.csv"
    sample.write_text("a,b\n", encoding="utf-8")
    return parser, sample


def test_a_candidate_printing_non_ascii_still_passes(tmp_path):
    """The child runs with `-I`, which ignores PYTHONIOENCODING, so before
    #287 it wrote cp1252 and the parent read cp1252. `-X utf8` moves the child
    with the parent; without it, this is the case that would start refusing."""
    from policyforge.ingest.parser_gate import trial_run

    parser, sample = _candidate(tmp_path, "print('Café')\nreturn []")
    run = trial_run(parser, sample, framework_slug="demo")
    assert run.ok, run


def test_a_candidate_writing_bytes_that_are_not_utf8_is_refused(tmp_path):
    from policyforge.ingest.parser_gate import trial_run

    parser, sample = _candidate(
        tmp_path, f"sys.stdout.buffer.write({BAD!r}); sys.stdout.flush()\nreturn []"
    )
    with pytest.raises(UndecodableOutput, match="trial run"):
        trial_run(parser, sample, framework_slug="demo")


def test_a_failing_candidate_s_stderr_is_shown_with_the_bad_byte_replaced(tmp_path):
    from policyforge.ingest.parser_gate import trial_run

    parser, sample = _candidate(
        tmp_path, f"sys.stderr.buffer.write({BAD!r} + b'\\n'); sys.stderr.flush(); sys.exit(3)"
    )
    run = trial_run(parser, sample, framework_slug="demo")
    assert not run.ok
    assert run.detail == "Bad�Name", run.detail


# ---- registry.is_tracked / is_ignored: nothing decoded ----------------------------


def test_tracking_questions_answer_for_a_non_ascii_path(repo, monkeypatch):
    from policyforge.frameworks.registry import is_ignored, is_tracked

    _commit(repo, "café.md", b"x")
    (repo / "résumé.md").write_bytes(b"untracked")
    monkeypatch.chdir(repo)
    assert is_tracked(Path("café.md")) is True
    assert is_tracked(Path("résumé.md")) is False
    assert is_ignored(Path("café.md")) is False


def test_tracking_questions_do_not_decode_at_all(child_writes):
    """Bytes mode: nothing is decoded, so no byte can make either answer
    fail. 0x8F, not 0xFF: cp1252 decodes 0xFF (as 'ÿ'), so only a byte
    undefined in cp1252 AND invalid in UTF-8 tells bytes mode from any text
    mode -- in text mode it kills the reader thread and stdout is None."""
    from policyforge.frameworks.registry import is_ignored, is_tracked

    child_writes(b"x\x8f")
    assert is_tracked(Path("x")) is True
    child_writes(b"x\x8f", code=1)
    assert is_ignored(Path("x")) is False
