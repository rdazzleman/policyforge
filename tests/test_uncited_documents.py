"""A Standard or Procedure with no citation at all is reported.

The tag is the whole traceability story, so a Standard carrying none is
not weakly traced, it is untraced. Two ways that happens and both matter:
generation cut off part-way, or citations written and then lost in an edit.
policyforge-b5 found one of each while reconciling the citation measurement
over the bundled 20-topic starter set.

The tier scoping is not a detail of this check, it is the check. All
nineteen Policies in that set carry no tag by design, so an unscoped rule
reports nineteen correct documents alongside the two wrong ones and gets
switched off within a week. Every test below that pins a Policy as silent
is holding that line.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.content.check import check_tree

CITED = "Accounts are reviewed quarterly. [NIST AC-2 | HIPAA 164.308(a)(3)(ii)(B)]\n"
UNCITED = "Accounts are reviewed quarterly.\n"


def _write(root: Path, relative: str, body: str, *, tier: str = "", title: str = "Doc") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    front = f"---\ntitle: {title}\nowner: IAM Engineering\n"
    front += f"tier: {tier}\n" if tier else ""
    path.write_text(f"{front}---\n\n# {title}\n\n{body}", encoding="utf-8")
    return path


def _uncited(report):
    return [f for f in report.warnings if "cites no framework requirement" in f.message]


# ---- what it catches ------------------------------------------------------


@pytest.mark.parametrize("directory,tier", [("standards", "standard"), ("procedures", "procedure")])
def test_a_binding_document_with_no_citation_is_reported(tmp_path, directory, tier):
    _write(tmp_path, f"{directory}/access.md", UNCITED)

    report = check_tree(tmp_path)

    assert [f.path for f in _uncited(report)] == [f"{directory}/access.md"]
    assert tier in _uncited(report)[0].message


def test_a_truncated_standard_is_reported(tmp_path):
    """b5's first finding: 1,066 bytes, cut off mid-sentence, no tag."""
    _write(tmp_path, "standards/network-boundary.md", "Firewall rules are reviewed and")

    assert len(_uncited(check_tree(tmp_path))) == 1


def test_a_full_length_procedure_with_no_tags_is_reported(tmp_path):
    """b5's second finding: well-formed, complete, and traced to nothing."""
    step = "{n}. The requester opens a ticket and the owner approves."
    body = "\n\n".join(step.format(n=n) for n in range(30))
    _write(tmp_path, "procedures/remote-access.md", body)

    assert len(_uncited(check_tree(tmp_path))) == 1


# ---- the tier scoping, which is the check ---------------------------------


def test_a_policy_with_no_citation_is_silent(tmp_path):
    """Policies state intent and cite nothing by design. Nineteen of the
    twenty-one documents in the measured set are in exactly this position,
    so a rule that reported them would be wrong far more often than right."""
    _write(tmp_path, "policies/access.md", UNCITED)

    assert _uncited(check_tree(tmp_path)) == []


def test_a_tree_of_policies_and_two_bad_documents_reports_only_the_two(tmp_path):
    """The measured shape, in miniature: many uncited Policies, one
    truncated Standard, one untagged Procedure, and cited documents
    alongside. Only the two are reported."""
    for n in range(19):
        _write(tmp_path, f"policies/p{n}.md", UNCITED, title=f"Policy {n}")
    for n in range(19):
        _write(tmp_path, f"standards/s{n}.md", CITED, title=f"Standard {n}")
    _write(tmp_path, "standards/truncated.md", "Firewall rules are reviewed and")
    for n in range(19):
        _write(tmp_path, f"procedures/pr{n}.md", CITED, title=f"Procedure {n}")
    _write(tmp_path, "procedures/untagged.md", UNCITED)

    report = check_tree(tmp_path)

    assert sorted(f.path for f in _uncited(report)) == [
        "procedures/untagged.md",
        "standards/truncated.md",
    ]
    assert report.documents == 59


def test_a_document_with_no_tier_is_silent(tmp_path):
    """Nothing says what it should cite, so nothing can be said about it."""
    _write(tmp_path, "notes.md", UNCITED)

    assert _uncited(check_tree(tmp_path)) == []


# ---- what it must not do --------------------------------------------------


def test_a_cited_standard_is_silent(tmp_path):
    _write(tmp_path, "standards/access.md", CITED)

    assert _uncited(check_tree(tmp_path)) == []


def test_one_bare_arc_tag_is_enough_to_be_cited(tmp_path):
    """The shape rule decides what counts, so a tag the old name list could
    not see is a citation here. Before `content/tags.py` this document
    would have been reported as citing nothing."""
    _write(tmp_path, "standards/physical.md", "Visitors sign in. [ARC PE-1 | NIST PE-3]\n")

    assert _uncited(check_tree(tmp_path)) == []


def test_a_placeholder_is_not_a_citation(tmp_path):
    """`[Ticketing System]` is not traceability, and neither is a
    placeholder whose label opens with a framework name."""
    _write(
        tmp_path,
        "standards/access.md",
        "Reviews happen `[HIPAA Documentation Review Frequency]` "
        "and are filed in the `[Ticketing System]`.\n",
    )

    assert len(_uncited(check_tree(tmp_path))) == 1


def test_it_is_a_warning_and_never_blocks(tmp_path):
    """A hand-written Standard not yet mapped to a framework is a normal
    thing to have; a gate that cannot be satisfied gets switched off."""
    _write(tmp_path, "standards/access.md", UNCITED)

    report = check_tree(tmp_path)

    assert report.ok
    assert report.errors == []
    assert _uncited(report)


# ---- the directories it must never walk -----------------------------------


def test_the_tools_own_state_and_the_synthesis_inputs_are_never_reported(tmp_path):
    """`.history/` holds previous versions and `synthesis/` holds the
    requirement lists the generator consumes. Both are full of documents
    that are not documents, and reporting them would bury the real finding.
    Inherited from `load_content_tree`, and pinned here because this check
    is the one that would notice them."""
    _write(tmp_path, "standards/access.md", CITED)
    _write(tmp_path, ".history/standards/access.md", UNCITED)
    _write(tmp_path, "synthesis/access.md", UNCITED)
    _write(tmp_path, "edits/access.md", UNCITED)

    report = check_tree(tmp_path)

    assert _uncited(report) == []
    assert report.documents == 1
