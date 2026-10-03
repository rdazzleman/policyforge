"""The shell's `satisfies` skill.

**The shell's own case for itself ended on a question it could not
answer.** `zardoz/__init__.py` argues for the shell with three examples —
*"what's our access review cadence?"*, then *"who owns that?"*, then
*"does it satisfy the HIPAA citation?"*. The first two are `bundle` and
`addresses`. The third had no skill.

`addresses` goes requirement -> document. This goes document ->
requirement, which is the direction an assessor reads in.

**The line naming the catalogs is the part to be careful with.** A shell
session falls back to `discover()` and sees every bundled catalog; someone
running `policyforge satisfies --controls ...` names a narrower set. Same
documents, different count of citations resolving to nothing, neither
wrong. Printing the configuration is what makes that information rather
than a contradiction.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.zardoz.skills import SKILLS, _catalogs_used

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"

DOCUMENT = """---
title: Access Control Policy
topic: Access Control
tier: Policy
---

# Access Control Policy

Accounts shall be reviewed quarterly. [NIST 800-53 AC-2]

A citation to a framework nobody loaded. [HITRUST 01.a]
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "access-control.md").write_text(DOCUMENT, encoding="utf-8")
    return tmp_path


def _state(tree: Path, *, controls_paths=(), topics=()):
    return SimpleNamespace(
        controls_paths=[str(p) for p in controls_paths],
        topics=list(topics),
        config={},
        content_dir=str(tree),
    )


def _run(state, args=()):
    return SKILLS["satisfies"].run(state, list(args))


# --------------------------------------------------------------------------
# The skill exists and answers the question the shell advertises
# --------------------------------------------------------------------------


def test_the_shell_can_answer_its_own_third_example_question():
    """The module docstring names three questions and shipped two skills."""
    assert "satisfies" in SKILLS
    assert "addresses" in SKILLS

    advertised = (ROOT / "src" / "policyforge" / "zardoz" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "satisfy" in advertised, "the docstring no longer makes the claim this closes"


def test_it_reports_what_a_document_cites_and_what_resolves_to_nothing(tree):
    state = _state(tree, controls_paths=[FRAMEWORKS / "nist-800-53-r5" / "controls.json"])

    report = _run(state)

    assert "access-control.md" in report
    assert "AC-2" in report
    assert "resolve to nothing" in report, "an unresolvable citation must be called out"


# --------------------------------------------------------------------------
# The catalogs line
# --------------------------------------------------------------------------


def test_the_answer_names_the_catalogs_it_was_computed_against(tree):
    """**The same question has two right answers depending on what is
    loaded**, and the shell loads more than the CLI usually does."""
    state = _state(tree, controls_paths=[FRAMEWORKS / "nist-800-53-r5" / "controls.json"])

    report = _run(state)

    assert report.startswith("Answered against 1 catalog(s):")
    assert "NIST 800-53" in report.splitlines()[0]


def test_loading_a_second_catalog_changes_the_line_and_the_count(tree):
    """Demonstrates the thing the line exists to explain: a narrower or
    wider catalog set answers the same question differently."""
    one = _run(_state(tree, controls_paths=[FRAMEWORKS / "nist-800-53-r5" / "controls.json"]))
    two = _run(
        _state(
            tree,
            controls_paths=[
                FRAMEWORKS / "nist-800-53-r5" / "controls.json",
                FRAMEWORKS / "hipaa-security-rule" / "controls.json",
            ],
        )
    )

    assert one.splitlines()[0] != two.splitlines()[0]
    assert "1 catalog(s)" in one.splitlines()[0]
    assert "2 catalog(s)" in two.splitlines()[0]


def test_the_line_names_catalogs_that_loaded_not_catalogs_that_were_asked_for(tree, tmp_path):
    """`_controls` skips a catalog it cannot read and carries on, so a line
    built from the requested paths would name one that contributed
    nothing. Derived from the controls instead."""
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    state = _state(
        tree,
        controls_paths=[FRAMEWORKS / "nist-800-53-r5" / "controls.json", broken],
    )

    report = _run(state)

    assert "Answered against 1 catalog(s):" in report.splitlines()[0]
    assert "broken" not in report.splitlines()[0]


def test_catalogs_that_share_a_framework_key_are_called_out():
    """Two catalogs under one key pool their requirement ids, so a
    citation to one can resolve against the other. The reader is told."""
    pooled = [
        SimpleNamespace(framework="45 CFR 171"),
        SimpleNamespace(framework="45 CFR 164"),
    ]

    line = _catalogs_used(pooled)

    assert "share a framework key" in line


def test_distinct_catalogs_are_not_called_out():
    """The warning must not fire on the normal case, or it is noise."""
    distinct = [
        SimpleNamespace(framework="NIST 800-53"),
        SimpleNamespace(framework="HIPAA Security Rule"),
    ]

    assert "share a framework key" not in _catalogs_used(distinct)


# --------------------------------------------------------------------------
# Narrowing, and refusing rather than answering the wrong question
# --------------------------------------------------------------------------


def test_a_topic_argument_narrows_to_that_topic(tree):
    state = _state(tree, controls_paths=[FRAMEWORKS / "nist-800-53-r5" / "controls.json"])

    assert "access-control.md" in _run(state, ["access-control"])


def test_a_topic_nothing_matches_says_so_rather_than_reporting_everything(tree):
    """Silently widening to the whole tree would answer a question nobody
    asked, and look like a complete answer."""
    state = _state(tree, controls_paths=[FRAMEWORKS / "nist-800-53-r5" / "controls.json"])

    report = _run(state, ["payment-card-security"])

    assert "No document" in report
    assert "access-control.md" not in report


def test_no_content_tree_is_reported_rather_than_answered(tmp_path):
    state = _state(tmp_path / "absent")

    assert "No content tree" in _run(state)


def test_no_catalogs_is_reported_rather_than_answered(tree, tmp_path, monkeypatch):
    """Without a catalog every citation is unknown, which would read as a
    document that cites nothing real."""
    monkeypatch.setattr("policyforge.zardoz.skills._controls", lambda state: [])
    state = _state(tree)

    assert "No control catalogs" in _run(state)


def test_an_empty_tree_is_reported_rather_than_answered(tmp_path):
    state = _state(tmp_path, controls_paths=[FRAMEWORKS / "nist-800-53-r5" / "controls.json"])

    assert "nothing to check" in _run(state)


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def test_the_offline_router_catches_the_unambiguous_phrasing():
    from policyforge.zardoz.skills import route_offline

    assert route_offline("which citations resolve to nothing?") == "satisfies"
    assert route_offline("are any resolving to nothing?") == "satisfies"


@pytest.mark.parametrize(
    "question",
    [
        "is the encryption section traceable to a decision log?",
        "does the standard satisfy the auditor's expectations for evidence?",
        "what does AC-2 require?",
        "who owns the backup standard?",
    ],
)
def test_an_ordinary_document_question_is_not_hijacked_by_the_new_hints(question):
    """**Measured rather than guessed.** `traceable` and `satisfy the` are
    the obvious stems for this skill and both were rejected: each hijacks
    a real question for the documents. A missed route costs a fallback; a
    hijacked one answers a question nobody asked.
    """
    from policyforge.zardoz.skills import NO_SKILL, route_offline

    assert route_offline(question) == NO_SKILL


def test_the_two_directions_are_distinguished_for_the_model_router():
    """`addresses` and `satisfies` are one question from opposite ends, so
    each `answers` text has to say which end it starts from or the router
    is choosing between two plausible matches.

    Not routed through `ALSO_PROMPT`: that prompt's own rule is *never add
    an analysis because it is related*, and these are not two questions.
    """
    addresses = SKILLS["addresses"].answers.lower()
    satisfies = SKILLS["satisfies"].answers.lower()

    assert "addresses" in satisfies, "satisfies must point at its opposite"
    assert "coverage" in satisfies, "and distinguish itself from the aggregate"
    assert "starts from a document" in satisfies, "it must say which end it starts from"
    assert "one named requirement" in addresses, "and addresses must say the other"
