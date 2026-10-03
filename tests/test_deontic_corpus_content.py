"""The generated corpus in `tests/fixtures/deontic_corpus/` holds only text
generated from public-domain catalogs (80's ruling on #376).

No HITRUST, GovRAMP or SOC 2 text, verbatim or paraphrased, and no licensed
framework's name. The repository is public, and licensed content is never
bundled, fixtures included. Every guard here is shown failing on a planted
line before its zero over the corpus is trusted.

**What this cannot see:** licensed text paraphrased with none of the names,
ids or phrases below. The stronger evidence is how the corpus was made: from
the bundled public-domain catalogs only (the fixture's README).
"""

from __future__ import annotations

import re
from pathlib import Path

import frontmatter
import pytest

from policyforge.content.deontic import _parts
from policyforge.content.tags import SOURCE_TAG_RE

DOCUMENTS = Path(__file__).resolve().parent / "fixtures" / "deontic_corpus" / "documents"

#: The licensed catalogs' names, and the ids and phrases only their text has.
LICENSED = {
    "HITRUST name": r"\bHITRUST\b|\bMyCSF\b",
    "HITRUST CSF version": r"\bCSF\s*v?\d{1,2}(?:\.\d)?\b",
    "HITRUST control reference label": r"Control Reference:",
    "HITRUST level requirement": (
        r"\bLevel [123] (?:Implementation|Organizational|System|Regulatory)\b"
    ),
    "HITRUST control id": r"(?<![\w.(])\d{2}\.[a-z]{1,2}(?![\w(])",
    "GovRAMP or StateRAMP": r"\bGovRAMP\b|\bStateRAMP\b",
    "SOC 2 name": r"\bSOC ?2\b|\bAICPA\b|Trust Services Criteria",
    "SOC 2 criterion id": r"\b(?:CC|A|PI|C|P)\d{1,2}\.\d{1,2}\b",
    "ISO 27001 or 27002": r"\bISO(?:/IEC)?\s*2700[12]\b",
    "PCI DSS": r"\bPCI[- ]DSS\b",
}
#: The frameworks a citation in the corpus may name: bundled, public domain.
PUBLIC_DOMAIN = {"NIST", "HIPAA", "FedRAMP", "ARC-AMPE", "ARC", "45", "42", "ONC"}
#: The only organisation the corpus may name, and phrases that end in the
#: same words but are HIPAA's terms, not organisations.
PLACEHOLDERS = {"Acme Health", "Example Org"}
HIPAA_TERMS = {"Protected Health", "Group Health", "Qualified Health"}


def licensed(text: str) -> list[str]:
    return [name for name, pattern in LICENSED.items() if re.search(pattern, text)]


def _bodies() -> list[tuple[str, str]]:
    return [
        (p.name, frontmatter.loads(p.read_text(encoding="utf-8")).content)
        for p in sorted(DOCUMENTS.rglob("*.md.txt"))
    ]


@pytest.mark.parametrize(
    "planted, expected",
    [
        ("01.a Invented Control Title (HITRUST CSF v11)", "HITRUST name"),
        ("09.ab Invented Control Title: Level 1 Implementation Requirements", "HITRUST control id"),
        ("Control Reference: 01.b Invented Control Title", "HITRUST control reference label"),
        ("Level 2 Implementation Requirements apply above 100 users.", "HITRUST level requirement"),
        ("Aligned to GovRAMP Moderate.", "GovRAMP or StateRAMP"),
        ("CC6.1 An invented criterion statement.", "SOC 2 criterion id"),
        ("Per the AICPA Trust Services Criteria.", "SOC 2 name"),
    ],
)
def test_the_guard_finds_a_planted_licensed_line(planted, expected):
    """80's condition: the guard must fail on a planted HITRUST line (and the
    others) before its zero over the corpus is trusted."""
    assert expected in licensed(planted)


def test_the_guard_passes_the_public_text_it_must_allow():
    for public in (
        "Acme Health must review accounts [NIST 800-53 AC-2].",
        "Section 03.01.01 of NIST SP 800-171 [NIST 800-171 03.01.01].",
        "Retain logs per HIPAA 164.316(b)(2)(i) [HIPAA 164.316(b)(2)(i)].",
        "Software is used in accordance with copyright laws [NIST 800-53 CM-10].",
    ):
        assert licensed(public) == [], public


def test_the_corpus_holds_no_licensed_text():
    found = [(name, hits) for name, body in _bodies() if (hits := licensed(body))]
    assert found == []


def test_every_citation_names_a_public_domain_framework():
    frameworks = {
        part.split()[0]
        for _, body in _bodies()
        for tag in SOURCE_TAG_RE.findall(body)
        for part in _parts(tag)
        if part.split()
    }
    assert frameworks <= PUBLIC_DOMAIN, frameworks - PUBLIC_DOMAIN


def test_the_only_organisations_are_the_placeholders():
    names = {
        m.group(0)
        for _, body in _bodies()
        for m in re.finditer(
            r"\b[A-Z][a-z]+ (?:Health|Org)\b|\b\w+ (?:Inc|LLC|Ltd|Corporation)\b", body
        )
    }
    assert names - HIPAA_TERMS == PLACEHOLDERS
