"""Every block-structure reader, over the generated corpus, equals the
committed baseline (#376).

The expectation is `tests/fixtures/deontic_corpus/baseline.json`, recorded
by `scripts/deontic_baseline.py --write` and never recomputed here: a test
that computes its own expectation agrees with itself by construction (1d on
#376). A change to what `deontic` reads is either a regression or a ruling,
and the script's printout of every changed row is how a PR says which.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "deontic_baseline", ROOT / "scripts" / "deontic_baseline.py"
)
baseline_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline_script)


def _committed() -> dict:
    return json.loads(baseline_script.BASELINE.read_text(encoding="utf-8"))


def test_every_reader_matches_the_committed_baseline():
    changed = baseline_script.changes(_committed()["docs"], baseline_script.record())
    assert not changed, (
        f"{len(changed)} changed row(s). If intended, regenerate with "
        "`python scripts/deontic_baseline.py --write` and put its printout in the PR:\n"
        + "\n".join(changed[:40])
    )


def test_the_baseline_covers_the_whole_corpus_and_names_its_commit():
    committed = _committed()
    on_disk = {
        p.relative_to(baseline_script.CORPUS / "documents").as_posix()
        for p in baseline_script.documents()
    }
    assert set(committed["docs"]) == on_disk
    assert len(on_disk) == 128
    assert re.fullmatch(r"[0-9a-f]{40}", committed["recorded_at"]), committed["recorded_at"]
    assert committed["readers"] == list(baseline_script.READERS)


def test_the_comparison_can_fail(monkeypatch):
    """A change to one decision is reported, row by row: the same arm the
    harness used on #376 (citation-only lines read as prose)."""
    from policyforge.content import deontic

    monkeypatch.setattr(deontic, "_only_citations", lambda line: False)
    changed = baseline_script.changes(_committed()["docs"], baseline_script.record())
    assert changed, "breaking citation attachment changed nothing: the comparison cannot fail"
    assert any(" analyze line " in line for line in changed)


@pytest.mark.parametrize("sign", ["-", "+"])
def test_a_changed_row_names_its_document_reader_and_line(sign):
    old = {"a.md": {reader: [] for reader in baseline_script.READERS}}
    new = {"a.md": {reader: [] for reader in baseline_script.READERS}}
    row = [3, "The owner must act.", "obligation", False, [], False]
    (old if sign == "-" else new)["a.md"]["analyze"] = [row]
    (line,) = baseline_script.changes(old, new)
    assert line.startswith(f"{sign} a.md analyze line 3: ")
