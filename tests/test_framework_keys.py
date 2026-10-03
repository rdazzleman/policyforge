"""Every NIST-family catalog keys to itself, so a citation cannot cross them.

The defect this file exists to keep closed. `normalize_framework` was the
first word of a framework's declared name, so `NIST 800-53`,
`NIST 800-171`, `NIST 800-172`, `NIST 800-137` and `NIST Cybersecurity
Framework` all keyed to `nist`. The catalog index keys on that value, so two
NIST catalogs did not compete for a name — they **merged**, requirement
identifiers and all.

The failure that produced was silent, which is what makes it worth a test
of its own. An 800-171 identifier cited as 800-53 was *found* in the shared
bucket and counted as evidence. Not reported as unknown, where a reader
would see it. Counted, in a report an assessor reads.

So the case pinned hardest below is the one that **passed** before this
fix: `[NIST 800-53 03.01.01]`, an 800-171 requirement wearing an 800-53
name. It must be refused. A test written only against the new prompt's
output would never generate that citation and would never catch its
return.

The second pinned case is the legacy short form, `[NIST AC-2]`, which is
what every document generated before this fix carries. With two NIST
catalogs loaded it must resolve to nothing *and be reported*. That is a
deliberate outcome rather than a regression: a reported unknown is a
failure a reader can act on, and it is the honest answer to a citation
that genuinely does not say which catalog it means. Regeneration is the
migration, and this test is what says so in code.
"""

from __future__ import annotations

import pytest

from policyforge.mapping.crosswalk import (
    FRAMEWORK_ALIASES,
    NIST_ANCHOR,
    hitrust_framework,
    normalize_framework,
)
from policyforge.topics.satisfies import resolve_framework, split_citation

# The declared names, as the catalogs themselves write them.
NIST_53 = "NIST 800-53"
NIST_171 = "NIST 800-171"
HIPAA = "HIPAA Security Rule"


# --- one key per family --------------------------------------------------


@pytest.mark.parametrize(
    "declared,key",
    [
        ("NIST 800-53", "nist-800-53"),
        ("NIST SP 800-53 r5", "nist-800-53"),
        ("NIST 800-171", "nist-800-171"),
        ("NIST 800-172", "nist-800-172"),
        ("NIST 800-137", "nist-800-137"),
        ("NIST Cybersecurity Framework", "nist-csf"),
    ],
)
def test_each_nist_family_keys_to_itself(declared, key):
    """`AC-2`, `03.01.01` and `GV.PO-02` are not interchangeable, so the
    catalogs holding them must not share an index entry."""
    assert normalize_framework(declared) == key


def test_the_nist_families_are_all_distinct_from_one_another():
    """Stated as a set, because the defect was two of them being equal."""
    names = [
        "NIST 800-53",
        "NIST 800-171",
        "NIST 800-172",
        "NIST 800-137",
        "NIST Cybersecurity Framework",
    ]
    keys = [normalize_framework(n) for n in names]
    assert len(set(keys)) == len(names), f"two families share a key: {keys}"


@pytest.mark.parametrize("written", ["NIST CSF", "NIST CSF 2.0"])
def test_the_abbreviation_people_actually_write_is_recognised(written):
    """A needle spelling the framework out in full misses `NIST CSF`, and a
    miss falls through to the first word — which is the bug, not a
    near-miss. `NIST CSF 2.0` is the current version's actual name, so the
    abbreviation is the likelier spelling rather than the exotic one."""
    assert normalize_framework(written) == "nist-csf"


def test_hitrust_csf_is_hitrust_s_and_not_nist_s():
    """The substring hazard in `FRAMEWORK_ALIASES`, pinned.

    The table is matched by substring and "HITRUST CSF" contains "csf", so
    a bare `csf` needle would file every HITRUST catalog under `nist-csf` —
    silently, since nothing downstream would complain.
    """
    assert normalize_framework("HITRUST CSF") == "hitrust"
    assert normalize_framework("HITRUST CSF v11") == "hitrust"


def test_the_hitrust_framework_id_survives_the_table_intact():
    """`hitrust-csf` is a framework *id*, and it keyed correctly for as long
    as it existed — by falling through to the first word, which has no
    space to stop at.

    This is here because a draft of the table added a `hitrust` needle to
    guard the case above, and that needle swallowed this id and returned
    `hitrust`, merging a catalog nothing had ever merged. The guard was
    also unnecessary: the CSF needles are narrow enough to miss "HITRUST
    CSF" on their own. A needle must be as narrow as the thing it names.
    """
    assert normalize_framework("hitrust-csf") == "hitrust-csf"


def test_no_needle_matches_a_framework_it_does_not_name():
    """The general form, so the next needle added is held to it too."""
    foreign = {
        "hitrust-csf": ["800-53", "800-171", "cybersecurity framework", "nist csf"],
        "HITRUST CSF": ["cybersecurity framework", "nist csf"],
        "GovRAMP": ["fedramp"],
    }
    live = {needle for needle, _ in FRAMEWORK_ALIASES}
    for name, must_not_match in foreign.items():
        lowered = name.lower()
        for needle in must_not_match:
            assert needle in live, f"{needle!r} is not in the table; this test is stale"
            assert needle not in lowered, f"{needle!r} wrongly matches {name!r}"


@pytest.mark.parametrize(
    "declared,key",
    [
        ("HIPAA Security Rule", "hipaa"),
        ("FedRAMP", "fedramp"),
        ("GovRAMP", "govramp"),
        ("ARC-AMPE", "arc-ampe"),
        ("SOC2", "soc2"),
    ],
)
def test_every_other_catalog_keys_exactly_as_before(declared, key):
    """The first word stays the fallback, so a catalog not named in the
    table — including one a user brings — is untouched by this change."""
    assert normalize_framework(declared) == key


def test_the_two_normalisers_now_agree():
    """`hitrust_framework` and `normalize_framework` were two functions with
    two tables, and they disagreed about `NIST 800-171`: one said
    `nist-800-171`, the other said `nist`. Two definitions of one concept
    is how this defect was born, so agreement is the property to hold."""
    for name in ["NIST 800-53", "NIST 800-171", "HIPAA Security Rule", "HITRUST CSF"]:
        assert hitrust_framework(name) == normalize_framework(name), name


def test_the_crosswalk_anchor_is_not_the_bare_family_name():
    """While 800-53 was filed under `nist`, `resolve_framework` found that
    key in the index and returned it before its own ambiguity rule could
    run — so `[NIST AC-2]` resolved to 800-53 by short-circuit rather than
    because anything established which NIST catalog was meant."""
    assert NIST_ANCHOR == "nist-800-53"
    assert NIST_ANCHOR != "nist"


# --- a citation cannot cross two catalogs --------------------------------


@pytest.fixture
def both_nist_catalogs():
    """Two NIST catalogs loaded together, which is wave 2's whole point."""
    ids = {
        "nist-800-53": {"AC-2", "AC-2(3)"},
        "nist-800-171": {"03.01.01"},
        "hipaa": {"164.308(a)(3)(i)"},
    }
    names = sorted({NIST_53, NIST_171, HIPAA}, key=len, reverse=True)
    return names, ids


def _resolve(part, names, ids):
    """(framework key, requirement id, whether the catalog has that id)."""
    framework, requirement_id, _qualifier = split_citation(part, names, ids)
    key = resolve_framework(framework, ids)
    return key, requirement_id, requirement_id in ids.get(key, ())


def test_an_800_171_id_cited_as_800_53_is_refused(both_nist_catalogs):
    """**The case that silently passed before this fix.**

    `03.01.01` is an 800-171 requirement. Cited under 800-53's name it was
    found in the merged bucket and counted as evidence. It must now fail to
    resolve, because 800-53 does not contain it.
    """
    names, ids = both_nist_catalogs
    key, requirement_id, found = _resolve("NIST 800-53 03.01.01", names, ids)

    assert key == "nist-800-53"
    assert requirement_id == "03.01.01"
    assert not found, "an 800-171 id resolved inside the 800-53 catalog"


def test_an_800_53_id_cited_as_800_171_is_refused(both_nist_catalogs):
    """The same crossing, the other way, so the test does not pass by the
    accident of one catalog being a superset of the other."""
    names, ids = both_nist_catalogs
    key, _requirement_id, found = _resolve("NIST 800-171 AC-2", names, ids)

    assert key == "nist-800-171"
    assert not found


@pytest.mark.parametrize(
    "part,key,requirement_id",
    [
        ("NIST 800-53 AC-2", "nist-800-53", "AC-2"),
        ("NIST 800-171 03.01.01", "nist-800-171", "03.01.01"),
        ("HIPAA Security Rule 164.308(a)(3)(i)", "hipaa", "164.308(a)(3)(i)"),
    ],
)
def test_a_citation_naming_its_catalog_resolves_to_that_catalog(
    both_nist_catalogs, part, key, requirement_id
):
    """Refusing everything would also pass the two tests above."""
    names, ids = both_nist_catalogs
    got_key, got_id, found = _resolve(part, names, ids)

    assert (got_key, got_id) == (key, requirement_id)
    assert found


def test_the_legacy_short_form_is_reported_rather_than_guessed(both_nist_catalogs):
    """`[NIST AC-2]` is what every document generated before this fix says.

    With two NIST catalogs loaded it names neither, so it resolves to
    nothing and is reported. That is the intended outcome and the reason
    the upgrade note has to say regeneration is the migration: the failure
    a user meets is visible, which the merged bucket's was not.
    """
    names, ids = both_nist_catalogs
    key, _requirement_id, found = _resolve("NIST AC-2", names, ids)

    assert key == "", "an ambiguous citation picked a catalog"
    assert not found


def test_the_short_form_still_works_while_only_one_nist_catalog_is_loaded():
    """The common case must not regress. A user with the bundled catalog
    alone has an unambiguous `NIST`, and their existing documents keep
    resolving exactly as they did."""
    ids = {"nist-800-53": {"AC-2"}, "hipaa": {"164.308(a)(3)(i)"}}
    names = sorted({NIST_53, HIPAA}, key=len, reverse=True)

    key, requirement_id, found = _resolve("NIST AC-2", names, ids)

    assert (key, requirement_id) == ("nist-800-53", "AC-2")
    assert found
