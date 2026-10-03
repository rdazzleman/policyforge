"""A shorthand part of a merged tag inherits the preceding part's framework (#333).

`[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1 Action 2]` is an
ordinary shorthand. Split on `|` alone, only the first part named the
Playbook, so the Playbook gate never read the sentence and the strength rule
read it instead -- the inversion #300 fixed: in b5's stored glm Governance
Standard (run301b, 17 Playbook sentences, every tag in this form), the
correct "NIST suggests ..." wording drew 16 strength warnings and a seeded
"Acme Health must ..." passed clean.

80's ruling: INHERIT from the nearest preceding part in the same tag, with
resolution required, through ONE function (`content/tags.tag_parts`) used by
the gate and by `satisfies` alike.

**The fixture** `fixtures/playbook_glm_governance_shorthand.md` is that
Standard, verbatim from b5's run301b output (glm-5.3-flash via OpenRouter).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.content.deontic import analyze, playbook_obligations
from policyforge.content.tags import TagPart, tag_parts

FIXTURE = Path(__file__).parent / "fixtures" / "playbook_glm_governance_shorthand.md"
FRAMEWORKS = Path(__file__).resolve().parent.parent / "data" / "frameworks"
PLAYBOOK = "NIST AI RMF Playbook"


# -- the function ---------------------------------------------------------------------


def test_a_shorthand_part_inherits_the_nearest_preceding_framework():
    names = [PLAYBOOK, "NIST 800-53"]
    two = tag_parts("[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1 Action 2]", names)
    assert two == [
        TagPart(PLAYBOOK, "Govern 1.1 Action 1"),
        TagPart(PLAYBOOK, "Govern 1.1 Action 2", inherited=True),
    ]
    # Three and four parts: each shorthand takes the NEAREST explicit framework.
    mixed = tag_parts(
        "[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1 Action 2 | "
        "NIST 800-53 AC-2 | AC-2(3)]",
        names,
    )
    assert [(p.framework, p.inherited) for p in mixed] == [
        (PLAYBOOK, False),
        (PLAYBOOK, True),
        ("NIST 800-53", False),
        ("NIST 800-53", True),
    ]
    assert mixed[3].citation == "NIST 800-53 AC-2(3)"


def test_a_first_part_with_nothing_to_inherit_names_no_framework():
    (only,) = tag_parts("[Govern 1.1 Action 2]", [PLAYBOOK])
    assert only == TagPart("", "Govern 1.1 Action 2")


def test_escaped_pipes_and_spacing_are_read_as_written():
    """Inside a markdown table the separator is written `\\|`."""
    parts = tag_parts(
        "[NIST AI RMF Playbook  Govern 1.1 Action 1 \\| Govern 1.1   Action 2]", [PLAYBOOK]
    )
    assert [p.citation for p in parts] == [
        "NIST AI RMF Playbook Govern 1.1 Action 1",
        "NIST AI RMF Playbook Govern 1.1 Action 2",
    ]


def test_an_abbreviation_the_caller_resolves_names_its_own_framework():
    """`ARC` is not a declared name, but `satisfies` resolves it to ARC-AMPE,
    so `ARC PE-1` must not inherit 800-53 (a regression this caught)."""
    parts = tag_parts("[NIST 800-53 AC-2 | ARC PE-1 | PE-2]", ["NIST 800-53"], {"ARC"}.__contains__)
    assert [(p.framework, p.inherited) for p in parts] == [
        ("NIST 800-53", False),
        ("ARC", False),
        ("ARC", True),
    ]


def test_the_gate_and_satisfies_both_read_parts_through_tag_parts(monkeypatch):
    """One instrument for one question (80's ruling). Replace the function
    and both readers change -- which a second copy of the splitting in
    either module could not do."""
    import policyforge.content.tags as tags
    from policyforge.content import deontic
    from policyforge.topics.satisfies import parse_citations

    calls = []
    real = tags.tag_parts

    def spy(tag, frameworks, names_framework=None):
        calls.append(tag)
        return real(tag, frameworks, names_framework)

    monkeypatch.setattr(tags, "tag_parts", spy)
    tag = "[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1 Action 2]"
    deontic._parts(tag)
    parse_citations(f"NIST suggests X. {tag}\n", [PLAYBOOK])
    assert calls == [tag, tag]


# -- the gate, on b5's real Standard ------------------------------------------------------


def test_the_gate_now_reads_all_17_real_shorthand_sentences_and_they_pass():
    text = FIXTURE.read_text(encoding="utf-8")
    cites = [s for s in analyze(text) if s.cites_the_playbook]
    assert len(cites) == 17
    assert all(s.cites_only_the_playbook for s in cites), "a shorthand part was not attributed"
    assert playbook_obligations(text) == []


def _check(body: str):
    from policyforge.content.check import check_tree

    root = Path(tempfile.mkdtemp())
    (root / "standards").mkdir()
    (root / "standards" / "ai-governance.md").write_text(body, encoding="utf-8")
    messages = [getattr(f, "message", str(f)) for f in check_tree(root).findings]
    playbook = [m for m in messages if "voluntary" in m and "Playbook" in m]
    strength = [m for m in messages if "permission" in m or "statement of fact" in m]
    return playbook, strength


def test_the_inversion_is_gone_the_right_wording_is_quiet_and_the_wrong_one_errors():
    """Before: 16 strength warnings on the correct wording, and a seeded
    obligation passed clean. Measured on the train at 1bf6345."""
    text = FIXTURE.read_text(encoding="utf-8")
    assert _check(text) == ([], [])

    first = next(s for s in analyze(text) if s.cites_the_playbook).text
    line = next(ln for ln in text.splitlines() if first[:60] in ln)
    seeded = text.replace(
        line, line.replace(first, "Acme Health must maintain awareness of AI law.", 1), 1
    )
    assert seeded != text
    playbook, strength = _check(seeded)
    assert len(playbook) == 1 and "Acme Health must" in playbook[0]
    assert strength == []


@pytest.mark.parametrize(
    "tag, playbook_only",
    [
        ("[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1 Action 2]", True),
        ("[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1]", True),  # a subcategory resolves
        # Inherited but resolving nowhere: STILL the Playbook's (80 on #340,
        # reversing #337's rule), so an invented id cannot make the sentence
        # mixed and escape the gate; `check` reports the part itself.
        ("[NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 9.9 Action 1]", True),
        # A part naming a framework on disk is that framework's, not inherited.
        ("[NIST AI RMF Playbook Govern 1.1 Action 1 | NIST 800-53 AC-2]", False),
        ("[NIST AI RMF Playbook Govern 1.1 Action 1 | HIPAA 164.308(a)(1)(i)]", False),
        # The other framework first: the shorthand inherits IT, not the Playbook.
        ("[NIST 800-53 AC-2 | Govern 1.1 Action 2]", False),
        # Nothing to inherit from.
        ("[Govern 1.1 Action 2]", False),
    ],
)
def test_an_inherited_part_is_the_playbooks_resolving_or_not(tag, playbook_only):
    (statement,) = analyze(f"NIST suggests reviewing the inventory. {tag}\n")
    assert statement.cites_only_the_playbook is playbook_only


# -- satisfies: inherited parts count; unresolved ones are reported ---------------------------


def _evidence(body: str):
    from policyforge.ingest.schema import load_controls
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.satisfies import document_evidence

    controls = [c for p in sorted(FRAMEWORKS.glob("*/controls.json")) for c in load_controls(p)]
    document = SimpleNamespace(
        body=body,
        relative_path="standards/a.md",
        title="A",
        tier="standard",
        metadata={"topic": ""},
        topic="",
        slug="a",
    )
    return document_evidence(document, controls=controls, crosswalk=build_crosswalk(controls))


def test_satisfies_counts_the_inherited_parts():
    evidence = _evidence(
        "NIST suggests X. [NIST AI RMF Playbook Govern 1.1 Action 1 | Govern 1.1 Action 2 | "
        "Govern 1.1 Action 3]\n"
    )
    assert evidence.unknown == []
    assert sorted(c.requirement_id for c in evidence.cited) == [
        "Govern 1.1 Action 1",
        "Govern 1.1 Action 2",
        "Govern 1.1 Action 3",
    ]


def test_the_real_standard_attributes_every_shorthand_part_and_leaves_nothing_unknown():
    evidence = _evidence(FIXTURE.read_text(encoding="utf-8"))
    playbook = [c for c in evidence.cited if c.framework == "nist-ai-rmf-playbook"]
    assert len(playbook) == 94
    assert evidence.unknown == []


def test_a_cross_framework_shorthand_is_reported_unresolved():
    """Inheritance never makes a citation of the wrong framework valid."""
    evidence = _evidence(
        "The organization must manage accounts. [NIST 800-53 AC-2 | Govern 1.1 Action 2]\n"
    )
    assert [c.requirement_id for c in evidence.cited] == ["AC-2"]
    assert evidence.unknown == ["NIST 800-53 Govern 1.1 Action 2"]


def test_a_first_part_shorthand_is_reported_not_guessed():
    evidence = _evidence("NIST suggests X. [Govern 1.1 Action 2 | Govern 1.1 Action 3]\n")
    assert evidence.cited == []
    assert len(evidence.unknown) == 2
