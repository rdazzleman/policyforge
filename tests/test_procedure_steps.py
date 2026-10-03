"""A cited Procedure step is a step, not "a requirement stated as fact" (#412).

80's ruling, after #363: a Procedure step carries out an obligation the
Standard above it states, and "Security Operations distributes the plan" is
how a step is meant to read. The strength check warned 79 times on two
correct example Procedures (266 times over the 45 committed ones). It still
reports the same sentence in a Standard, and a step weakened to "should" or
"may" in either tier.
"""

from __future__ import annotations

from policyforge.content.check import check_tree

STEP = (
    "Security Operations distributes the incident response plan to every team. [NIST 800-53 IR-8]"
)
WEAKENED = "Security Operations should distribute the incident response plan. [NIST 800-53 IR-8]"


def _strength(tmp_path, tier_dir: str, sentence: str) -> list[str]:
    root = tmp_path / "output"
    (root / tier_dir).mkdir(parents=True)
    (root / tier_dir / "incident-response.md").write_text(
        f"# Incident Response\n\n{sentence}\n", encoding="utf-8"
    )
    return [
        f.message for f in check_tree(root).findings if "states a cited requirement" in f.message
    ]


def test_a_cited_step_in_a_procedure_is_not_a_requirement_stated_as_fact(tmp_path):
    assert _strength(tmp_path, "procedures", STEP) == []


def test_the_same_sentence_in_a_standard_still_warns(tmp_path):
    (warning,) = _strength(tmp_path, "standards", STEP)
    assert "statement of fact" in warning


def test_a_weakened_step_in_a_procedure_still_warns(tmp_path):
    """The exemption is for a step with no modality, not for every step:
    "should" in a Procedure promises less than the framework it cites."""
    (warning,) = _strength(tmp_path, "procedures", WEAKENED)
    assert "as a recommendation" in warning
