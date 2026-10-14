"""Help text and docstrings say what the code does (#388).

Four sentences promised features the code didn't have: Zardoz drafting an
`edit-topic` command, entailment checking on answers, a `/shadow` command
that could only ever say "none yet", and the SSP using decided parameter
values. 80's ruling: correct the claims, don't build the features (except
entailment, where the judge was built and only never handed to the shell,
so the note was made true; that half is tested in
`test_one_boundary_decision.py`).

Each test pins a corrected sentence against the code it describes, so a
change to either side turns one red: build the feature and the sentence
must change with it, or bring the old sentence back and it fails.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from click.testing import CliRunner

import policyforge.zardoz as zardoz_pkg

ZARDOZ = Path(zardoz_pkg.__file__).parent


def _calls_anywhere(names: set[str], paths) -> list[str]:
    """Every use of one of `names` as a called function or imported name, in
    the parsed source of `paths`: a mention in a docstring is not a use."""
    found = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in names:
                    found.append(f"{path.name}: {name}()")
            elif isinstance(node, ast.ImportFrom):
                found += [f"{path.name}: import {a.name}" for a in node.names if a.name in names]
    return found


# ---- Zardoz drafting an edit-topic command ---------------------------------


def test_zardoz_help_does_not_promise_to_draft_a_command():
    from policyforge.cli import cli

    result = CliRunner().invoke(cli, ["zardoz", "--help"])

    assert result.exit_code == 0, result.output
    assert "draft" not in result.output.lower()
    assert "edit-topic" in result.output, "it still says where a change is made"


def test_no_zardoz_module_says_it_drafts_an_edit_topic_command():
    """Nothing in the package writes an `edit-topic` command for anyone, so
    no module may say it does."""
    drafting = [
        path.name
        for path in sorted(ZARDOZ.glob("*.py"))
        if "draft a `policyforge edit-topic`" in path.read_text(encoding="utf-8")
        or "*draft* an edit" in path.read_text(encoding="utf-8")
    ]
    assert drafting == []


# ---- /shadow ---------------------------------------------------------------


def test_there_is_no_shadow_command_and_help_does_not_list_one():
    from policyforge.zardoz.shell import COMMANDS, ShellState, dispatch

    assert "shadow" not in COMMANDS
    assert "/shadow" not in dispatch("/help", ShellState())


def test_the_shadow_module_says_it_is_not_wired_and_it_is_not():
    """`shadow.py` said a candidate runs on every real question. Nothing calls
    it; its docstring now says so, and this keeps the two in step."""
    import policyforge.zardoz.shadow as shadow

    callers = _calls_anywhere(
        {"run_shadow", "shadow_retrieval", "configured_shadows"},
        [p for p in ZARDOZ.glob("*.py") if p.name != "shadow.py"],
    )
    assert callers == [], "wired now: update shadow.py's docstring and this test"
    assert "**Built, not wired.**" in shadow.__doc__


# ---- the SSP and the parameter ledger --------------------------------------


def test_the_ledger_does_not_say_the_ssp_uses_it_and_the_ssp_does_not():
    import policyforge.parameters.ledger as ledger
    from policyforge.cli.documents import ssp_cmd

    source = inspect.getsource(ssp_cmd.callback)
    assert "parameters.ledger" not in source and "apply_to_controls" not in source, (
        "ssp reads the ledger now: update the ledger's docstring and this test"
    )
    assert "and so does the SSP" not in ledger.__doc__
    assert "Not the SSP yet" in ledger.__doc__
