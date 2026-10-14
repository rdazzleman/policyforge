"""`policyforge --version` prints the installed package's version (#219).

The expected value comes from the other side of the check: the installed
distribution's metadata (`importlib.metadata`), which is what pip or brew put
on the machine. One test replaces that metadata with a version no source file
carries, so a string typed into the CLI, even one equal to today's version,
can't pass.
"""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

import policyforge
from policyforge.cli import cli


def _installed(monkeypatch, value):
    """Make the installed metadata report `value` (or raise it)."""

    def fake(name):
        assert name == "policyforge", name
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(importlib.metadata, "version", fake)


def test_prints_the_installed_metadata_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout == f"policyforge {importlib.metadata.version('policyforge')}\n"


def test_the_version_follows_the_metadata_not_a_typed_string(monkeypatch):
    _installed(monkeypatch, "9.9.9")
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "policyforge 9.9.9\n"


def test_running_code_that_declares_another_version_is_named_on_stderr(monkeypatch):
    """A stale editable install: the metadata is old, the code is new. stdout
    keeps the installed version unchanged; stderr names both."""
    _installed(monkeypatch, "9.9.9")
    result = CliRunner().invoke(cli, ["--version"])
    assert "9.9.9" in result.stderr and policyforge.__version__ in result.stderr
    assert "Reinstall" in result.stderr


def test_no_note_when_the_installed_version_and_the_code_agree(monkeypatch):
    _installed(monkeypatch, policyforge.__version__)
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == f"policyforge {policyforge.__version__}\n"
    assert result.stderr == ""


def test_not_installed_is_said_and_prints_no_version(monkeypatch):
    _installed(monkeypatch, importlib.metadata.PackageNotFoundError("policyforge"))
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception  # no traceback
    assert result.stdout == ""
    assert "Not installed as a package" in result.stderr


@pytest.mark.parametrize("args", [["--version", "generate"], ["generate", "--help"]])
def test_version_is_eager_and_leaves_subcommands_alone(args):
    """`--version` answers before a subcommand runs; a subcommand's own
    options are untouched by it."""
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    if args[0] == "--version":
        assert result.stdout.startswith("policyforge ")
    else:
        assert "policyforge " not in result.stdout.splitlines()[0]


def test_the_command_the_issue_ran():
    """`python -m policyforge.cli --version`, as measured on the issue, in a
    child process: the user's path, not the test runner's."""
    result = subprocess.run(
        [sys.executable, "-m", "policyforge.cli", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"policyforge {importlib.metadata.version('policyforge')}\n"
