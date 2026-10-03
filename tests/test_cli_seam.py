"""Every command reaches config and the provider through one seam.

The CLI tests substitute a fake config and a fake provider by patching
`policyforge.cli.load_config` and `policyforge.cli.get_provider` — fifty-eight
sites. That works only if commands look those names up through
`policyforge.cli` when they run. When cli.py was one module, that was
automatic. Split into a package, a command module that wrote

    from policyforge.llm.base import get_provider

would bind the real function into its own globals, the patch would stop
reaching it, and a test that believed it was talking to a fake would build a
real provider instead — against a real endpoint, if a key were present.

`policyforge/cli/_common.py` defines the seam and every command module
imports from it. This test makes that a rule rather than a habit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import policyforge.cli

#: (module the real function lives in, name) — importing either directly into
#: a command module bypasses the seam.
FORBIDDEN = {
    ("policyforge.config", "load_config"),
    ("policyforge.llm.base", "get_provider"),
}

#: The two files allowed to hold the real functions: the package root, which
#: is what tests patch, and the seam itself.
ALLOWED = {"__init__.py", "_common.py"}


def bypasses(source: str) -> list[str]:
    """Imports in `source` that would bind a seam name past the seam.

    Walks the whole tree, so an import inside a function body — which is
    how nearly every command here imports — is caught as readily as one at
    the top of the file.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if (node.module, alias.name) in FORBIDDEN:
                    found.append(f"line {node.lineno}: from {node.module} import {alias.name}")
    return found


def test_no_command_module_imports_config_or_the_provider_directly():
    package = Path(policyforge.cli.__file__).parent
    offenders = {}
    for path in sorted(package.glob("*.py")):
        if path.name in ALLOWED:
            continue
        hits = bypasses(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.name] = hits
    assert not offenders, (
        "These command modules bind config or the provider past the seam, so a test "
        "patching policyforge.cli.load_config / get_provider will not reach them. "
        "Import them from policyforge.cli._common instead:\n"
        + "\n".join(f"  {name}: {hits}" for name, hits in offenders.items())
    )


def test_the_detector_catches_a_planted_bypass():
    """A guard that cannot fail proves nothing."""
    planted = (
        "def command():\n"
        "    from policyforge.llm.base import get_provider\n"
        "    return get_provider({})\n"
    )
    assert bypasses(planted) == ["line 2: from policyforge.llm.base import get_provider"]
    assert bypasses("from policyforge.cli._common import get_provider\n") == []


def test_patching_the_package_reaches_a_command_in_a_submodule(monkeypatch):
    """The behaviour the rule protects, checked directly."""
    from click.testing import CliRunner

    from policyforge.llm.base import LLMResponse

    class Fake:
        def generate(self, **kwargs):
            return LLMResponse(text="ok", model="fake")

        def check(self):
            return True

    monkeypatch.setattr(
        policyforge.cli, "load_config", lambda: {"llm": {"provider": "anthropic", "model": "m"}}
    )
    monkeypatch.setattr(policyforge.cli, "get_provider", lambda config: Fake())

    # llm-check lives in policyforge.cli.llm, not in the package root.
    assert policyforge.cli.cli.commands["llm-check"].callback.__module__ == "policyforge.cli.llm"
    result = CliRunner().invoke(policyforge.cli.cli, ["llm-check"])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_a_missing_config_is_an_empty_config(monkeypatch):
    from policyforge.cli._common import load_config_or_empty

    def _missing():
        raise FileNotFoundError("config/config.yaml")

    monkeypatch.setattr(policyforge.cli, "load_config", _missing)
    assert load_config_or_empty() == {}


def test_a_broken_config_is_not_silently_an_empty_one(monkeypatch):
    """Only a *missing* file is tolerated. Running with no settings when the
    operator wrote some is how a boundary gets skipped."""
    import pytest

    from policyforge.cli._common import load_config_or_empty

    def _broken():
        raise ValueError("config/config.yaml: mapping values are not allowed here")

    monkeypatch.setattr(policyforge.cli, "load_config", _broken)
    with pytest.raises(ValueError):
        load_config_or_empty()


def test_catalogs_load_in_the_order_given(tmp_path):
    import json

    from policyforge.cli._common import load_catalogs

    def catalog(name, *ids):
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps(
                [
                    {"control_id": i, "title": i, "framework": name, "framework_version": "1"}
                    for i in ids
                ]
            ),
            encoding="utf-8",
        )
        return path

    first, second = catalog("a", "AC-1", "AC-2"), catalog("b", "IA-5")
    assert [c.control_id for c in load_catalogs([first, second])] == ["AC-1", "AC-2", "IA-5"]
    assert [c.control_id for c in load_catalogs([second, first])] == ["IA-5", "AC-1", "AC-2"]
    assert load_catalogs([]) == []
