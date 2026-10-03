"""A quote is grounded when its words are the text's words, in order.

Each accepting case is a shape measured from real model output on the HIPAA
probe; each refusing case is what the check exists to stop.
"""

from __future__ import annotations

import pytest

from policyforge.crosswalk.grounding import grounded, is_whole_text

SI_3 = (
    "Malicious Code Protection a. Implement [Selection (one or more): signature based; "
    "non-signature based] malicious code protection mechanisms at system entry and exit "
    "points to detect and eradicate malicious code;"
)
SC_8 = (
    "Transmission Confidentiality and Integrity Protect the [Selection (one or more): "
    "confidentiality; integrity] of transmitted information."
)


@pytest.mark.parametrize(
    ("quote", "text"),
    [
        ("malicious code protection mechanisms at system entry and exit points", SI_3),
        # Parameter brackets read aloud.
        ("Implement signature based malicious code protection mechanisms", SI_3),
        ("Protect the confidentiality; integrity of transmitted information", SC_8),
        # Parameter brackets dropped.
        ("Protect the of transmitted information", SC_8),
        # Two clauses joined by an ellipsis, in the text's order.
        ("malicious code protection mechanisms ... detect and eradicate malicious code", SI_3),
        ("malicious code protection mechanisms … detect and eradicate malicious code", SI_3),
        # Case and punctuation differ.
        ("MALICIOUS CODE PROTECTION MECHANISMS, at system entry", SI_3),
    ],
)
def test_a_real_quote_is_grounded(quote, text):
    assert grounded(quote, text)


@pytest.mark.parametrize(
    "quote",
    [
        "Implement multifactor authentication for privileged remote access",
        "code protection malicious implement mechanisms",
        "detect and eradicate malicious code ... malicious code protection mechanisms",
        # A fragment too short to prove anything, carried by a long one.
        "Implement ... malicious code protection mechanisms at system entry",
        "malicious code",
        "",
    ],
)
def test_a_fabricated_reordered_or_thin_quote_is_not(quote):
    assert not grounded(quote, SI_3)


def test_a_whole_short_specification_can_be_quoted_and_only_whole():
    spec = "Periodic security updates."
    assert is_whole_text("Periodic security updates.", spec)
    assert not is_whole_text("security updates", spec)
    assert not grounded("security updates", f"Security reminders {spec}")


def test_an_empty_specification_has_no_whole_to_quote():
    """The one-word quote the old minimum let through by counting an empty text."""
    assert not is_whole_text("Implementation", "")
    assert not grounded("Implementation", "Implementation specifications")


# The examples the matcher's docstring promises, which the first version failed:
# a miss moved the search to the end of its window and every later word missed.


def test_a_dropped_article_in_the_quote_still_matches():
    """The text has a word the quote leaves out."""
    assert grounded("implement malicious code protection mechanisms at entry and exit points", SI_3)


def test_an_inserted_word_in_the_quote_still_matches():
    """The quote has a word the text lacks, and the rest still count."""
    assert grounded("malicious code protection mechanisms and at system entry", SI_3)
    assert grounded("the malicious code protection mechanisms at system entry", SI_3)


def test_a_short_fragment_may_not_miss_a_word():
    """Under six words, one missing word is a sixth of the evidence or more."""
    assert not grounded("malicious code and protection mechanisms", SI_3)


def test_two_inserted_words_in_a_short_quote_are_refused():
    assert not grounded("malicious foo code bar protection mechanisms at entry", SI_3)


PS_4 = (
    "Personnel Termination a. Disable system access within [Assignment: organization-defined "
    "time period]; b. Terminate or revoke any authenticators and credentials associated with "
    "the individual;"
)


def test_a_fragment_that_is_the_title_is_not_refused_as_too_short():
    """The quote shape glm-5.3-flash returned for PS-4 on the termination case."""
    quote = (
        "Personnel Termination ... Disable system access within [Assignment: "
        "organization-defined time period]; b. Terminate or revoke any authenticators"
    )
    assert not grounded(quote, PS_4)
    assert grounded(quote, PS_4, heading="Personnel Termination")


def test_a_title_alone_is_not_a_quote():
    assert not grounded("Personnel Termination", PS_4, heading="Personnel Termination")


def test_a_short_fragment_that_is_not_the_title_is_still_refused():
    quote = "Personnel Access ... Disable system access within [Assignment: time period]"
    assert not grounded(quote, PS_4, heading="Personnel Termination")


POLICY_1 = (
    "Policy and Procedures a. Develop, document, and disseminate to personnel: 1. access "
    "control policy that addresses purpose and scope"
)


def test_a_leading_title_counts_toward_the_minimum():
    """A -1 control's title is "Policy and Procedures"; rule 5 sends models there."""
    quote = "Policy and Procedures ... access control policy"
    assert grounded(quote, POLICY_1, heading="Policy and Procedures")


def test_a_whole_four_word_title_is_matched_like_any_quote():
    title = "Physical and Environmental Protection"
    text = f"{title} Develop, document, and disseminate a physical protection policy"
    assert grounded(title, text, heading=title)


def test_a_title_fragment_after_the_first_is_held_to_the_minimum():
    quote = "Disable system access within ... Personnel Termination"
    assert not grounded(quote, PS_4, heading="Personnel Termination")


# ---- a set-aside title never grounds a quote by itself (third review) ------


def _bundled(control_id):
    """Title and text exactly as `propose_for` builds them for this control."""
    from pathlib import Path

    from policyforge.crosswalk.candidates import catalog_entries
    from policyforge.ingest.schema import load_controls

    root = Path(__file__).resolve().parents[1] / "data" / "frameworks"
    entry = catalog_entries(load_controls(root / "nist-800-53-r5" / "controls.json"))[control_id]
    return entry.title, f"{entry.title} {entry.text}"


@pytest.mark.parametrize("control_id", ["AC-1", "SI-1", "IR-1", "PS-4"])
@pytest.mark.parametrize(
    "shape",
    [
        "{t} ... {t}",
        "{t} ... policy",
        "{t} ... {t} ... {t}",
        "{t}",
    ],
)
def test_a_title_cannot_ground_a_quote_by_itself(control_id, shape):
    title, text = _bundled(control_id)
    assert not grounded(shape.format(t=title), text, heading=title)


def test_a_title_then_the_controls_own_words_still_grounds():
    title, text = _bundled("AC-1")
    assert grounded("Policy and Procedures ... access control policy", text, heading=title)
    title, text = _bundled("PS-4")
    assert grounded("Personnel Termination ... Disable system access within", text, heading=title)


def test_a_requirement_heading_is_held_to_the_same_rules():
    title = "Security incident procedures"
    text = f"{title} Implement policies and procedures to address security incidents."
    for shape in ("{t} ... {t}", "{t} ... incidents", "{t} ... {t} ... {t}"):
        assert not grounded(shape.format(t=title), text, heading=title)
    assert grounded(f"{title} ... address security incidents", text, heading=title)
