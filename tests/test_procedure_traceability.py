"""A Procedure's steps carry no tags; a section lists each with its Standard section
and its citations (#22).

The user, reviewing a real 20-topic run: *"The citations in the procedures
documents are not necessary. I should be able to trace the section of the
procedure back to the standards documents ... It should be in another
section."* 80's ruling (comment 5944): **the citations move into that
section; they aren't dropped**, so every reader of tags (`satisfies`, drift,
`check`) finds a traced Procedure as before.

Every Procedure and Standard here is written by hand from the shapes a
generated one takes (numbered `##` sections with the tags on bullets, steps
as `### N. Title [tag]`), read against the real catalogs. The expected values
are the document's own sections, not output of the function under test.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from policyforge.content.check import (
    ContentDocument,
    _check_citations,
    _check_playbook_obligations,
    _check_uncited,
)
from policyforge.content.tags import source_tags
from policyforge.content.traceability import NO_MATCH, strip_tags, trace_procedure
from policyforge.ingest.schema import load_controls
from policyforge.topics.satisfies import catalog_scope, document_evidence

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
TITLE = "Acme Information Security Standard: Access Control"


def _controls(*names: str):
    return [c for name in names for c in load_controls(FRAMEWORKS / name / "controls.json")]


@pytest.fixture(scope="module")
def scope():
    return catalog_scope(_controls("nist-800-53-r5"))


STANDARD = """\
# Acme Information Security Standard: Access Control

Intro text that cites nothing.

## 1. Account Lockout

- **Thresholds**: Lock the account after five failed attempts `[NIST 800-53 AC-7]`.
- **Notice**: Display a banner `[NIST 800-53 AC-8]`.

## 2. Session Management

### 2.1 Concurrent Sessions

- Limit concurrent sessions `[NIST 800-53 AC-10]`.

### 2.2 Session Termination

- Terminate sessions after inactivity `[NIST 800-53 AC-12]`.

## 3. Transmission

- Protect transmitted information [NIST 800-53 SC-8].
"""

PROCEDURE = """\
# Acme Access Control Procedure

## Purpose

Operate the controls.

## Prerequisites

- Access to [Identity Provider] and [Tier 2 Support]
- Approval from [FedRAMP 3PAO]

## Procedure Steps

### 1. Configure Lockout [NIST 800-53 AC-7 | NIST 800-53 AC-8]

1. Log in to [Identity Provider].
1. Set the limit to five, escalating to [Tier 2 Support]
   after [FedRAMP 3PAO] review.

### 2. Show the Banner [NIST 800-53 AC-8]

1. Open the login screen configuration.

### 3. Limit Sessions [NIST 800-53 AC-10]

1. Set the concurrent session limit.

### 4. End Idle Sessions [NIST 800-53 AC-12]

1. Set the idle timeout.

### 5. Encrypt Transmission [NIST SC-8]

1. Enable TLS.

## Roles & Responsibilities

IAM Engineering performs these steps.

## Related Standard

Acme Information Security Standard: Access Control
"""

_LINE = re.compile(r"^- \*\*(?P<title>.+?)\*\*: (?P<rest>.*)$", re.MULTILINE)


def _trace(text: str) -> dict[str, str]:
    """{Procedure section: the rest of its line} from the document's own section."""
    _, _, section = text.partition("\n## Traceability\n")
    return {m.group("title"): m.group("rest") for m in _LINE.finditer(section)}


def _steps(text: str) -> str:
    return text.split("\n## Traceability\n")[0]


def test_each_step_is_mapped_to_the_most_specific_standard_section(scope):
    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    assert traced.written and traced.unmatched == ()
    expected = {
        "1. Configure Lockout": "implements 1. Account Lockout.",
        "2. Show the Banner": "implements 1. Account Lockout.",
        # Under `## 2`, a nested `###` answers, not its parent.
        "3. Limit Sessions": "implements 2.1 Concurrent Sessions.",
        "4. End Idle Sessions": "implements 2.2 Session Termination.",
        # `[NIST SC-8]` here, `[NIST 800-53 SC-8]` there: one requirement.
        "5. Encrypt Transmission": "implements 3. Transmission.",
    }
    lines = _trace(traced.text)
    assert set(lines) == set(expected)
    for step, said in expected.items():
        assert lines[step].startswith(said), step


def test_the_citations_move_into_the_section_as_written(scope):
    """80's ruling: each line carries that step's citation tags, verbatim: a
    merged tag keeps its `|`, so a reader comparing tags as strings (`check`
    against the synthesis, the edit path) sees the tag it always saw."""
    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    lines = _trace(traced.text)
    assert lines["1. Configure Lockout"].endswith("[NIST 800-53 AC-7 | NIST 800-53 AC-8]")
    assert lines["3. Limit Sessions"].endswith("[NIST 800-53 AC-10]")
    assert lines["5. Encrypt Transmission"].endswith("[NIST SC-8]")
    assert traced.moved == 5


def test_no_step_body_carries_a_tag_afterwards(scope):
    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    steps = _steps(traced.text)
    # The two role placeholders match the tag's shape; they are not citations.
    assert set(source_tags(steps)) == {"[Tier 2 Support]", "[FedRAMP 3PAO]"}
    assert "[NIST" not in steps
    assert "### 1. Configure Lockout\n" in traced.text
    assert "### 3. Limit Sessions\n" in traced.text


def test_a_role_in_citation_shape_stays_in_its_step(scope):
    """`[Tier 2 Support]` and `[FedRAMP 3PAO]` match the tag's SHAPE (a name,
    then a token with a digit) and are placeholders. Moving them would delete
    who does the step."""
    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    in_step = traced.text.split("### 1. Configure Lockout")[1].split("### 2.")[0]
    assert "[Tier 2 Support]" in in_step and "[FedRAMP 3PAO]" in in_step
    assert "[Identity Provider]" in traced.text
    assert "[Tier 2 Support]" not in _trace(traced.text)["1. Configure Lockout"]


def test_a_step_no_standard_section_answers_is_listed_with_its_citation(scope):
    procedure = PROCEDURE.replace(
        "## Roles & Responsibilities",
        "### 6. Review Logs [NIST 800-53 AU-6]\n\n1. Review them.\n\n## Roles & Responsibilities",
    )
    traced = trace_procedure(procedure, STANDARD, TITLE, scope)
    assert traced.written and traced.unmatched == ("6. Review Logs",)
    # The citation moved with the step, so nothing is lost and the gap shows.
    assert _trace(traced.text)["6. Review Logs"] == f"{NO_MATCH}. [NIST 800-53 AU-6]"
    assert "[NIST 800-53 AU-6]" not in _steps(traced.text)


def test_a_procedure_with_no_citation_and_no_match_is_left_alone(scope):
    bare = "# P\n\n## Procedure Steps\n\n### 1. Do It\n\n1. Do it.\n"
    traced = trace_procedure(bare, STANDARD, TITLE, scope)
    assert not traced.written and traced.text == bare
    assert traced.unmatched == ("1. Do It",)


def test_a_second_pass_changes_nothing(scope):
    first = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    second = trace_procedure(first.text, STANDARD, TITLE, scope)
    assert second.text == first.text and not second.written


def test_a_heading_inside_a_code_fence_is_not_a_section(scope):
    standard = STANDARD.replace(
        "- **Notice**: Display a banner `[NIST 800-53 AC-8]`.",
        "- **Notice**: Display a banner `[NIST 800-53 AC-8]`.\n\n```\n## 9. Not A Section\n```",
    )
    traced = trace_procedure(PROCEDURE, standard, TITLE, scope)
    assert "9. Not A Section" not in traced.text


def test_a_standard_with_no_sections_reaches_nothing_but_the_citations_still_move(scope):
    traced = trace_procedure(PROCEDURE, "# Title\n\nFlat text [NIST 800-53 AC-7].\n", TITLE, scope)
    assert len(traced.unmatched) == 5
    assert all(rest.startswith(NO_MATCH) for rest in _trace(traced.text).values())
    assert traced.moved == 5


def test_strip_tags_returns_what_it_took_and_removes_a_backticked_tag_whole(scope):
    text, taken = strip_tags("Set it `[NIST 800-53 AC-7]`. Then [NIST 800-53 AC-8].", scope)
    assert text == "Set it. Then."
    assert taken == ["[NIST 800-53 AC-7]", "[NIST 800-53 AC-8]"]


def test_strip_tags_never_breaks_a_code_span_that_holds_more_than_a_tag(scope):
    text, taken = strip_tags("Run `echo [NIST 800-53 AC-7] now` first.", scope)
    # The span holds more than the tag, so its backticks are not the tag's:
    # the tag goes, both backticks stay, and the span is still one span.
    assert text == "Run `echo now` first."
    assert taken == ["[NIST 800-53 AC-7]"]


# ---- the Playbook: NIST's suggestion, only in NIST's sentence (#300) --------

PLAYBOOK_STANDARD = (
    "# Std\n\n## 1. Governance\n\n"
    "NIST suggests, among its actions for Govern 1.1, documenting legal "
    "requirements [NIST AI RMF Playbook Govern 1.1 Action 1].\n"
)
PLAYBOOK_PROCEDURE = (
    "# Proc\n\n## Procedure Steps\n\n### 1. Document Legal Requirements\n\n"
    "NIST suggests documenting legal requirements "
    "[NIST AI RMF Playbook Govern 1.1 Action 1].\n\n"
    "1. List them in [Policy Register].\n"
)


def _doc(tier: str, body: str) -> ContentDocument:
    return ContentDocument(
        path=Path(f"{tier}.md"),
        relative_path=f"{tier}/x.md",
        title="x",
        body=body,
        tier=tier,
        slug="x",
        metadata={"topic": "Access Control", "owner": "IAM"},
    )


def test_a_playbook_citation_is_written_as_nists_and_passes_the_gate():
    both = catalog_scope(_controls("nist-800-53-r5", "nist-ai-rmf-playbook"))
    traced = trace_procedure(PLAYBOOK_PROCEDURE, PLAYBOOK_STANDARD, "Std", both)
    assert traced.written and traced.moved == 1
    line = _trace(traced.text)["1. Document Legal Requirements"]
    assert line == (
        "implements 1. Governance. NIST suggests the actions this section draws on "
        "[NIST AI RMF Playbook Govern 1.1 Action 1]."
    )
    assert "[NIST AI RMF" not in _steps(traced.text)
    assert "[Policy Register]" in traced.text
    assert _check_playbook_obligations([_doc("procedure", traced.text)]) == []


def test_the_gate_reads_the_trace_line_so_a_bare_playbook_tag_would_be_an_error():
    """The arm that must fail, or the test above proves nothing: the same
    line with the tag left bare after the section name is an ERROR."""
    bare = (
        "# Proc\n\n## Traceability\n\n"
        "- **1. Document Legal Requirements**: implements 1. Governance. "
        "[NIST AI RMF Playbook Govern 1.1 Action 1]\n"
    )
    findings = _check_playbook_obligations([_doc("procedure", bare)])
    assert len(findings) == 1 and findings[0].severity == "error"


# ---- 80's pin: a traced Procedure is still found by its own citations -------

#: Hand-written, not generator output (80's ruling): a traced Procedure as a
#: person would read it, with no tag in any step and its citations in the section.
TRACED = """\
# Acme Access Control Procedure

## Procedure Steps

### 1. Configure Lockout

1. Set the limit to five.

### 2. End Idle Sessions

1. Set the idle timeout.

## Traceability

Each section of this Procedure, the Standard section it implements, and its citations.

- **1. Configure Lockout**: implements 1. Account Lockout. [NIST 800-53 AC-7]
- **2. End Idle Sessions**: implements 2.2 Session Termination. [NIST 800-53 AC-12]
"""
UNTAGGED = re.sub(r" \[NIST 800-53 [A-Z]+-\d+\]$", "", TRACED, flags=re.MULTILINE)


def _tree(root: Path, body: str) -> Path:
    (root / "procedures").mkdir(parents=True)
    (root / "procedures" / "access.md").write_text(
        f"---\ntier: procedure\n---\n\n{body}", encoding="utf-8"
    )
    return root


def test_the_pin_arm_is_a_procedure_with_the_tags_gone_from_the_lines():
    assert source_tags(UNTAGGED) == [] and "[NIST 800-53 AC-7]" in TRACED
    assert "**1. Configure Lockout**: implements 1. Account Lockout." in UNTAGGED


def test_satisfies_names_a_traced_procedure_for_a_control_it_cites():
    controls = _controls("nist-800-53-r5")
    doc = SimpleNamespace(
        relative_path="procedures/access.md",
        title="Acme Access Control Procedure",
        tier="procedure",
        topic="Access Control",
        body=TRACED,
    )
    evidence = document_evidence(doc, controls=controls, crosswalk={})
    assert evidence.unknown == []
    assert sorted((c.framework, c.requirement_id) for c in evidence.cited) == [
        ("nist-800-53", "AC-12"),
        ("nist-800-53", "AC-7"),
    ]
    # The arm: the same Procedure with the tags gone from the lines cites nothing.
    gone = document_evidence(
        SimpleNamespace(**{**vars(doc), "body": UNTAGGED}), controls=controls, crosswalk={}
    )
    assert gone.cited == []


def test_drift_reaches_a_traced_procedure_for_a_control_it_cites(tmp_path):
    from policyforge.frameworks.drift import documents_citing

    ids = {"AC-7", "AC-12"}
    hits = documents_citing(
        {"AC-7"}, _tree(tmp_path / "with", TRACED), framework="NIST 800-53", catalog_ids=ids
    )
    assert hits == {"AC-7": ["procedures/access.md"]}
    # The arm: with the tags removed from the lines, no document is reached.
    hits = documents_citing(
        {"AC-7"}, _tree(tmp_path / "without", UNTAGGED), framework="NIST 800-53", catalog_ids=ids
    )
    assert hits == {}


def test_a_generated_trace_is_found_the_same_way(scope, tmp_path):
    from policyforge.frameworks.drift import documents_citing

    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    ids = {"AC-7", "AC-8", "AC-10", "AC-12", "SC-8"}
    hits = documents_citing(
        {"AC-10"}, _tree(tmp_path / "gen", traced.text), framework="NIST 800-53", catalog_ids=ids
    )
    assert hits == {"AC-10": ["procedures/access.md"]}


# ---- what `check` makes of a traced Procedure -------------------------------


def test_check_calls_a_traced_procedure_cited_and_a_bare_one_uncited(scope):
    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    assert _check_uncited([_doc("procedure", traced.text)]) == []
    bare = "# P\n\n## Procedure Steps\n\n### 1. A\n\nDo it.\n"
    findings = _check_uncited([_doc("procedure", bare)])
    assert len(findings) == 1 and "cites no framework requirement" in findings[0].message


def test_check_finds_a_traced_procedures_synthesis_tags_where_they_moved(scope, tmp_path):
    synthesis = tmp_path / "synthesis"
    synthesis.mkdir()
    (synthesis / "x.md").write_text(
        "- Lock accounts. [NIST 800-53 AC-7 | NIST 800-53 AC-8]\n", encoding="utf-8"
    )
    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    assert _check_citations([_doc("procedure", traced.text)], synthesis) == []
    # The arm: a trace that lost the tag is reported, as the step would have been.
    lost = traced.text.replace(" [NIST 800-53 AC-7 | NIST 800-53 AC-8]", "")
    assert len(_check_citations([_doc("procedure", lost)], synthesis)) == 1


# ---- through `generate` ------------------------------------------------------


class _Writer:
    """Returns a fixed Procedure, as a provider would."""

    def __init__(self, text: str):
        self.text = text

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=self.text, model="fake-writer")

    def check(self):
        return True


def _generate_procedure(tmp_path, monkeypatch, *, drafted: str, standard: str):
    import policyforge.cli as cli_mod
    from policyforge.llm.ledger import wrap

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(
        "---\ntopic: Access Control\nowner: Security Operations\n---\n\n"
        "- Lock accounts. [NIST 800-53 AC-7]\n",
        encoding="utf-8",
    )
    standard_file = tmp_path / "standard.md"
    standard_file.write_text(standard, encoding="utf-8")
    config = {"org": {"name": "Acme", "industry": "Health"}, "llm": {"provider": "local"}}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: wrap(_Writer(drafted), cfg))
    out = tmp_path / "out" / "access-control.md"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate", "--tier", "procedure", "--synthesis", str(synthesis),
            "--standard", str(standard_file), "--out", str(out),
            "--history-dir", str(tmp_path / "history"),
        ],
    )  # fmt: skip
    return result, out


GENERATE_STANDARD = (
    "# Acme Information Security Standard: Access Control\n\n"
    "## 1. Account Lockout\n\n- Lock the account `[NIST 800-53 AC-7]`.\n"
)


def test_generate_writes_a_traced_procedure(tmp_path, monkeypatch):
    drafted = (
        "# Acme Access Control Procedure\n\n## Purpose\n\nLock accounts.\n\n"
        "## Procedure Steps\n\n### 1. Configure Lockout [NIST 800-53 AC-7]\n\n"
        "1. Set the limit to five.\n\n"
        "## Related Standard\n\nAcme Information Security Standard: Access Control\n"
    )
    result, out = _generate_procedure(
        tmp_path, monkeypatch, drafted=drafted, standard=GENERATE_STANDARD
    )
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "### 1. Configure Lockout\n" in text
    assert source_tags(_steps(text)) == []
    assert (
        _trace(text)["1. Configure Lockout"] == "implements 1. Account Lockout. [NIST 800-53 AC-7]"
    )
    assert "Traced 1 of 1 Procedure section(s)" in result.output
    assert "moved 1 citation tag(s)" in result.output


def test_generate_says_when_a_step_reaches_no_standard_section(tmp_path, monkeypatch):
    drafted = (
        "# Acme Access Control Procedure\n\n## Procedure Steps\n\n"
        "### 1. Configure Lockout [NIST 800-53 AC-7]\n\n1. Set it.\n\n"
        "### 2. Review Logs [NIST 800-53 AU-6]\n\n1. Read them.\n"
    )
    result, out = _generate_procedure(
        tmp_path, monkeypatch, drafted=drafted, standard=GENERATE_STANDARD
    )
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "1 Procedure section(s) reach no section of the Standard" in result.output
    assert _trace(text)["2. Review Logs"] == f"{NO_MATCH}. [NIST 800-53 AU-6]"
    assert "[NIST 800-53 AU-6]" not in _steps(text)


# ---- what the pass did not reach is said, not left silent (1d on PR 426) ----

#: Shapes a model produces when it doesn't write `###` step sections. The
#: prompt asks for `###`; a different model or a long topic is how these arrive.
NUMBERED_LIST = """\
# Acme Access Control Procedure

## Procedure Steps

1. Configure Lockout [NIST 800-53 AC-7]
   - Set the limit to five.
2. Show the Banner [NIST 800-53 AC-8]
   - Open the login screen configuration.
"""

UNDER_FOUR_HASHES = """\
# Acme Access Control Procedure

## Procedure Steps

#### 1. Configure Lockout [NIST 800-53 AC-7]

1. Set the limit to five.

#### 2. Show the Banner [NIST 800-53 AC-8]

1. Open the login screen configuration.
"""

TAG_OUTSIDE_THE_STEPS = """\
# Acme Access Control Procedure

## Purpose

Lock accounts after repeated failures [NIST 800-53 AC-7].

## Procedure Steps

### 1. Configure Lockout [NIST 800-53 AC-7]

1. Set the limit to five.
"""


@pytest.mark.parametrize("shape", [NUMBERED_LIST, UNDER_FOUR_HASHES])
def test_steps_that_are_not_three_hash_sections_are_counted_as_left_inline(scope, shape):
    traced = trace_procedure(shape, STANDARD, TITLE, scope)
    assert not traced.written and traced.sections == 0 and traced.text == shape
    assert traced.left == 2, "both resolvable tags are still in the body, and it says so"


def test_a_citation_outside_the_steps_is_counted_though_the_steps_were_traced(scope):
    traced = trace_procedure(TAG_OUTSIDE_THE_STEPS, STANDARD, TITLE, scope)
    assert traced.written and traced.sections == 1
    assert traced.left == 1
    assert "[NIST 800-53 AC-7]" in traced.text.split("## Traceability")[0]


def test_a_fully_traced_procedure_leaves_nothing_inline(scope):
    traced = trace_procedure(PROCEDURE, STANDARD, TITLE, scope)
    assert traced.written and traced.left == 0


def test_the_count_is_resolvable_tags_only(scope):
    """A role in tag shape is not a citation, and counting it would send the
    user looking for a tag that was never one."""
    text = """\
# P

## Procedure Steps

1. Escalate to [Tier 2 Support] after [FedRAMP 3PAO] review.
"""
    assert trace_procedure(text, STANDARD, TITLE, scope).left == 0


def test_generate_says_when_citations_are_still_inline(tmp_path, monkeypatch):
    result, out = _generate_procedure(
        tmp_path, monkeypatch, drafted=NUMBERED_LIST, standard=GENERATE_STANDARD
    )
    assert result.exit_code == 0, result.output
    assert "citation tag(s) are still inline in this Procedure" in result.output
    assert "no `###` step sections were found under 'Procedure Steps'" in result.output
    # And the document is as drafted: the tags are still where the model put them.
    assert "[NIST 800-53 AC-7]" in out.read_text(encoding="utf-8")


def test_generate_is_quiet_about_inline_citations_when_none_are_left(tmp_path, monkeypatch):
    drafted = """\
# Acme Access Control Procedure

## Procedure Steps

### 1. Configure Lockout [NIST 800-53 AC-7]

1. Set the limit to five.
"""
    result, _ = _generate_procedure(
        tmp_path, monkeypatch, drafted=drafted, standard=GENERATE_STANDARD
    )
    assert result.exit_code == 0, result.output
    assert "still inline" not in result.output


# ---- 9b's read of PR 426: a `###` after the steps is not a step -------------


def test_a_three_hash_heading_under_a_later_section_is_not_owned_by_the_last_step(scope):
    """The owner of a section is the step above it only until the next heading
    at step level or higher. Roles & Responsibilities is a `##`, so an
    `### Escalation Contacts` beneath it is not step 5's: its tag stays where
    it is, is counted as left inline, and step 5's line carries its own tag only."""
    procedure = PROCEDURE.replace(
        "IAM Engineering performs these steps.",
        """### Escalation Contacts [NIST 800-53 AC-7]

Call the on-call engineer.""",
    )
    traced = trace_procedure(procedure, STANDARD, TITLE, scope)
    assert traced.written and traced.moved == 5
    steps = _steps(traced.text)
    roles = steps.split("## Roles & Responsibilities")[1]
    assert "### Escalation Contacts [NIST 800-53 AC-7]" in roles
    assert traced.left == 1
    # Step 5's line has its own tag and not the contact heading's.
    assert _trace(traced.text)["5. Encrypt Transmission"].endswith("[NIST SC-8]")
    assert "AC-7" not in _trace(traced.text)["5. Encrypt Transmission"]
