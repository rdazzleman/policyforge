"""release_check's `--version` step, run as the shell runs it (#224).

From 1.8.0 the container install requires `policyforge --version` to print
exactly `policyforge <the version cut>` with nothing on stderr, because
`--version` writes a note to stderr when the installed metadata and the
running code disagree, and that skew is what a user would have installed.

The step is shell text joined into one `bash -lc` chain in the container,
so reading the string can't say whether it fails. These tests run the
step's own text in bash, with `policyforge` replaced by a shell function
that prints what each case gives. The cases are written from what
`--version` and click can print, not from the step's own pattern.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import release_check

STALE_NOTE = (
    "Note: the code running declares 1.8.0 (/x/policyforge/__init__.py), but the "
    "installed package's metadata says 1.7.0. Reinstall to make them agree."
)


def _bash() -> str:
    """A real bash. On Windows, the one beside git first: `bash` on PATH can
    be System32's WSL launcher, which is not this shell."""
    found = None
    if sys.platform == "win32" and shutil.which("git"):
        for parent in Path(shutil.which("git")).resolve().parents[:3]:
            if (parent / "bin" / "bash.exe").is_file():
                found = str(parent / "bin" / "bash.exe")
                break
    found = found or shutil.which("bash")
    if found is None:
        pytest.fail("no bash: the container runs this step in bash, so it must be tested in one")
    return found


def _run_step(step: str, stdout: str, stderr: str, rc: int) -> subprocess.CompletedProcess:
    stand_in = (
        'policyforge() { printf "%s" "$PF_OUT"; printf "%s" "$PF_ERR" >&2; return "$PF_RC"; }'
    )
    env = dict(os.environ, PF_OUT=stdout, PF_ERR=stderr, PF_RC=str(rc))
    return subprocess.run(
        [_bash(), "-lc", f"{stand_in}; {step}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


def test_the_harness_can_tell_a_pass_from_a_fail():
    """Both arms through the same harness first, so a broken harness can't
    read as every case passing or every case failing."""
    _, step = release_check.version_step("1.8.0")
    assert _run_step(step, "policyforge 1.8.0\n", "", 0).returncode == 0
    assert _run_step(step, "policyforge 1.7.0\n", "", 0).returncode != 0


@pytest.mark.parametrize(
    ("stdout", "stderr", "rc", "rule"),
    [
        # The skew the step exists for: right stdout, the stale-install note.
        ("policyforge 1.8.0\n", STALE_NOTE + "\n", 0, "wrote to stderr"),
        # Metadata of another release.
        ("policyforge 1.7.0\n", "", 0, "expected"),
        # Nothing printed.
        ("", "", 0, "expected"),
        # A release without the option: click's usage error.
        (
            "",
            "Usage: policyforge [OPTIONS] COMMAND [ARGS]...\nError: No such option: --version\n",
            2,
            "exited 2",
        ),
        # Not installed as a package (the source run by path).
        (
            "",
            "Not installed as a package, so there is no installed PolicyForge version to report.\n",
            1,
            "exited 1",
        ),
        # Near misses of the exact line.
        ("policyforge 1.8.0 \n", "", 0, "expected"),
        ("policyforge 1.8.00\n", "", 0, "expected"),
        ("Policyforge 1.8.0\n", "", 0, "expected"),
        ("policyforge 1.8.0\nsomething else\n", "", 0, "expected"),
        ("policyforge, version 1.8.0\n", "", 0, "expected"),
    ],
    ids=[
        "stale-install-note",
        "other-version",
        "empty",
        "no-such-option",
        "not-installed",
        "trailing-space",
        "longer-version",
        "capitalised",
        "extra-line",
        "click-default-format",
    ],
)
def test_the_step_fails_and_says_which_rule(stdout, stderr, rc, rule):
    _, step = release_check.version_step("1.8.0")
    proc = _run_step(step, stdout, stderr, rc)
    assert proc.returncode != 0, (proc.stdout, proc.stderr)
    assert "version: " in proc.stderr and rule in proc.stderr, proc.stderr


def test_a_failing_run_shows_what_was_on_stderr():
    """The note itself reaches the output, not only the rule's name, so the
    operator sees which two versions disagreed."""
    _, step = release_check.version_step("1.8.0")
    proc = _run_step(step, "policyforge 1.8.0\n", STALE_NOTE + "\n", 0)
    assert STALE_NOTE in proc.stderr


def test_the_passing_case_says_what_it_read_on_stdout_and_nothing_on_stderr():
    """b5 on #231: a pass leaves the version it checked in the run's output."""
    _, step = release_check.version_step("1.8.0")
    proc = _run_step(step, "policyforge 1.8.0\n", "", 0)
    assert proc.returncode == 0 and "version:" not in proc.stderr, proc.stderr
    assert proc.stdout.strip() == "version: policyforge 1.8.0", proc.stdout


# --- #232: the same step in release.py's install, before the user approves ---


def _release():
    import release

    return release


def test_the_pre_approval_install_runs_the_same_step_right_after_the_install():
    """Reused, not retyped: the chain carries `version_step`'s exact text,
    after `brew install` and before anything else reads the install."""
    script = _release()._install_script("1.8.0")
    _, step = release_check.version_step("1.8.0")
    assert script.count(step) == 1
    chain = script.split(" && ")
    install = chain.index("brew install --build-from-source local/candidate/policyforge")
    assert " && ".join(chain[install + 1 :]).startswith(step)


def test_the_pre_approval_install_keeps_its_own_source_version_read():
    """9b's ask on #232: the `__version__` read stays, last, beside the step."""
    script = _release()._install_script("1.8.0")
    last = script.split(" && ")[-1]
    assert "policyforge.__version__" in last and last.endswith('= "1.8.0"'), last
    assert script.index("policyforge --version") < script.index("policyforge.__version__")


def test_an_older_release_says_not_run_in_the_pre_approval_install_too():
    """Below 1.8.0 the chain echoes the same line `release_check` prints, and
    the echo is run here: the line has backticks, which an unquoted or
    double-quoted echo would run as a command."""
    script = _release()._install_script("1.7.0")
    # The step's command, not the flag: the not-run line names the flag.
    assert "$(policyforge --version" not in script
    (echo,) = [link for link in script.split(" && ") if "version: not run" in link]
    proc = subprocess.run(
        [_bash(), "-lc", echo],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0 and proc.stdout.strip() == release_check.VERSION_NOT_RUN, (
        proc.stdout,
        proc.stderr,
    )


def test_both_installs_print_one_not_run_line(monkeypatch):
    """One definition: the line `run_install_check` adds is the constant the
    pre-approval chain echoes."""
    _, lines = _commands_run(monkeypatch, "1.7.0")
    assert release_check.VERSION_NOT_RUN in lines
    assert release_check.VERSION_NOT_RUN in _release()._install_script("1.7.0")


@pytest.mark.parametrize("given", ["1.8.0", "v1.8.0"])
def test_a_leading_v_names_the_same_release(given):
    _, step = release_check.version_step(given)
    assert "'policyforge 1.8.0'" in step


@pytest.mark.parametrize(
    "bad",
    [
        "1.8",
        "1.8.0.1",
        "1.8.0-rc1",
        "1.8.0; rm -rf /",
        "1.8.0'",
        "",
        "v",
        " 1.8.0",
        "1." + chr(0xFF18) + ".0",  # a fullwidth 8: a digit to `int` and to `\d`
    ],
)
def test_anything_but_x_y_z_is_refused_before_it_reaches_a_shell(bad):
    # The refusal's own message: `int()` raises ValueError on most of these
    # too, and would pass a bare `raises(ValueError)` with the check gone.
    with pytest.raises(ValueError, match="not a release version"):
        release_check.version_step(bad)


@pytest.mark.parametrize(
    ("version", "has_step"),
    [
        ("1.6.0", False),
        ("1.7.0", False),
        ("1.7.9", False),
        ("1.8.0", True),
        ("1.8.1", True),
        ("1.10.0", True),  # compared as numbers: "1.10.0" < "1.8.0" as text
        ("2.0.0", True),
    ],
)
def test_the_step_starts_at_the_first_release_that_has_the_option(version, has_step):
    assert (release_check.version_step(version) is not None) is has_step
    names = [name for name, _ in release_check.install_steps(version)]
    assert ("version" in names) is has_step
    if has_step:
        assert names.index("version") == names.index("install") + 1


class _Recorded:
    returncode = 0
    stdout = ""
    stderr = ""


def _commands_run(monkeypatch, version: str) -> tuple[list[list[str]], list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Recorded()

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)
    ran, lines = release_check.run_install_check(version)
    assert ran is True
    return calls, lines


def test_the_container_runs_the_step_for_a_release_that_has_it(monkeypatch):
    calls, lines = _commands_run(monkeypatch, "1.8.0")
    script = calls[-1][-1]
    assert "policyforge --version" in script and "'policyforge 1.8.0'" in script
    assert script.index("brew install") < script.index("policyforge --version")
    assert not any("not run" in line for line in lines)


def test_an_older_release_says_the_step_was_not_run(monkeypatch):
    calls, lines = _commands_run(monkeypatch, "1.7.0")
    assert "--version" not in calls[-1][-1]
    assert any(line.startswith("version: not run") and "1.8.0" in line for line in lines)
    assert lines[-1] == "docker exit status: 0", "the verdict line must stay last"


def test_main_passes_the_version_cut(monkeypatch, capsys):
    """Wiring: the version `main` was given is the one the container checks."""
    seen = []
    formula = 'url "https://github.com/rdazzleman/policyforge/archive/refs/tags/v1.8.0.tar.gz"'
    monkeypatch.setattr(release_check, "fetch_formula", lambda url=None: formula)
    monkeypatch.setattr(release_check, "hash_of", lambda url: None)
    # Assertion 5 lists the archive the formula names; no network here.
    monkeypatch.setattr(release_check, "archive_members", lambda url: [])
    # ...and judges its files against the release tag's history (#237), which
    # this stub has none of: the test reads nothing of this checkout's git.
    monkeypatch.setattr(release_check, "content_findings", lambda files, tag: [])

    def fake_check(version, image=None):
        seen.append(version)
        return False, ["docker is not on PATH"]

    monkeypatch.setattr(release_check, "run_install_check", fake_check)
    release_check.main(["--version", "1.9.3", "--allow-skip", "install"])
    capsys.readouterr()
    assert seen == ["1.9.3"]
