"""A framework citation is not an unfilled role (#189).

`generate`'s fill pass (`org.context.apply_substitutions`) listed every
bracketed citation tag (`[NIST 800-53 AC-2]`, `[NIST AI RMF Govern 1.1]`)
among the roles left unfilled, and Zardoz's answer check
(`zardoz.answer.undisclosed_placeholders`) warned that an answer quoting one
had reproduced a placeholder. Both now ask `content.tags.is_citation_tag`.

80's ruling (3512 on #214): a span is a citation only when it names a
framework a catalog on disk declares AND a requirement id that exists in that
catalog, resolved exactly as the citation checker (`topics.satisfies`)
resolves a tag. Everything else is reported: roles in any case, a role that
names a framework and carries a digit (`[FedRAMP 3PAO]`; 9b), a tag whose id
doesn't exist, and a bare framework name (an incomplete citation, flagged on
purpose).

The expected sets come from the OTHER side of the check, not from the rule
under test: the citations from each bundled catalog's own framework name and
a real id of its, and the roles from every label `org.roles` declares. A few
inputs neither list spells are written by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.content.tags import is_citation_tag
from policyforge.ingest.schema import load_controls
from policyforge.org.context import OrgProfile, apply_substitutions
from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES
from policyforge.topics.satisfies import parse_citations, resolve_framework
from policyforge.zardoz.answer import undisclosed_placeholders

ROOT = Path(__file__).resolve().parent.parent


def _catalog_citations() -> list[str]:
    """One citation per bundled catalog: its declared framework name and the
    first requirement id it carries, as a document cites it."""
    tags = []
    for path in sorted((ROOT / "data" / "frameworks").glob("*/controls.json")):
        controls = load_controls(path)
        if controls:
            first = controls[0]
            tags.append(f"[{first.framework} {first.control_id}]")
    return tags


CITATIONS = _catalog_citations()
ROLES = [role.label for role in (*TEAM_ROLES.values(), *VENDOR_ROLES.values())]


def test_the_populations_are_not_empty():
    """Extent: every bundled catalog with controls, and every declared role."""
    assert len(CITATIONS) >= 13, CITATIONS
    assert len(ROLES) >= 40, len(ROLES)


@pytest.mark.parametrize("tag", CITATIONS)
def test_each_catalogs_own_citation_is_a_citation(tag):
    assert is_citation_tag(tag), tag


@pytest.mark.parametrize("label", ROLES)
def test_every_declared_role_stays_a_role(label):
    assert not is_citation_tag(f"[{label}]"), label


# Hand-written: the shapes documents carry that neither list spells.
CITED_BY_HAND = [
    "[NIST 800-53 AC-2]",
    "[NIST AI RMF Govern 1.1]",
    "[NIST 800-53 AC-2 | AC-3]",
    "[HIPAA Security Rule 164.312(a)(1)]",
    "[HIPAA 164.312(a)(1)]",
    "[ARC PE-1]",
]
REPORTED_BY_HAND = [
    "[CISO]",
    "[IT]",
    "[HR]",
    "[Security Lead]",
    "[CISO - interim]",
    "[Tier 2 Support]",  # an id-like word, no catalog
    "[FedRAMP Liaison]",  # a catalog, no id
    "[FedRAMP 3PAO]",  # a catalog and a digit, but 3PAO is no id (9b on #214)
    "[NIST 800-53 Assessor]",  # a role naming a revision (9b on #214)
    "[NIST SP 800-53 ZZ-99]",  # a tag whose id doesn't exist (80, 3512)
    "[NIST]",  # a bare name: an incomplete citation, flagged (80, 3512)
    "[HIPAA]",
    "[ISO 27001 A.5.1]",  # a framework with no catalog on disk
    "[Assignment: organization-defined frequency]",
]


@pytest.mark.parametrize("tag", CITED_BY_HAND)
def test_a_citation_written_by_hand_is_a_citation(tag):
    assert is_citation_tag(tag), tag


@pytest.mark.parametrize("tag", REPORTED_BY_HAND)
def test_a_placeholder_written_by_hand_is_reported(tag):
    assert not is_citation_tag(tag), tag


@pytest.mark.parametrize("tag", ["[NIST SP 800-53 AC-2]", "[NIST 03.01.01]"])
def test_a_tag_unresolvable_against_every_catalog_on_disk_is_reported(tag):
    """The checker's own steps, over EVERY catalog on disk (80's rulings 3512,
    3516): `NIST SP 800-53 AC-2` splits as framework `NIST` with the id
    `SP 800-53 AC-2`, which no catalog has. `[NIST 03.01.01]` is an 800-171
    id under the bare name, which names 800-53 only (6044 on #17). So
    neither resolves, and neither is a citation here."""
    from policyforge.content.tags import known_requirement_index

    index = known_requirement_index()
    resolved = [
        rid in index.get(resolve_framework(fw, index), ())
        for fw, rid, _, _ in parse_citations(tag, (), index)
    ]
    assert not any(resolved), (tag, "the checker resolves it now; revisit this test")
    assert not is_citation_tag(tag), tag


def test_a_short_form_tag_resolves_alike_with_800_53_alone_and_on_disk():
    """`satisfies --controls` with 800-53 alone resolves `[NIST SC-8]` as
    cited, and so does this rule with no run's set given (every catalog on
    disk): `NIST` alone names 800-53 (6044 on #17, replacing 3525's
    difference between the two)."""
    from policyforge.topics.satisfies import _catalog_index

    only_800_53 = _catalog_index(
        load_controls(ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json")
    )
    [(framework, requirement_id, _, _)] = parse_citations("[NIST SC-8]", (), only_800_53)
    catalog = resolve_framework(framework, only_800_53)
    assert requirement_id in only_800_53.get(catalog, ()), "satisfies resolves it with 800-53 alone"
    assert is_citation_tag("[NIST SC-8]"), "and it resolves here too, as ruled"


# ---- the two readers ---------------------------------------------------------


def test_the_fill_pass_reports_placeholders_and_not_citations():
    text = " ".join(["Reviewed by", *REPORTED_BY_HAND, *CITED_BY_HAND, *CITATIONS]) + "."
    result = apply_substitutions(text, OrgProfile(name="X", industry="Y"))
    assert "CISO" in result.outstanding and "FedRAMP 3PAO" in result.outstanding
    assert "NIST 800-53 Assessor" in result.outstanding
    cited = {tag.strip("[]") for tag in (*CITED_BY_HAND, *CITATIONS)}
    assert not cited & set(result.outstanding), sorted(cited & set(result.outstanding))
    assert result.text == text, "nothing is substituted with an empty profile"


def test_80s_table_still_holds():
    """80's table (3462): the four roles read exactly as before."""
    text = "Owned by [CISO], [IT], [Security Lead] and [CISO - interim]."
    result = apply_substitutions(text, OrgProfile(name="X", industry="Y"))
    assert result.outstanding == ["CISO", "IT", "Security Lead"]


def test_zardoz_does_not_call_a_quoted_citation_a_placeholder():
    for tag in [*CITED_BY_HAND, *CITATIONS]:
        assert undisclosed_placeholders(f"Access is reviewed quarterly {tag}.") == [], tag


def test_zardoz_still_flags_a_quoted_role_and_an_unresolved_tag():
    assert undisclosed_placeholders("Tickets go to [CISO] for review.") == ["[CISO]"]
    assert undisclosed_placeholders("Assessed by the [FedRAMP 3PAO].") == ["[FedRAMP 3PAO]"]


def test_real_model_output_naming_frameworks_bare_is_still_reported():
    """80's ruling 2 (3512), on real model output from the deontic corpus: a
    bare `[NIST]`, `[FedRAMP]` or `[ARC-AMPE]` is an incomplete citation, and
    reporting it is the intended, loud direction."""
    path = (
        ROOT
        / "tests/fixtures/deontic_corpus/documents/procedures"
        / "dfe1edd7f132-endpoint-protection-device-controls.md.txt"
    )
    line = next(
        text
        for text in path.read_text(encoding="utf-8").splitlines()
        if "([NIST], [FedRAMP], [ARC-AMPE])" in text
    )
    result = apply_substitutions(line, OrgProfile(name="X", industry="Y"))
    assert result.outstanding == ["NIST", "FedRAMP", "ARC-AMPE"]
