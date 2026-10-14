"""`generate` checks that `--out` can be written before any model call (#226).

The draft is written only after the model has been paid for. An `--out`
under an existing FILE made one model call, then ended in an uncaught
`FileExistsError` (measured on PR 225, 3693): a paid draft lost, and a
traceback. Now the write's own failures are found first, exit 2, one line
naming the path, before config, catalogs or a provider.

What the write already does is unchanged and not a failure: missing parent
directories are created, and an existing file `generate` wrote and nobody
edited is overwritten (its earlier versions stay in `--history-dir`). A file
that was edited, or that `generate` didn't write, is refused first, before
any model call (#385).

Proven with a provider factory that raises: reaching it fails the test.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from click.testing import CliRunner

SYNTHESIS = "---\ntopic: Access Control\n---\n\n- Staff must review access. [NIST 800-53 AC-2]\n"
DRAFT = "# Access Control Standard\n\nStaff must review access. [NIST 800-53 AC-2]\n"


class _ProviderBuilt(Exception):
    """Raised where the provider would be built."""


def _unbuildable(config):
    raise _ProviderBuilt


class _Draft:
    def __init__(self):
        self.calls = 0

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls += 1
        return LLMResponse(text=DRAFT, model="fake")

    def check(self):
        return True


def _run(tmp_path, monkeypatch, out: str, factory=_unbuildable):
    import policyforge.cli as cli_mod

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(SYNTHESIS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"org": {"name": "Acme"}, "llm": {"provider": "local"}}
    )
    monkeypatch.setattr(cli_mod, "get_provider", factory)
    return CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--synthesis",
            str(synthesis),
            "--out",
            out,
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )


def _refused(result, path_text: str):
    assert not isinstance(result.exception, _ProviderBuilt), result.output
    assert result.exit_code == 2, (result.exit_code, result.output)
    assert f"--out {path_text}" in result.output, result.output


# ---- what the write would fail on: refused before any provider -------------


def test_a_file_where_a_directory_is_needed_is_refused_first(tmp_path, monkeypatch):
    """The measured case: `--out <existing file>/x.md`."""
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    out = blocker / "x.md"
    result = _run(tmp_path, monkeypatch, str(out))
    _refused(result, str(out))
    assert "is a file, not a directory" in result.output


def test_a_file_deeper_in_the_way_is_refused_first(tmp_path, monkeypatch):
    """The file is two levels up, below directories that don't exist yet."""
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    out = blocker / "sub" / "deeper" / "x.md"
    result = _run(tmp_path, monkeypatch, str(out))
    _refused(result, str(out))
    assert "is a file, not a directory" in result.output


def test_an_existing_directory_as_the_target_is_refused_first(tmp_path, monkeypatch):
    target = tmp_path / "standards"
    target.mkdir()
    result = _run(tmp_path, monkeypatch, str(target))
    _refused(result, str(target))
    assert "is a directory" in result.output


# ---- what the write already does: still allowed ----------------------------


def test_valid_out_paths_still_reach_the_provider(tmp_path, monkeypatch):
    """The control case: with nothing in the way, the unbuildable provider IS
    what stops the run, so the tests above prove the check comes first."""
    for out in (str(tmp_path / "new" / "dirs" / "x.md"), "relative/x.md"):
        result = _run(tmp_path, monkeypatch, out)
        assert isinstance(result.exception, _ProviderBuilt), (out, result.output)


def test_the_check_leaves_nothing_behind(tmp_path, monkeypatch):
    """The probe removes the file and every directory it made, so a run
    stopped at the provider leaves the tree as it found it."""
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    out = tmp_path / "new" / "deeper" / "x.md"
    result = _run(tmp_path, monkeypatch, str(out))
    assert isinstance(result.exception, _ProviderBuilt), result.output
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert after == sorted([*before, "access-control.md"]), set(after) - set(before)


def test_a_new_relative_path_is_created_and_written(tmp_path, monkeypatch):
    """The passing case, end to end with a fake provider: missing parent
    directories are made, as `write_markdown` always did."""
    from policyforge.llm import ledger

    draft = _Draft()
    result = _run(
        tmp_path, monkeypatch, "relative/sub/x.md", factory=lambda c: ledger.wrap(draft, c)
    )
    assert result.exit_code == 0, result.output
    assert draft.calls == 1
    written = (tmp_path / "relative" / "sub" / "x.md").read_text(encoding="utf-8")
    assert "# Access Control Standard" in written, written


def test_an_existing_generated_target_is_overwritten_as_before(tmp_path, monkeypatch):
    """`generate` overwrites a file it wrote and nobody has touched; that
    stays as it was. A file it didn't write, or that was edited since, is
    refused (#385, tests/test_generate_overwrite_guard.py)."""
    from policyforge.content.provenance import stamp_with_body_hash
    from policyforge.llm import ledger

    target = tmp_path / "x.md"
    target.write_text(stamp_with_body_hash("an older draft\n", {}), encoding="utf-8")
    result = _run(tmp_path, monkeypatch, str(target), factory=lambda c: ledger.wrap(_Draft(), c))
    assert result.exit_code == 0, result.output
    written = target.read_text(encoding="utf-8")
    assert "# Access Control Standard" in written and "an older draft" not in written, written


def test_an_existing_target_it_did_not_write_is_refused_before_the_model_is_called(
    tmp_path, monkeypatch
):
    """The same file with no stamp: someone's own document, so no model call
    is made and the file is as it was (#385)."""
    from policyforge.llm import ledger

    target = tmp_path / "x.md"
    target.write_text("an older draft\n", encoding="utf-8")
    draft = _Draft()
    result = _run(tmp_path, monkeypatch, str(target), factory=lambda c: ledger.wrap(draft, c))
    assert result.exit_code == 2, result.output
    assert draft.calls == 0
    assert target.read_text(encoding="utf-8") == "an older draft\n"


@pytest.mark.parametrize("name", ["afile/x.md", "afile/sub/x.md"])
def test_the_measured_case_makes_no_model_call(tmp_path, monkeypatch, name):
    """PR 225's measurement, turned round: with a working provider, the bad
    `--out` now stops the run before the one call it used to make."""
    from policyforge.llm import ledger

    (tmp_path / "afile").write_text("x", encoding="utf-8")
    draft = _Draft()
    result = _run(tmp_path, monkeypatch, name, factory=lambda c: ledger.wrap(draft, c))
    assert result.exit_code == 2, result.output
    assert draft.calls == 0


# ---- agreement with the real write (1d on PR 241) ----------------------------

#: Targets written from the domain, not from the check: names Windows refuses
#: (`?`, `<`, `|`, a directory ending in a space), a name too long for any
#: common filesystem, a file where a directory is needed, an existing target
#: read-only or not, and an existing directory. The EXPECTED answer is the
#: real `write_markdown` on whatever platform this runs, so no case is skipped:
#: `x?y.md` is refused on Windows and written on Linux, and the check has to
#: agree with the write on both.
TARGETS = {
    "plain": "plain/x.md",
    "question-mark": "x?y.md",
    "less-than": "x<y.md",
    "pipe": "x|y.md",
    "trailing-space-dir": "trail /x.md",
    "too-long": "n" * 300 + ".md",
    "file-in-the-way": "afile/x.md",
    "existing": "existing.md",
    "existing-read-only": "readonly.md",
    "existing-directory": "adir",
}


def _setup(directory: Path) -> None:
    (directory / "afile").write_text("x", encoding="utf-8")
    (directory / "existing.md").write_text("old", encoding="utf-8")
    (directory / "readonly.md").write_text("old", encoding="utf-8")
    (directory / "readonly.md").chmod(stat.S_IREAD)
    (directory / "adir").mkdir()


def _tree(directory: Path) -> dict[str, bytes | None]:
    return {
        p.relative_to(directory).as_posix(): (p.read_bytes() if p.is_file() else None)
        for p in directory.rglob("*")
    }


@pytest.mark.parametrize("case", list(TARGETS))
def test_the_check_agrees_with_the_real_write(tmp_path, case):
    from policyforge.cli.documents import _out_problem
    from policyforge.export.markdown_exporter import write_markdown

    _setup(tmp_path)
    target = tmp_path / TARGETS[case]
    try:
        before = _tree(tmp_path)
        problem = _out_problem(target)
        assert _tree(tmp_path) == before, "the check changed the tree"
        try:
            write_markdown(DRAFT, output_dir=target.parent, filename=target.name)
            written = True
        except OSError:
            written = False
        assert (problem == "") is written, (case, problem, written)
    finally:
        (tmp_path / "readonly.md").chmod(stat.S_IREAD | stat.S_IWRITE)


# ---- when even asking raises (9b on PR 241, CI run 551) ----------------------


def _raising(method_name: str, target: Path):
    """`Path.<method_name>` raising ENAMETOOLONG for `target`, as Python
    3.10-3.13's pathlib does on Linux for a name the OS refuses outright
    (3.14 returns False). Forced, so it's pinned on every version."""
    import errno
    import pathlib

    real = getattr(pathlib.Path, method_name)

    def method(self, *args, **kwargs):
        if self == target:
            raise OSError(errno.ENAMETOOLONG, "File name too long", str(self))
        return real(self, *args, **kwargs)

    return method


@pytest.mark.parametrize(
    ("method_name", "where"),
    [
        # The target itself, asked first: `out_path.is_dir()`.
        ("is_dir", "target"),
        # A too-long DIRECTORY on the way: the walk's `nearest.exists()`.
        ("exists", "intermediate"),
    ],
    ids=["is_dir-on-the-target", "exists-on-the-walk"],
)
def test_a_name_the_os_refuses_when_asked_is_reported_not_raised(
    tmp_path, monkeypatch, method_name, where
):
    import pathlib

    from policyforge.cli.documents import _out_problem

    long = "n" * 300
    target = tmp_path / (long + ".md") if where == "target" else tmp_path / long / "x.md"
    raising_at = target if where == "target" else tmp_path / long
    monkeypatch.setattr(pathlib.Path, method_name, _raising(method_name, raising_at))
    problem = _out_problem(target)
    assert problem.startswith(f"--out {target} can't be written"), problem
    assert "too long" in problem.lower(), problem


def test_through_the_cli_a_refused_name_exits_2_before_any_provider(tmp_path, monkeypatch):
    import pathlib

    target = tmp_path / ("n" * 300 + ".md")
    monkeypatch.setattr(pathlib.Path, "is_dir", _raising("is_dir", target))
    result = _run(tmp_path, monkeypatch, str(target))
    _refused(result, str(target))
