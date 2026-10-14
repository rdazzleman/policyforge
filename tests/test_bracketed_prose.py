"""A bracketed sentence is not a placeholder to fill (issue 267; 80's ruling,
comment 6476).

A model writes a Procedure's prerequisites as `[Access to policy management
system]`, and the reader reported each as an unfilled placeholder with advice
to fill a role or record a value. The ruling:

* **Invariant:** no role key or recorded parameter is ever dropped from the
  placeholder report, and every span the reader stops counting is listed,
  under its own heading, left exactly as written.
* **Content principle:** a placeholder is a label. Every word is capitalised
  apart from the short connectives (`of`, `and`, `or`, `the`, `for`, `to`,
  `&`, `/`) and anything after a colon. A lower-case content word makes a
  span prose; being a whole list item does not.

Every expected value below is written by hand from the ruling and the domain,
not from `is_bracketed_prose` or the reader's pattern.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from policyforge.org.context import OrgProfile, apply_substitutions, is_bracketed_prose
from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES

# ---- 80's cases (6476), both directions, and the shapes the domain adds ---------------

LABELS = [
    # 80's list: must stay placeholders
    "Identity Provider",
    "Responsible Team",
    "Access Review Frequency",
    "Head of Security",
    "Chief Information Security Officer",
    "Assignment: organization-defined frequency",
    "Backup and Recovery Owner",
    # 80's edge, decided: a title-cased sentence is a label
    "Access To Policy Management System",
    # 80's measurement (6577): parameter labels the first list misread
    "Systems in Plan Scope",
    "Review Frequency per Section 2.2",
    "Systems or Components in Scope for Penetration Testing",
    "Authorized Privileged Users or Roles with Read-Only Access",
    "PII Elements from Privacy Risk Assessment",
    "System Covered by External Assessment",
    # the connectives, one at a time, and the symbols
    "Plan for Business Continuity",
    "Records of Processing Activities",
    "Security or Privacy Lead",
    "Chief Information Security Officer & Privacy Officer",
    "IT / Security Operations",
    # acronyms, digits, parenthesised forms, mixed-case words
    "Security Operations Center (SOC)",
    "Tier 2 Support",
    "Device Owner for macOS",
    "SIEM Administrator",
    # everything after a colon is exempt
    "Selection: one or more of the following",
]
PROSE = [
    # 80's list: must become prose
    "Access to AI system design documentation",
    "Annual budget approval for training programs",
    "Quarterly review schedule established by AI Risk / Product",
    "Access to policy management system",
    # prerequisites and steps as a model writes them
    "Access to the ticketing platform",
    "Quarterly review by the security team",
    "Approval from the data owner",
    "Written notice to affected customers within 60 days",
    "Annual training completed by all staff",
    "Security Operations Center (optional)",
    "Report any incident to the on-call engineer",
    # the four committed spans that are prose by the principle (6577)
    "ARC-AMPE SSPP template",
    "Detection & Response tooling",
    "Privacy Officer / HIPAA Compliance function",
    "Source tag not supplied in input requirements",
]

#: The closed class of 6577, written out here by hand, so a word taken out of
#: the code's set fails below instead of quietly making labels prose.
FUNCTION_WORDS = [
    "in", "on", "at", "by", "with", "from", "per", "as", "via", "into", "within",
    "a", "an", "the", "under", "over", "than", "of", "and", "or", "for", "to",
]  # fmt: skip


@pytest.mark.parametrize("label", LABELS)
def test_a_label_is_not_prose(label):
    assert not is_bracketed_prose(label), label


@pytest.mark.parametrize("label", PROSE)
def test_a_sentence_is_prose(label):
    assert is_bracketed_prose(label), label


@pytest.mark.parametrize("word", [*FUNCTION_WORDS, "&", "/"])
def test_a_function_word_never_makes_a_span_prose(word):
    """Each word of the closed class, between two capitalised words, at the
    end of a label's middle: a label can carry it."""
    assert not is_bracketed_prose(f"Plan {word} Scope"), word


@pytest.mark.parametrize("word", ["system", "approval", "schedule", "tooling", "function"])
def test_a_lower_case_content_word_makes_a_span_prose(word):
    assert is_bracketed_prose(f"Plan {word} Scope"), word


# ---- through the reader ----------------------------------------------------------------


def _read(text: str, **kwargs):
    return apply_substitutions(text, OrgProfile(name="Example Org"), **kwargs)


def test_prose_is_left_as_written_and_listed_apart_from_the_placeholders():
    text = (
        "## Prerequisites\n\n"
        "- [Access to AI system design documentation]\n"
        "- [Annual budget approval for training programs]\n"
        "- [Quarterly review schedule established by AI Risk / Product]\n"
        "- [Access to policy management system]\n\n"
        "## Steps\n\n"
        "The [Responsible Team] sets the [Access Review Frequency] and the [Head of Security] "
        "signs. The [Backup and Recovery Owner] keeps the [Identity Provider] current.\n"
    )

    result = _read(text)

    assert result.text == text, "nothing is rewritten: prose is left as written"
    assert result.prose == [
        "Access to AI system design documentation",
        "Annual budget approval for training programs",
        "Quarterly review schedule established by AI Risk / Product",
        "Access to policy management system",
    ]
    assert result.outstanding == [
        "Responsible Team",
        "Access Review Frequency",
        "Head of Security",
        "Backup and Recovery Owner",
        "Identity Provider",
    ]
    assert not set(result.prose) & set(result.unfilled + result.roles_left)
    assert result.roles_left == ["Identity Provider"]


def test_every_bracket_is_in_exactly_one_list():
    """Conservation, against a hand-written list of the brackets the document
    holds (the other side of the check): what the reader counts plus what it
    reads as prose is every bracket, once each, in document order."""
    text = (
        "[Head of Security] approves [Access to the ticketing platform]; "
        "the [Identity Provider] and [Plan for Business Continuity] are reviewed "
        "[Quarterly review by the security team] by the [Head of Security] again."
    )
    in_order = [
        "Head of Security",
        "Access to the ticketing platform",
        "Identity Provider",
        "Plan for Business Continuity",
        "Quarterly review by the security team",
    ]

    result = _read(text)

    assert sorted(result.outstanding + result.prose) == sorted(in_order)
    assert len(result.outstanding + result.prose) == len(set(in_order))


def test_a_span_written_twice_is_listed_once():
    result = _read("[Access to policy management system] and [Access to policy management system]")

    assert result.prose == ["Access to policy management system"]


@pytest.mark.parametrize("label", [r.label for r in (*VENDOR_ROLES.values(), *TEAM_ROLES.values())])
def test_a_declared_role_is_never_prose(label):
    """The invariant's first half: no role key is ever dropped. The labels are
    the vocabulary's own, so a label added with a lower-case word fails here
    instead of vanishing from the roles report."""
    result = _read(f"The [{label}] approves.")

    assert result.prose == [], label
    assert label in result.outstanding


def test_a_role_the_config_fills_is_filled(tmp_path):
    from policyforge.org.context import load_org_profile

    profile = load_org_profile(
        {"org": {"name": "Example Org", "vendors": {"identity_provider": "Okta"}}}
    )

    result = apply_substitutions("Sign in with [Identity Provider].", profile)

    assert result.text == "Sign in with Okta."
    assert result.prose == [] and result.outstanding == []


def test_the_documents_owner_is_written_plain_even_when_it_reads_as_prose():
    """The owner is a name from the synthesis, not a label: it is filled
    before the prose test, so an owner with a lower-case word in it (`Platform
    engineering team`) is written without its brackets and listed nowhere."""
    result = apply_substitutions(
        "[Platform engineering team] approves.",
        OrgProfile(name="Example Org"),
        owner="Platform engineering team",
    )

    assert result.text == "Platform engineering team approves."
    assert result.prose == [] and result.outstanding == []


def test_a_citation_shaped_span_keeps_its_own_list():
    """Prose is checked after the citation test, so a bracket written as a
    citation that doesn't resolve is still reported as one."""
    result = _read("Per [NIST ZZ-99] and [ISO 27001 A.5.1].")

    assert result.unresolved_citations == ["NIST ZZ-99", "ISO 27001 A.5.1"]
    assert result.prose == []


@pytest.mark.parametrize(
    "span", ["NIST ZZ-99 requirement", "ISO 27001 A.5.1 policy", "NIST ZZ-99 and later"]
)
def test_a_citation_with_a_lower_case_word_is_still_a_citation_not_prose(span):
    """The citation test comes first: a span written as a citation that
    doesn't resolve keeps its list and its advice, whatever its words."""
    result = _read(f"Per [{span}].")

    assert result.unresolved_citations == [span]
    assert result.prose == []


def test_the_ignored_standard_title_is_still_written_plain():
    result = _read(
        "## Related Standard\n\n[Access to Policy Standard]\n", ignore=["Access to Policy Standard"]
    )

    assert "[" not in result.text
    assert result.prose == [] and result.outstanding == []


def test_the_three_lists_still_partition_outstanding():
    result = _read(
        "[Responsible Team] [CISO] [Access to policy management system] [Identity Provider]"
    )

    assert sorted(result.roles_left + result.unfilled + result.unresolved_citations) == sorted(
        result.outstanding
    )
    assert "Access to policy management system" not in result.outstanding


# ---- through `generate`, the user's path ------------------------------------------------

SYNTHESIS = (
    "---\ntopic: Access Control\nderived_from:\n- nist-800-53-r5\n---\n\n"
    "- Staff must review access. [NIST 800-53 AC-2]\n"
)
STANDARD = "# Access Control Standard\n\nStaff must review access. [NIST 800-53 AC-2]\n"
DRAFT = (
    "# Access Control Procedure\n\n"
    "## Prerequisites\n\n"
    "- [Access to policy management system]\n"
    "- [Quarterly review schedule established by AI Risk / Product]\n\n"
    "## Steps\n\n"
    "The [Responsible Team] reviews access [NIST 800-53 AC-2].\n"
)


class _Draft:
    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=DRAFT, model="fake")

    def check(self):
        return True


def test_generate_lists_bracketed_prose_under_its_own_heading(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from policyforge.llm import ledger

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(SYNTHESIS, encoding="utf-8")
    standard = tmp_path / "standard.md"
    standard.write_text(STANDARD, encoding="utf-8")
    out = tmp_path / "out" / "access-control.md"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"org": {"name": "Acme"}, "llm": {"provider": "local"}}
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: ledger.wrap(_Draft(), config))

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "procedure",
            "--synthesis",
            str(synthesis),
            "--standard",
            str(standard),
            "--out",
            str(out),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert (
        "Bracketed prose, left as written (2): Access to policy management system, "
        "Quarterly review schedule established by AI Risk / Product"
    ) in lines
    # The placeholder line names only the real one, and the sentences aren't in it.
    unfilled = next(line for line in lines if "left unfilled" in line)
    assert unfilled == "1 placeholder(s) left unfilled: Responsible Team"
    written = out.read_text(encoding="utf-8")
    assert "[Access to policy management system]" in written, "left exactly as written"


def test_the_committed_corpus_reads_exactly_four_spans_as_prose():
    """80's acceptance (6577), measured: over the 128 committed documents the
    reader reads four spans as prose and none of the nine parameter labels the
    first list misread. The four are written here by hand."""
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("fc", root / "scripts" / "fresh_corpus.py")
    fc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fc)
    docs = fc._read(root / fc.COMMITTED, ".md.txt")
    assert len(docs) == 128, "the corpus was read"

    prose = sorted(
        {span for text in docs.values() for span in fc.placeholder_classes(text)["bracketed prose"]}
    )

    assert prose == sorted(
        [
            "ARC-AMPE SSPP template",
            "Detection & Response tooling",
            "Privacy Officer / HIPAA Compliance function",
            "Source tag not supplied in input requirements",
        ]
    )
