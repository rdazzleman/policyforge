"""scripts/skip_gate.py: CI's skips must be exactly the committed list (#31).

The JUnit shapes are pytest's own: a skipped case holds <skipped type=
"pytest.skip" message="...">, an xfail holds <skipped type="pytest.xfail">.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import skip_gate  # noqa: E402

PRINTED = "tests.test_printed_commands_run"
FLAG = "test_every_flag_in_a_printed_command_exists"


def _junit(tmp_path: Path, cases) -> Path:
    body = []
    for classname, name, kind, message in cases:
        inner = f'<skipped type="{kind}" message="{message}"/>' if kind else ""
        body.append(f'<testcase classname="{classname}" name="{name}">{inner}</testcase>')
    path = tmp_path / "pytest.xml"
    xml = f"<testsuites><testsuite>{''.join(body)}</testsuite></testsuites>"
    path.write_text(xml, encoding="utf-8")
    return path


def _listing(tmp_path: Path) -> Path:
    path = tmp_path / "expected.txt"
    path.write_text(
        f"# a comment\n2 {PRINTED}::{FLAG} | is illustrative, not an invocation\n", encoding="utf-8"
    )
    return path


def _printed(line: int, other: int = 325):
    return [
        (
            PRINTED,
            f"{FLAG}[policyforge/zardoz/shell.py:{line}-zardoz sync]",
            "pytest.skip",
            f"policyforge/zardoz/shell.py:{line} is illustrative, not an invocation",
        ),
        (
            PRINTED,
            f"{FLAG}[policyforge/zardoz/skills.py:{other}-satisfies]",
            "pytest.skip",
            f"policyforge/zardoz/skills.py:{other} is illustrative, not an invocation",
        ),
    ]


def _run(tmp_path, cases):
    return skip_gate.main(["skip_gate.py", str(_junit(tmp_path, cases)), str(_listing(tmp_path))])


def test_exactly_the_listed_skips_pass(tmp_path):
    assert _run(tmp_path, [*_printed(552), ("tests.test_a", "test_runs", None, "")]) == 0


def test_one_added_skip_fails_and_is_named(tmp_path, capsys):
    new = ("tests.test_litellm_provider", "test_retries", "pytest.skip", "no litellm")
    assert _run(tmp_path, [*_printed(552), new]) == 1
    assert "NOT EXPECTED  +1  tests.test_litellm_provider::test_retries" in capsys.readouterr().out


def test_one_more_of_a_listed_kind_fails(tmp_path, capsys):
    third = (
        PRINTED,
        f"{FLAG}[policyforge/cli/x.py:9-x]",
        "pytest.skip",
        "policyforge/cli/x.py:9 is illustrative, not an invocation",
    )
    assert _run(tmp_path, [*_printed(552), third]) == 1
    assert "NOT EXPECTED  +1" in capsys.readouterr().out


def test_a_listed_skip_that_did_not_happen_fails(tmp_path, capsys):
    assert _run(tmp_path, _printed(552)[:1]) == 1
    assert "EXPECTED, DIDN'T SKIP  -1" in capsys.readouterr().out


def test_moving_the_illustrated_line_does_not_fail(tmp_path):
    """The key drops `file.py:NNN`, so an edit above line 552 in shell.py
    (which renumbers the id and the reason) changes nothing."""
    assert _run(tmp_path, _printed(600, other=331)) == 0


def test_an_xfail_is_not_a_skip(tmp_path):
    xfail = ("tests.test_b", "test_known_bug", "pytest.xfail", "reason")
    assert _run(tmp_path, [*_printed(552), xfail]) == 0


def test_the_committed_list_parses_and_names_real_tests():
    want = skip_gate.expected(skip_gate.DEFAULT_LIST)
    assert want, "the committed list is empty"
    for key in want:
        module, rest = key.split("::", 1)
        assert (ROOT / (module.replace(".", "/") + ".py")).is_file(), key
        assert " | " in rest and rest.split(" | ", 1)[1], key
