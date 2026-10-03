"""ingest/parser_codegen.py tests: prompt grounding, code-fence stripping,
and the empty-sample guard — everything except an actual LLM call."""

from __future__ import annotations


class FakeProvider:
    def __init__(self, text="from __future__ import annotations\n"):
        self.text = text
        self.calls = []

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        self.calls.append({"system": system, "prompt": prompt, "temperature": temperature})
        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def test_generate_byoc_parser_grounds_prompt_in_framework_and_sample():
    from policyforge.ingest.parser_codegen import generate_byoc_parser

    provider = FakeProvider(
        text="from __future__ import annotations\n\ndef load_hitrust_export(): ..."
    )

    result = generate_byoc_parser(
        framework="HITRUST CSF",
        framework_slug="hitrust",
        sample_text="control_id,title\nAC-1,Access Control Policy\n",
        provider=provider,
    )

    assert result.startswith("from __future__ import annotations")
    assert len(provider.calls) == 1
    prompt = provider.calls[0]["prompt"]
    assert "HITRUST CSF" in prompt
    assert "hitrust" in prompt
    assert "control_id,title" in prompt
    assert "AC-1,Access Control Policy" in prompt
    # Deterministic codegen — not creative prose.
    assert provider.calls[0]["temperature"] == 0


def test_generate_byoc_parser_strips_markdown_code_fence():
    from policyforge.ingest.parser_codegen import generate_byoc_parser

    provider = FakeProvider(text="```python\nfrom __future__ import annotations\n\nx = 1\n```")

    result = generate_byoc_parser(
        framework="GovRAMP",
        framework_slug="govramp",
        sample_text="some,export,data",
        provider=provider,
    )

    assert "```" not in result
    assert result == "from __future__ import annotations\n\nx = 1\n"


def test_the_prompt_offers_exactly_the_imports_the_gate_allows():
    """The prompt used to offer pandas, which is not a dependency, so a
    parser following it failed on first run. Now the list is read from the
    gate itself, and the two cannot drift apart."""
    from policyforge.ingest.parser_codegen import _SYSTEM_PROMPT
    from policyforge.ingest.parser_gate import ALLOWED_IMPORTS

    assert "ALLOWED_IMPORTS" not in _SYSTEM_PROMPT, "placeholder was never filled in"
    for module in ALLOWED_IMPORTS - {"__future__"}:
        assert module in _SYSTEM_PROMPT
    assert "pandas, re" not in _SYSTEM_PROMPT


def test_generate_byoc_parser_rejects_empty_sample():
    from policyforge.ingest.parser_codegen import generate_byoc_parser

    try:
        generate_byoc_parser(
            framework="HITRUST CSF", framework_slug="hitrust", sample_text="   ", provider=None
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an empty sample_text")
