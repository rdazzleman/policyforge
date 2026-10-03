"""generate/policy_writer.py tests: the Standard tier (unchanged behavior,
renamed from generate_procedure), the Policy tier, the Procedure tier, and
the title extraction helper that lets a Policy/Procedure reference its
Standard by name."""

from __future__ import annotations


class FakeProvider:
    def __init__(self, text="# Doc\n\n..."):
        self.text = text
        self.calls = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls.append({"system": system, "prompt": prompt})
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def test_generate_standard_grounds_prompt_in_synthesis_and_org_context():
    from policyforge.generate.policy_writer import OrgContext, generate_standard

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=["Okta"])
    provider = FakeProvider(text="# Authenticator Management Standard\n\n...")

    result = generate_standard("- Authenticators must be managed. [NIST IA-5]", org, provider)

    assert result == "# Authenticator Management Standard\n\n..."
    assert len(provider.calls) == 1
    prompt = provider.calls[0]["prompt"]
    assert "Acme Corp" in prompt
    assert "Okta" in prompt
    assert "Authenticators must be managed." in prompt
    assert "STANDARD" in provider.calls[0]["system"]


def test_generate_standard_rejects_empty_synthesis():
    from policyforge.generate.policy_writer import OrgContext, generate_standard

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=[])
    try:
        generate_standard("   ", org, provider=None)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty topic_synthesis")


def test_generate_policy_grounds_prompt_and_references_standard_title():
    from policyforge.generate.policy_writer import OrgContext, generate_policy

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=["Okta"])
    provider = FakeProvider(text="# Authenticator Management Policy\n\n...")

    result = generate_policy(
        "- Authenticators must be managed. [NIST IA-5]\n"
        "- Sessions must time out after 15 minutes. [NIST AC-11]",
        org,
        provider,
        standard_title="Authenticator Management Standard",
    )

    assert result == "# Authenticator Management Policy\n\n..."
    assert len(provider.calls) == 1
    prompt = provider.calls[0]["prompt"]
    assert "Acme Corp" in prompt
    assert "Authenticator Management Standard" in prompt
    assert "Authenticators must be managed." in prompt
    # The Policy system prompt must forbid framework-ID citations in its output.
    assert "Never cite a framework or control" in provider.calls[0]["system"]


def test_generate_policy_rejects_empty_synthesis():
    from policyforge.generate.policy_writer import OrgContext, generate_policy

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=[])
    try:
        generate_policy("   ", org, provider=None, standard_title="Some Standard")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty topic_synthesis")


def test_generate_policy_requires_standard_title():
    from policyforge.generate.policy_writer import OrgContext, generate_policy

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=[])
    try:
        generate_policy("- a requirement", org, provider=None, standard_title="   ")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a blank standard_title")


def test_generate_procedure_grounds_prompt_and_references_standard_title():
    from policyforge.generate.policy_writer import OrgContext, generate_procedure

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=["Okta"])
    provider = FakeProvider(text="# Authenticator Management Procedure\n\n...")

    result = generate_procedure(
        "- Authenticators must be managed. [NIST IA-5]",
        org,
        provider,
        standard_title="Authenticator Management Standard",
    )

    assert result == "# Authenticator Management Procedure\n\n..."
    assert len(provider.calls) == 1
    prompt = provider.calls[0]["prompt"]
    assert "Acme Corp" in prompt
    assert "Authenticator Management Standard" in prompt
    assert "Authenticators must be managed." in prompt
    # The Procedure system prompt must preserve source tags, unlike Policy.
    assert "source tag" in provider.calls[0]["system"]
    assert "PROCEDURE" in provider.calls[0]["system"]


def test_generate_procedure_rejects_empty_synthesis():
    from policyforge.generate.policy_writer import OrgContext, generate_procedure

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=[])
    try:
        generate_procedure("   ", org, provider=None, standard_title="Some Standard")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty topic_synthesis")


def test_generate_procedure_requires_standard_title():
    from policyforge.generate.policy_writer import OrgContext, generate_procedure

    org = OrgContext(name="Acme Corp", industry="Fintech", vendors=[])
    try:
        generate_procedure("- a requirement", org, provider=None, standard_title="   ")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a blank standard_title")


def test_extract_title_pulls_leading_h1():
    from policyforge.generate.policy_writer import extract_title

    markdown = "# Authenticator Management Standard\n\nSome body text.\n"

    assert extract_title(markdown) == "Authenticator Management Standard"


def test_extract_title_raises_without_a_heading():
    from policyforge.generate.policy_writer import extract_title

    try:
        extract_title("Just a paragraph, no heading.\n")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no '# ' heading is present")
