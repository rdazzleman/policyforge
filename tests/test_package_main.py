"""Tests for executing policyforge as a module via python -m policyforge (#427)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _src_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "src")


def test_python_m_policyforge_version_matches_console_script():
    """sys.executable -m policyforge --version, with PYTHONPATH set to the
    checkout's src, exits 0 and prints the same version string as the
    console script."""
    src = _src_dir()
    env = dict(os.environ, PYTHONPATH=src)

    proc_module = subprocess.run(
        [sys.executable, "-m", "policyforge", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert proc_module.returncode == 0, proc_module.stderr

    console_script = shutil.which("policyforge")
    if not console_script:
        candidate = Path(sys.executable).parent / (
            "policyforge.exe" if sys.platform == "win32" else "policyforge"
        )
        if candidate.is_file():
            console_script = str(candidate)

    if console_script:
        proc_script = subprocess.run(
            [console_script, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        assert proc_script.returncode == 0, proc_script.stderr
        expected_version = proc_script.stdout.strip()
    else:
        proc_script = subprocess.run(
            [
                sys.executable,
                "-c",
                "from policyforge.cli import cli; cli(prog_name='policyforge')",
                "--version",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        assert proc_script.returncode == 0, proc_script.stderr
        expected_version = proc_script.stdout.strip()

    assert proc_module.stdout.strip() == expected_version
    assert proc_module.stdout.strip().startswith("policyforge ")


def test_python_m_policyforge_help_reports_prog_name():
    """python -m policyforge --help's first line reads 'Usage: policyforge'."""
    src = _src_dir()
    env = dict(os.environ, PYTHONPATH=src)

    proc = subprocess.run(
        [sys.executable, "-m", "policyforge", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    first_line = proc.stdout.splitlines()[0]
    assert first_line.startswith("Usage: policyforge"), f"Unexpected first line: {first_line!r}"


def test_python_m_policyforge_error_lines_name_policyforge():
    """80's ruling on 427 covers error lines as well as usage: an unknown
    command run through `-m` says `policyforge`, not `__main__.py` or
    `python -m policyforge`, and exits 2 as the console script does."""
    proc = subprocess.run(
        [sys.executable, "-m", "policyforge", "no-such-command"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(os.environ, PYTHONPATH=_src_dir()),
        check=False,
    )
    assert proc.returncode == 2, proc.stderr
    assert proc.stderr.startswith("Usage: policyforge "), proc.stderr
    assert "No such command 'no-such-command'" in proc.stderr
