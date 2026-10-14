"""A Markdown link's text is never a placeholder (issue 295).

9b measured at the tip: `[Unknown System](https://...)` was reported as an
unfilled placeholder, and `[Ticketing System](https://...)`, with that role
filled as Jira, became `Jira(https://...)`, a broken link written into the
document. 80's scope: a bracketed span followed at once by `(` or `[` is link
syntax, for every label form; not `:` and not a space.
"""

from __future__ import annotations

import pytest

from policyforge.org.context import VENDOR_ROLES, Assignment, OrgProfile, apply_substitutions

URL = "https://wiki.example/ticketing"


def _filled() -> OrgProfile:
    """`Ticketing System` declared, so the declared-label path is the one
    that would fill a link's text."""
    return OrgProfile(
        vendors={"ticketing": Assignment(role=VENDOR_ROLES["ticketing"], value="Jira")}
    )


LINKS = [
    ("inline link, declared label", f"Raise it in the [Ticketing System]({URL})."),
    ("inline link, generic label", f"See the [Unknown System]({URL}) first."),
    ("reference link", "Raise it in the [Ticketing System][tickets].\n\n[tickets]: https://x\n"),
    ("image", f"![Ticketing System]({URL})"),
    ("hedged label", f"[Ticketing System — e.g. the service desk]({URL})"),
    (
        "parenthesised label (268's class)",
        f"[Security Information and Event Management (SIEM) System]({URL})",
    ),
    (
        "parenthesised label, reference link",
        "See the [Mobile Device Management (MDM) Platform][mdm].\n\n[mdm]: https://x\n",
    ),
]


@pytest.mark.parametrize(("name", "text"), LINKS, ids=[link[0] for link in LINKS])
def test_a_link_is_left_as_written_and_not_reported(name, text):
    result = apply_substitutions(text, _filled())
    assert result.text == text
    assert result.outstanding == [] and result.filled == []


def test_the_issue_case_no_longer_writes_a_broken_link():
    """The destructive direction: fill-in rewrote the link's text."""
    result = apply_substitutions(f"Use the [Ticketing System]({URL}).", _filled())
    assert "Jira(" not in result.text and f"[Ticketing System]({URL})" in result.text


STILL = [
    ("alone", "Raise it with [Owner].", "Owner"),
    ("colon after", "[Owner]: approves every exception.", "Owner"),
    ("space then paren", "[Owner] (see below) approves it.", "Owner"),
]


@pytest.mark.parametrize(("name", "text", "label"), STILL, ids=[s[0] for s in STILL])
def test_a_bracket_not_followed_at_once_by_link_syntax_is_still_a_placeholder(name, text, label):
    assert apply_substitutions(text, OrgProfile()).outstanding == [label]


def test_a_filled_role_beside_a_link_to_it_fills_only_the_placeholder():
    text = f"The [Ticketing System] is in the [Ticketing System]({URL})."
    result = apply_substitutions(text, _filled())
    assert result.text == f"The Jira is in the [Ticketing System]({URL})."
    assert result.filled == [("Ticketing System", "Jira")]
