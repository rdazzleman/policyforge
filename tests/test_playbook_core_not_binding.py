"""A Core citation does not exempt a Playbook sentence from the gate (#341).

The AI RMF Core states outcomes, not obligations, so citing it beside the
Playbook does not make a sentence binding. Only a catalog that states
obligations (800-53, HIPAA, ...) hands a sentence to the strength rule.
80's ruling, with hand-written sentences: glm's "Playbook + Core" sentences
turned out to be analyzer merges across headings (#349), not real cases.
"""

from __future__ import annotations

import pytest

from policyforge.content.deontic import analyze, playbook_obligations
from policyforge.crosswalk.overlay import (
    NON_BINDING_FRAMEWORKS,
    NOT_CROSSWALK_ANCHORABLE,
    OUTCOME_CATALOGS,
)
from policyforge.mapping.crosswalk import normalize_framework

PLAYBOOK = "NIST AI RMF Playbook Map 1.6 Action 1"
ACTORS = ("Acme Health",)


def _flags(sentence: str) -> int:
    return len(playbook_obligations(sentence + "\n", ACTORS))


@pytest.mark.parametrize(
    "other",
    [
        "NIST AI RMF Map 1.6",
        "NIST AI RMF Govern 1",
        # CSF 2.0 states outcomes too (80, on #408), by either name it ships under.
        "NIST CSF 2.0 GV.OC-01",
        "NIST Cybersecurity Framework 2.0 GV.OC-01",
    ],
    ids=["core-subcategory", "core-category", "csf-by-row-name", "csf-by-manifest-name"],
)
def test_a_playbook_obligation_is_refused_even_with_a_core_tag(other):
    """The case the ruling is about: the organization's obligation, tagged
    to the Playbook and the Core. Before, the Core tag made it exempt."""
    assert _flags(f"Acme Health must document the system requirements. [{PLAYBOOK} | {other}]") == 1


def test_its_800_53_twin_is_still_exempt():
    """The same sentence citing a catalog that states obligations: that
    source's strength rule governs it, not the Playbook gate."""
    assert (
        _flags(
            f"Acme Health must document the system requirements. [{PLAYBOOK} | NIST 800-53 SA-4]"
        )
        == 0
    )


def test_a_correct_suggestion_with_a_core_tag_passes():
    """NIST framed as NIST's, with the Core beside the Playbook: no finding.
    The gate reads it and accepts it."""
    sentence = (
        f"NIST suggests, among its 8 actions for Map 1.6, proactively incorporating "
        f"trustworthy characteristics into system requirements. [{PLAYBOOK} | NIST AI RMF Map 1.6]"
    )
    (statement,) = analyze(sentence + "\n")
    assert statement.cites_only_the_playbook, "the gate reads it"
    assert _flags(sentence) == 0


def test_a_core_only_sentence_is_not_a_playbook_sentence():
    """No Playbook part, no Playbook rules: the Core alone is left to the
    checks that already govern it."""
    (statement,) = analyze("Acme Health must understand AI risks. [NIST AI RMF Map 1.6]\n")
    assert not statement.cites_only_the_playbook


def test_the_non_binding_set_is_the_refusal_tables_ai_rmf_entries():
    """Named once, beside the refusal table, and held inside it: a catalog
    cannot be non-binding without a written reason there."""
    refused = {normalize_framework(name) for name in NOT_CROSSWALK_ANCHORABLE}
    assert refused | set(OUTCOME_CATALOGS) >= NON_BINDING_FRAMEWORKS
    assert not refused & set(OUTCOME_CATALOGS), "a reason in both tables is two reasons"
    # CSF 2.0 by 80's ruling on #408: outcomes, as the Core.
    assert {"nist-ai-rmf", "nist-ai-rmf-playbook", "nist-csf"} == NON_BINDING_FRAMEWORKS
    assert "cfr-171-information-blocking" not in NON_BINDING_FRAMEWORKS, (
        "refused for a different reason; the ruling named exactly the two AI RMF catalogs"
    )
