"""An invented paragraph is not evidence; a real one keeps resolving (#438).

80's ruling 6235, both halves of one invariant:

1. Below an id the catalog subdivides, a part it doesn't list is reported:
   the catalog's own list says it isn't there. The Privacy and Breach
   catalogs split every paragraph, so `164.502(a)(1)(40)` was counted as
   evidence for `164.502` before this.
2. Below a leaf (an id the catalog doesn't subdivide) a real part resolves:
   the Security Rule stops at `164.316(b)(1)`, whose text holds its (i), and
   ONC stops at criteria such as `170.315(b)(1)`.

Below a leaf, the leaf-text check 6235 also asked for is switched on per
catalog by #441 (`tests/test_leaf_parts.py`). Where it isn't, 42 CFR Part 2
and 171, an invented part below a leaf still resolves, and the last test
pins that residual where it is stated.

Every tag is written by hand from the regulations, against the shipped
catalogs.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.content.tags import is_unresolved_citation
from policyforge.ingest.schema import load_controls
from policyforge.topics.satisfies import catalog_scope, document_evidence

FRAMEWORKS = Path(__file__).resolve().parent.parent / "data" / "frameworks"
CATALOGS = (
    "nist-800-53-r5",
    "hipaa-security-rule",
    "hipaa-privacy-rule",
    "hipaa-breach-notification-rule",
    "cfr-170-315-onc-certification",
    "cfr-42-part-2-sud-records",
    "cfr-171-information-blocking",
)


@pytest.fixture(scope="module")
def run():
    controls = []
    for name in CATALOGS:
        controls += load_controls(FRAMEWORKS / name / "controls.json")
    return SimpleNamespace(controls=controls, scope=catalog_scope(controls))


def _evidence(tag, run):
    document = SimpleNamespace(
        relative_path="standards/x.md", title="X", body=f"## S\n\nDo it. {tag}\n"
    )
    return document_evidence(document, controls=run.controls, crosswalk={})


@pytest.mark.parametrize(
    "tag",
    [
        # The issue's table: a part the Privacy or Breach catalog doesn't list.
        "[HIPAA 164.502(a)(1)(40)]",
        "[HIPAA 164.500(b)(1)(xx)]",
        "[HIPAA 164.404(z)(9)]",
        # Below a paragraph split into its revision-added (A)-(C).
        "[HIPAA 164.502(a)(1)(vi)(Z)]",
        # The other family-rule catalogs, below a paragraph they subdivide.
        "[Substance Use Disorder Records 2.16(zz)(9)]",
        "[Information Blocking 171.100(zz)(9)]",
    ],
)
def test_an_invented_part_below_a_subdivided_paragraph_is_reported(tag, run):
    evidence = _evidence(tag, run)
    assert evidence.cited == []
    assert evidence.unknown == [tag.strip("[]")]
    assert is_unresolved_citation(tag, run.scope)


@pytest.mark.parametrize(
    ("tag", "cited"),
    [
        # Real parts below a leaf: the catalog stops there.
        ("[HIPAA 164.316(b)(1)(i)]", ("hipaa", "164.316(b)(1)")),
        ("[HIPAA 164.316(b)(1)(ii)]", ("hipaa", "164.316(b)(1)")),
        (
            "[ONC Certification Criteria 170.315(b)(1)(iii)]",
            ("cfr-170-315-onc-certification", "170.315(b)(1)"),
        ),
        # Carried ids.
        ("[HIPAA 164.502(b)]", ("hipaa-privacy", "164.502(b)")),
        ("[HIPAA 164.308(a)(1)(ii)(A)]", ("hipaa", "164.308(a)(1)(ii)(A)")),
    ],
)
def test_a_real_part_or_a_carried_id_still_resolves(tag, cited, run):
    evidence = _evidence(tag, run)
    assert cited in {c.key for c in evidence.cited}
    assert evidence.unknown == []
    assert not is_unresolved_citation(tag, run.scope)


def test_a_standard_cited_by_its_paragraph_still_stands_for_its_specifications(run):
    """80's ruling on #423, unchanged: the catalog carries `164.308(a)(1)(i)`
    and `(ii)(A)`-`(D)` beneath `164.308(a)(1)`, so it stands for them."""
    catalog, standing = run.scope.standing_for("HIPAA", "164.308(a)(1)")
    assert catalog == "hipaa"
    assert standing
    assert not is_unresolved_citation("[HIPAA 164.308(a)(1)]", run.scope)


def test_the_stated_residual_an_invented_part_below_a_leaf_still_resolves(run):
    """Where a catalog's manifest doesn't say `leaf_parts: listed` (#441),
    nothing tells `(99)` from a real part below one of its leaves. Pinned so
    a change to it is a decision, not a drift; ONC's `(99)` is reported now."""
    assert run.scope.cites("Substance Use Disorder Records", "2.19(a)(99)")
    assert not run.scope.cites("ONC Certification Criteria", "170.315(a)(1)(99)")
