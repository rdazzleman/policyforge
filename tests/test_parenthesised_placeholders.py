"""A placeholder label with a parenthesised acronym is read, as written (#268).

`[Security Information and Event Management (SIEM) System]` was never matched
by the placeholder reader, so `generate` never reported it (b5 found 19 such
spans in the committed corpus). 80's scope: the label is the whole span,
verbatim; a balanced, un-nested parenthesised group of the label's own
characters is accepted; a declared role fills it only on the whole label; a
citation such as `[NIST 800-53 AC-7(3)]` stays a citation.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from policyforge.org.context import (
    _PLACEHOLDER_RE,
    TEAM_ROLES,
    VENDOR_ROLES,
    Assignment,
    OrgProfile,
    apply_substitutions,
)
from policyforge.org.roles import Role

#: b5's spans, from the committed and the fresh corpus (the issue's body).
LABELS = [
    "Configuration Management Database (CMDB)",
    "Security Information and Event Management (SIEM) System",
    "Privileged Access Management (PAM) System",
    "Mobile Device Management (MDM) Platform",
    "Endpoint Detection and Response (EDR)",
    "Intrusion Detection System (IDS/IPS)",
    "Plan of Action and Milestones (POA&M) System",
]
SIEM = LABELS[1]


@pytest.mark.parametrize("label", LABELS)
def test_a_parenthesised_label_is_reported_whole_when_undeclared(label):
    result = apply_substitutions(f"Logs go to the [{label}].", OrgProfile())
    assert result.outstanding == [label]
    assert result.text == f"Logs go to the [{label}].", "an unfilled bracket stays as written"


def test_it_sits_beside_a_plain_placeholder_in_the_report():
    """The issue's measurement: only `SIEM Platform` was reported."""
    text = f"Send alerts from the [{SIEM}] and the [SIEM Platform]."
    assert apply_substitutions(text, OrgProfile()).outstanding == [SIEM, "SIEM Platform"]


def _declaring(label: str) -> OrgProfile:
    role = Role(key="siem", label=label, description="A test role.")
    return OrgProfile(vendors={"siem": Assignment(role=role, value="Splunk")})


def test_a_role_declared_with_the_whole_label_fills_it():
    result = apply_substitutions(f"Logs go to the [{SIEM}].", _declaring(SIEM))
    assert result.text == "Logs go to the Splunk."
    assert result.outstanding == [] and result.filled == [(SIEM, "Splunk")]


@pytest.mark.parametrize("declared", ["SIEM", "Security Information and Event Management"])
def test_a_role_matching_part_of_the_label_does_not(declared):
    """No acronym stripping or guessing (80's scope, point 3)."""
    result = apply_substitutions(f"Logs go to the [{SIEM}].", _declaring(declared))
    assert result.outstanding == [SIEM] and result.filled == []


@pytest.mark.parametrize("tag", ["[NIST 800-53 AC-7(3)]", "[NIST 800-53 SI-4(2)]"])
def test_a_citation_with_parentheses_stays_a_citation(tag):
    result = apply_substitutions(f"Lock the account after failures {tag}.", OrgProfile())
    assert result.outstanding == [] and result.filled == []
    assert tag in result.text


@pytest.mark.parametrize("span", ["[Unbalanced (SIEM System]", "[Nested ((X)) Thing]"])
def test_unbalanced_or_nested_parentheses_are_not_read(span):
    """As at the tip: only a balanced, un-nested group is part of a label."""
    assert apply_substitutions(f"See {span}.", OrgProfile()).outstanding == []


#: The tip's reader before #268, kept as the reference the corpus is compared
#: with: the same literals, and the generic class without parentheses.
def _tip_pattern() -> re.Pattern:
    from policyforge.org.context import _HEDGE_DASHES

    labels = [role.label for role in (*VENDOR_ROLES.values(), *TEAM_ROLES.values())]
    literals = "|".join(
        re.escape(label[0]) + "(?i:" + re.escape(label[1:]) + ")"
        for label in sorted(set(labels), key=lambda label: (-len(label), label))
    )
    return re.compile(
        rf"\[(?:({literals})|([A-Z][A-Za-z0-9/ .&-]*?))"
        rf"(?:\s+[{_HEDGE_DASHES}]\s+[^\]]+)?\]"
    )


CORPUS = Path(__file__).resolve().parent / "fixtures" / "deontic_corpus" / "documents"


def test_every_span_without_a_parenthesis_reads_as_at_the_tip():
    """80's scope: the committed corpus gives the same result for every span
    that has no parenthesis, and the parenthesised ones are the only new
    reads."""
    documents = sorted(CORPUS.rglob("*.md.txt"))
    assert len(documents) > 100, "premise: the committed corpus is here"
    tip = _tip_pattern()
    added = 0
    for path in documents:
        text = path.read_text(encoding="utf-8")
        old = [(m.span(), m.group(0)) for m in tip.finditer(text)]
        new = [(m.span(), m.group(0)) for m in _PLACEHOLDER_RE.finditer(text)]
        assert [s for s in new if "(" not in s[1]] == old, path.name
        added += sum("(" in s[1] for s in new)
    assert added, "premise: the corpus holds parenthesised spans (b5 counted 19)"


def test_what_generate_now_reports_over_the_corpus_is_pinned():
    """The extent of the new reads, not only that there are some (1d on PR 294).
    Under a run's scope of 800-53 alone, with no role declared, the corpus's
    newly read labels are five placeholders and 325 distinct parenthesised
    citations of catalogs outside that scope, which `is_citation_tag` lists as
    unresolved like their siblings without parentheses (the tip dropped them
    silently, 243's shape). Counted as distinct labels across the corpus."""
    from policyforge.ingest.schema import load_controls
    from policyforge.topics.satisfies import catalog_scope

    frameworks = Path(__file__).resolve().parent.parent / "data" / "frameworks"
    scope = catalog_scope(load_controls(frameworks / "nist-800-53-r5" / "controls.json"))
    placeholders: set[str] = set()
    citations: set[str] = set()
    for path in sorted(CORPUS.rglob("*.md.txt")):
        result = apply_substitutions(path.read_text(encoding="utf-8"), OrgProfile(), scope)
        for label in result.outstanding:
            if "(" in label:
                (citations if label in result.unresolved_citations else placeholders).add(label)

    assert placeholders == {
        "Configuration Management Database (CMDB)",
        "Mobile Device Management (MDM) Platform",
        "Mobile Device Management (MDM) System",
        "Privileged Access Management (PAM) System",
        "Security Information and Event Management (SIEM) System",
    }
    by_framework = {
        f: sum(c.startswith(f + " ") for c in citations) for f in ("HIPAA", "ARC-AMPE", "FedRAMP")
    }
    assert by_framework == {"HIPAA": 171, "ARC-AMPE": 144, "FedRAMP": 10}
    assert len(citations) == 325, "no parenthesised citation of another framework"


def test_an_unclosed_bracket_is_read_in_linear_time():
    r"""The first build's class, `(?:[...]+|\(...\))*?`, backtracked
    exponentially on a bracket that never closes: 4.5 s at 24 characters,
    hours at 40. Run in a child with a timeout, so a regression fails here
    rather than hanging the suite."""
    code = (
        "from policyforge.org.context import _PLACEHOLDER_RE\n"
        "assert _PLACEHOLDER_RE.search('[A' + 'b' * 40 + '!') is None\n"
        "assert _PLACEHOLDER_RE.search('[A' + 'b' * 20000 + '!') is None\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
