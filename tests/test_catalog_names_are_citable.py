"""Every bundled catalog must be citable in a document.

**Two shipped that were not, and nothing noticed.** `45 CFR 171` and
`42 CFR Part 2` declared names beginning with a digit.
`content/tags.SOURCE_TAG_RE` builds a framework name out of
capital-initial words, so `[45 CFR 171 171.203(a)]` is not a tag at all —
it is prose. A real information-blocking policy, tagged the obvious way,
produced `0 citation(s)` and `satisfies --strict` exited **0**, while
`policyforge check` said *"No errors — safe to publish."*

**`FRAMEWORK_ALIASES` cannot fix this and that is the trap.** The alias
table is the mechanism this codebase reaches for when a framework name
keys wrongly, and it already carries the NIST-family entries. It is
**downstream of the tag regex**: the pattern rejects the string before any
normalisation runs, so no alias entry can make a digit-initial name
citable. Renaming is the only fix; the alias is what pins the key
afterwards.

**Parsing back to itself is the second half, and it is the half that
catches a plausible wrong answer.** `Part 2` is a legal tag and parses as
`Part` — the digit ends the name the same way `800-53` ends `NIST`. A tag
that matches and resolves to a framework called *Part* is worse than one
that does not match, because it looks like success.

The third assertion is the ripple that nearly shipped: renaming a catalog
silently re-opened `NOT_CROSSWALK_ANCHORABLE`, which is keyed on the
declared name. The guard stopped firing and `crosswalk seed` wrote the 76
rows it exists to refuse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.content.tags import source_tags
from policyforge.crosswalk.overlay import NOT_CROSSWALK_ANCHORABLE, _refusal_reason
from policyforge.mapping.crosswalk import normalize_framework
from policyforge.topics.satisfies import split_citation

CATALOGS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


def _bundled() -> list[tuple[str, str]]:
    """(directory, declared framework name) for every bundled catalog.

    Derived from the tree rather than listed, so a catalog added later is
    covered by construction instead of by someone remembering this file.
    """
    found = []
    for directory in sorted(CATALOGS.iterdir()):
        controls = directory / "controls.json"
        if not controls.exists():
            continue
        entries = json.loads(controls.read_text(encoding="utf-8"))
        declared = {c.get("framework", "") for c in entries}
        assert len(declared) == 1, f"{directory.name} declares {declared}"
        found.append((directory.name, declared.pop()))
    assert found, "no bundled catalogs found — the population is empty"
    return found


@pytest.mark.parametrize("directory, declared", _bundled(), ids=lambda v: v)
def test_the_declared_name_is_a_legal_tag(directory: str, declared: str):
    """A name beginning with a digit is prose, not a citation."""
    tag = f"[{declared} X-1]"

    assert source_tags(tag) == [tag], (
        f"{directory} declares {declared!r}, which is not a legal source tag, so no "
        f"document can cite this catalog. A framework name must begin with a capital "
        f"letter. FRAMEWORK_ALIASES cannot fix this — it runs after the pattern."
    )


@pytest.mark.parametrize("directory, declared", _bundled(), ids=lambda v: v)
def test_a_tag_naming_the_catalog_splits_back_to_it(directory: str, declared: str):
    """The name has to survive the split that resolution actually uses.

    `split_citation` is the path — it is given every declared name and
    matches longest-first, which is why `NIST 800-53` splits whole even
    though a digit-initial word would otherwise end a name. A name that
    does not come back is one a citation cannot reach.

    Note this is NOT `framework_name`, which returns `NIST` for
    `NIST 800-53` and has no caller in `src/`. Asserting against it would
    fail on two correct catalogs and pass on nothing useful.
    """
    known = [name for _, name in _bundled()]

    framework, requirement, _ = split_citation(f"{declared} X-1", known)

    assert framework == declared, (
        f"{directory} declares {declared!r} but a citation naming it splits as "
        f"{framework!r} with id {requirement!r}, so it would resolve against a "
        f"framework this repository does not have."
    )


#: Catalogs whose declared name begins with a generic word, so the
#: first-word fallback would hand the next catalog an easy collision:
#: `Information Sharing`, `Substance Abuse`. **Enumerated on purpose.**
#: Whether a first word is generic is a judgement — `Information` is,
#: `FedRAMP` is not — and a judgement is a list with reasons rather than
#: something to compute. The *population* above is derived from the tree;
#: the *decisions* here are written down. Those are different, and
#: treating enumeration as always wrong is what produced the substring
#: assertion this replaces: it passed vacuously for
#: `information` ⊂ `cfr-171-information-blocking`, so **either pin could
#: be deleted with the whole suite green**.
_MUST_BE_PINNED = {
    "Information Blocking": "cfr-171-information-blocking",
    "Substance Use Disorder Records": "cfr-42-part-2-sud-records",
}


@pytest.mark.parametrize("declared, expected", sorted(_MUST_BE_PINNED.items()))
def test_a_generic_first_word_is_pinned_to_its_catalog(declared: str, expected: str):
    """Renaming for citability must not re-create the keying collision.

    `Information Blocking` keys to `information` and
    `Substance Use Disorder Records` to `substance` without an alias —
    the same first-word fallback that merged every NIST catalog into one
    bucket. The rename fixes the citation and leaves that mechanism
    intact, aimed at new words.
    """
    assert normalize_framework(declared) == expected, (
        f"{declared!r} keys to {normalize_framework(declared)!r} — the first-word "
        f"fallback. Pin it in FRAMEWORK_ALIASES as {expected!r}, or the next catalog "
        f"whose name starts with that word merges into this one."
    )


def test_no_two_bundled_catalogs_share_a_key():
    """Derived from the tree, so a catalog added later is covered.

    This is the collision itself rather than a proxy for it. It does not
    subsume the pins above: dropping only the two new aliases leaves
    `information` and `substance` distinct from everything else, because
    no bundled catalog starts with those words *today*. The harm lives in
    the fallback, and nothing in the present population exercises it.
    """
    keys: dict[str, str] = {}
    for directory, declared in _bundled():
        key = normalize_framework(declared)
        assert key not in keys, (
            f"{directory} ({declared!r}) and {keys[key]} both key to {key!r}, so their "
            f"requirement ids share one bucket and a citation can cross between them."
        )
        keys[key] = directory


def test_renaming_a_catalog_does_not_silently_unrefuse_its_crosswalk():
    """`NOT_CROSSWALK_ANCHORABLE` is keyed on the declared name.

    Renaming 45 CFR 171 to make it citable stopped this guard matching,
    and `crosswalk seed` wrote the 76 rows it exists to refuse — a
    deliberate refusal re-opened by a change nowhere near it.
    """
    refused = set(NOT_CROSSWALK_ANCHORABLE)
    declared = {name for _, name in _bundled()}

    reachable = {name for name in declared if _refusal_reason(name) is not None}

    assert reachable, (
        f"no bundled catalog's declared name is in NOT_CROSSWALK_ANCHORABLE, whose "
        f"keys are {sorted(refused)}. If a catalog was renamed, the guard stopped "
        f"firing and `crosswalk seed` will write the overlay it exists to refuse."
    )
