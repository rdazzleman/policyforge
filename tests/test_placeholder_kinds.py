"""`generate` reports roles apart from other unfilled placeholders (#228).

Of the brackets `generate` left as "roles", most were not roles: they were
organisation-defined parameter values the model named itself
(`[Access Review Frequency]`), or systems (`[Certificate Authority]`), and
"Add them under `org.vendors` or `org.teams`" was wrong advice for them.

80's ruling (3918, option (iv)): two placeholder lists beside PR 227's
citation list.
- **Roles:** a label the role vocabulary declares (`org.roles`), matched as
  the fill pass matches it, so setting `org.vendors` or `org.teams` would
  fill it. Today's advice.
- **Unfilled:** everything else. Neutral advice naming both places, since
  nothing here can tell a role the vocabulary lacks from a parameter value.
  Carrying a parameter's identity through generation is #240.

The expected sets come from the other side of the check: the labels
`org.roles` declares, and hand-written placeholders a model writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.org.context import OrgProfile, apply_substitutions
from policyforge.org.roles import TEAM_ROLES, VENDOR_ROLES

ROOT = Path(__file__).resolve().parent.parent
CORPUS = sorted((ROOT / "tests" / "fixtures" / "deontic_corpus").rglob("*.txt"))
ROLES = [role.label for role in (*VENDOR_ROLES.values(), *TEAM_ROLES.values())]


#: Every declared label, including "Governance, Risk and Compliance", whose
#: comma the generic placeholder class can't read: it's matched verbatim since
#: #243 (80's ruling (c)), so this was a strict xfail and now passes.
@pytest.mark.parametrize("label", ROLES)
def test_every_declared_role_is_a_role(label):
    result = apply_substitutions(f"Owned by [{label}].", OrgProfile())
    assert result.roles_left == [label] and result.unfilled == [], result


UNFILLED = [
    "Access Review Frequency",  # a parameter value the model named
    "Alert Recipients",
    "Certificate Authority",  # a system
    "CISO",  # a role the vocabulary doesn't declare: no config fills it
    "Security Lead",
]


@pytest.mark.parametrize("label", UNFILLED)
def test_an_undeclared_label_is_unfilled_not_a_role(label):
    result = apply_substitutions(f"Set by [{label}].", OrgProfile())
    assert result.unfilled == [label] and result.roles_left == [], result


def test_a_role_filled_from_config_is_in_neither_list():
    """The fill pass and the roles list read the same labels: a role the
    organisation configured is filled, so it's not left at all."""
    from policyforge.org.context import load_org_profile

    label = ROLES[0]
    key = next(k for k, r in {**VENDOR_ROLES, **TEAM_ROLES}.items() if r.label == label)
    block = "vendors" if key in VENDOR_ROLES else "teams"
    profile = load_org_profile({"org": {"name": "X", block: {key: "Acme"}}})
    result = apply_substitutions(f"Owned by [{label}].", profile)
    assert result.filled and result.roles_left == [] and result.unfilled == [], result


def test_the_three_lists_partition_what_is_left_across_the_corpus():
    """Conservation: in every corpus document, roles, unfilled and unresolved
    citations sum to `outstanding`. And the extent the issue measured holds:
    most of what was called a role is unfilled."""
    roles = unfilled = 0
    for path in CORPUS:
        r = apply_substitutions(path.read_text(encoding="utf-8"), OrgProfile())
        parts = r.roles_left + r.unfilled + r.unresolved_citations
        assert sorted(parts) == sorted(r.outstanding), path.name
        roles += len(r.roles_left)
        unfilled += len(r.unfilled)
    assert len(CORPUS) > 100  # extent: the corpus was read
    assert 0 < roles < unfilled, (roles, unfilled)


# ---- through `generate`, the user's path ------------------------------------

SYNTHESIS = "---\ntopic: Access Control\n---\n\n- Staff must review access. [NIST 800-53 AC-2]\n"
DRAFT = (
    "# Access Control Standard\n\n"
    "Tickets go to the [Ticketing System] every [Access Review Frequency], "
    "signed by the [Certificate Authority] [NIST 800-53 AC-2] [ISO 27001 A.5.1].\n"
)


class _Draft:
    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=DRAFT, model="fake")

    def check(self):
        return True


def test_generate_prints_each_list_with_its_own_advice(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from policyforge.llm import ledger

    synthesis = tmp_path / "access-control.md"
    synthesis.write_text(SYNTHESIS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod, "load_config", lambda: {"org": {"name": "Acme"}, "llm": {"provider": "local"}}
    )
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: ledger.wrap(_Draft(), config))
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--synthesis",
            str(synthesis),
            "--out",
            str(tmp_path / "out" / "access-control.md"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    roles = lines.index("1 role(s) left as placeholders: Ticketing System")
    assert lines[roles + 1] == (
        "  Add them under `org.vendors` or `org.teams` (`policyforge roles` lists the keys), "
        "then regenerate."
    )
    unfilled = lines.index(
        "2 placeholder(s) left unfilled: Access Review Frequency, Certificate Authority"
    )
    assert lines[unfilled + 1] == (
        "  Fill a role under `org.vendors` or `org.teams`, or record a value in the "
        "parameters ledger (`config/parameters.yaml`), then regenerate."
    )
    assert any(line.startswith("1 citation(s) that don't resolve") for line in lines)


@pytest.mark.parametrize("written", ["Ticketing system", "TICKETING SYSTEM"])
def test_a_role_in_another_case_is_a_role_as_the_fill_pass_reads_it(written):
    """The fill pass looks a label up lowercased (`OrgProfile.substitutions`),
    so a model's `[Ticketing system]` IS filled when `ticketing_system` is
    configured. The roles list must read it the same way, or it would call
    unfilled a placeholder that config fills."""
    from policyforge.org.context import load_org_profile

    unconfigured = apply_substitutions(f"Logged in the [{written}].", OrgProfile())
    assert unconfigured.roles_left == [written] and unconfigured.unfilled == [], unconfigured
    key = next(
        k for k, r in {**VENDOR_ROLES, **TEAM_ROLES}.items() if r.label == "Ticketing System"
    )
    block = "vendors" if key in VENDOR_ROLES else "teams"
    configured = apply_substitutions(
        f"Logged in the [{written}].",
        load_org_profile({"org": {"name": "X", block: {key: "Acme"}}}),
    )
    assert configured.filled, "the fill pass fills the case variant, so it's a role"
