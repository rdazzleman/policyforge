"""Two meanings of "anchor", kept apart on purpose.

    NIST_ANCHOR     the CROSSWALK anchor - what every other framework's
                    requirements are mapped ONTO. Singular.
    TOPIC_ANCHORS   which catalogs a TOPIC may claim identifiers from.
                    A set.

They were one constant until 2026-09-20. **The reason to split them is
that the obvious way to let topics anchor a second catalog — grep
`NIST_ANCHOR`, widen it — also widens the crosswalk-anchor sites**, and a
catalog that becomes a crosswalk target becomes one `crosswalk seed` will
generate a mapping for.

For the NIST AI RMF that mapping is refused on product grounds: it would
assert an 800-53 control *achieves* an AI RMF outcome, the claim NIST
declined to make when it split the Playbook out. So the natural refactor
publishes the thing the project decided not to publish, while looking
exactly like the change that was asked for.

These tests make that impossible rather than discouraged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.mapping.crosswalk import (
    NIST_ANCHOR,
    TOPIC_ANCHORS,
    anchors_a_topic,
    build_crosswalk,
    normalize_framework,
)

ROOT = Path(__file__).resolve().parent.parent

#: **The assignment, enumerated so a reviewer diffs rather than re-derives.**
#: Every module that referenced the single constant was consciously placed
#: on one side. A file in the B list must not mention `NIST_ANCHOR` at all;
#: that is what `test_no_b_site_still_reads_the_crosswalk_anchor` holds.
B_SITE_FILES = (
    "cli/documents.py",
    "cli/programme.py",
    "synthesis/merge.py",
    "zardoz/skills.py",
)

A_SITE_FILES = (
    "crosswalk/candidates.py",
    "crosswalk/overlay.py",
    "ingest/hipaa_crosswalk_loader.py",
    "mapping/crosswalk.py",
)

#: **The one file guarded function by function (#335).** Every other file on
#: either list stays per-file. `topics/satisfies.py` holds both sides: its
#: crosswalk traversal (`document_evidence`, `nist_anchors`) maps citations
#: ONTO 800-53, an A question, and whether a citation answers for a topic's
#: anchor is a B question. Per-file, the B side could only be written as a
#: hard-coded framework list or hidden behind an import from elsewhere, and
#: the second passes this guard by hiding the name. So the unit here is the
#: function: the population is derived with `ast` (nested included), every
#: function is classified with a reason, and an unclassified or stale entry
#: fails by name, so a new function lands as unclassified, never unguarded.
#: Module-level code keeps the A rule.
MIXED_SITE = "topics/satisfies.py"
A, B, NEITHER = "A", "B", "neither"
MIXED_SITE_FUNCTIONS = {
    "Citation.key": (NEITHER, "builds a (framework, id) key; no anchor"),
    "Reached.in_part": (NEITHER, "reads a relationship name"),
    "Reached.reviewed": (NEITHER, "reads a provenance label"),
    "DocumentEvidence.unreviewed": (NEITHER, "filters reached rows by provenance"),
    "DocumentEvidence.nist_anchors": (A, "the cited ids the crosswalk traversal starts from"),
    "DocumentEvidence.occurrences": (NEITHER, "counts citation instances"),
    "DocumentEvidence.distinct": (NEITHER, "counts distinct citations"),
    "resolve_framework": (NEITHER, "maps a written framework name to a loaded catalog"),
    "split_citation": (NEITHER, "parses one tag part"),
    "parse_citations": (NEITHER, "finds tags in a body"),
    "_catalog_index": (NEITHER, "indexes catalog ids by framework"),
    "document_evidence": (A, "traverses the crosswalk from the cited 800-53 ids"),
    "_answers_for_an_anchor": (B, "whether a citation can answer for a topic anchor"),
    "build_report": (A, "hands the crosswalk to document_evidence; asks B only via the above"),
    "_provenance_of": (NEITHER, "labels an overlay row"),
    "_declared_relationships": (NEITHER, "reads each catalog's declared relationship (#408)"),
    "_declared_family_links": (NEITHER, "reads each catalog's links to whole families (#448)"),
    "as_records": (A, "reports the crosswalk traversal's anchors"),
    "_by_framework": (NEITHER, "groups rows for display"),
    "format_report": (A, "reports the crosswalk traversal's anchors"),
}
#: Forbidden per side, as names, the same textual reading as the per-file
#: guards. B is also kept off the traversal itself, not only the constant.
TOPIC_ANCHOR_NAMES = ("TOPIC_ANCHORS", "anchors_a_topic")
CROSSWALK_ANCHOR_NAMES = ("NIST_ANCHOR", "crosswalk", "nist_anchors")

#: **`synthesis/merge.py` moved from A to B on 2026-09-20, and the reason it
#: was on the wrong side is the more useful finding: the A/B boundary is not
#: file-shaped.**
#:
#: `build_synthesis_topic` resolves a topic's anchors to controls, which is a
#: B question. It also expands each one through the crosswalk, which is an A
#: concept -- but it does that via `crosswalk.get(nist_id)`, **without ever
#: naming `NIST_ANCHOR`**. So one function did both, the file-level guard
#: could not see it, and the anchor resolution stayed hardcoded to 800-53.
#:
#: The cost was not theoretical: every AI topic resolved to ZERO controls
#: while `/coverage` reported those same topics owning 91 requirements. Two
#: views of one registry disagreeing, with only one of them user-facing.
#:
#: Found by policyforge-80, who had reviewed and approved the enumeration --
#: **it was checkable, and checking the files is not the same as checking
#: the concepts.** The residual this leaves is named rather than closed: a
#: file can still consume the crosswalk without naming the constant, so
#: `test_ai_topics_resolve_to_their_own_controls` guards the behaviour
#: directly rather than relying on the lists below.

#: `mapping/crosswalk.py` is excluded from the "must not mention the topic
#: anchor" guard for one stated reason: **it is where both constants are
#: defined.** It necessarily names them.
#:
#: Written as a visible exclusion rather than by quietly dropping it from
#: the tuple, because a guard that states its own reach can be wrong out
#: loud. The residual it leaves: a genuine A-side *misuse* inside the
#: definition module would not be caught here. `_is_nist` there is the one
#: consumer, and `test_the_crosswalk_offers_only_the_anchor_as_a_target`
#: covers it behaviourally.
DEFINITION_SITE = "mapping/crosswalk.py"
A_SITE_CONSUMERS = tuple(f for f in A_SITE_FILES if f != DEFINITION_SITE)


def _code_identifiers(relative: str) -> set[str]:
    """Names appearing in a file's EXECUTABLE source, comments and
    docstrings excluded.

    **The guards below read code, not prose, and the difference is not
    pedantic.** Their first version matched raw text, so explaining the
    A/B distinction *in a comment* made the file fail its own guard: a
    check that cannot tell `NIST_ANCHOR` the identifier from `NIST_ANCHOR`
    the word punishes writing down why the rule exists. A rule that
    penalises its own explanation will lose to the explanation being
    deleted.

    Tokenising rather than regexing, and taking NAME tokens only, is the
    same move policyforge-9b used to establish that #166's loader change
    was comments-only — compare what runs, not what is written.
    """
    source = (ROOT / "src" / "policyforge" / relative).read_text(encoding="utf-8")
    return _names_in(source)


def _names_in(source: str) -> set[str]:
    """NAME tokens in `source`: code only, comments and strings excluded."""
    return set(_name_counts(source))


def _name_counts(source: str):
    import io
    import tokenize
    from collections import Counter

    return Counter(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.NAME
    )


def _mixed_site_parts() -> tuple[dict[str, set[str]], set[str]]:
    """Each function's code names by qualified name, and the module-level
    code's names. Derived with `ast`, nested functions and methods included;
    a function's names include any function nested in it, which only makes
    the outer one's check stricter.

    **One subtraction, stated:** each `from a.b.c import x` inside a function
    removes ONE occurrence each of `a`, `b` and `c`, the module path, because
    the B function imports `anchors_a_topic` from `mapping.crosswalk` and the
    path is not a use of the traversal. It is counted, not set-removed, so a
    real `crosswalk` in the same function still counts."""
    import ast
    import textwrap

    source = (ROOT / "src" / "policyforge" / MIXED_SITE).read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    functions: dict[str, set[str]] = {}
    inside: set[int] = set()

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = prefix + child.name
                first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                segment = "".join(lines[first - 1 : child.end_lineno])
                counts = _name_counts(textwrap.dedent(segment))
                for node in ast.walk(child):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        counts.subtract(node.module.split("."))
                functions[qualified] = {name for name, n in counts.items() if n > 0}
                inside.update(range(first, child.end_lineno + 1))
                visit(child, qualified + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            else:
                visit(child, prefix)

    visit(ast.parse(source), "")
    module = "".join(line for n, line in enumerate(lines, 1) if n not in inside)
    return functions, _names_in(module)


def test_exactly_which_catalogs_a_topic_may_anchor():
    """Pinned by exact equality, not by membership.

    **Adding a catalog here grows `/coverage`'s denominator** — its
    requirements become things a topic *could* own and therefore things a
    topic can fail to own. Admitting the AI RMF moved the programme
    headline from 80.3% to 73.7% with the numerator unchanged at 814.
    That is a real report, but it is indistinguishable from lost work if
    it happens without anyone deciding it. So the set is a decision, and
    this is where the decision is recorded.

    Exact equality rather than `in`, because a subset check would let a
    catalog be added silently — which is the case that matters.

    The previous version of this test asserted the set equalled
    `{NIST_ANCHOR}`, which was correct for the commit that split the two
    constants apart and changed no behaviour. It went red when the AI RMF
    was admitted, which is the guard working: a deliberate change to a
    decision should have to be written down twice.
    """
    assert frozenset({NIST_ANCHOR, "nist-ai-rmf"}) == TOPIC_ANCHORS
    assert anchors_a_topic("NIST 800-53") is True
    assert anchors_a_topic("NIST AI RMF") is True
    # Reachable only through the crosswalk, and must stay that way.
    assert anchors_a_topic("HIPAA Security Rule") is False
    assert anchors_a_topic("Information Blocking") is False
    assert anchors_a_topic("NIST 800-171") is False


def test_a_category_claims_its_subcategories():
    """`Govern 1` must claim `Govern 1.1`..`Govern 1.7`.

    "Anchoring a control also claims its enhancements" is the registry's
    documented rule, implemented by `parent_of`. Before the AI RMF
    grammar was added, `parent_of('Govern 1.1')` returned `None`, so a
    topic anchoring the category claimed **nothing** and seven
    subcategories reported orphaned — no error, a plausible number, and a
    topic author would have "fixed" it by anchoring all seven by hand.
    **The workaround looks like diligence**, which is what makes the
    silence expensive.
    """
    from policyforge.topics.anchoring import parent_of

    assert parent_of("Govern 1.1", "NIST AI RMF") == "Govern 1"
    assert parent_of("Manage 4.3", "NIST AI RMF") == "Manage 4"
    assert parent_of("AC-2(1)", "NIST 800-53") == "AC-2"
    # A top-level identifier has no parent, in either grammar.
    assert parent_of("Govern 1", "NIST AI RMF") is None
    assert parent_of("AC-2", "NIST 800-53") is None
    # Catalogs no topic can anchor have no parent a topic could own, even
    # where the id reads like an anchored one's (#377): the Playbook's
    # `Govern 1.1` is NIST's suggestions, not the Core subcategory.
    assert parent_of("Govern 1.1", "NIST AI RMF Playbook") is None
    assert parent_of("AC-2(1)", "FedRAMP") is None
    assert parent_of("03.01.01", "NIST 800-171") is None
    assert parent_of("164.308(a)(1)", "HIPAA Security Rule") is None


def test_the_two_concepts_are_different_kinds_of_thing():
    """A scalar and a set, so they cannot be silently interchanged.

    If someone later writes `NIST_ANCHOR` where `TOPIC_ANCHORS` belongs,
    `in` against a string is a substring test and would quietly answer
    True for any framework key containing "nist-800-53". The type
    difference is load-bearing, not cosmetic.
    """
    assert isinstance(NIST_ANCHOR, str)
    assert isinstance(TOPIC_ANCHORS, frozenset)


@pytest.mark.parametrize("relative", B_SITE_FILES)
def test_no_b_site_still_reads_the_crosswalk_anchor(relative: str):
    """A topic-anchor site naming `NIST_ANCHOR` is the bug coming back.

    This is the guard that survives the people who were here. Someone
    adding a framework filter to `programme.py` reaches for the constant
    they can see; this makes them reach for the right one.
    """
    assert "NIST_ANCHOR" not in _code_identifiers(relative), (
        f"{relative} decides which catalogs a TOPIC may anchor, so it must use "
        f"`anchors_a_topic`/`TOPIC_ANCHORS`. `NIST_ANCHOR` is the crosswalk "
        f"anchor — what requirements are mapped ONTO — and widening this site "
        f"with it would make a catalog a crosswalk target."
    )


@pytest.mark.parametrize("relative", A_SITE_FILES)
def test_every_a_site_still_exists_and_still_uses_the_scalar(relative: str):
    """The other half of the enumeration.

    Without this, the B-site guard above is satisfied by deleting the
    concept entirely. Naming both lists means the assignment is the
    artefact, not one side of it.
    """
    assert "NIST_ANCHOR" in _code_identifiers(relative), (
        f"{relative} was assigned to the crosswalk-anchor side and no longer "
        f"mentions it. If that is deliberate, move it to B_SITE_FILES and say "
        f"why; if it is not, the crosswalk anchor has been widened."
    )


def _catalogs():
    from policyforge.ingest.schema import load_controls

    controls = []
    for path in sorted((ROOT / "data" / "frameworks").glob("*/controls.json")):
        controls.extend(load_controls(path))
    return controls


@pytest.mark.parametrize("relative", A_SITE_CONSUMERS)
def test_no_a_site_reads_the_topic_anchor(relative: str):
    """**The test this module exists for, and the only form of it that
    actually fails.**

    The converse of the B-site guard: a crosswalk-anchor site must never
    consult `TOPIC_ANCHORS` or `anchors_a_topic`. That is precisely the
    edit which turns "let topics anchor the AI RMF" into "publish an AI
    RMF crosswalk", and it is a one-line change someone makes while
    believing they are doing the first.

    **This replaced a version that could not fail.** The first attempt
    widened `TOPIC_ANCHORS` with `monkeypatch.setattr` and asserted the
    crosswalk was unmoved. It passed -- and it passed just as happily
    with `candidates.py` deliberately rewritten to follow
    `TOPIC_ANCHORS`, which is the exact defect it existed to catch. The
    reason: `candidates.py` does `from ... import TOPIC_ANCHORS`, binding
    its own module-level name at import time, so patching the attribute
    on `mapping.crosswalk` never reaches it. **A guard against a
    dangerous refactor that was itself a check that cannot fail** --
    found only by performing the mutation instead of trusting the green.

    Reading the source is cruder and it works, because the thing being
    prevented is textual: a name appearing in a file where it does not
    belong.
    """
    names = _code_identifiers(relative)
    for forbidden in ("TOPIC_ANCHORS", "anchors_a_topic"):
        assert forbidden not in names, (
            f"{relative} decides what requirements are mapped ONTO, and it now "
            f"consults `{forbidden}` -- the set of catalogs a TOPIC may anchor. "
            f"Widening that set would make those catalogs crosswalk targets. "
            f"For the AI RMF that publishes a claim NIST declined to make: that "
            f"an 800-53 control ACHIEVES an AI RMF outcome."
        )


def test_every_function_in_the_mixed_site_is_classified():
    """The population is derived, so a new function fails by name until
    someone decides its side, and a stale entry fails too: renaming the
    B function cannot silently drop it from B (1d on #335)."""
    functions, _ = _mixed_site_parts()
    assert len(functions) >= 15, "the premise: the parse found the file's functions"

    unclassified = sorted(set(functions) - set(MIXED_SITE_FUNCTIONS))
    stale = sorted(set(MIXED_SITE_FUNCTIONS) - set(functions))
    assert unclassified == [], f"{MIXED_SITE}: classify these A, B or neither: {unclassified}"
    assert stale == [], f"{MIXED_SITE}: these classified functions no longer exist: {stale}"
    assert {side for side, _ in MIXED_SITE_FUNCTIONS.values()} == {A, B, NEITHER}


def test_the_mixed_sites_module_level_code_keeps_the_a_rule():
    """Nothing at import time may read the topic anchor: the B function
    imports it inside itself."""
    _, module = _mixed_site_parts()
    for forbidden in TOPIC_ANCHOR_NAMES:
        assert forbidden not in module, f"{MIXED_SITE}: module-level code names `{forbidden}`"
    assert "NIST_ANCHOR" in module, "the premise: the A side's import is where it was"


@pytest.mark.parametrize("function", sorted(MIXED_SITE_FUNCTIONS))
def test_each_mixed_site_function_stays_on_its_side(function: str):
    """A functions keep the per-file A rule. B functions may name the topic
    anchor and nothing of the crosswalk anchor or its traversal. Neither
    names nothing from either side."""
    functions, _ = _mixed_site_parts()
    if function not in functions:
        pytest.fail(f"{MIXED_SITE}: `{function}` is classified but gone")
    side, _reason = MIXED_SITE_FUNCTIONS[function]
    # B may not reach the traversal by calling it either: every A function's
    # own name is forbidden to B, derived from the classification rather than
    # listed, so a function classified A later is covered too (1d on #342:
    # `document_evidence` called inside the B function stayed green).
    a_functions = tuple(
        name.rsplit(".", 1)[-1] for name, (s, _) in MIXED_SITE_FUNCTIONS.items() if s == A
    )
    forbidden = {
        A: TOPIC_ANCHOR_NAMES,
        B: CROSSWALK_ANCHOR_NAMES + a_functions,
        NEITHER: TOPIC_ANCHOR_NAMES + CROSSWALK_ANCHOR_NAMES,
    }[side]
    present = sorted(set(forbidden) & functions[function])
    assert present == [], f"{MIXED_SITE}: {side} function `{function}` names {present}"


def test_the_mixed_site_still_has_both_sides():
    """The other half, as for the per-file lists: deleting the traversal or
    the credit function must not satisfy the guard."""
    functions, _ = _mixed_site_parts()
    a_names = set().union(*(functions[f] for f, (s, _) in MIXED_SITE_FUNCTIONS.items() if s == A))
    b_names = set().union(*(functions[f] for f, (s, _) in MIXED_SITE_FUNCTIONS.items() if s == B))
    assert "NIST_ANCHOR" in a_names
    assert "anchors_a_topic" in b_names


def test_the_crosswalk_offers_only_the_anchor_as_a_target():
    """The behavioural half, with no patching, so nothing is bypassed.

    `catalog_entries` is what a crosswalk proposal offers a model to
    choose from. An AI RMF subcategory appearing there is this failure in
    its user-visible form.
    """
    from policyforge.crosswalk.candidates import catalog_entries

    controls = _catalogs()
    ai_rmf_ids = {
        identifier
        for control in controls
        if normalize_framework(control.framework) == "nist-ai-rmf"
        for identifier in [
            control.control_id,
            *(e.enhancement_id for e in control.enhancements),
        ]
    }
    assert ai_rmf_ids, "the AI RMF catalog is not installed, so this proves nothing"

    leaked = ai_rmf_ids & set(catalog_entries(controls))
    assert not leaked, (
        f"{sorted(leaked)[:5]} are offered as crosswalk targets, which lets a "
        f"model map requirements onto AI RMF outcomes."
    )
    assert "nist-ai-rmf" not in build_crosswalk(controls)


def test_seeding_the_ai_rmf_is_refused():
    """The user-facing path, rather than the internals."""
    from policyforge.crosswalk.overlay import NotAnchorableError, seed_overlay

    with pytest.raises(NotAnchorableError, match="outcomes, not obligations"):
        seed_overlay(_catalogs(), "NIST AI RMF")


def test_the_anchor_error_message_names_the_set_rather_than_a_catalog():
    """A message that hardcodes "NIST 800-53" becomes false the moment the
    set widens, and **a false error message is worse than a missing one** —
    confident, quoted back, and it sends the reader to fix the wrong thing.
    Same family as a remedy command that cannot run.
    """
    for relative in ("cli/programme.py", "cli/documents.py"):
        source = (ROOT / "src" / "policyforge" / relative).read_text(encoding="utf-8")
        assert "contain a catalog a topic can anchor" in source
        assert "None of the --controls files contain NIST 800-53 controls" not in source


def test_the_catalog_count_is_derived_not_remembered():
    """`_catalogs()` above underpins two assertions; if it silently read
    nothing they would both pass vacuously."""
    directories = [
        d for d in (ROOT / "data" / "frameworks").iterdir() if (d / "controls.json").exists()
    ]
    assert len(directories) >= 8
    loaded = {
        json.loads((d / "controls.json").read_text(encoding="utf-8"))[0]["framework"]
        for d in directories
    }
    assert "NIST AI RMF" in loaded
    assert len(_catalogs()) > 500


def test_a_topic_anchoring_ai_rmf_categories_covers_them_end_to_end(tmp_path):
    """**The user's path, not the helper's.**

    `test_a_category_claims_its_subcategories` exercises `parent_of`
    directly, which proves the regex and nothing about whether a topic
    registry can actually own an AI RMF category. Running the right
    function with your own arguments is not evidence about the product.

    So: write a real topic registry anchoring the six Govern categories,
    load it the way the CLI does, and require that all twenty-five Govern
    identifiers come back owned — six categories and nineteen
    subcategories — with no unknown anchors.
    """
    import yaml

    from policyforge.cli._common import load_catalogs
    from policyforge.topics.coverage import analyze_coverage, scope_label
    from policyforge.topics.registry import load_topics

    registry = tmp_path / "topics.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "topics": [
                    {
                        "name": "AI Governance",
                        "owner": "AI Risk / GRC",
                        "cadence": "annual",
                        "description": "Governance of AI systems.",
                        "nist_controls": [f"Govern {n}" for n in range(1, 7)],
                        "evidence": ["AI system inventory"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    paths = [str(p) for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))]
    controls = load_catalogs(paths)
    anchorable = [c for c in controls if anchors_a_topic(c.framework)]
    report = analyze_coverage(
        load_topics(registry),
        anchorable,
        scope=scope_label(anchorable),
        other_controls=[c for c in controls if not anchors_a_topic(c.framework)],
        crosswalk=build_crosswalk(controls),
    )

    govern = {
        identifier
        for control in controls
        if control.framework == "NIST AI RMF"
        for identifier in [
            control.control_id,
            *(e.enhancement_id for e in control.enhancements),
        ]
        if identifier.startswith("Govern")
    }
    assert len(govern) == 25, f"expected 6 categories + 19 subcategories, got {len(govern)}"

    assert not report.unknown_anchors, (
        f"the registry's AI RMF anchors did not resolve: {dict(report.unknown_anchors)}"
    )
    assert not (govern & set(report.orphaned)), (
        f"anchoring the six Govern categories left "
        f"{sorted(govern & set(report.orphaned))[:5]} unowned — a category is "
        f"not claiming its subcategories."
    )


def test_the_scope_line_names_the_catalogs_it_counted():
    """**A percentage whose denominator can change silently is not a
    measurement.**

    Admitting the AI RMF moved the programme headline 80.3% -> 73.7% with
    the numerator unchanged at 814. From the number alone that is
    indistinguishable from work having been lost — and the same arithmetic
    with the opposite meaning was a real defect once, when every installed
    catalog was counted as anchorable and installing one lowered the score.

    The reader cannot tell those apart by looking at the percentage, so
    the scope line names what was counted.
    """
    from policyforge.topics.coverage import scope_label

    controls = [c for c in _catalogs() if anchors_a_topic(c.framework)]
    label = scope_label(controls)
    assert "NIST 800-53" in label
    assert "NIST AI RMF" in label
    assert scope_label(controls, "moderate").startswith("moderate baseline (")
    assert scope_label([]) == "all controls (no anchorable catalog)"


def test_the_shipped_registry_owns_every_ai_rmf_requirement():
    """The example registry ships clean, including the AI RMF.

    **Shipping orphans would demonstrate the failure this tool exists to
    detect** — and the AI RMF is the case where it would be easiest to do
    accidentally, because its 91 requirements only entered the denominator
    when the catalog became anchorable. Before the five AI topics: 291
    orphaned. After: 200, which is exactly the count from before the AI RMF
    was anchorable at all. The 800-53 side is untouched.
    """
    from policyforge.cli._common import load_catalogs
    from policyforge.topics.coverage import analyze_coverage, scope_label
    from policyforge.topics.registry import load_topics

    paths = [str(p) for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))]
    controls = load_catalogs(paths)
    anchorable = [c for c in controls if anchors_a_topic(c.framework)]
    report = analyze_coverage(
        load_topics(ROOT / "config" / "topics.example.yaml"),
        anchorable,
        scope=scope_label(anchorable),
        other_controls=[c for c in controls if not anchors_a_topic(c.framework)],
        crosswalk=build_crosswalk(controls),
    )

    ai_rmf = {
        identifier
        for control in controls
        if control.framework == "NIST AI RMF"
        for identifier in [
            control.control_id,
            *(e.enhancement_id for e in control.enhancements),
        ]
    }
    assert len(ai_rmf) == 91, f"expected 19 categories + 72 subcategories, got {len(ai_rmf)}"

    assert not (ai_rmf & set(report.orphaned)), (
        f"the shipped registry leaves {sorted(ai_rmf & set(report.orphaned))[:5]} "
        f"unowned. A bundled, anchorable catalog with no topic owning it puts "
        f"every user's coverage down for requirements nobody decided to take on."
    )
    assert not report.unknown_anchors, dict(report.unknown_anchors)
    assert not (ai_rmf & set(report.contested)), (
        f"{sorted(ai_rmf & set(report.contested))} is claimed by two topics. "
        f"Third-party AI spans Govern 6, Map 4 and Manage 3 and is deliberately "
        f"ONE topic for that reason; a contested AI id usually means it got "
        f"split back along the framework's functions."
    )


def test_third_party_ai_is_one_topic_across_three_functions():
    """The registry's rule is that **split accountability breaks a topic**,
    and this is the case that tests whether it was followed.

    Cutting the AI RMF one-topic-per-function is the obvious move and it
    would split third-party AI — Govern 6 (policy), Map 4 (what the vendor
    brings), Manage 3 (managing it in production) — across three teams for
    one vendor model. Asserted because the obvious cut is the one someone
    will reach for later.
    """
    from policyforge.topics.registry import load_topics

    topics = load_topics(ROOT / "config" / "topics.example.yaml")
    owners = {
        anchor: topic.name
        for topic in topics
        for anchor in topic.nist_controls
        if anchor in ("Govern 6", "Map 4", "Manage 3")
    }
    assert len(owners) == 3, f"expected all three third-party anchors, found {owners}"
    assert len(set(owners.values())) == 1, (
        f"third-party AI is split across {sorted(set(owners.values()))}. One team "
        f"is accountable for a vendor model end to end; the framework files it "
        f"under three functions but that is not three processes."
    )


# ---- in scope is what the registry ANCHORS, not what it COULD anchor -------


def _registry_without_ai(tmp_path):
    """The shipped registry with every AI topic removed."""
    import yaml

    doc = yaml.safe_load((ROOT / "config" / "topics.example.yaml").read_text(encoding="utf-8"))
    doc["topics"] = [
        t
        for t in doc["topics"]
        if not any(
            str(c).split()[0] in ("Govern", "Map", "Measure", "Manage") for c in t["nist_controls"]
        )
    ]
    path = tmp_path / "topics.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _coverage_for(registry_path):
    from policyforge.cli._common import load_catalogs
    from policyforge.topics.coverage import analyze_coverage, scope_label, split_by_adoption
    from policyforge.topics.registry import load_topics

    paths = [str(p) for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))]
    controls = load_catalogs(paths)
    topics = load_topics(registry_path)
    in_scope, unadopted, reachable = split_by_adoption(topics, controls)
    report = analyze_coverage(
        topics,
        in_scope,
        scope=scope_label(in_scope),
        other_controls=reachable,
        crosswalk=build_crosswalk(controls),
    )
    return report, unadopted


def test_a_registry_that_anchors_no_ai_is_unaffected_by_the_bundled_catalog(tmp_path):
    """**The defect this ruling fixes, asserted as a number.**

    A customer who does no AI upgrades to a release that bundles the AI
    RMF. Before the fix their denominator grew by 91 and they lost six
    points for work they never took on — measured by 80: 1105 in scope,
    291 orphaned, against 1014 and 200.

    `anchors_a_topic` answers *could this be anchored*; the coverage scope
    needs *is this anchored*. Those read the same and only diverge once a
    bundled catalog is anchorable **but optional**, which the AI RMF is and
    no earlier catalog was.

    The property to hold: **an existing user's number does not move unless
    they change their own registry.**
    """
    report, unadopted = _coverage_for(_registry_without_ai(tmp_path))
    owned = len(report.in_scope) - len(report.orphaned)

    assert len(report.in_scope) == 1014, (
        f"a registry anchoring no AI RMF id has {len(report.in_scope)} in scope; "
        f"the bundled catalog is being counted against a user who never adopted it"
    )
    assert owned == 814
    assert len(report.orphaned) == 200, "the 800-53 side moved, which it must not"
    assert not any(c.framework == "NIST AI RMF" for c in report_frameworks(report))

    # Excluded, but named: the exclusion must not become a hiding place.
    assert any(c.framework == "NIST AI RMF" for c in unadopted)


def report_frameworks(report):
    """Controls whose ids are in the report's scope, for readability above."""
    from policyforge.cli._common import load_catalogs

    paths = [str(p) for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))]
    in_scope = set(report.in_scope)
    return [
        c
        for c in load_catalogs(paths)
        if c.control_id in in_scope or any(e.enhancement_id in in_scope for e in c.enhancements)
    ]


def test_anchoring_one_ai_identifier_adopts_the_whole_catalog(tmp_path):
    """Adoption is per catalog, not per identifier.

    Anything finer makes coverage unfalsifiable: a registry would only ever
    be measured against what it had already claimed, so it could never be
    told it had missed something.
    """
    import yaml

    doc = yaml.safe_load((ROOT / "config" / "topics.example.yaml").read_text(encoding="utf-8"))
    doc["topics"] = [
        t
        for t in doc["topics"]
        if not any(
            str(c).split()[0] in ("Govern", "Map", "Measure", "Manage") for c in t["nist_controls"]
        )
    ]
    doc["topics"].append(
        {
            "name": "A Toe In The Water",
            "owner": "AI Risk",
            "cadence": "annual",
            "description": "One anchor, to see what happens.",
            "nist_controls": ["Govern 1"],
            "evidence": ["nothing yet"],
        }
    )
    path = tmp_path / "topics.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    report, unadopted = _coverage_for(path)
    assert len(report.in_scope) == 1105, (
        "anchoring one AI RMF identifier did not bring the catalog into scope"
    )
    assert not unadopted, "the catalog is adopted, so nothing should be reported unadopted"
    # Govern 1 and its seven subcategories are owned; the rest are honest gaps.
    assert "Govern 1" not in report.orphaned
    assert "Map 1" in report.orphaned


def test_an_unadopted_catalog_is_named_rather_than_silently_absent(tmp_path):
    """80's condition on the ruling, and the reason it is a condition.

    **Excluding a catalog from the denominator is right; making it
    invisible is not** — the exclusion would become somewhere a framework
    could hide, and a user could not discover an unadopted framework
    without it first distorting their percentage.
    """
    from policyforge.topics.coverage import unadopted_note

    _, unadopted = _coverage_for(_registry_without_ai(tmp_path))
    note = "\n".join(unadopted_note(unadopted))
    assert "NIST AI RMF" in note
    assert "91 requirements" in note
    assert "anchored by no topic" in note
    assert unadopted_note([]) == [], "an all-adopted registry prints no section"


def test_generation_still_sees_every_anchorable_catalog():
    """**`documents.py` deliberately keeps the wider set, and that is not an
    oversight.**

    Generation needs a control to be AVAILABLE, which is a different
    question from whether it belongs in a denominator. Narrowing the
    generation path by adoption too would mean a topic anchoring `Govern 1`
    could not draw on the catalog it anchors.
    """
    source = (ROOT / "src" / "policyforge" / "cli" / "documents.py").read_text(encoding="utf-8")
    assert "anchors_a_topic(c.framework)" in source, (
        "documents.py no longer uses the wide anchorable set. If that is "
        "deliberate, check a topic anchoring an AI RMF category can still "
        "generate — it draws on controls this filter selects."
    )
    assert "split_by_adoption" not in source, (
        "documents.py now scopes generation by adoption. That is the coverage "
        "question, not the generation one."
    )


def test_adoption_only_ever_narrows(tmp_path):
    """**A guard has to state what it ALLOWS, not only what it refuses.**

    Narrowing the scope to adopted catalogs must not turn *you have a typo*
    into *you have no registry*. A registry whose every anchor is a typo
    adopts nothing, and the informative answer is still the full scope plus
    `unknown_anchors` — which is the field that exists to say so.

    Found by the suite, not by reasoning: an existing test anchoring `ZZ-9`
    against a catalog of `AC-2` went red reporting "no topic anchors any
    catalog on disk".

    The empty registry is **not** a case here, and checking that was worth
    more than the assertion I first wrote about it: `parse_topics` refuses
    `topics: []` outright. The product rejects it one layer up, which is a
    better place than a fallback in the scope rule.
    """
    import yaml

    from policyforge.cli._common import load_catalogs
    from policyforge.topics.coverage import split_by_adoption
    from policyforge.topics.registry import load_topics

    paths = [str(p) for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))]
    controls = load_catalogs(paths)

    for label, topic_list in [
        (
            "every anchor a typo",
            [
                {
                    "name": "Typos",
                    "owner": "O",
                    "cadence": "annual",
                    "description": "x",
                    "nist_controls": ["ZZ-9"],
                    "evidence": ["x"],
                }
            ],
        ),
        (
            "anchors that name no catalog here",
            [
                {
                    "name": "Elsewhere",
                    "owner": "O",
                    "cadence": "annual",
                    "description": "x",
                    "nist_controls": ["A.5.1", "A.5.2"],
                    "evidence": ["x"],
                }
            ],
        ),
    ]:
        path = tmp_path / f"{label.replace(' ', '-')}.yaml"
        path.write_text(yaml.safe_dump({"topics": topic_list}), encoding="utf-8")
        in_scope, unadopted, _ = split_by_adoption(load_topics(path), controls)
        assert in_scope, f"{label}: scope collapsed to nothing instead of staying wide"
        assert not unadopted, (
            f"{label}: a catalog was reported unadopted even though nothing was "
            f"adopted — narrowing fired when there was nothing to narrow to"
        )
        frameworks = {c.framework for c in in_scope}
        assert frameworks == {"NIST 800-53", "NIST AI RMF"}, frameworks

    # The empty registry never reaches the scope rule: the parser refuses it.
    from policyforge.topics.registry import TopicRegistryError, parse_topics

    with pytest.raises(TopicRegistryError, match="non-empty"):
        parse_topics({"topics": []})


def test_ai_topics_resolve_to_their_own_controls():
    """**The defect the file-shaped A/B guard could not see.**

    `build_synthesis_topic` resolved a topic's anchors with `NIST_ANCHOR`
    hardcoded, so every AI topic pulled **zero** controls — while
    `/coverage` reported those same topics owning 91 requirements. Two
    views of one registry, disagreeing, and only the wrong one was on the
    generation path.

    Asserted on behaviour rather than on which constant a file mentions,
    because the miss was precisely that a file can consume the crosswalk
    without naming `NIST_ANCHOR`.
    """
    from policyforge.cli._common import load_catalogs
    from policyforge.synthesis.merge import build_synthesis_topic

    paths = [str(p) for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))]
    controls = load_catalogs(paths)
    crosswalk = build_crosswalk(controls)

    # **Exact equality, and it is load-bearing for a second reason.**
    # Moving `merge.py` to the B list means the source-text A-guard no
    # longer watches its crosswalk expansion — policyforge-80's concern.
    # This assertion covers it behaviourally instead: an AI RMF anchor must
    # retrieve ONLY AI RMF controls, because there is no AI RMF crosswalk
    # and there must not be one. Verified by mutation — widening the
    # crosswalk expansion here turns this red.
    ai = build_synthesis_topic("AI", ["Govern 1", "Govern 2"], controls, crosswalk)
    assert {c.control_id for c in ai.controls} == {"Govern 1", "Govern 2"}, (
        f"an AI-anchored topic resolved to {[c.control_id for c in ai.controls]}"
    )

    # 800-53 must not regress: its anchors still pull their crosswalk
    # equivalents from the other frameworks.
    nist = build_synthesis_topic("NIST", ["AC-2"], controls, crosswalk)
    assert len(nist.controls) > 1, "800-53 anchors no longer expand through the crosswalk"
    assert normalize_framework(nist.controls[0].framework) == "nist-800-53"

    # And a mixed topic -- 80's grounded shape -- yields both.
    mixed = build_synthesis_topic("Mixed", ["Govern 1", "PM-1"], controls, crosswalk)
    frameworks = {normalize_framework(c.framework) for c in mixed.controls}
    assert {"nist-ai-rmf", "nist-800-53"} <= frameworks, sorted(frameworks)


def test_every_anchorable_catalog_resolves_through_synthesis():
    """**The guard policyforge-9b asked for: it must fail if a third
    anchorable catalog is added and this path is not widened again.**

    The shape that produced the defect: `TOPIC_ANCHORS` gained
    `nist-ai-rmf` in #169 and `build_synthesis_topic` did not notice,
    because it named one framework key directly. **A constant that grows
    while its consumers do not** — and the consumer failed silently,
    returning zero controls rather than raising.

    Derived from `TOPIC_ANCHORS` rather than listing the catalogs, so a
    third one is covered the day it is added and nobody has to remember
    this test exists. That is the whole point: the previous guards were
    all things someone had to think of.
    """
    from policyforge.cli._common import load_catalogs
    from policyforge.synthesis.merge import build_synthesis_topic

    paths = [str(p) for p in sorted((ROOT / "data" / "frameworks").glob("*/controls.json"))]
    controls = load_catalogs(paths)
    crosswalk = build_crosswalk(controls)

    # One real identifier per anchorable catalog, taken from the catalogs
    # themselves rather than written down here.
    sample: dict[str, str] = {}
    for control in controls:
        key = normalize_framework(control.framework)
        if key in TOPIC_ANCHORS:
            sample.setdefault(key, control.control_id)

    assert set(sample) == set(TOPIC_ANCHORS), (
        f"no control found on disk for {sorted(set(TOPIC_ANCHORS) - set(sample))}. "
        f"An anchorable catalog with nothing installed cannot be checked here — "
        f"install it or say why it is exempt."
    )

    for key, control_id in sorted(sample.items()):
        topic = build_synthesis_topic(f"probe {key}", [control_id], controls, crosswalk)
        retrieved = {c.control_id for c in topic.controls}
        assert control_id in retrieved, (
            f"a topic anchoring {control_id!r} from {key!r} retrieved "
            f"{sorted(retrieved)}. `TOPIC_ANCHORS` contains {key!r} but the "
            f"synthesis path does not resolve it — the same defect as the AI "
            f"RMF one, with a different catalog."
        )
