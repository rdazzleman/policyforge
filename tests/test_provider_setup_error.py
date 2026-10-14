"""A provider that can't be built says so in one line, with exit 2 (#359).

A missing key variable or optional extra used to raise `RuntimeError` while
the provider was built, so `policyforge llm-check` with the example config
and no key printed a 39-line traceback and exited 1, with a good message at
the bottom of it. Those seven sites raise `ProviderSetupError` now: a
`ConfigError`, which the CLI prints in one line with exit 2, and still a
`RuntimeError`, so the callers that catch that (the Zardoz shell starts
without a model; the entailment judge) keep catching it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.cli import cli
from policyforge.config import ConfigError, ProviderSetupError

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "config.example.yaml"


def test_llm_check_without_a_key_is_one_line_and_exit_2(monkeypatch):
    """The issue's case: the example config (provider anthropic) and no key."""
    monkeypatch.setenv("POLICYFORGE_CONFIG", str(EXAMPLE_CONFIG))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = CliRunner().invoke(cli, ["llm-check"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "ANTHROPIC_API_KEY" in result.output
    assert len(result.output.strip().splitlines()) <= 6, result.output


def _no_module(monkeypatch, name):
    """`import name` and `from name import X` raise ImportError."""
    monkeypatch.setitem(sys.modules, name, None)


def _missing_key(monkeypatch):
    monkeypatch.delenv("PF_TEST_UNSET_KEY", raising=False)
    return "PF_TEST_UNSET_KEY"


def _anthropic(monkeypatch):
    from policyforge.llm.anthropic_provider import AnthropicProvider

    AnthropicProvider(model="m", api_key_env=_missing_key(monkeypatch))


def _gemini(monkeypatch):
    from policyforge.llm.gemini_provider import GeminiProvider

    GeminiProvider(model="m", api_key_env=_missing_key(monkeypatch))


def _litellm(monkeypatch):
    from policyforge.llm.litellm_provider import LiteLLMProvider

    LiteLLMProvider(model="openai/m", api_key_env=_missing_key(monkeypatch))


def _litellm_extra(monkeypatch):
    """The seventh site (1d on PR 363): the litellm package itself missing."""
    from policyforge.llm.litellm_provider import LiteLLMProvider

    _no_module(monkeypatch, "litellm")
    LiteLLMProvider(model="openai/m")


def _openai_compat(monkeypatch):
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    OpenAICompatProvider(
        model="m", base_url="http://localhost:1/v1", api_key_env=_missing_key(monkeypatch)
    )


def _bedrock(monkeypatch):
    from policyforge.llm.bedrock_provider import BedrockProvider

    _no_module(monkeypatch, "boto3")
    BedrockProvider(model="m")


def _vertex(monkeypatch):
    from policyforge.llm.vertex_provider import VertexProvider

    # `from anthropic import AnthropicVertex` fails as it does without the extra.
    monkeypatch.setitem(sys.modules, "anthropic", types.ModuleType("anthropic"))
    VertexProvider(model="m", project_id="p")


@pytest.mark.parametrize(
    ("build", "says"),
    [
        (_anthropic, "PF_TEST_UNSET_KEY is not set"),
        (_gemini, "PF_TEST_UNSET_KEY is not set"),
        (_litellm, "PF_TEST_UNSET_KEY is not set"),
        (_litellm_extra, 'pip install "policyforge[litellm]"'),
        (_openai_compat, "PF_TEST_UNSET_KEY is not set"),
        (_bedrock, 'pip install "policyforge[bedrock]"'),
        (_vertex, 'pip install "policyforge[vertex]"'),
    ],
    ids=["anthropic", "gemini", "litellm", "litellm-extra", "openai-compat", "bedrock", "vertex"],
)
def test_each_setup_site_raises_a_config_error_that_is_still_a_runtime_error(
    monkeypatch, build, says
):
    with pytest.raises(ProviderSetupError) as raised:
        build(monkeypatch)

    assert isinstance(raised.value, ConfigError), "the CLI's one-line handler catches it"
    assert isinstance(raised.value, RuntimeError), "callers that caught RuntimeError still do"
    assert says in str(raised.value), "the message text is unchanged"


def test_zardoz_discover_without_a_key_groups_by_convention_and_says_why(tmp_path, monkeypatch):
    """80's ruling on 359 (a), from ba's finding on PR 363: `zardoz discover`
    catches ValueError around building its model and falls back to grouping
    by convention, and a ProviderSetupError is a ValueError. So with no key it
    no longer fails: it carries on without the model, and the line it prints
    names the variable that's missing."""
    import policyforge.zardoz.discover as discover

    config = tmp_path / "config.yaml"
    config.write_text(
        "llm:\n  provider: anthropic\n  model: m\n  api_key_env: PF_TEST_UNSET_KEY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POLICYFORGE_CONFIG", str(config))
    monkeypatch.delenv("PF_TEST_UNSET_KEY", raising=False)
    monkeypatch.setattr("policyforge.export.confluence_search.search_pages", lambda **_: [])
    given = []
    real = discover.discover_topics
    monkeypatch.setattr(
        discover,
        "discover_topics",
        lambda pages, *, space, provider: (
            given.append(provider) or real(pages, space=space, provider=provider)
        ),
    )

    result = CliRunner().invoke(
        cli,
        [
            "zardoz",
            "discover",
            "--space",
            "SEC",
            "--host",
            "https://x",
            "--out",
            str(tmp_path / "t.yaml"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "no usable llm config" in result.output
    assert "PF_TEST_UNSET_KEY" in result.output, "the line names the missing variable"
    assert "grouping by convention only" in result.output
    assert given == [None], "the crawl went ahead without a model"
