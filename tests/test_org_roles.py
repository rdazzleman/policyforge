"""Role-keyed tools and teams, and filling them in exactly.

The property under test is determinism. A prompt asking a model to use a
name consistently gets it right most of the time, and "most of the time"
across forty documents is a set where the same system is called three
things. Substitution happens after generation, in code, so the same document
and the same config produce the same output every time — which is also what
makes it testable with no API key.
"""

from __future__ import annotations

from policyforge.org.context import (
    apply_substitutions,
    load_org_profile,
    render_for_prompt,
)
from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES, resolve_role, unknown_roles


def _profile():
    return load_org_profile(
        {
            "org": {
                "name": "Acme Health",
                "industry": "Healthcare",
                "vendors": {"identity_provider": "Okta", "ticketing": "Jira"},
                "teams": {"identity_access": "IAM Engineering"},
            }
        }
    )


# --------------------------------------------------------------------------
# The taxonomy
# --------------------------------------------------------------------------


def test_the_taxonomies_are_not_empty_and_cover_the_obvious_ground():
    for key in ("identity_provider", "endpoint_protection", "siem", "ticketing", "backup"):
        assert key in VENDOR_ROLES
    for key in ("security_engineering", "identity_access", "grc", "people_operations"):
        assert key in TEAM_ROLES


def test_every_label_is_distinct():
    """Labels are the substitution key, so two roles sharing one would make
    the fill ambiguous."""
    labels = [r.label.lower() for r in VENDOR_ROLES.values()]
    labels += [r.label.lower() for r in TEAM_ROLES.values()]
    assert len(labels) == len(set(labels))


def test_a_key_matches_its_own_entry():
    for key, role in {**VENDOR_ROLES, **TEAM_ROLES}.items():
        assert role.key == key


def test_placeholders_read_as_english():
    """`[Identity Provider]` is something a reader can fill in;
    `[identity_provider]` is a leaked implementation detail."""
    assert VENDOR_ROLES["identity_provider"].placeholder == "[Identity Provider]"
    assert "_" not in VENDOR_ROLES["endpoint_protection"].placeholder


def test_role_lookup_forgives_how_people_write_keys():
    for written in (
        "identity_provider",
        "identity-provider",
        "Identity Provider",
        " IDENTITY_PROVIDER ",
    ):
        assert resolve_role(written, VENDOR_ROLES) is VENDOR_ROLES["identity_provider"]


def test_an_unknown_key_is_reported_rather_than_matched():
    assert resolve_role("blockchain_firewall", VENDOR_ROLES) is None
    assert unknown_roles(["ticketing", "nonsense"], VENDOR_ROLES) == ["nonsense"]


# --------------------------------------------------------------------------
# Reading config
# --------------------------------------------------------------------------


def test_a_role_mapping_is_read_into_assignments():
    profile = _profile()

    assert profile.vendors["identity_provider"].value == "Okta"
    assert profile.teams["identity_access"].value == "IAM Engineering"


def test_the_legacy_flat_list_still_works():
    """Config files in the wild use it, and breaking them to gain precision
    would be a poor trade."""
    profile = load_org_profile({"org": {"vendors": ["Okta", "CrowdStrike"]}})

    assert profile.unkeyed_vendors == ["Okta", "CrowdStrike"]
    assert profile.vendors == {}


def test_a_typo_in_a_role_key_is_surfaced_not_swallowed():
    profile = load_org_profile({"org": {"vendors": {"identity_provdier": "Okta"}}})

    assert profile.unknown == ["identity_provdier"]
    assert profile.vendors == {}


def test_blank_values_are_ignored():
    profile = load_org_profile({"org": {"vendors": {"ticketing": "", "siem": None}}})

    assert profile.vendors == {}
    assert profile.unknown == []


def test_an_empty_org_block_is_not_an_error():
    profile = load_org_profile({})

    assert profile.name == ""
    assert profile.vendors == {}


# --------------------------------------------------------------------------
# Filling in
# --------------------------------------------------------------------------


def test_an_assigned_role_is_replaced_everywhere():
    result = apply_substitutions(
        "Disable in [Identity Provider]; re-enable in [Identity Provider].", _profile()
    )

    assert result.text == "Disable in Okta; re-enable in Okta."
    assert len(result.filled) == 2


def test_the_same_input_always_gives_the_same_output():
    profile = _profile()
    document = "Log it in [Ticketing System] and tell [Identity and Access Management]."

    outputs = {apply_substitutions(document, profile).text for _ in range(10)}

    assert len(outputs) == 1


def test_a_hedged_placeholder_resolves_to_the_configured_name():
    """Models write `[Ticketing System - Jira]` when they have been given a
    name but are unsure the role is right."""
    result = apply_substitutions("Filed in [Ticketing System - Jira].", _profile())

    assert result.text == "Filed in Jira."


def test_a_hyphenated_label_is_not_mistaken_for_a_hedge():
    """`[On-Call Paging]` parsed as the role "On" hedged with "Call Paging"
    before the separator required surrounding whitespace."""
    result = apply_substitutions("Page [On-Call Paging].", _profile())

    assert result.outstanding == ["On-Call Paging"]
    assert result.text == "Page [On-Call Paging]."


def test_an_unassigned_role_is_left_alone_and_reported():
    """A document saying [Backup System] is reporting a gap in the
    configuration, not failing."""
    result = apply_substitutions("Restore from [Backup System].", _profile())

    assert "[Backup System]" in result.text
    assert result.outstanding == ["Backup System"]


def test_a_team_role_fills_the_same_way_a_tool_does():
    result = apply_substitutions("[Identity and Access Management] reviews it.", _profile())

    assert result.text == "IAM Engineering reviews it."


def test_ordinary_bracketed_prose_is_not_mangled():
    result = apply_substitutions("See [1] and the [NIST AC-2] tag.", _profile())

    assert "[1]" in result.text
    assert "[NIST AC-2]" in result.text


# --------------------------------------------------------------------------
# What the generator is told
# --------------------------------------------------------------------------


def test_the_prompt_states_assigned_roles_as_facts():
    rendered = render_for_prompt(_profile())

    assert "Identity Provider: Okta" in rendered
    assert "Identity and Access Management: IAM Engineering" in rendered


def test_the_prompt_does_not_list_roles_nobody_filled():
    """Naming thirty unfilled roles invites the model to mention systems the
    organization never said it had."""
    rendered = render_for_prompt(_profile())

    assert "Backup System" not in rendered
    assert "Vulnerability Scanner" not in rendered


def test_with_no_tools_at_all_the_prompt_asks_for_role_placeholders():
    rendered = render_for_prompt(load_org_profile({"org": {"name": "X"}}))

    assert "square brackets" in rendered
    assert "[Identity Provider]" in rendered


def test_the_generator_uses_the_profile_when_one_is_supplied():
    from policyforge.generate.policy_writer import OrgContext, _render_org

    rendered = _render_org(OrgContext(name="X", industry="Y", profile=_profile()))

    assert "Identity Provider: Okta" in rendered


def test_the_generator_falls_back_to_the_old_shape_without_one():
    from policyforge.generate.policy_writer import OrgContext, _render_org

    rendered = _render_org(OrgContext(name="X", industry="Y", vendors=["Okta"]))

    assert "Okta" in rendered
