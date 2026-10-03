"""ingest/parser_gate.py — S-02, model-written code checked before it runs.

The sample export is untrusted input to a prompt whose output is code, and
that code runs over the licensed file it parses. These tests are mostly
refusals, like the rest of the boundary tests, and every refusal is paired
in spirit with the ordinary parser it must not also refuse: a gate that
rejected `str.replace` would be switched off within a week.

The trial runs spawn a real child interpreter, so they are slower than the
rest of the suite. None of them touches the network — the one that tries is
refused before a packet is built, and aims at TEST-NET besides.
"""

from __future__ import annotations

import pytest

from policyforge.ingest.parser_gate import check_generated_parser, trial_run
from tests.licensed_text import unmask as _m

#: A parser of the shape the codegen prompt asks for, doing nothing unusual.
ORDINARY = _m("""from __future__ import annotations

import csv
import re
from pathlib import Path

from .schema import Control


def load_@GOVRAMP_EXPORT@(export_path: Path) -> list[Control]:
    \"\"\"Reads a two-column CSV export.\"\"\"
    with open(export_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    controls = []
    for row in rows:
        title = re.sub(r"\\s+", " ", row["title"]).strip().replace("  ", " ")
        controls.append(
            Control(
                control_id=row["id"],
                title=title,
                framework="GovRAMP",
                framework_version="rev5",
                source_path=str(export_path),
            )
        )
    return controls
""")


def _reasons(source: str) -> list[str]:
    return [v.reason for v in check_generated_parser(source, framework_slug="govramp")]


def _with_body(body: str, imports: str = "") -> str:
    return (
        "from __future__ import annotations\n"
        f"{imports}\n\n"
        "def load_govramp_export(export_path):\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
    )


# --------------------------------------------------------------------------
# Static: what an ordinary parser is allowed
# --------------------------------------------------------------------------


def test_an_ordinary_parser_passes():
    assert _reasons(ORDINARY) == []


def test_the_hitrust_shape_passes_too():
    """Built on the public assembler, as the prompt now asks (#32)."""
    source = _with_body(
        "return build_controls([Record(reference='01.a')], source_path=str(export_path))",
        imports="from .levelled import Record, build_controls",
    )
    assert check_generated_parser(source, framework_slug="govramp") == []


def test_a_drafted_parser_may_not_build_on_the_private_hitrust_module():
    """`hitrust` moves to the private plugin (#32), so a drafted parser that
    imported it would break on every public install."""
    source = _with_body(
        "return build_controls([Record(reference='01.a')], source_path=str(export_path))",
        imports=_m("from .@HITRUST@ import Record, build_controls"),
    )
    reasons = _reasons(source)
    assert any("sibling module 'hitrust'" in r for r in reasons), reasons


def test_reading_a_workbook_passes():
    source = _with_body(
        "book = openpyxl.load_workbook(export_path, read_only=True)\n"
        "return [row for row in book.active.iter_rows(values_only=True)]",
        imports="import openpyxl",
    )
    assert _reasons(source) == []


def test_ordinary_string_and_dict_methods_are_not_mistaken_for_file_operations():
    """`replace` and `copy` are the two names a careless denylist gets wrong."""
    source = _with_body("row = {'a': 'x y'}.copy()\nreturn [row['a'].replace(' ', '')]")
    assert _reasons(source) == []


def test_open_for_reading_with_an_explicit_mode_passes():
    source = _with_body("with open(export_path, 'r', encoding='utf-8') as f:\n    return f.read()")
    assert _reasons(source) == []


# --------------------------------------------------------------------------
# Static: what it is not
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "imports",
    [
        "import requests",
        "import urllib.request",
        "from urllib import request",
        "import socket",
        "import subprocess",
        "import os",
        "import shutil",
        "import ctypes",
        "import pandas",
        "import importlib",
    ],
)
def test_an_import_outside_the_allowlist_is_refused(imports):
    reasons = _reasons(_with_body("return []", imports=imports))
    assert any("has no use for" in r for r in reasons), reasons


def test_an_import_hidden_inside_the_function_is_refused_too():
    assert any("socket" in r for r in _reasons(_with_body("import socket\nreturn []")))


def test_a_sibling_module_not_offered_is_refused():
    reasons = _reasons(_with_body("return []", imports=_m("from .@BYOC_LOADER@ import load")))
    assert any("sibling module 'byoc_loader'" in r for r in reasons)


def test_a_relative_import_reaching_outside_ingest_is_refused():
    reasons = _reasons(_with_body("return []", imports="from ..llm import base"))
    assert any("outside ingest/" in r for r in reasons)


@pytest.mark.parametrize("call", ["eval('1')", "exec('x = 1')", "__import__('os')"])
def test_turning_a_string_into_code_is_refused(call):
    assert _reasons(_with_body(f"{call}\nreturn []"))


def test_getattr_is_refused_because_it_walks_past_the_attribute_checks():
    source = _with_body(
        "from pathlib import Path\ngetattr(Path(export_path), 'write_' + 'text')('x')\nreturn []"
    )
    assert any("getattr" in r for r in _reasons(source))


def test_reaching_through_dunder_attributes_is_refused():
    reasons = _reasons(_with_body("return ().__class__.__bases__[0].__subclasses__()"))
    assert any("__class__" in r for r in reasons)
    assert any("__subclasses__" in r for r in reasons)


@pytest.mark.parametrize(
    "body",
    [
        "open(export_path, 'w').write('x')",
        "open(export_path, mode='a')",
        "open(export_path, 'r+')",
        "from pathlib import Path\nPath(export_path).open('wb')",
    ],
)
def test_opening_anything_for_writing_is_refused(body):
    reasons = _reasons(_with_body(f"{body}\nreturn []"))
    assert any("writ" in r for r in reasons), reasons


def test_a_mode_the_check_cannot_read_is_refused():
    reasons = _reasons(_with_body("mode = 'w'\nopen(export_path, mode)\nreturn []"))
    assert any("not a literal" in r for r in reasons)


@pytest.mark.parametrize(
    "body",
    [
        "from pathlib import Path\nPath('leak.txt').write_text('x')",
        "book = openpyxl.Workbook()\nbook.save('copy.xlsx')",
        "import json\njson.dump({}, None)",
    ],
)
def test_write_helpers_are_refused_by_name(body):
    reasons = _reasons(_with_body(f"{body}\nreturn []", imports="import openpyxl"))
    assert any("writes or runs something" in r for r in reasons), reasons


def test_a_parser_without_the_expected_function_is_refused():
    reasons = _reasons("def load_something_else(p):\n    return []\n")
    assert any("load_govramp_export" in r for r in reasons)


def test_code_that_is_not_python_is_refused_with_its_line():
    violations = check_generated_parser("def load_govramp_export(:\n", framework_slug="govramp")
    assert violations and "not valid Python" in violations[0].reason
    assert violations[0].line == 1


def test_every_violation_is_reported_with_a_line_number():
    source = _with_body("import socket\neval('1')\nreturn []")
    violations = check_generated_parser(source, framework_slug="govramp")
    # `_with_body` puts the function on line 4, so its body starts on 5.
    assert {v.line for v in violations} >= {5, 6}
    assert all(str(v).startswith("line ") for v in violations)


# --------------------------------------------------------------------------
# Dynamic: the trial run
# --------------------------------------------------------------------------


def _write(tmp_path, source: str, sample: str = "id,title\nAC-1,Policy\nAC-2,Accounts\n"):
    parser = tmp_path / "govramp_loader.py"
    parser.write_text(source, encoding="utf-8")
    export = tmp_path / "export.csv"
    export.write_text(sample, encoding="utf-8")
    return parser, export


def test_an_ordinary_parser_runs_and_reports_how_many_records_it_found(tmp_path):
    parser, export = _write(tmp_path, ORDINARY)
    result = trial_run(parser, export, framework_slug="govramp")

    assert result.ok, result.detail
    assert result.records == 2
    assert result.refused == ()


def test_a_parser_that_opens_a_socket_is_refused_before_it_connects(tmp_path):
    parser, export = _write(
        tmp_path,
        "def load_govramp_export(export_path):\n"
        "    import socket\n"
        "    socket.create_connection(('192.0.2.1', 80), timeout=1)\n"
        "    return []\n",
    )
    result = trial_run(parser, export, framework_slug="govramp")

    assert not result.ok
    assert any(r.startswith("socket.") for r in result.refused), result


def test_a_parser_that_writes_is_refused_and_nothing_is_written(tmp_path):
    parser, export = _write(
        tmp_path,
        "from pathlib import Path\n"
        "def load_govramp_export(export_path):\n"
        "    Path(export_path).with_suffix('.leak').write_text('licensed content')\n"
        "    return []\n",
    )
    result = trial_run(parser, export, framework_slug="govramp")

    assert not result.ok
    assert any("open for writing" in r for r in result.refused)
    assert not (tmp_path / "export.leak").exists()


def test_an_attempt_the_parser_swallows_still_fails_the_trial(tmp_path):
    """Catching the refusal and returning records anyway is still trying."""
    parser, export = _write(
        tmp_path,
        "import subprocess\n"
        "def load_govramp_export(export_path):\n"
        "    try:\n"
        "        subprocess.run(['whoami'])\n"
        "    except Exception:\n"
        "        pass\n"
        "    return [1, 2, 3]\n",
    )
    result = trial_run(parser, export, framework_slug="govramp")

    assert not result.ok
    assert any(r.startswith("subprocess.") for r in result.refused)


def test_a_parser_that_crashes_says_why(tmp_path):
    parser, export = _write(
        tmp_path, "def load_govramp_export(export_path):\n    raise ValueError('no title column')\n"
    )
    result = trial_run(parser, export, framework_slug="govramp")

    assert not result.ok
    assert "no title column" in result.detail


def test_a_parser_that_never_returns_is_stopped(tmp_path):
    parser, export = _write(
        tmp_path, "def load_govramp_export(export_path):\n    while True:\n        pass\n"
    )
    result = trial_run(parser, export, framework_slug="govramp", timeout=3)

    assert not result.ok
    assert "did not finish" in result.detail
