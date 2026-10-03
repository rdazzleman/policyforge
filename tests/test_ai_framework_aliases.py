"""Keys for the two AI catalogs, pinned before either catalog exists.

**The cheap moment is now.** `NIST AI RMF` would take the bare `nist`
key — the orphan the NIST-family split existed to remove — and
`HITRUST AI Security Certification` collides outright with `HITRUST CSF`.
That second one is not hypothetical: HITRUST's AI certification is an
add-on to a CSF assessment and cannot be held standalone, so a user who
brings one almost certainly has both, and their requirement ids would
share a bucket.

Both names are already citable — capital-initial, so neither repeats the
digit-initial trap that made two CFR catalogs impossible to cite. What
was missing was the key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.content.tags import source_tags
from policyforge.mapping.crosswalk import normalize_framework

CATALOGS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


@pytest.mark.parametrize(
    "written",
    ["NIST AI RMF", "NIST AI Risk Management Framework", "AI RMF"],
)
def test_every_spelling_of_ai_rmf_keys_to_one_catalog(written: str):
    """NIST writes the name both ways and neither spelling contains the
    other, so one needle cannot cover both."""
    assert normalize_framework(written) == "nist-ai-rmf"


@pytest.mark.parametrize("written", ["HITRUST AI Security Certification", "HITRUST AI"])
def test_hitrust_ai_does_not_share_a_key_with_the_csf(written: str):
    """The collision that is real rather than anticipated."""
    assert normalize_framework(written) == "hitrust-ai"
    assert normalize_framework(written) != normalize_framework("HITRUST CSF")


def test_both_names_are_citable_so_the_cfr_trap_is_not_repeated():
    """A framework name beginning with a digit is not a source tag, which
    is what made `45 CFR 171` and `42 CFR Part 2` impossible to cite.
    Neither AI name has that shape, and this says so before anyone writes
    a catalog that assumes it."""
    for tag in ("[NIST AI RMF GOVERN-1.1]", "[HITRUST AI 01.a]"):
        assert source_tags(tag) == [tag], f"{tag} is not a legal source tag"


@pytest.mark.parametrize(
    "identifier",
    [
        "hitrust-csf",
        "hitrust-ai",
        "nist-ai-rmf",
        "nist-ai-100-2",
        "nist-800-53",
        "cfr-171-information-blocking",
    ],
)
def test_a_needle_does_not_swallow_a_catalog_identifier(identifier: str):
    """A `hitrust` needle was removed once for exactly this: it matched
    inside the id `hitrust-csf` and returned `hitrust`. These needles
    carry a space, so they cannot match a hyphenated id."""
    assert normalize_framework(identifier) == identifier


#: Catalogs that ship README-only, for a user to bring their own controls.
#: They carry no `controls.json` and no `framework.yaml`, so there is no
#: declared name in this tree to key — the guard below cannot reach them,
#: and says so rather than counting them as covered.
#: **`hitrust-ai` is here deliberately, and its key is covered elsewhere.**
#: The guard's own message asks for that decision rather than a quiet
#: addition: its key cannot be checked from a declared name, because a
#: README-only catalog has none to declare. It is checked at the alias
#: level instead, by `test_hitrust_ai_does_not_share_a_key_with_the_csf`
#: above, which needs no catalog because the pin lives in
#: `FRAMEWORK_ALIASES`. That is the stronger place for it: the pin is what
#: a user's own parsed catalog keys through, wherever they keep it.
BYOC_ONLY = {"govramp", "hitrust-ai", "hitrust-csf", "soc2-tsc"}


def test_no_bundled_catalog_changed_key():
    """The regression guard, derived from the tree rather than listed, so
    a catalog added later is covered without anyone remembering this.

    **Covered: catalogs with a `controls.json`.** Not covered: the
    README-only ones in `BYOC_ONLY`, which have no declared name in this
    tree to check. 80 found that gap — `hitrust-ai` will land as BYOC,
    so the collision this module exists to prevent is outside this
    test's reach at exactly the moment it matters.

    The second assertion is the answer to that: **the skipped set is
    pinned, so a new README-only catalog turns this red** and somebody
    decides whether it needs covering, rather than being skipped in
    silence. A guard that states its own reach can be wrong out loud.
    """
    expected = {
        "arc-ampe": "arc-ampe",
        "cfr-171-information-blocking": "cfr-171-information-blocking",
        "cfr-42-part-2-sud-records": "cfr-42-part-2-sud-records",
        "fedramp": "fedramp",
        "hipaa-security-rule": "hipaa",
        "nist-800-171-r3": "nist-800-171",
        "nist-800-53-r5": "nist-800-53",
        "nist-ai-rmf": "nist-ai-rmf",
        # Its own key, not the Core's: the "ai rmf playbook" needle is ordered
        # before "ai rmf", which "NIST AI RMF Playbook" also contains (#177).
        "nist-ai-rmf-playbook": "nist-ai-rmf-playbook",
        # "NIST CSF 2.0" keys by the `nist csf` needle, never HITRUST's (#408).
        "nist-csf-2-0": "nist-csf",
        # Keyed apart from the Security Rule's `hipaa` by the reserved
        # `hipaa privacy rule` and `hipaa breach` needles (#409).
        "hipaa-privacy-rule": "hipaa-privacy",
        "hipaa-breach-notification-rule": "hipaa-breach",
        # Re-landed by #179; its own declared name is pinned, its siblings not.
        "cfr-170-315-onc-certification": "cfr-170-315-onc-certification",
    }
    seen = {}
    skipped = set()
    for directory in sorted(CATALOGS.iterdir()):
        controls = directory / "controls.json"
        if not controls.exists():
            skipped.add(directory.name)
            continue
        declared = json.loads(controls.read_text(encoding="utf-8"))[0]["framework"]
        seen[directory.name] = normalize_framework(declared)

    assert skipped == BYOC_ONLY, (
        f"the catalogs this guard cannot see have changed: {sorted(skipped)}. "
        f"A README-only catalog has no declared name here to key, so it is "
        f"skipped — which is fine for `govramp` and `hitrust-csf` and is NOT "
        f"fine silently. If `hitrust-ai` is in that list, the HITRUST collision "
        f"this module exists to prevent is untested; add it to BYOC_ONLY only "
        f"after deciding how its key is covered instead."
    )

    assert seen == expected, (
        "a bundled catalog's key moved. The AI needles carry a space and should "
        "not reach any of these; if one did, a citation could cross between "
        "catalogs silently."
    )
    assert len(set(seen.values())) == len(seen), "two bundled catalogs now share a key"


# ---- #176: siblings of a pinned name ----------------------------------------
#
# `normalize_framework` falls back to the first word, so every sibling of a
# pinned name fell through: `NIST AI 100-1` -- the RMF's own document number
# -- keyed to bare `nist` while both of its prose spellings were pinned. Pin
# what has been READ; record what is deliberately not pinned.


@pytest.mark.parametrize("written", ["NIST AI 100-1", "AI 100-1"])
def test_the_rmf_document_number_keys_to_the_rmf(written: str):
    assert normalize_framework(written) == "nist-ai-rmf"


@pytest.mark.parametrize("written", ["NIST AI 100-2", "NIST AI 100-2 E2023", "AI 100-2"])
def test_the_ai_taxonomy_keys_by_its_document_number(written: str):
    """Fell to bare `nist` before #176, and is not the RMF's key. Pinned by
    document number only -- the one spelling no other publisher shares."""
    key = normalize_framework(written)
    assert key == "nist-ai-100-2"
    assert key != normalize_framework("NIST AI RMF")


@pytest.mark.parametrize(
    "written",
    [
        "Adversarial Machine Learning",
        "AI Taxonomy",
        "OECD AI Taxonomy",
        "OECD Framework for the Classification of AI Systems",
        "Microsoft AI Taxonomy",
        "MITRE ATLAS",
    ],
)
def test_other_publishers_ai_taxonomies_are_not_filed_under_nist(written: str):
    """**Decisions, recorded where the next person will meet them** (1d, on
    #296). OECD, the EU, ISO, Microsoft and MITRE all publish an "AI
    Taxonomy" and use "adversarial machine learning"; a needle on either
    phrase merged them into NIST's key. If one of these starts returning
    `nist-ai-100-2`, someone pinned a shared phrase -- the table's comment
    says why not. The unprefixed "AI Taxonomy" keeps the bare `ai` orphan
    until #295 keys catalogs by what they declare."""
    assert normalize_framework(written) != "nist-ai-100-2"


def test_nist_ai_taxonomy_is_not_pinned_without_a_source():
    """**Pinned behaviour, not an endorsement** (1d, on #296). "NIST AI
    Taxonomy" is not a spelling any NIST source has been found to declare --
    CPRT's `AITAXONOMY` is a code identifier -- so it is not pinned, and it
    keeps the bare `nist` orphan until #295. If a NIST publication or CPRT
    name is found that declares it, pinning it is right: change this test in
    the same commit, and name the source in the table's comment."""
    assert normalize_framework("NIST AI Taxonomy") == "nist"


def test_the_taxonomy_is_citable():
    """Capital-initial, like the other AI names, so the digit-initial trap
    that made two CFR catalogs uncitable does not recur here either."""
    for tag in ("[NIST AI 100-2 NISTAML.01]",):
        assert source_tags(tag) == [tag], f"{tag} is not a legal source tag"


@pytest.mark.parametrize(
    "written",
    [
        "ONC Health IT Certification Program",
        "ONC Certification Program Requirements",
        "ONC Disincentives for Health IT Developers",
    ],
)
def test_onc_siblings_share_the_bare_key(written: str):
    """**Pinned behaviour, not an endorsement** (#176). Every ONC name keys
    to bare `onc`: harmless with one ONC catalog, a collision the day a
    second lands. Not pre-pinned, because their declared names are a guess
    until someone reads the regulation. When a second ONC catalog arrives
    this test is the one that should change -- deliberately, with the names
    read -- and the table's comment says so."""
    assert normalize_framework(written) == "onc"


@pytest.mark.parametrize(
    ("written", "must_not"),
    [
        ("NIST AI 100-10", "nist-ai-rmf"),
        ("NIST AI 100-12", "nist-ai-rmf"),
        ("NIST AI 100-19", "nist-ai-rmf"),
        ("NIST AI 100-20", "nist-ai-100-2"),
        ("NIST SP 800-530", "nist-800-53"),
    ],
)
def test_a_document_number_does_not_claim_its_successors(written: str, must_not: str):
    """**80 and 1d, on #296.** A document number is a prefix of its
    successors, and a substring needle matched them all: AI 100-10 to
    100-19 keyed to the RMF, 100-20 onward to the Taxonomy. A digit-ending
    needle now matches only where no digit follows."""
    assert normalize_framework(written) != must_not


@pytest.mark.parametrize(
    ("written", "key"),
    [
        ("NIST AI 100-2 E2023", "nist-ai-100-2"),
        ("NIST AI 100-2e2025", "nist-ai-100-2"),
        ("NIST SP 800-53r5", "nist-800-53"),
        ("NIST SP 800-53A", "nist-800-53"),
    ],
)
def test_a_document_number_followed_by_a_letter_still_matches(written: str, key: str):
    """The passing twins. `100-2e2025` is the Taxonomy's 2025 edition, so the
    rule is "no DIGIT follows", not a word boundary -- a letter may."""
    assert normalize_framework(written) == key
