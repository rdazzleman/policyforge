"""The public assembler stands without the licensed parsers (#32, part 2).

`ingest/levelled.py` is what `generate-parser` output builds on, so it has to
work on an install that has no licensed parser at all. And the licensed
library's own counts are the parser's data (80's ruling, #32 comment 562),
so the public `summarize` carries none.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from policyforge.ingest import levelled

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from licensed_modules import LICENSED_MODULES as PRIVATE  # (#48: one list)


def test_the_assembler_builds_with_every_licensed_parser_unimportable():
    """In a fresh interpreter, with every licensed module blocked from import: the
    public assembler still builds a catalog from records."""
    blocked = ", ".join(repr(f"policyforge.ingest.{m}") for m in PRIVATE)
    code = f"""
import sys
for name in [{blocked}]:
    sys.modules[name] = None  # any import of it now fails
from policyforge.ingest.levelled import Record, build_controls, summarize
controls = build_controls([Record(reference="01.a Invented", level="Level 1",
                                  statement="Invented.", mapping="ACME 9000:2020 4.1a")])
assert [c.control_id for c in controls] == ["01.a"], controls
print(summarize(controls).requirements)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_the_public_summary_holds_no_library_counts():
    """Without counts, no "partial export" claim can be made; with them, it is."""
    thin = levelled.build_controls(
        [levelled.Record(reference="01.a Invented", level="Level 1", statement="Invented.")]
    )
    assert not any("in a full library" in w for w in levelled.summarize(thin).warnings)
    counted = levelled.summarize(thin, library_counts={"references": 100})
    assert any("1 references parsed, against 100 in a full library" in w for w in counted.warnings)


def test_the_drafting_prompt_builds_on_the_public_assembler():
    from policyforge.ingest import parser_codegen

    rules = parser_codegen._HITRUST_RULES
    assert "from .levelled import Record, build_controls" in rules
    assert ".hitrust" not in rules


def test_the_public_prompt_and_assembler_keep_the_id_shapes():
    """80's ruling on #32 (comment 665): control titles are content and left
    this tree; the id SHAPES are format and stay. The check that the titles
    and the library's counts are gone needs the list of what they were, which
    is itself content, so it runs in the licensed-framework plugin's suite
    against this tree (#32, part 3)."""
    import inspect

    from policyforge.ingest import parser_codegen

    for where, text in {
        "the drafting prompt": parser_codegen._HITRUST_BRIEFING + parser_codegen._RECORD_SCHEMA,
        "levelled.py": inspect.getsource(levelled),
    }.items():
        assert "NN.a <control title>" in text, f"{where} lost the id shape"
