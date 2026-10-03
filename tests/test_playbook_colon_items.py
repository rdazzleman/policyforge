"""The Playbook gate judges each item of a colon list (#354, 80's ruling (a)).

A colon lead-in and its items are one statement for citation crediting
(#351). When one item cited only the Playbook and a sibling cited 800-53, the
joined statement read as mixed and the Playbook item's obligation escaped the
gate. Now each item is judged as "lead-in + that item" against its own
citations, reported at the item's line, while crediting stays joined.

The 33 generated Standards hold 51 colon lists (188 items) and no item that
cites the Playbook, so these hand-written cases carry the fix; over the
corpus the gate's findings are unchanged (1 before, the same 1 after).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from policyforge.content.deontic import analyze, playbook_obligations

P = "[NIST AI RMF Playbook Map 1.6 Action 1]"
P2 = "[NIST AI RMF Playbook Map 1.6 Action 2]"
AC2 = "[NIST 800-53 AC-2]"
ACTORS = ("Acme Health",)


def _errors(body: str) -> list[str]:
    """Playbook ERRORs through `policyforge check`'s own path, with the owner
    set and a cited intro line, as 80 measured on #354."""
    from policyforge.content.check import check_tree

    root = Path(tempfile.mkdtemp())
    (root / "standards").mkdir()
    text = f"# A\n\nIntro. [NIST 800-53 PM-1]\n\n{body}\n"
    (root / "standards" / "a.md").write_text(text, encoding="utf-8")
    findings = check_tree(root, org_actors=ACTORS).findings
    return [f.message for f in findings if f.severity == "error" and "Playbook" in f.message]


def _lines(messages: list[str]) -> list[int]:
    return [int(m.split(":")[0].removeprefix("line ")) for m in messages]


def test_a_playbook_item_beside_an_800_53_item_no_longer_escapes():
    """Must fail: one ERROR, at the Playbook item's line, not the lead-in's."""
    errors = _errors(
        f"Acme Health must:\n\n- maintain an AI law register {P}\n- review access {AC2}"
    )
    assert _lines(errors) == [7], errors
    assert "Acme Health must: maintain an AI law register" in errors[0]


def test_a_lead_in_with_nist_as_subject_frames_every_item():
    """Must pass: an allowed opener, then NIST as subject, ending in a colon."""
    for lead in (
        "NIST suggests:",
        "Among the 8 actions the Playbook lists for Map 1.6, NIST suggests:",
    ):
        assert (
            _errors(f"{lead}\n\n- maintaining an AI law register {P}\n- reviewing access {P2}")
            == []
        )


def test_an_all_playbook_colon_list_is_still_an_error_now_once_per_item():
    """Must stay an ERROR. 80 accepted the count: one per item, at its line."""
    errors = _errors(
        f"Acme Health must:\n\n- maintain an AI law register {P}\n- review access {P2}"
    )
    assert _lines(errors) == [7, 8], errors


def test_plain_items_keep_their_verdict():
    body = (
        f"- Acme Health must maintain an AI law register {P}.\n"
        f"- Acme Health must review access {AC2}."
    )
    assert _lines(_errors(body)) == [5]


def test_crediting_stays_joined():
    """The statement `analyze` returns is unchanged: one, carrying both tags."""
    (statement,) = analyze(f"Acme Health must:\n\n- maintain a register {P}\n- review access {AC2}")
    assert statement.citations == (P, AC2)


def test_a_citation_under_the_list_is_every_items():
    """The list's own citation, below it after a blank line, applies to each
    item -- the shape the generated Standards use under a colon list."""
    text = f"Acme Health must:\n\n- maintain a register\n- review access\n\n{P}"
    assert [s.line for s in playbook_obligations(text, ACTORS)] == [3, 4]


def test_a_lead_ins_own_citation_is_shared_and_an_items_own_is_not():
    text = f"Acme Health must: {P}\n- maintain a register\n- review access {AC2}"
    # Item 1 cites only the Playbook (the lead-in's); item 2 adds AC-2: mixed.
    assert [s.line for s in playbook_obligations(text, ACTORS)] == [2]


def test_an_items_continuation_line_is_part_of_the_item():
    text = f"Acme Health must:\n\n- review access\n  every quarter {P}\n- rotate keys {AC2}"
    (unit,) = playbook_obligations(text, ACTORS)
    assert unit.line == 3 and unit.text == f"Acme Health must: review access every quarter {P}"


# 1d on #356: an item with two sentences escaped the same way two items did.
# The oracle is the SAME item as a plain item, not this gate's own rule.
_ITEMS = [
    f"review access {AC2}. Acme Health must keep a register {P}.",
    f"review access {AC2}.\n  Acme Health must keep a register {P}.",
    f"keep a register {P}. Acme Health must review access {AC2}.",
    f"review access {AC2}",
    f"Acme Health must keep a register {P}",
    f"keep a register {P}. NIST suggests reviewing it {P2}.",
    f"NIST suggests keeping a register {P}. Acme Health must review it {AC2}.",
    f"keep a register {P}.\n  NIST suggests reviewing it {P2}.",
    f"keep a register.\n  {P}",
]


@pytest.mark.parametrize("item", _ITEMS, ids=range(len(_ITEMS)))
def test_an_item_under_an_organizations_lead_in_gets_its_plain_item_verdict(item):
    plain = len(playbook_obligations(f"- {item}", ACTORS))
    listed = playbook_obligations(f"Acme Health must:\n\n- {item}\n- review keys {AC2}", ACTORS)
    assert len(listed) == plain, [s.text for s in listed]


def test_the_items_include_both_verdicts():
    """Otherwise the oracle comparison could agree by being constant."""
    assert {len(playbook_obligations(f"- {i}", ACTORS)) for i in _ITEMS} == {0, 1}


def test_a_later_sentences_finding_names_its_own_line():
    text = f"Acme Health must:\n\n- review access {AC2}.\n  Acme Health must keep a register {P}."
    assert [s.line for s in playbook_obligations(text, ACTORS)] == [4]


def test_a_nist_lead_in_does_not_frame_an_items_later_sentence():
    """`NIST suggests:` runs on into an item's first sentence only. A later
    imperative is the organization's own instruction, as it would be in a
    plain item, and is reported at its line."""
    text = f"NIST suggests:\n\n- maintaining a register {P}.\n  Review it quarterly {P2}."
    assert [s.line for s in playbook_obligations(text, ACTORS)] == [4]
