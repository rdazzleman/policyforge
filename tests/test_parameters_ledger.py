"""The parameter ledger: one decided value per ODP, and why.

SP 800-53 leaves ~1,200 values to the organization. Today they get decided
implicitly inside generated prose by a model with no memory of what it chose
for the neighbouring control, so the Access Control Standard says quarterly
and the SSP says annually and nobody decided anything.

The properties worth defending are that a decision is recorded once and
applied everywhere, that an *undecided* parameter stays visibly undecided,
and that a decision is never silently lost when the catalog changes.
"""

from __future__ import annotations

from pathlib import Path

from policyforge.ingest.schema import Control, ControlEnhancement
from policyforge.parameters.ledger import (
    SELECTION,
    Decision,
    apply_to_controls,
    build_report,
    extract_parameters,
    load_ledger,
    render_ledger,
)


def _control(control_id="AC-2", statement="", enhancements=()):
    return Control(
        control_id=control_id,
        title=f"{control_id} title",
        framework="NIST-800-53",
        framework_version="Rev 5",
        baseline="Low, Moderate, High",
        control_statement=statement,
        enhancements=[
            ControlEnhancement(
                enhancement_id=f"{control_id}({n + 1})",
                title="e",
                baseline="Moderate",
                description=text,
            )
            for n, text in enumerate(enhancements)
        ],
    )


REVIEW = (
    "a. Review accounts [Assignment: organization-defined frequency];\n"
    "b. Notify [Assignment: organization-defined personnel or roles] within "
    "[Assignment: organization-defined time period];\n"
    "c. Disable within [Assignment: organization-defined time period]."
)


# --------------------------------------------------------------------------
# Finding them
# --------------------------------------------------------------------------


def test_each_distinct_parameter_is_found_once():
    parameters = extract_parameters([_control(statement=REVIEW)])

    assert sorted(p.key for p in parameters) == [
        "AC-2/frequency",
        "AC-2/personnel-or-roles",
        "AC-2/time-period",
    ]


def test_a_repeated_marker_is_one_decision_not_several():
    """A control asking for a time period twice is asking one question
    twice, and a ledger that made you answer it twice is one people
    abandon."""
    parameters = extract_parameters([_control(statement=REVIEW)])

    period = next(p for p in parameters if p.key == "AC-2/time-period")
    assert period.occurrences == 2


def test_enhancements_carry_parameters_too():
    """An SSP has to answer for an enhancement's values as much as its
    parent's."""
    control = _control(
        statement="a. Do the thing.",
        enhancements=["Review [Assignment: organization-defined frequency]."],
    )

    assert [p.key for p in extract_parameters([control])] == ["AC-2/frequency"]


def test_a_selection_records_the_options_it_offers():
    control = _control(
        statement="Apply at [Selection (one or more): organization-level; system-level]."
    )

    parameter = extract_parameters([control])[0]

    assert parameter.kind == SELECTION
    assert parameter.choices == ["organization-level", "system-level"]


def test_context_starts_at_a_word_boundary():
    """A snippet opening mid-word reads as corruption."""
    long_lead = "x" * 40 + " some preceding words that run on for a while here "
    control = _control(statement=long_lead + "[Assignment: organization-defined frequency]")

    context = extract_parameters([control])[0].context

    assert not context.startswith("x")


def test_two_parameters_that_slug_alike_do_not_merge():
    control = _control(
        statement=(
            "a. [Assignment: organization-defined frequency];\n"
            "b. [Assignment: organization-defined frequency (for review)]."
        )
    )

    keys = sorted(p.key for p in extract_parameters([control]))

    assert len(keys) == 2, keys


def test_a_control_with_no_parameters_yields_none():
    assert extract_parameters([_control(statement="a. Do the thing.")]) == []


# --------------------------------------------------------------------------
# Applying a decision
# --------------------------------------------------------------------------


def test_a_decided_value_replaces_every_occurrence():
    controls = [_control(statement=REVIEW)]
    decisions = {"AC-2/time-period": Decision(value="24 hours")}

    resolved, filled = apply_to_controls(controls, decisions)

    assert "24 hours" in resolved[0].control_statement
    assert "organization-defined time period" not in resolved[0].control_statement
    assert filled == 1


def test_an_undecided_parameter_stays_visibly_undecided():
    """Better than a number nobody chose."""
    controls = [_control(statement=REVIEW)]

    resolved, _ = apply_to_controls(controls, {"AC-2/frequency": Decision(value="quarterly")})

    assert "quarterly" in resolved[0].control_statement
    assert "[Assignment: organization-defined personnel or roles]" in resolved[0].control_statement


def test_the_caller_s_controls_are_not_mutated():
    """They are shared with everything else in a run; a stage that quietly
    rewrote them would make the blast radius impossible to reason about."""
    controls = [_control(statement=REVIEW)]

    apply_to_controls(controls, {"AC-2/frequency": Decision(value="quarterly")})

    assert "[Assignment: organization-defined frequency]" in controls[0].control_statement


def test_a_decision_reaches_an_enhancement():
    control = _control(
        statement="a. Do the thing.",
        enhancements=["Review [Assignment: organization-defined frequency]."],
    )

    resolved, filled = apply_to_controls([control], {"AC-2/frequency": Decision(value="monthly")})

    assert "monthly" in resolved[0].enhancements[0].description
    assert filled == 1


def test_a_blank_value_is_not_a_decision():
    controls = [_control(statement=REVIEW)]

    _, filled = apply_to_controls(controls, {"AC-2/frequency": Decision(value="   ")})

    assert filled == 0


# --------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------


def test_a_missing_ledger_is_an_empty_one(tmp_path):
    assert load_ledger(tmp_path / "nope.yaml") == {}


def test_a_bare_value_is_accepted(tmp_path):
    """It is what people write first; the report will say the reasoning is
    missing rather than the file failing to load."""
    path = tmp_path / "p.yaml"
    path.write_text("parameters:\n  AC-2/frequency: quarterly\n", encoding="utf-8")

    decisions = load_ledger(path)

    assert decisions["AC-2/frequency"].value == "quarterly"
    assert decisions["AC-2/frequency"].rationale == ""


def test_the_full_form_carries_the_reasoning(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text(
        "parameters:\n"
        "  AC-2/frequency:\n"
        "    value: quarterly\n"
        "    rationale: HITRUST specifies it\n"
        "    source: HITRUST CSF 01.c\n",
        encoding="utf-8",
    )

    decision = load_ledger(path)["AC-2/frequency"]

    assert (decision.value, decision.source) == ("quarterly", "HITRUST CSF 01.c")


def test_rendering_preserves_decisions_already_made(tmp_path):
    parameters = extract_parameters([_control(statement=REVIEW)])
    decisions = {"AC-2/frequency": Decision(value="quarterly", source="HITRUST 01.c")}

    path = tmp_path / "p.yaml"
    path.write_text(render_ledger(parameters, decisions), encoding="utf-8")

    reloaded = load_ledger(path)
    assert reloaded["AC-2/frequency"].value == "quarterly"
    assert reloaded["AC-2/frequency"].source == "HITRUST 01.c"
    assert "AC-2/time-period" in reloaded, "undecided parameters are scaffolded too"


def test_a_decision_whose_parameter_vanished_is_kept_not_dropped(tmp_path):
    """A rewording upstream should cost somebody a question, not a decision
    they made and defended."""
    parameters = extract_parameters([_control(statement=REVIEW)])
    decisions = {"GONE-9/frequency": Decision(value="annually", rationale="we decided this")}

    rendered = render_ledger(parameters, decisions)

    assert "GONE-9/frequency" in rendered
    assert "no longer match any parameter" in rendered


def test_the_rendered_file_says_what_a_blank_means(tmp_path):
    rendered = render_ledger(extract_parameters([_control(statement=REVIEW)]), {})

    assert "stays as [Assignment: ...]" in rendered
    assert "rationale" in rendered


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def test_the_report_separates_decided_from_undecided():
    controls = [_control(statement=REVIEW)]
    report = build_report(controls, {"AC-2/frequency": Decision(value="quarterly")})

    assert [p.key for p in report.decided] == ["AC-2/frequency"]
    assert len(report.undecided) == 2


def test_a_value_with_no_reasoning_is_called_out():
    """'Why quarterly' is the question asked a year later."""
    report = build_report(
        [_control(statement=REVIEW)], {"AC-2/frequency": Decision(value="quarterly")}
    )

    assert [p.key for p in report.unreasoned] == ["AC-2/frequency"]
    assert "no rationale" in report.format_report()


def test_reasoning_satisfies_the_call_out():
    report = build_report(
        [_control(statement=REVIEW)],
        {"AC-2/frequency": Decision(value="quarterly", rationale="HITRUST")},
    )

    assert report.unreasoned == []


def test_stale_decisions_are_reported():
    report = build_report([_control(statement=REVIEW)], {"GONE-9/x": Decision(value="annually")})

    assert report.stale == ["GONE-9/x"]
    assert "match no parameter" in report.format_report()


def test_the_grouped_view_puts_the_most_leveraged_decisions_first():
    """A thousand parameters are not a thousand questions — 'frequency' is
    asked a hundred times, and deciding one kind at a sitting is how a
    person actually works through this."""
    controls = [
        _control("AC-2", "[Assignment: organization-defined frequency]"),
        _control("AU-6", "[Assignment: organization-defined frequency]"),
        _control("CP-9", "[Assignment: organization-defined personnel or roles]"),
    ]

    rows = build_report(controls, {}).by_label()

    assert rows[0][:2] == ("frequency", 2)
    assert "By the kind of value" in build_report(controls, {}).format_report(group=True)


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def test_init_scaffolds_the_ledger(tmp_path):
    import json

    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    controls = tmp_path / "controls.json"
    controls.write_text(
        json.dumps(
            [
                {
                    "control_id": "AC-2",
                    "title": "Account Management",
                    "framework": "NIST-800-53",
                    "framework_version": "Rev 5",
                    "baseline": "Low, Moderate, High",
                    "control_statement": REVIEW,
                    "discussion": "",
                    "enhancements": [],
                    "related_controls": [],
                    "source_crosswalk": {},
                    "source_path": None,
                    "family": None,
                    "family_abbr": None,
                }
            ]
        ),
        encoding="utf-8",
    )
    ledger = tmp_path / "parameters.yaml"

    result = CliRunner().invoke(
        cli_mod.cli,
        ["parameters", "--controls", str(controls), "--ledger", str(ledger), "--init"],
    )

    assert result.exit_code == 0, result.output
    assert ledger.exists()
    assert "AC-2/frequency" in ledger.read_text(encoding="utf-8")
    assert "3 organization-defined parameter(s)" in result.output


def test_the_real_catalog_yields_a_workable_number_of_decisions():
    """Scoping is the difference between a feature and a wall: the whole
    catalog is a thousand parameters, and a baseline is what you must
    actually answer for."""
    catalog = Path("data/frameworks/nist-800-53-r5/controls.json")
    if not catalog.exists():  # pragma: no cover - catalog is bundled
        return

    from policyforge.ingest.schema import load_controls

    controls = load_controls(catalog)
    everything = extract_parameters(controls)
    low = extract_parameters([c for c in controls if c.baseline and "low" in c.baseline.lower()])

    assert len(everything) > 500
    assert len(low) < len(everything), "a baseline is a smaller commitment than the catalog"
