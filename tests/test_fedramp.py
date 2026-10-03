"""FedRAMP's control tailoring, and the things it is not.

The fixtures here are invented, but their *shape* is copied exactly from
`fedramp-consolidated-rules.json`: the zero-padded dash-separated control
keys, the `parameters`/`guidance` split, the `varies_by_class` block keyed
by certification class. FedRAMP's dataset is public domain and could have
been vendored wholesale; small hand-built fixtures are used anyway because
a fixture written to exercise the awkward cases — a control tailored only
through its enhancements, a parameter that resolves two ways by class —
exercises them reliably, where a slice of the real file would only do so by
luck.

The last two tests are different in kind: they assert against the committed
catalog rather than a fixture, because the claim "this catalog carries no
baseline" is a property of the file that ships, and the way it would break
is somebody regenerating it from a future dataset that does carry one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.ingest import fedramp
from policyforge.ingest.schema import Control, ControlEnhancement, load_controls

CATALOG = Path("data/frameworks/fedramp/controls.json")


@pytest.fixture
def nist_catalog() -> list[Control]:
    return [
        Control(
            control_id="AC-6",
            title="Least Privilege",
            framework="NIST-800-53",
            framework_version="Rev 5",
            family="Access Control",
            family_abbr="AC",
            control_statement="Employ the principle of least privilege.",
            enhancements=[
                ControlEnhancement(
                    enhancement_id="AC-6(1)",
                    title="Authorize Access to Security Functions",
                    baseline="Moderate, High",
                    description="Authorize access for [Assignment: ...].",
                ),
            ],
        ),
        Control(
            control_id="AC-20",
            title="Use of External Systems",
            framework="NIST-800-53",
            framework_version="Rev 5",
            family="Access Control",
            family_abbr="AC",
            control_statement="Establish terms and conditions.",
        ),
    ]


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("AC-20", ("AC-20", "")),
        ("AC-06-01", ("AC-6", "AC-6(1)")),
        ("SA-09-05", ("SA-9", "SA-9(5)")),
        ("IA-05", ("IA-5", "")),
    ],
)
def test_control_keys_normalize_to_the_oscal_spelling(key, expected):
    """FedRAMP writes `AC-06-01`; everything else here writes `AC-6(1)`.

    The identifier is the join key between a profile and the catalog it
    profiles, so a catalog left in the source's own spelling would crosswalk
    to nothing at all.
    """
    assert fedramp.normalize_control_id(key) == expected


def test_an_unrecognized_key_is_kept_rather_than_dropped():
    """A control missing from a scope report is worse than an odd id."""
    assert fedramp.normalize_control_id("AC-6 REVISED") == ("AC-6 REVISED", "")


# --------------------------------------------------------------------------
# Tailoring
# --------------------------------------------------------------------------


def test_parameters_and_guidance_land_on_the_enhancement_that_earned_them(nist_catalog):
    rules = {
        "info": {"version": "2026.09.13.02"},
        "CTL": {
            "AC": {
                "AC-06-01": {
                    "parameters": [
                        {"parameterId": "ac-06.01_odp.02", "value": "all functions"},
                    ],
                },
                "AC-20": {"guidance": ["AC-20 describes system access."]},
            }
        },
    }
    controls, summary = fedramp.parse_fedramp_rules(rules, nist_catalog)
    by_id = {c.control_id: c for c in controls}

    enhancement = by_id["AC-6"].enhancements[0]
    assert enhancement.parameter_values == {"ac-06.01_odp.02": "all functions"}
    assert by_id["AC-20"].additional_requirements == "AC-20 describes system access."
    assert summary.tailored == 2


def test_a_parent_exists_to_hold_a_tailored_enhancement_but_claims_no_tailoring(nist_catalog):
    """FedRAMP says nothing about AC-6 while saying something about AC-6(1).

    `Control.enhancements` is the only container the schema has, so the
    parent has to exist. What it must not do is look tailored: attributing
    the enhancement's parameter values to its parent would put FedRAMP's
    name on a decision FedRAMP did not make.
    """
    rules = {
        "info": {"version": "x"},
        "CTL": {"AC": {"AC-06-01": {"guidance": ["Only the enhancement."]}}},
    }
    controls, _ = fedramp.parse_fedramp_rules(rules, nist_catalog)

    parent = controls[0]
    assert parent.control_id == "AC-6"
    assert parent.parameter_values == {}
    assert parent.additional_requirements == ""
    assert parent.title == "Least Privilege"  # still carries the NIST text
    assert parent.enhancements[0].additional_requirements == "Only the enhancement."


def test_guidance_is_labelled_by_certification_class(nist_catalog):
    rules = {
        "info": {"version": "x"},
        "CTL": {
            "AC": {
                "AC-20": {
                    "guidance": ["Applies throughout."],
                    "varies_by_class": {
                        "b": {"guidance": ["Level 1."]},
                        "d": {"guidance": ["Level 3."]},
                    },
                }
            }
        },
    }
    controls, summary = fedramp.parse_fedramp_rules(rules, nist_catalog)
    text = controls[0].additional_requirements

    assert text.startswith("Applies throughout.")
    assert "Certification Class B:\nLevel 1." in text
    assert "Certification Class D:\nLevel 3." in text
    assert text.index("Class B") < text.index("Class D"), "classes read in assurance order"
    assert summary.varies_by_class == ["AC-20"]


def test_classes_that_decided_a_parameter_the_same_way_are_named_together(nist_catalog):
    """SA-9(5)'s real shape: two classes, the same value, one of them extra.

    Printing an identical sentence twice makes a reader compare two lines to
    discover there is no difference, so agreeing classes share a label — but
    a value only one class decided still says which one.
    """
    rules = {
        "info": {"version": "x"},
        "CTL": {
            "AC": {
                "AC-20": {
                    "varies_by_class": {
                        "c": {"parameters": [{"parameterId": "odp.01", "value": "same"}]},
                        "d": {
                            "parameters": [
                                {"parameterId": "odp.01", "value": "same"},
                                {"parameterId": "odp.02", "value": "only D"},
                            ]
                        },
                    }
                }
            }
        },
    }
    controls, _ = fedramp.parse_fedramp_rules(rules, nist_catalog)
    values = controls[0].parameter_values

    assert values["odp.01"] == "Certification Classes C, D: same"
    assert values["odp.02"] == "Certification Class D: only D"


def test_a_control_absent_from_the_catalog_is_reported_and_not_crosswalked(nist_catalog):
    """Identity is the obvious mapping and it is still an assertion.

    A mapping onto a control nobody checked exists reaches an assessor as a
    citation, so an unresolvable id gets no crosswalk and a line in the
    summary instead.
    """
    rules = {"info": {"version": "x"}, "CTL": {"ZZ": {"ZZ-01": {"guidance": ["New."]}}}}
    controls, summary = fedramp.parse_fedramp_rules(rules, nist_catalog)

    assert summary.unresolved == ["ZZ-1"]
    assert controls[0].source_crosswalk == {}


def test_a_resolved_control_is_anchored_on_its_800_53_equivalent(nist_catalog):
    rules = {"info": {"version": "x"}, "CTL": {"AC": {"AC-06-01": {"guidance": ["g"]}}}}
    controls, _ = fedramp.parse_fedramp_rules(rules, nist_catalog)

    assert controls[0].source_crosswalk == {fedramp.NIST_SOURCE: "AC-6"}
    assert controls[0].enhancements[0].source_crosswalk == {fedramp.NIST_SOURCE: "AC-6(1)"}


# --------------------------------------------------------------------------
# The committed catalog
# --------------------------------------------------------------------------


@pytest.mark.skipif(not CATALOG.exists(), reason="FedRAMP catalog not built")
def test_the_committed_catalog_asserts_no_baseline():
    """The one claim this catalog must never quietly stop making.

    FedRAMP's machine-readable Low/Moderate/High selection was withdrawn
    with `GSA/fedramp-automation`. Until an official replacement exists,
    every control here has to say "unknown" rather than name a baseline —
    `ssp --baseline` and `coverage` both read this field, and a fabricated
    value would misstate somebody's scope with no visible symptom.
    """
    controls = load_controls(CATALOG)
    assert controls, "catalog is empty"
    assert all(not c.baseline for c in controls)
    assert all(not e.baseline for c in controls for e in c.enhancements)


@pytest.mark.skipif(not CATALOG.exists(), reason="FedRAMP catalog not built")
def test_the_committed_catalog_is_anchored_on_800_53():
    """Every control carries the crosswalk that makes it reachable by `map`."""
    raw = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert all(c["source_crosswalk"] == {fedramp.NIST_SOURCE: c["control_id"]} for c in raw)


def test_every_declared_rule_is_tailored_or_not_a_mapping():
    """**External extent: the question no other test here asks.**

    Every assertion around this one asks whether a tailored control is
    right. None asks whether they are *all here*, and a parse that drops
    entries returns a **shorter** tailoring — internally consistent,
    every entry well-formed, missing controls FedRAMP requires.

    FedRAMP publishes no machine-readable baseline any more, so this
    dataset is the whole of what the catalog can know. There is nothing
    else to notice the shortfall against.

    `unparsed` and `unresolved` are deliberately absent from the
    invariant: they are **annotations**, not exclusions. An entry with an
    odd key or a control missing from 800-53 is still emitted and still
    counted as tailored. Reading them as exclusions gives an invariant
    that is wrong in a way the published dataset cannot reveal, because
    both lists are empty against it.
    """
    nist = [
        Control(
            control_id="AC-6",
            title="Least privilege",
            framework="NIST 800-53",
            framework_version="Rev 5",
        )
    ]
    rules = {
        "info": {"version": "x"},
        "CTL": {"AC": {"AC-06-01": {"guidance": ["g"]}, "AC-06-02": {"guidance": ["h"]}}},
    }

    _, summary = fedramp.parse_fedramp_rules(rules, nist)

    assert summary.tailored == 2


def test_an_entry_that_is_not_a_mapping_is_counted_rather_than_dropped():
    """A non-dict entry is the one thing skipped, and it was skipped
    **uncounted** — so a dataset that turned half its entries into
    strings would parse short and say nothing. Now it is accounted for
    and the invariant still closes."""
    nist = [
        Control(
            control_id="AC-6",
            title="Least privilege",
            framework="NIST 800-53",
            framework_version="Rev 5",
        )
    ]
    rules = {
        "info": {"version": "x"},
        "CTL": {"AC": {"AC-06-01": {"guidance": ["g"]}, "AC-06-02": "not a mapping"}},
    }

    controls, summary = fedramp.parse_fedramp_rules(rules, nist)

    assert summary.tailored == 1
    assert controls, "the well-formed entry must still be emitted"


def test_a_rule_neither_tailored_nor_rejected_raises():
    """The guard's contract, asserted directly.

    No current input reaches it — the loop consumes every entry — so this
    is a guard against a future shape change rather than a live defect,
    which is worth saying because "no test exercises it end to end" and
    "it cannot happen" are different claims.
    """
    summary = fedramp.Summary()
    summary.tailored = 5

    with pytest.raises(ValueError, match=r"unaccounted for"):
        fedramp._require_every_rule(summary, declared=9, not_a_mapping=1)

    # And it must ALLOW a parse that reconciles, or it refuses everything.
    fedramp._require_every_rule(summary, declared=6, not_a_mapping=1)


def test_the_extent_guard_is_actually_called(monkeypatch):
    """**A correct guard nobody calls is not a guard.**

    The test above proves the contract and passes with the call site
    deleted — I confirmed that by deleting it. This is the third loader
    where the same gap appeared, and the lesson from the first two is
    that "the function is right" and "the function runs" want separate
    tests.
    """
    called: list[tuple[int, int]] = []
    real = fedramp._require_every_rule

    def spy(summary, *, declared, not_a_mapping):
        called.append((declared, not_a_mapping))
        return real(summary, declared=declared, not_a_mapping=not_a_mapping)

    monkeypatch.setattr(fedramp, "_require_every_rule", spy)
    nist = [
        Control(
            control_id="AC-6",
            title="Least privilege",
            framework="NIST 800-53",
            framework_version="Rev 5",
        )
    ]
    fedramp.parse_fedramp_rules(
        {"info": {"version": "x"}, "CTL": {"AC": {"AC-06-01": {"guidance": ["g"]}}}}, nist
    )

    assert called, "parse_fedramp_rules returned without checking extent"
