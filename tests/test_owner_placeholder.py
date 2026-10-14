"""Tests for #422: document's own owner is unbracketed and not reported unfilled."""

from __future__ import annotations

from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.llm.base import LLMResponse
from policyforge.org.context import OrgProfile, apply_substitutions, load_org_profile


class FakeProvider:
    def __init__(self, text: str = "# Doc\n\n..."):
        self.text = text
        self.calls: list[dict] = []

    def generate(
        self, *, system: str, prompt: str, max_tokens: int = 4096, temperature: float = 0.2
    ):
        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def test_apply_substitutions_unbrackets_matching_owner():
    """A hand-written document body containing [IT Asset Management], with owner
    IT Asset Management, comes out unbracketed and isn't reported unfilled.
    [Network Security] in the same body, with that owner, stays bracketed (#422)."""
    body = "Document owned by [IT Asset Management]. Contact [Network Security]."
    result = apply_substitutions(body, OrgProfile(), owner="IT Asset Management")

    assert result.text == "Document owned by IT Asset Management. Contact [Network Security]."
    assert "IT Asset Management" not in result.outstanding
    assert "IT Asset Management" not in result.unfilled
    assert "Network Security" in result.outstanding
    assert "Network Security" in result.unfilled


def test_apply_substitutions_owner_matching_is_case_sensitive():
    """Matching is case-sensitive: [It Asset Management] does not match
    owner 'IT Asset Management'."""
    body = "Document owned by [It Asset Management]."
    result = apply_substitutions(body, OrgProfile(), owner="IT Asset Management")

    assert result.text == "Document owned by [It Asset Management]."
    assert "It Asset Management" in result.outstanding


def test_apply_substitutions_owner_matching_is_whitespace_trimmed():
    """Matching is whitespace-trimmed: extra padding around owner argument still matches."""
    body = "Document owned by [IT Asset Management]."
    result = apply_substitutions(body, OrgProfile(), owner="  IT Asset Management  ")

    assert result.text == "Document owned by IT Asset Management."
    assert "IT Asset Management" not in result.outstanding


def test_generate_unbrackets_synthesis_owner_and_does_not_report_it_unfilled(tmp_path, monkeypatch):
    """End-to-end generate unbrackets topic owner from synthesis frontmatter
    and does not report it as an unfilled placeholder."""
    monkeypatch.chdir(tmp_path)
    synthesis_path = tmp_path / "media-handling.md"
    synthesis_path.write_text(
        "---\ntopic: Media Handling\nowner: IT Asset Management\n---\n\n"
        "- Media must be sanitized. [NIST MP-6]\n",
        encoding="utf-8",
    )
    config = {
        "org": {"name": "Acme", "industry": "H"},
        "zardoz": {"host": "https://wiki.example.com"},
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(
        cli_mod,
        "get_provider",
        lambda _cfg: FakeProvider(
            "# Media Handling Standard\n\n"
            "Maintained by [IT Asset Management]. Consult [Network Security].\n"
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        ["generate", "--tier", "standard", "--synthesis", str(synthesis_path)],
    )

    assert result.exit_code == 0, result.output
    output_doc = tmp_path / "docs" / "standards" / "media-handling.md"
    content = output_doc.read_text(encoding="utf-8")

    assert "Maintained by IT Asset Management." in content
    assert "Consult [Network Security]." in content
    assert "IT Asset Management" not in result.output.split("left unfilled:")[1]
    assert "Network Security" in result.output


def test_a_declared_team_keeps_its_value_when_it_is_also_the_owner():
    """b5 on 422: `IT Operations` is a team role the organization filled under
    `org.teams` and may also be a topic's owner. The value it gave wins; the
    owner's name only fills a label nothing else fills."""
    profile = load_org_profile({"org": {"teams": {"IT Operations": "Acme Infrastructure Group"}}})
    body = "Run by [IT Operations]. Ask [Network Security]."
    result = apply_substitutions(body, profile, owner="IT Operations")

    assert result.text == "Run by Acme Infrastructure Group. Ask [Network Security]."
    assert result.filled == [("IT Operations", "Acme Infrastructure Group")]


def test_an_owner_that_is_an_undeclared_role_label_is_a_name_not_a_role_left_over():
    """`Security Operations` is a role label, so unbracketed-and-unfilled would
    advise the user to add it under `org.teams`. As the document's own owner it
    is neither left as a role nor reported."""
    result = apply_substitutions(
        "Run by [Security Operations].", OrgProfile(), owner="Security Operations"
    )

    assert result.text == "Run by Security Operations."
    assert result.roles_left == [] and result.unfilled == []
    assert result.filled == [("Security Operations", "Security Operations")]


def test_no_owner_changes_nothing():
    """No owner given (the frontmatter has none): the label is still a role
    left as a placeholder, as before."""
    for owner in ("", None, "   "):
        result = apply_substitutions("Run by [Security Operations].", OrgProfile(), owner=owner)
        assert result.text == "Run by [Security Operations]."
        assert result.roles_left == ["Security Operations"]


def test_only_the_whole_owner_is_unbracketed_not_a_part_of_it_or_a_longer_label():
    """ba on 432: a label contained in the owner (`[IT]`) and a label that
    contains it (`[IT Asset Management Board]`) are other placeholders; they
    stay bracketed and are reported, not filled with the owner's name."""
    body = "Ask [IT] or [Asset Management]; escalate to [IT Asset Management Board]."
    result = apply_substitutions(body, OrgProfile(), owner="IT Asset Management")

    assert result.text == body
    assert result.filled == []
    assert result.outstanding == ["IT", "Asset Management", "IT Asset Management Board"]
