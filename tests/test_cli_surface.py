"""The command-line interface, pinned: every command, every option.

Written before `cli.py` was split into a package, and committed on its own,
so the split has something to be proven against. A refactor that moves
three thousand lines is exactly the change where a dropped option, a flag
that became a value, or a changed default hides in plain sight — the diff is
too large to read and every line of it looks like a move.

The snapshot in `fixtures/cli_surface.json` is generated from the interface
as it stood; `test_the_command_surface_is_unchanged` compares the live CLI to
it. It is meant to change only on purpose. When a command legitimately gains
or changes an option, regenerate it:

    python -m tests.test_cli_surface

and let the fixture's diff be the review of that change.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import click
from click.testing import CliRunner

FIXTURE = Path(__file__).parent / "fixtures" / "cli_surface.json"


def _default(value):
    """A default, in a form that compares stably across runs and platforms."""
    import enum

    # Click 8.4 marks "no default" with `Sentinel.UNSET` rather than None, so
    # the distinction between "defaults to None" and "has no default" is
    # preserved here instead of being flattened into one value.
    if isinstance(value, enum.Enum):
        return f"<{value.name}>"
    if callable(value):
        return "<callable>"
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (list, tuple)):
        return [_default(v) for v in value]
    return value


def _flag_default(param):
    """What the command receives when a boolean flag is left off.

    Click 8.5 stopped storing `False` as an unset boolean flag's default and
    stores `Sentinel.UNSET` instead; the command still receives `False`. The
    snapshot records that received value, so it pins the interface rather
    than which Click release is installed.
    """
    import enum

    value = param.default
    if getattr(param, "is_bool_flag", False) and isinstance(value, enum.Enum):
        return False
    return _default(value)


def _type(param) -> dict:
    kind = param.type
    described: dict = {"name": getattr(kind, "name", type(kind).__name__)}
    if isinstance(kind, click.Choice):
        described["choices"] = list(kind.choices)
        described["case_sensitive"] = kind.case_sensitive
    if isinstance(kind, click.Path):
        described["exists"] = kind.exists
        described["file_okay"] = kind.file_okay
        described["dir_okay"] = kind.dir_okay
    return described


def _param(param) -> dict:
    described = {
        "name": param.name,
        "kind": type(param).__name__,
        "opts": list(param.opts),
        "secondary_opts": list(param.secondary_opts),
        "type": _type(param),
        "required": param.required,
        "default": _flag_default(param),
        "multiple": param.multiple,
        "nargs": param.nargs,
    }
    if isinstance(param, click.Option):
        described["is_flag"] = param.is_flag
        described["help"] = param.help or ""
        described["hidden"] = param.hidden
    return described


def surface(command: click.Command, name: str = "") -> dict:
    """Everything about a command that a user or a script depends on."""
    described = {
        "name": name or command.name,
        # Cleaned because Python 3.13 strips docstring indentation at compile
        # time and 3.12 does not: the fixture was written on one and CI runs
        # the other, so raw help text differed on every command.
        "help": inspect.cleandoc(command.help or ""),
        "params": [_param(p) for p in command.params],
    }
    if isinstance(command, click.Group):
        described["invoke_without_command"] = command.invoke_without_command
        described["commands"] = {
            sub_name: surface(sub, sub_name) for sub_name, sub in sorted(command.commands.items())
        }
    return described


def _live():
    from policyforge.cli import cli

    return surface(cli, "policyforge")


def test_the_command_surface_is_unchanged():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = _live()

    missing = sorted(set(expected["commands"]) - set(actual["commands"]))
    added = sorted(set(actual["commands"]) - set(expected["commands"]))
    assert not missing, f"commands that disappeared: {missing}"
    assert not added, f"commands that appeared without the fixture being regenerated: {added}"

    for name in expected["commands"]:
        assert actual["commands"][name] == expected["commands"][name], (
            f"`policyforge {name}` changed. If that was intended, regenerate the fixture "
            f"(python -m tests.test_cli_surface) and review its diff."
        )
    assert actual == expected


def test_the_entry_point_still_resolves():
    """pyproject.toml names `policyforge.cli:cli`. A package split that moved
    `cli` would install a `policyforge` command that fails on first use."""
    from tests._pyproject import load_pyproject

    target = load_pyproject()["project"]["scripts"]["policyforge"]
    module_name, attribute = target.split(":")

    import importlib

    assert isinstance(getattr(importlib.import_module(module_name), attribute), click.Group)


def test_every_command_prints_its_help():
    """Cheapest end-to-end proof that every command still imports and wires up.

    Nearly every command imports its dependencies inside the function body,
    so a broken import survives until someone runs that one command. --help
    does not execute the body, but it does build the command, which is where
    a move most often breaks — a decorator left behind, a name no longer in
    scope.
    """
    from policyforge.cli import cli

    def walk(group, prefix):
        for name, command in sorted(group.commands.items()):
            yield [*prefix, name]
            if isinstance(command, click.Group):
                yield from walk(command, [*prefix, name])

    for path in walk(cli, []):
        result = CliRunner().invoke(cli, [*path, "--help"])
        assert result.exit_code == 0, f"policyforge {' '.join(path)} --help: {result.output}"


# ---- behaviour the surface cannot show ----------------------------------
#
# Two commands whose CLI wiring nothing else tests: their loaders are tested
# directly, so a move could drop a behaviour here and every loader test would
# stay green. Pinned because the difference between them is deliberate.


class _Summary:
    def format_report(self):
        return ["summary"]


def test_etl_fedramp_refuses_without_the_nist_catalog(tmp_path, monkeypatch):
    """FedRAMP publishes no control text of its own, so this must fail —
    and fail before fetching anything."""
    from policyforge.cli import cli

    def _no_fetch():
        raise AssertionError("must not fetch when the catalog it joins against is missing")

    monkeypatch.setattr("policyforge.ingest.fedramp.fetch_fedramp_rules", _no_fetch)
    result = CliRunner().invoke(
        cli,
        ["etl-fedramp", "--nist", str(tmp_path / "absent.json"), "--out", str(tmp_path / "o.json")],
    )
    assert result.exit_code != 0
    assert "No 800-53 catalog" in result.output
    assert "FedRAMP publishes no control text" in result.output


def test_etl_arc_ampe_carries_on_without_the_nist_catalog(tmp_path, monkeypatch):
    """Unlike FedRAMP, ARC-AMPE's catalog is usable alone: it says so and
    continues, without a crosswalk. The difference is intended."""
    from policyforge.cli import cli

    seen = {}
    monkeypatch.setattr("policyforge.ingest.arc_ampe.fetch_arc_ampe", lambda: b"xlsx")
    monkeypatch.setattr("policyforge.ingest.arc_ampe.load_workbook_from_bytes", lambda b: "wb")

    def _parse(workbook, *, version, nist_ids):
        seen["nist_ids"] = nist_ids
        seen["version"] = version
        return [], _Summary()

    monkeypatch.setattr("policyforge.ingest.arc_ampe.parse_arc_ampe", _parse)
    result = CliRunner().invoke(
        cli,
        [
            "etl-arc-ampe",
            "--nist",
            str(tmp_path / "absent.json"),
            "--out",
            str(tmp_path / "o.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "no crosswalk is recorded" in result.output
    assert seen["nist_ids"] is None
    assert seen["version"]  # falls back to the pinned version


def test_etl_arc_ampe_version_option_reaches_the_parser(tmp_path, monkeypatch):
    from policyforge.cli import cli

    seen = {}
    monkeypatch.setattr("policyforge.ingest.arc_ampe.fetch_arc_ampe", lambda: b"xlsx")
    monkeypatch.setattr("policyforge.ingest.arc_ampe.load_workbook_from_bytes", lambda b: "wb")
    monkeypatch.setattr(
        "policyforge.ingest.arc_ampe.parse_arc_ampe",
        lambda wb, *, version, nist_ids: seen.update(version=version) or ([], _Summary()),
    )
    CliRunner().invoke(
        cli,
        [
            "etl-arc-ampe",
            "--version",
            "v9.99",
            "--nist",
            str(tmp_path / "absent.json"),
            "--out",
            str(tmp_path / "o.json"),
        ],
    )
    assert seen["version"] == "v9.99"


def test_the_help_surface_is_ascii():
    """Every console can show it.

    `policyforge --help` opened with an em-dash that a Windows console on a
    legacy code page, or a piped Python process, printed as a replacement
    character; thirty-eight commands and options carried the same one. Help
    text is the first thing a new user reads, on whatever terminal they
    have, so it stays within ASCII. Runtime messages are not held to this.
    """
    import re

    from policyforge.cli import cli

    offenders = []

    def scan(where, text):
        for ch in sorted(set(re.findall(r"[^\x00-\x7F]", text or ""))):
            offenders.append(f"{where}: U+{ord(ch):04X}")

    def walk(group, prefix):
        for name, command in sorted(group.commands.items()):
            path = " ".join([*prefix, name])
            scan(f"{path} (help)", command.help)
            for param in command.params:
                scan(f"{path} --{param.name}", getattr(param, "help", None))
            if isinstance(command, click.Group):
                walk(command, [*prefix, name])

    scan("policyforge (help)", cli.help)
    walk(cli, [])

    assert offenders == [], "non-ASCII in help text: " + ", ".join(offenders)


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(_live(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {FIXTURE}")


def test_the_cli_runs_as_a_module():
    """`python -m policyforge.cli` works on the single-file module, via its
    `if __name__ == "__main__"` guard. A package runs `__main__.py` instead,
    so a split that forgot one would break this invocation with nothing else
    failing."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "policyforge.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
