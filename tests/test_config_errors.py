"""A configuration that can't be found or read is one line and exit 2 (#254).

`python -m policyforge.cli llm-check` with no config used to print a 32-line
traceback ending in a good message (80 measured it on #254). The CLI now prints
that message alone, `Error: <message>`, and exits 2, Click's usage-error code,
from one place every command shares (`cli._Group.invoke`).

The error classes keep the types callers already catch: an unparsable or empty
`config.yaml` must still let `config_or_defaults` warn once and fall back, as
it did before this issue (ag's first version broke that; measured on the
carry).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
import yaml
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.config import (
    ConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigReadError,
)

#: A `config.yaml` that is a directory, not a file.
DIRECTORY = object()
#: A config saved in Windows' default code page: "Café" is one byte, 0xE9, which
#: isn't UTF-8 (b5 on PR 270).
CP1252 = ("org:" + chr(10) + "  name: Caf" + chr(0xE9) + chr(10)).encode("cp1252")

#: Each way a config can't be read, written by hand: (name, file contents,
#: None for no file or DIRECTORY, what the one line names).
BROKEN = [
    ("missing", None, "not found"),
    ("unparsable", "llm: [\n", "Could not parse"),
    ("empty", "", "is empty"),
    ("a list", "- a\n- b\n", "should contain a mapping"),
    ("not UTF-8", CP1252, "isn't UTF-8"),
    ("a directory", DIRECTORY, "can't be read"),
]


def _config(tmp_path, contents):
    path = tmp_path / "config.yaml"
    if contents is DIRECTORY:
        path.mkdir()
    elif isinstance(contents, bytes):
        path.write_bytes(contents)
    elif contents is not None:
        path.write_text(contents, encoding="utf-8")
    return path


def _generate(tmp_path):
    synthesis = tmp_path / "topic.md"
    synthesis.write_text("- Staff must review access." + chr(10), encoding="utf-8")
    return ["generate", "--synthesis", str(synthesis), "--out", str(tmp_path / "out" / "x.md")]


@pytest.mark.parametrize(("name", "contents", "says"), BROKEN, ids=[b[0] for b in BROKEN])
@pytest.mark.parametrize("command", ["llm-check", "generate"])
def test_a_config_that_cant_be_read_is_one_line_and_exit_2(
    tmp_path, monkeypatch, name, contents, says, command
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POLICYFORGE_CONFIG", str(_config(tmp_path, contents)))
    args = ["llm-check"] if command == "llm-check" else _generate(tmp_path)
    result = CliRunner().invoke(cli_mod.cli, args)
    assert result.exit_code == 2, (result.exit_code, result.output)
    lines = result.output.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("Error: ") and says in lines[0], result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("name", "contents", "says"), [b for b in BROKEN if b[0] != "missing"], ids=lambda b: b
)
def test_config_or_defaults_still_falls_back_on_a_config_it_cant_read(
    tmp_path, monkeypatch, name, contents, says
):
    """The behaviour before #254, kept: warn once, then the default search
    paths. A `ConfigError` outside the types it catches would escape."""
    from policyforge.frameworks.registry import config_or_defaults

    monkeypatch.setenv("POLICYFORGE_CONFIG", str(_config(tmp_path, contents)))
    assert isinstance(config_or_defaults("a test"), dict)


def test_each_error_is_also_the_exception_callers_already_catch():
    assert issubclass(ConfigError, ValueError)
    assert issubclass(ConfigNotFoundError, FileNotFoundError)
    assert issubclass(ConfigParseError, yaml.YAMLError)
    assert issubclass(ConfigReadError, OSError)


def test_an_unrelated_error_still_shows_its_traceback(monkeypatch):
    """Only the config errors are caught: a bug elsewhere isn't turned into
    one quiet line."""
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"provider": "anthropic"}})

    def crash(config):
        raise RuntimeError("something else")

    monkeypatch.setattr(cli_mod, "get_provider", crash)
    with pytest.raises(RuntimeError, match="something else"):
        CliRunner().invoke(cli_mod.cli, ["llm-check"], catch_exceptions=False)


def test_the_real_cli_with_no_config_says_one_line_and_exits_2(tmp_path):
    """The issue's own measurement, through `python -m policyforge.cli` in an
    empty directory with no config and no API key."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.endswith("_API_KEY") and k != "POLICYFORGE_CONFIG"
    }
    done = subprocess.run(
        [sys.executable, "-m", "policyforge.cli", "llm-check"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    said = (done.stdout + done.stderr).strip().splitlines()
    assert done.returncode == 2, (done.returncode, said)
    assert len(said) == 1 and said[0].startswith("Error: ") and "not found" in said[0], said
