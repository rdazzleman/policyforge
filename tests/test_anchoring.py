"""One parent/child rule for every caller (#377).

The rule was written at least seven times, and each copy broke on its own
(#312, #318, #335, #339, #345, #369). These tests hold the one copy to what
each broken one got wrong, and hold every other module to not writing an
eighth.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from policyforge.mapping.crosswalk import TOPIC_ANCHORS
from policyforge.topics import anchoring
from policyforge.topics.anchoring import anchor_keys, parent_of

SRC = Path(__file__).resolve().parent.parent / "src" / "policyforge"


def test_every_catalog_a_topic_may_anchor_has_a_grammar():
    """A catalog made anchorable without a grammar would have its children
    owned by nobody, silently: `Govern 1` reported seven orphans that way."""
    assert set(anchoring._PARENT_RES) == set(TOPIC_ANCHORS)


@pytest.mark.parametrize(
    ("requirement_id", "framework", "keys"),
    [
        ("AC-2(3)", "NIST 800-53", {"AC-2(3)", "AC-2"}),
        ("AC-2", "NIST 800-53", {"AC-2"}),
        ("Govern 1.1", "NIST AI RMF", {"Govern 1.1", "Govern 1"}),
        ("Govern 1", "NIST AI RMF", {"Govern 1"}),
        # #336: the Playbook's ids read like the Core's, and a rule that read
        # no framework claimed 72 Playbook rows under an anchored category.
        ("Govern 1.1", "NIST AI RMF Playbook", set()),
        # Reached through the crosswalk, never anchored beside 800-53.
        ("AC-2(3)", "FedRAMP", set()),
        ("164.308(a)(1)", "HIPAA Security Rule", set()),
    ],
)
def test_the_ids_a_topic_may_anchor(requirement_id, framework, keys):
    assert anchor_keys(requirement_id, framework) == keys


def test_a_grammar_is_applied_only_to_its_own_catalog():
    """An 800-53 grammar applied to an AI RMF id, or the reverse, finds no
    parent: the grammar is chosen by the framework, not tried in turn."""
    assert parent_of("Govern 1.1", "NIST 800-53") is None
    assert parent_of("AC-2(1)", "NIST AI RMF") is None


# ---- discover (#345) ----


@pytest.mark.parametrize(
    ("body", "proposed"),
    [
        # The AI governance page that proposed nothing.
        ("Risk [NIST AI RMF Govern 1.1] and [NIST AI RMF Govern 1.2]", ["Govern 1"]),
        # Its 800-53 twin, unchanged.
        ("Accounts [NIST 800-53 AC-2(1)] and [NIST 800-53 AC-2]", ["AC-2"]),
        # A topic owns nothing in the Playbook.
        ("Suggested [NIST AI RMF Playbook Govern 1.1 Action 3]", []),
        # The house shorthand, and a sentence with no tag at all.
        ("[NIST AC-2] [NIST AC-6(5)]", ["AC-2", "AC-6"]),
        ("This page implements AC-2 and AC-2(3) and IA-5.", ["AC-2", "IA-5"]),
        # A catalog topics do not anchor proposes no anchor of its own.
        ("Access [HIPAA Security Rule 164.308(a)(3)(i)]", []),
    ],
)
def test_discovery_proposes_what_a_topic_would_anchor(body, proposed):
    from policyforge.zardoz.discover import _anchor_controls

    assert _anchor_controls([body]) == proposed


# ---- no caller writes its own copy ----

#: The spellings of an id grammar: a two-letter family and a dash
#: (`[A-Z]{2}-`, with or without a group between), a dotted subcategory
#: (`\d+\.\d+`), and an enhancement's parentheses (`\(\d+\)`). Matched
#: against every string literal, so a copy hidden in a regex is found.
_GRAMMAR = re.compile(r"\[A-Z(?:a-z)?\]\{2\}\)?-|\\d\+\\\.\\d\+|\\d\+\)\\\.|\\\(\\d\+\\\)")

#: `.split("(")` and its relatives: the other way to find a parent.
_SPLITS = {"split", "rsplit", "partition", "rpartition"}


def _reads_anchors(tree: ast.AST) -> bool:
    """Whether a module reads a topic's anchors, by attribute or `getattr`."""
    return any(
        (isinstance(node, ast.Attribute) and node.attr == "nist_controls")
        or (isinstance(node, ast.Constant) and node.value == "nist_controls")
        for node in ast.walk(tree)
    )


def _anchor_readers() -> dict[str, ast.AST]:
    """Every module that reads a topic's anchors: the population, derived
    from the code rather than listed, so a new reader joins it unasked."""
    readers = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.name != "anchoring.py" and _reads_anchors(tree):
            readers[path.relative_to(SRC).as_posix()] = tree
    return readers


def _copies(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SPLITS
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in {"(", "."}
        ):
            found.append(f"line {node.lineno}: .{node.func.attr}({node.args[0].value!r})")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _GRAMMAR.search(node.value)
        ):
            found.append(f"line {node.lineno}: id grammar {node.value[:50]!r}")
    return found


def test_the_population_is_not_empty_and_holds_the_known_callers():
    """The scan's own extent, checked against the callers #377 names: a
    scan that found no readers would pass the test below by finding nothing."""
    readers = set(_anchor_readers())
    for module in (
        "topics/coverage.py",
        "topics/bundles.py",
        "topics/satisfies.py",
        "frameworks/drift.py",
        "zardoz/discover.py",
        "cli/programme.py",
    ):
        assert module in readers, module


def test_no_module_that_reads_anchors_writes_its_own_parent_rule():
    """Every copy found so far lived in a module that reads `nist_controls`,
    and each spelled the grammar itself. Only `topics/anchoring.py` may."""
    offenders = {
        module: copies for module, tree in _anchor_readers().items() if (copies := _copies(tree))
    }
    assert offenders == {}, (
        "These modules read a topic's anchors and spell an id grammar or split "
        "an id themselves; use policyforge.topics.anchoring instead:\n"
        + "\n".join(f"  {m}: {c}" for m, c in sorted(offenders.items()))
    )


# ---- document reach: the broader breadth (80's ruling on #377) ----


def _docs(tmp_path, **bodies):
    root = tmp_path / "docs" / "standards"
    root.mkdir(parents=True)
    for name, body in bodies.items():
        (root / f"{name}.md").write_text(f"# {name}\n\n{body}\n", encoding="utf-8")
    return tmp_path / "docs"


CORE = {"Govern 1", "Govern 1.3", "Govern 1.5", "Govern 2", "Govern 2.1"}
PLAYBOOK = {"Govern 1.1", "Govern 1.1 Action 3", "Govern 1.1 Action 5", "Govern 1.2 Action 1"}


def test_an_ai_rmf_change_reaches_every_document_in_its_category(tmp_path):
    """Before #377 no AI RMF change reached any document: the recogniser knew
    only 800-53 shapes. Now `Govern 1.3` reaches `Govern 1` and `Govern 1.x`."""
    from policyforge.frameworks.drift import documents_citing

    root = _docs(
        tmp_path,
        exact="Documented. [NIST AI RMF Govern 1.3]",
        category="Governed. [NIST AI RMF Govern 1]",
        sibling="Inventoried. [NIST AI RMF Govern 1.5]",
        other="Trained. [NIST AI RMF Govern 2.1]",
        playbook="NIST suggests it. [NIST AI RMF Playbook Govern 1.1 Action 3]",
    )
    hits = documents_citing({"Govern 1.3"}, root, framework="NIST AI RMF", catalog_ids=CORE)
    assert hits == {
        "Govern 1.3": ["standards/category.md", "standards/exact.md", "standards/sibling.md"]
    }


def test_a_playbook_change_reaches_only_playbook_citations_of_its_subcategory(tmp_path):
    from policyforge.frameworks.drift import documents_citing

    root = _docs(
        tmp_path,
        same="NIST suggests it. [NIST AI RMF Playbook Govern 1.1 Action 5]",
        other="NIST suggests it. [NIST AI RMF Playbook Govern 1.2 Action 1]",
        core="Documented. [NIST AI RMF Govern 1.1]",
    )
    hits = documents_citing(
        {"Govern 1.1 Action 3"}, root, framework="NIST AI RMF Playbook", catalog_ids=PLAYBOOK
    )
    assert hits == {"Govern 1.1 Action 3": ["standards/same.md"]}


def test_an_800_53_change_reaches_its_siblings_and_the_shorthand(tmp_path):
    """Sibling reach stays, now by rule; `[NIST AC-2(3)]` resolves to 800-53
    because it is the catalog being diffed."""
    from policyforge.frameworks.drift import documents_citing

    root = _docs(
        tmp_path,
        sibling="Reviewed. [NIST AC-2(3)]",
        full="Reviewed. [NIST 800-53 AC-2]",
        other="Backed up. [NIST CP-9]",
    )
    hits = documents_citing(
        {"AC-2(1)"}, root, framework="NIST 800-53", catalog_ids={"AC-2", "AC-2(1)", "AC-2(3)"}
    )
    assert hits == {"AC-2(1)": ["standards/full.md", "standards/sibling.md"]}


def test_a_crosswalked_change_reaches_800_53_documents_through_its_mapping(tmp_path):
    """80's ruling on #377: FedRAMP reaches FedRAMP-cited documents, and NIST
    ones only through the crosswalk. An 800-171 change reaches 800-53
    documents in the families NIST maps it to, in full and in shorthand."""
    from policyforge.frameworks.drift import documents_citing

    root = _docs(
        tmp_path,
        own="Managed. [NIST 800-171 03.01.01]",
        mapped="Reviewed. [NIST 800-53 AC-2(5)]",
        shorthand="Reviewed. [NIST AC-2]",
        unmapped="Backed up. [NIST 800-53 CP-9]",
    )
    crosswalk = {"AC-2": {"nist-800-171": ["03.01.01"]}}
    hits = documents_citing(
        {"03.01.01"}, root, framework="NIST 800-171", catalog_ids={"03.01.01"}, crosswalk=crosswalk
    )
    assert hits == {
        "03.01.01": ["standards/mapped.md", "standards/own.md", "standards/shorthand.md"]
    }


def test_topic_keys_reach_a_crosswalked_catalog_only_through_the_loaded_crosswalk():
    from policyforge.topics.anchoring import topic_keys

    crosswalk = {"AC-2(3)": {"nist-800-171": ["03.01.01"]}, "RA-1": {"hipaa": ["164.308(a)(1)(i)"]}}
    assert topic_keys("03.01.01", "NIST 800-171", crosswalk) == {"AC-2(3)", "AC-2"}
    assert topic_keys("164.308(a)(1)(i)", "HIPAA Security Rule", crosswalk) == {"RA-1"}
    # Only what is loaded (80's condition): no crosswalk, no reach.
    assert topic_keys("03.01.01", "NIST 800-171") == set()
    # A partial relationship does not reach, by coverage's one reading (#185).
    partial = {("nist-800-171", "03.01.01", "AC-2(3)"): "superset"}
    assert topic_keys("03.01.01", "NIST 800-171", crosswalk, partial) == set()


def test_the_programme_parameter_scope_reads_the_one_rule(tmp_path):
    """80's ruling (B), through the real command: a topic anchoring `AC-2`
    brings 800-171's parameters for the requirements NIST maps to AC-2.

    Not asserted here: that no Playbook row is claimed (#336). Neither AI
    catalog carries a single parameter, so a ledger could not show one
    either way, and an assertion on it would pass by construction. That
    case is held where it can fail, in `test_the_ids_a_topic_may_anchor`.
    """
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    catalogs = SRC.parent.parent / "data" / "frameworks"
    topics = tmp_path / "topics.yaml"
    topics.write_text(
        "topics:\n  - name: T\n    owner: O\n    nist_controls: [AC-2]\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "parameters.yaml"
    args = ["parameters", "--topics", str(topics), "--ledger", str(ledger), "--init"]
    for directory in ("nist-800-53-r5", "nist-ai-rmf", "nist-ai-rmf-playbook", "nist-800-171-r3"):
        args += ["--controls", str(catalogs / directory / "controls.json")]

    result = CliRunner().invoke(cli_mod.cli, args)

    assert result.exit_code == 0, result.output
    keys = [
        line.strip().rstrip(":")
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":")
    ]
    assert any(k.startswith("AC-2/") for k in keys), keys
    assert any(k.startswith("03.01.01/") for k in keys), keys
    # Each says it came from 800-171 (80's ruling): its id does not.
    text = ledger.read_text(encoding="utf-8")
    assert "  # 03.01.01 (from NIST 800-171): " in text
    assert "  # AC-2: " in text


HUB = {
    "AC-2": {"fedramp": ["AC-2"], "arc-ampe": ["AC-2"], "hipaa": ["164.308(a)(3)(ii)(A)"]},
    "CP-9": {"fedramp": ["CP-9"]},
}


def _hub_docs(tmp_path):
    return _docs(
        tmp_path,
        nist="Reviewed. [NIST 800-53 AC-2(3)]",
        fedramp="Reviewed. [FedRAMP AC-2]",
        arc="Reviewed. [ARC AC-2]",
        hipaa="Authorised. [HIPAA Security Rule 164.308(a)(3)(ii)(A)]",
        backup="Backed up. [FedRAMP CP-9]",
    )


def test_an_800_53_change_reaches_every_catalog_mapped_to_its_family(tmp_path):
    """80's hub rule (ii) on #377. Main reached FedRAMP and ARC-AMPE citations
    by id shape and HIPAA's not at all; through the crosswalk, all three."""
    from policyforge.frameworks.drift import documents_citing

    hits = documents_citing(
        {"AC-2(1)"},
        _hub_docs(tmp_path),
        framework="NIST 800-53",
        catalog_ids={"AC-2", "AC-2(1)", "AC-2(3)", "CP-9"},
        crosswalk=HUB,
    )
    assert hits == {
        "AC-2(1)": [
            "standards/arc.md",
            "standards/fedramp.md",
            "standards/hipaa.md",
            "standards/nist.md",
        ]
    }


def test_a_fedramp_change_reaches_arc_ampe_through_the_hub_and_not_by_shape(tmp_path):
    from policyforge.frameworks.drift import documents_citing

    root = _hub_docs(tmp_path)
    hits = documents_citing(
        {"AC-2"}, root, framework="FedRAMP", catalog_ids={"AC-2", "CP-9"}, crosswalk=HUB
    )
    assert hits == {
        "AC-2": [
            "standards/arc.md",
            "standards/fedramp.md",
            "standards/hipaa.md",
            "standards/nist.md",
        ]
    }
    # With no crosswalk loaded, only FedRAMP's own family: ARC's `AC-2` reads
    # like FedRAMP's, and is not reached by its shape.
    alone = documents_citing({"AC-2"}, root, framework="FedRAMP", catalog_ids={"AC-2", "CP-9"})
    assert alone == {"AC-2": ["standards/fedramp.md"]}


# ---- citations as documents write them (1d on #418, 80's ruling) ----


def _loaded():
    """Every shipped catalog, as the drift command loads them."""
    from policyforge.ingest.schema import load_controls
    from policyforge.mapping.crosswalk import build_crosswalk, normalize_framework

    controls = [
        c
        for path in sorted((SRC.parent.parent / "data" / "frameworks").glob("*/controls.json"))
        for c in load_controls(path)
    ]
    catalogs: dict[str, set[str]] = {}
    for c in controls:
        ids = catalogs.setdefault(normalize_framework(c.framework), set())
        ids.add(c.control_id)
        ids.update(e.enhancement_id for e in c.enhancements)
    return build_crosswalk(controls), catalogs


@pytest.mark.parametrize(
    ("citation", "how"),
    [
        # 1d's table: reached on 74779c4, dropped by 260f92a. check accepts each.
        ("[NIST 800-53 AC-6(1)]", "resolved"),
        ("[FedRAMP AC-6(1)]", "resolved"),
        ("[NIST 800-53 AC-6(1)(a)]", "resolved"),
        ("[NIST SP 800-53 AC-6(1)]", "resolved"),
        ("[NIST 800-53 Rev 5 AC-6(1)]", "resolved"),
        ("[FedRAMP AC-6(3)]", "resolved"),  # in FedRAMP's baseline, not its tailoring
        # Shorthand and its statement part.
        ("[NIST AC-6]", "resolved"),
        ("[NIST AC-6(1)(b)]", "resolved"),
        # Nothing resolves it: reached by 800-53 shape, and marked so.
        ("[NIST 800-35 AC-6]", "by id shape"),
        ("[CIS AC-6(2)]", "by id shape"),
    ],
)
def test_a_citation_as_written_is_resolved_or_reached_loudly(tmp_path, citation, how):
    from policyforge.frameworks.drift import documents_reached

    crosswalk, catalogs = _loaded()
    root = _docs(tmp_path, doc=f"Least privilege. {citation}")
    hits = documents_reached(
        {"AC-6(1)"},
        root,
        framework="NIST 800-53",
        catalog_ids=catalogs["nist-800-53"],
        crosswalk=crosswalk,
        catalogs=catalogs,
    )
    assert hits == {"AC-6(1)": {"standards/doc.md": how}}


# ---- the regulatory catalogs' declared unit (#423, 80's rulings) ----


def _families():
    from policyforge.frameworks.registry import declared_family_rules
    from policyforge.ingest.schema import load_controls
    from policyforge.topics.anchoring import families_for

    root = SRC.parent.parent / "data" / "frameworks"
    controls = [c for p in sorted(root.glob("*/controls.json")) for c in load_controls(p)]
    return families_for(controls, declared_family_rules(roots=[root]))


@pytest.mark.parametrize(
    ("cited", "family"),
    [
        # Written from the regulation, not read off the catalog.
        ("164.308(a)(1)(ii)(A)", "164.308(a)(1)(i)"),  # a spec, to its standard
        ("164.308(a)(1)", "164.308(a)(1)(i)"),  # the standard, cited by paragraph
        ("164.310(a)(2)(iii)", "164.310(a)(1)"),  # facility access controls
        ("164.316(b)", "164.316(b)(1)"),  # documentation, cited by paragraph
        ("164.316(b)(1)(i)", "164.316(b)(1)"),  # a statement part below an id
        ("164.314(a)(2)(ii)", "164.314(a)(1)"),  # the declared override
        ("164.306(d)", "164.306(d)"),  # structure only: its own family
    ],
)
def test_a_hipaa_citation_stands_for_its_standard(cited, family):
    from policyforge.topics.anchoring import regulatory_families

    assert regulatory_families(cited, _families()["hipaa"]) == {family}


def test_a_section_cited_whole_stands_for_every_standard_in_it():
    from policyforge.topics.anchoring import regulatory_families

    standards = regulatory_families("164.308(a)", _families()["hipaa"])
    assert "164.308(a)(1)(i)" in standards and "164.308(a)(8)" in standards
    assert len(standards) == 8


@pytest.mark.parametrize(
    ("key", "cited", "family"),
    [
        ("cfr-42-part-2-sud-records", "2.16(b)", "2.16"),
        ("cfr-171-information-blocking", "171.202(d)", "171.202"),
        ("cfr-170-315-onc-certification", "170.315(b)(1)(iii)", "170.315(b)(1)"),
    ],
)
def test_the_other_regulatory_catalogs_use_their_declared_unit(key, cited, family):
    from policyforge.topics.anchoring import regulatory_families

    assert regulatory_families(cited, _families()[key]) == {family}


def test_every_id_a_manifest_names_for_its_family_exists():
    """80's condition: an override or structure-only entry naming an id the
    catalog no longer has would silently stop applying. A rename fails here."""
    import json

    from policyforge.frameworks.registry import declared_family_rules

    root = SRC.parent.parent / "data" / "frameworks"
    rules = declared_family_rules(roots=[root])
    assert {r.family for r in rules.values()} == {"section", "criterion", "structure"}
    for rule in {r.id: r for r in rules.values()}.values():
        rows = json.loads((rule.path / "controls.json").read_text(encoding="utf-8"))
        ids = {r["control_id"] for r in rows} | {
            e["enhancement_id"] for r in rows for e in r.get("enhancements") or []
        }
        named = {*rule.family_overrides, *rule.family_overrides.values(), *rule.structure_only}
        assert named <= ids, (rule.id, named - ids)


@pytest.mark.parametrize(
    ("child", "framework", "citation"),
    [
        # Before #423 none of these reached: a child change, a parent citation.
        ("164.308(a)(1)(ii)(A)", "HIPAA Security Rule", "[HIPAA Security Rule 164.308(a)(1)]"),
        (
            "164.308(a)(1)(ii)(A)",
            "HIPAA Security Rule",
            "[HIPAA Security Rule 164.308(a)(1)(ii)(B)]",
        ),
        ("164.310(a)(2)(i)", "HIPAA Security Rule", "[HIPAA Security Rule 164.310(a)(1)]"),
        ("2.16(a)", "Substance Use Disorder Records", "[Substance Use Disorder Records 2.16]"),
        ("171.202(b)", "Information Blocking", "[Information Blocking 171.202(d)]"),
    ],
)
def test_a_regulatory_child_change_reaches_its_family(tmp_path, child, framework, citation):
    from policyforge.frameworks.drift import documents_reached

    crosswalk, catalogs = _loaded()
    root = _docs(tmp_path, doc=f"Family cited. {citation}")
    hits = documents_reached(
        {child},
        root,
        framework=framework,
        catalog_ids={child},
        crosswalk=crosswalk,
        catalogs=catalogs,
        families=_families(),
    )
    assert hits == {child: {"standards/doc.md": "resolved"}}


def test_a_change_does_not_reach_another_standard_in_its_section(tmp_path):
    """The family is the standard, not the section: a change inside
    164.308(a)(1) does not reach a document citing 164.308(a)(3)."""
    from policyforge.frameworks.drift import documents_reached

    crosswalk, catalogs = _loaded()
    root = _docs(tmp_path, doc="Other standard. [HIPAA Security Rule 164.308(a)(3)(ii)(A)]")
    hits = documents_reached(
        {"164.308(a)(1)(ii)(A)"},
        root,
        framework="HIPAA Security Rule",
        catalog_ids={"164.308(a)(1)(ii)(A)"},
        crosswalk=crosswalk,
        catalogs=catalogs,
        families=_families(),
    )
    assert "standards/doc.md" not in hits.get("164.308(a)(1)(ii)(A)", {})


def test_satisfies_counts_a_standard_cited_by_paragraph_as_the_standard():
    """80's ruling: `check` resolves it the same way drift does. Before #423,
    `/satisfies` listed `164.308(a)(1)` as a citation nobody can follow."""
    from types import SimpleNamespace

    from policyforge.ingest.schema import load_controls
    from policyforge.topics.satisfies import document_evidence

    controls = load_controls(
        SRC.parent.parent / "data" / "frameworks" / "hipaa-security-rule" / "controls.json"
    )
    doc = SimpleNamespace(
        relative_path="s.md",
        title="S",
        tier="standard",
        topic="t",
        body="Risk is managed. [HIPAA Security Rule 164.308(a)(1)]",
    )
    evidence = document_evidence(doc, controls=controls, crosswalk={})
    assert evidence.unknown == []
    assert [(c.framework, c.requirement_id) for c in evidence.cited] == [
        ("hipaa", "164.308(a)(1)(i)")
    ]


def test_drift_prints_a_document_reached_by_shape_as_such(tmp_path):
    """Loud, not silent (80's ruling): the report says which documents were
    reached only by shape, so the citation gets fixed where it is written."""
    import copy

    from policyforge.frameworks.drift import analyze_drift
    from policyforge.ingest.schema import load_controls

    crosswalk, catalogs = _loaded()
    old = load_controls(
        SRC.parent.parent / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"
    )
    new = copy.deepcopy(old)
    next(c for c in new if c.control_id == "AC-6").control_statement += " Reworded upstream."
    root = _docs(
        tmp_path, good="Least privilege. [NIST SP 800-53 AC-6]", bad="Typo. [NIST 800-35 AC-6]"
    )
    report = analyze_drift(old, new, content_root=root, crosswalk=crosswalk, catalogs=catalogs)
    assert report.impacts["AC-6"].by_shape == ["standards/bad.md"]
    text = report.format_report()
    assert "standards/bad.md (reached by id shape; citation did not resolve)" in text
    assert "standards/good.md (reached by id shape" not in text


def test_a_fedramp_citation_of_a_control_it_does_not_tailor_goes_through_identity(tmp_path):
    """1d's `[FedRAMP AC-2(3)]`: no `AC-2` anywhere in FedRAMP's tailoring
    catalog, so neither the id nor a statement-part strip finds it there.
    FedRAMP's crosswalk maps every id to 800-53's same id (derived from the
    data), so it is 800-53's AC-2(3), as ruling (B) reaches it."""
    from policyforge.frameworks.drift import documents_reached

    crosswalk, catalogs = _loaded()
    assert not {i for i in catalogs["fedramp"] if i == "AC-2" or i.startswith("AC-2(")}
    root = _docs(tmp_path, doc="Reviewed. [FedRAMP AC-2(3)]")
    hits = documents_reached(
        {"AC-2(1)"},
        root,
        framework="NIST 800-53",
        catalog_ids=catalogs["nist-800-53"],
        crosswalk=crosswalk,
        catalogs=catalogs,
    )
    assert hits == {"AC-2(1)": {"standards/doc.md": "resolved"}}


@pytest.mark.parametrize(
    ("key", "cited"),
    [
        # A cited section the catalog does not carry, whose number is a prefix
        # of ones it does (policyforge-b5 on #429): Part 2's § 2.1 is
        # "Statutory authority", not 2.16 or 2.19; 171.2 is not the 171.20x
        # exceptions; 164.31 is not 164.310 or 164.312.
        ("cfr-42-part-2-sud-records", "2.1"),
        ("cfr-171-information-blocking", "171.2"),
        ("hipaa", "164.31"),
    ],
)
def test_a_number_prefix_is_not_an_ancestor(key, cited):
    from policyforge.topics.anchoring import regulatory_families

    assert regulatory_families(cited, _families()[key]) == set()
