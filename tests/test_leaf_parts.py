"""A part cited below a catalog leaf stands only when the leaf's text lists it,
in the catalogs whose manifests say so (#441; 80's 6235 rule 2 on #438).

Every tag and text here is written by hand from the regulations or read from
the shipped catalogs and the committed eCFR fixtures, never from the check.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.frameworks.registry import declared_leaf_checks
from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule
from policyforge.ingest.schema import load_controls
from policyforge.topics.anchoring import lists_part
from policyforge.topics.satisfies import catalog_scope

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
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
def scope():
    controls = [c for d in CATALOGS for c in load_controls(FRAMEWORKS / d / "controls.json")]
    return catalog_scope(controls)


# ---- the check, on texts written out by hand ---------------------------------------

PLAN = "(14) Device list. (i) Record. (ii) Parse the following: (A) The first. (B) The second."
DEEP = (
    "(iv) Sources.\n(A) Evidence:\n(1) Citation;\n(B) Predictive:\n(1) Details:\n"
    "(i) One;\n(ii) Two;\n(iii) Three;\n(iv) Four;\n(v) Five;\n(2) Update.\n(v) Next."
)


@pytest.mark.parametrize(
    ("leaf", "text", "part", "listed"),
    [
        # A list item, at the start of the text, after a clause or a line break.
        ("x(b)(1)", "(i) Maintain the policies; and (ii) If an action", "(i)", True),
        ("x(b)(1)", "(i) Maintain the policies; and (ii) If an action", "(ii)", True),
        ("x(b)(1)", "(i) Maintain the policies; and (ii) If an action", "(iii)", False),
        ("x(a)", "Lead in.\n(1) One.\n(2) Two.", "(2)", True),
        # After a dash, a `]`, a closing quote.
        ("x(a)", "must\u2014 (A) Comply; (B) Report.", "(B)", True),
        ("x(b)", "Prescribing. (i) [Reserved] (ii) For technology", "(ii)", True),
        ("x(b)", "kept until [date];\u201d and (B) Held under", "(B)", True),
        # A chain: `(A)(1)`, and `(2)` inside the `(A)`.
        ("x(a)", "(a) Lead. (A)(1) The data. (2) Next.", "(A)(1)", True),
        ("x(a)", "(a) Lead. (A)(1) The data. (2) Next.", "(A)(2)", True),
        # References are not list items.
        ("x(a)", "as in paragraph (b) of this section.", "(b)", False),
        ("x(a)", "described in paragraphs (a)(5)(iii)(A)(1) or (2) of this section.", "(2)", False),
        ("x(a)", "described in paragraphs (a)(5)(iii)(A)(1) or (2) of this section.", "(5)", False),
        # The leaf's own label is not one of its parts.
        (
            "170.315(a)(1)",
            "(1) Computerized provider order entry. (i) Enable a user.",
            "(1)",
            False,
        ),
        ("170.315(a)(1)", "(1) Computerized provider order entry. (i) Enable a user.", "(i)", True),
        # Deeper items are parts of the item they sit in, not of the leaf.
        ("170.315(a)(14)", PLAN, "(A)", False),
        ("170.315(a)(14)", PLAN, "(ii)(A)", True),
        ("170.315(a)(14)", PLAN, "(ii)(B)", True),
        ("170.315(a)(14)", PLAN, "(i)(A)", False),
        # A kind recurs deeper: the (v) under (7) is not (iv)'s next item, and
        # a (1) under (B) is not the (1) under (A) (9b on PR 455).
        ("x(b)", DEEP, "(iv)(B)(2)", True),
        ("x(b)", DEEP, "(iv)(B)(1)(v)", True),
        ("x(b)", DEEP, "(v)", True),
        ("x(b)", DEEP, "(iv)(B)(3)", False),
        ("x(b)", DEEP, "(iv)(A)(2)", False),
        # A marker that is neither the next item nor a first one opens no list.
        ("x(a)", "(i) One. (iii) Three.", "(i)(iii)", False),
        # A list of references after a clause is not a list.
        (
            "x(b)",
            "Not for (b)(6); (b)(7)(iii), (iv), and (v); (b)(9) of this section.",
            "(b)",
            False,
        ),
        # A text that lists nothing lists no part.
        ("x(a)", "A covered entity must comply.", "(1)", False),
        ("x(a)", "(i) One.", "", False),
    ],
)
def test_a_part_is_listed_only_as_a_list_item_of_the_leaf(leaf, text, part, listed):
    assert lists_part(leaf, text, part) is listed


# ---- the shipped catalogs -----------------------------------------------------------


@pytest.mark.parametrize(
    ("framework", "requirement"),
    [
        # 441's rows, and ONC's from 438: none of these paragraphs exists.
        ("HIPAA", "164.410(b)(9)"),
        ("HIPAA", "164.530(i)(1)(iv)"),
        ("HIPAA", "164.530(j)(2)(ix)"),
        ("HIPAA", "164.312(a)(2)(iv)(1)"),
        ("HIPAA", "164.308(a)(1)(ii)(A)(1)"),
        ("HIPAA", "164.316(b)(1)(iii)"),
        # Below the folded (b)(1): its text, (b)'s, lists no (i); (b)(3) is unlisted.
        ("HIPAA", "164.502(b)(1)(i)"),
        ("HIPAA", "164.502(b)(3)"),
        # Not a first item in either reading (b5 on PR 455).
        ("HIPAA", "164.502(a)(1)(a)"),
        ("HIPAA", "164.404(i)"),
        ("HIPAA", "164.500(i)"),
        ("ONC Certification Criteria", "170.315(a)(1)(99)"),
        ("ONC Certification Criteria", "170.315(b)(1)(A)"),
        ("ONC Certification Criteria", "170.315(b)(11)(b)"),  # 9b on PR 455
        ("ONC Certification Criteria", "170.315(b)(11)(iv)(A)(14)"),
        ("ONC Certification Criteria", "170.315(b)(1)(iii)(A)(9)"),
    ],
)
def test_an_invented_part_below_a_leaf_is_reported(scope, framework, requirement):
    assert not scope.cites(framework, requirement)


@pytest.mark.parametrize(
    ("framework", "requirement"),
    [
        # Real, and below a leaf: the leaf's text lists each.
        ("HIPAA", "164.316(b)(1)(i)"),
        ("HIPAA", "164.316(b)(1)(ii)"),  # its "(ii)" was lost at ingest before 441
        ("HIPAA", "164.318(a)(2)"),
        ("HIPAA", "164.404(d)(1)(i)"),
        ("HIPAA", "164.404(d)(1)(ii)"),
        ("ONC Certification Criteria", "170.315(b)(1)(iii)"),
        ("ONC Certification Criteria", "170.315(b)(1)(i)(A)"),
        ("ONC Certification Criteria", "170.315(b)(1)(ii)(A)(1)"),
        ("ONC Certification Criteria", "170.315(b)(1)(iii)(A)(1)"),
        ("ONC Certification Criteria", "170.315(b)(11)(iv)(B)(7)"),
        ("ONC Certification Criteria", "170.315(b)(11)(iv)(B)(9)(ii)"),
        ("ONC Certification Criteria", "170.315(b)(11)(v)"),
        # The minimum-necessary standard: eCFR folds (b)(1) into (b)'s own
        # paragraph, and (b)(2) is an id (1d on PR 455).
        ("HIPAA", "164.502(b)(1)"),
        # Carried, or with a carried id beneath: unchanged by 441.
        ("HIPAA", "164.502(b)"),
        ("HIPAA", "164.308(a)(1)"),
    ],
)
def test_a_real_part_keeps_resolving(scope, framework, requirement):
    assert scope.cites(framework, requirement)


@pytest.mark.parametrize(
    ("framework", "requirement"),
    [("Substance Use Disorder Records", "2.16(i)"), ("Substance Use Disorder Records", "2.19(i)")],
)
def test_the_first_item_rule_invents_nothing_where_the_leaf_check_is_off(
    scope, framework, requirement
):
    """The first-item rule runs in every family-rule catalog, the residual
    ones included: `(i)` as the ninth letter of a letter list is invented."""
    assert not scope.cites(framework, requirement)


def test_the_catalogs_that_keep_the_residual_are_named(scope):
    """42 CFR Part 2 and 171 aren't measured whole (their full source isn't on
    disk), so a part below one of their leaves still stands, and the run
    says which catalogs that is."""
    assert scope.leaf_residual() == ["cfr-171-information-blocking", "cfr-42-part-2-sud-records"]
    assert scope.cites("Substance Use Disorder Records", "2.19(a)(99)")
    assert scope.cites("Information Blocking", "171.103(b)(99)")


def test_satisfies_says_which_catalogs_keep_the_residual(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    docs = tmp_path / "docs" / "standards"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text(
        "---\ntitle: A\ntier: standard\n---\n\n# A\n\nKeep records. [HIPAA 164.316(b)(1)(ii)]\n",
        encoding="utf-8",
    )
    args = ["satisfies", "--all", "--content-dir", str(tmp_path / "docs")]
    for name in ("hipaa-security-rule", "cfr-42-part-2-sud-records"):
        args += ["--controls", str(FRAMEWORKS / name / "controls.json")]
    result = CliRunner().invoke(cli_mod.cli, args)
    assert result.exit_code == 0, result.output
    assert "an invented one still counts: cfr-42-part-2-sud-records." in result.output


# ---- the manifest setting ------------------------------------------------------------


def _catalog(root: Path, body: str) -> None:
    directory = root / "demo"
    directory.mkdir(parents=True)
    (directory / "framework.yaml").write_text(
        "id: demo\nframework_id: demo\nlicence: public-domain\nfamily: section\n" + body,
        encoding="utf-8",
    )
    (directory / "controls.json").write_text("[]", encoding="utf-8")


def test_a_switch_with_a_measurement_is_read(tmp_path):
    _catalog(tmp_path, "leaf_parts: listed\nleaf_parts_measured: 12 of 12 leaves, 0 red\n")
    assert sorted(declared_leaf_checks(roots=[tmp_path])) == ["demo"]


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ("leaf_parts: listd\nleaf_parts_measured: x\n", "is not one of listed"),
        ("leaf_parts: listed\n", "has no leaf_parts_measured"),
    ],
)
def test_a_misspelled_or_unmeasured_switch_is_refused(tmp_path, body, said):
    _catalog(tmp_path, body)
    with pytest.raises(ValueError, match=said):
        declared_leaf_checks(roots=[tmp_path])


def test_every_shipped_switch_says_what_it_rests_on():
    on = declared_leaf_checks(roots=[FRAMEWORKS])
    assert sorted({f.id for f in on.values()}) == [
        "cfr-170-315-onc-certification",
        "hipaa-breach-notification-rule",
        "hipaa-privacy-rule",
        "hipaa-security-rule",
    ]
    for framework in on.values():
        assert "937b6df5" in framework.leaf_parts_measured
        assert "0 turned red" in framework.leaf_parts_measured


# ---- the Security Rule's loader -----------------------------------------------------


def test_a_continuation_paragraph_keeps_its_marker():
    """`(ii) If an action ...` is its own <P> in eCFR, appended to the open
    item. The loader dropped its marker, so the text read as one item."""
    xml = (ROOT / "tests" / "fixtures" / "ecfr_45cfr164_subpart_c.xml").read_text(encoding="utf-8")
    text = {c.control_id: c.control_statement for c in parse_hipaa_security_rule(xml)}
    assert "electronic) form; and (ii) If an action, activity" in text["164.316(b)(1)"]
    assert "April 20, 2005. (2) A small health plan" in text["164.318(a)"]


def test_the_shipped_security_catalog_is_the_fixed_loaders_output():
    xml = (ROOT / "tests" / "fixtures" / "ecfr_45cfr164_subpart_c.xml").read_text(encoding="utf-8")
    parsed = {c.control_id: c.control_statement for c in parse_hipaa_security_rule(xml)}
    shipped = {
        c.control_id: c.control_statement
        for c in load_controls(FRAMEWORKS / "hipaa-security-rule" / "controls.json")
    }
    assert parsed == shipped


# ---- a first item folded into its parent's paragraph (1d on PR 455) ----------------


@pytest.mark.parametrize(
    ("ids", "cited", "stands"),
    [
        ({"x(b)", "x(b)(2)", "x(b)(3)"}, "x(b)(1)", True),
        ({"x(b)", "x(b)(ii)"}, "x(b)(i)", True),
        ({"x(b)", "x(b)(B)"}, "x(b)(A)", True),
        ({"x", "x(b)", "x(c)"}, "x(a)", True),
        # Not the first item, or not its siblings' kind: still invented.
        ({"x(b)", "x(b)(2)", "x(b)(3)"}, "x(b)(4)", False),
        ({"x(b)", "x(b)(2)"}, "x(b)(a)", False),
        ({"x(b)", "x(b)(2)"}, "x(b)(A)", False),
        # By the item, not by kind: (i) is a letter too (b5 on PR 455).
        ({"x(a)", "x(a)(ii)"}, "x(a)(a)", False),
        ({"x", "x(a)", "x(b)"}, "x(i)", False),
        ({"x(b)", "x(b)(3)"}, "x(b)(1)", False),
    ],
)
def test_a_lists_first_item_folded_into_its_parent_stands(ids, cited, stands):
    from policyforge.topics.anchoring import stands_below

    assert stands_below(ids, cited) is stands
