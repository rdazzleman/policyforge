"""The short list a model judges: word matches, published pairs, family policy controls."""

from __future__ import annotations

from policyforge.crosswalk.candidates import (
    WordIndex,
    candidates_for,
    catalog_entries,
)
from policyforge.ingest.schema import Control, ControlEnhancement


def _nist(control_id, title, text, enhancements=()):
    return Control(
        control_id=control_id,
        title=title,
        framework="NIST 800-53",
        framework_version="r5",
        control_statement=text,
        enhancements=list(enhancements),
    )


CATALOG = [
    _nist("SI-1", "Policy and Procedures", "Develop system and information integrity policy."),
    _nist(
        "SI-3",
        "Malicious Code Protection",
        "Implement malicious code protection mechanisms to detect and eradicate malicious code.",
        [
            ControlEnhancement(
                enhancement_id="SI-3(4)",
                title="Updates Only by Privileged Users",
                baseline="",
                description="Update malicious code protection mechanisms only when directed.",
            )
        ],
    ),
    _nist("AT-2", "Literacy Training and Awareness", "Provide security literacy training."),
    _nist("AT-1", "Policy and Procedures", "Develop awareness and training policy."),
    _nist("RA-3", "Risk Assessment", "Conduct a risk assessment."),
    Control(
        control_id="164.308(a)(1)(i)",
        title="Security management process",
        framework="HIPAA Security Rule",
        framework_version="45 CFR 164",
        control_statement="Implement policies and procedures.",
    ),
]


def _candidates(text, published=()):
    entries = catalog_entries(CATALOG)
    return candidates_for(
        text, published=list(published), entries=entries, index=WordIndex(entries), k=2
    )


def test_only_the_anchor_framework_is_a_candidate():
    entries = catalog_entries(CATALOG)
    assert "164.308(a)(1)(i)" not in entries
    assert "SI-3(4)" in entries


def test_word_matches_bring_their_familys_policy_control():
    found = _candidates("guarding against and detecting malicious software and malicious code")
    assert "SI-3" in found
    assert "SI-1" in found


def test_a_published_pair_is_a_candidate_even_when_no_word_matches():
    """Word overlap found 23% of NIST's HIPAA pairs; the rest must still be judged."""
    found = _candidates("guarding against malicious code", published=["AT-2"])
    assert "AT-2" in found


def test_a_published_id_the_catalog_lacks_is_not_offered():
    assert "ZZ-9" not in _candidates("malicious code", published=["ZZ-9"])


def test_the_list_is_sorted_so_its_order_carries_no_hint():
    found = _candidates("malicious code risk assessment", published=["AT-2"])
    assert found == sorted(found)


def test_the_word_index_is_deterministic_on_ties():
    entries = catalog_entries(CATALOG)
    index = WordIndex(entries)
    assert index.top("policy and procedures", 5) == index.top("policy and procedures", 5)
    assert index.top("policy and procedures", 2) == ["AT-1", "SI-1"]
