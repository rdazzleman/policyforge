"""The harness measures the provider production runs, built the same way.

`scripts/eval_zardoz.py --model` used to construct a `LiteLLMProvider` by
name and meter it, while every command built its provider through
`get_provider`, which wraps it in the call ledger. When that wrapper hid
every `supports_*` flag, the harness kept sending effort and the CLI did
not, and five recorded epochs described a request production never made.
Nothing compared the two paths, so nothing could have said so.

These tests compare them. For every provider the factory can build offline,
the eval path and the production path must yield the same provider class
under their wrappers, the same answer to every discovered `supports_*`
flag, and the same effort, caching, grounding and schema decisions in
`llm/effort.py` — the functions that decide what a request contains.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.provider import EVAL_LEDGER_NAME, EVAL_SITE, Metered, build_provider, eval_config
from policyforge.llm import effort, ledger
from policyforge.llm.base import LLMProvider, LLMResponse, get_provider
from policyforge.llm.ledger import RecordingProvider

#: Discovered, not listed: a flag added to `LLMProvider` is held to parity
#: without anyone editing this file.
FLAGS = sorted(
    name
    for name in dir(LLMProvider)
    if name.startswith("supports_") and callable(getattr(LLMProvider, name))
)

#: The decisions `llm/effort.py` makes from those flags — what actually
#: shapes a request.
DECISIONS = (
    effort.accepts_effort,
    effort.accepts_caching,
    effort.accepts_grounding,
    effort.accepts_schema,
)

_KEY = "POLICYFORGE_TEST_KEY"

OFFLINE_CONFIGS = {
    "anthropic": {"provider": "anthropic", "model": "claude-sonnet-5", "api_key_env": _KEY},
    "litellm": {
        "provider": "litellm",
        "model": "openrouter/deepseek/deepseek-v4-flash",
        "api_key_env": _KEY,
    },
    "local": {"provider": "local", "model": "qwen3:14b", "base_url": "http://localhost:11434/v1"},
    "bedrock": {"provider": "bedrock", "model": "anthropic.claude-sonnet-5", "region": "us-east-1"},
    "vertex": {"provider": "vertex", "model": "claude-sonnet-5", "project_id": "example-project"},
    "cascade": {
        "provider": "cascade",
        "primary": {
            "provider": "local",
            "model": "qwen3:14b",
            "base_url": "http://127.0.0.1:11434/v1",
        },
        "escalate_to": {"provider": "anthropic", "model": "claude-sonnet-5", "api_key_env": _KEY},
    },
}

#: Built from the core dependencies alone; a skip on one of these is the
#: test quietly stopping.
ALWAYS_CONSTRUCTIBLE = {"anthropic", "local", "cascade"}


def _real_provider(provider):
    """The provider under the meter and under the ledger."""
    inner = provider.provider if isinstance(provider, Metered) else provider
    return inner.inner if isinstance(inner, RecordingProvider) else inner


def test_the_flags_are_discovered_not_listed():
    assert len(FLAGS) >= 5


@pytest.mark.parametrize("name", sorted(OFFLINE_CONFIGS))
def test_the_harness_builds_what_production_builds(tmp_path, monkeypatch, name):
    monkeypatch.setenv(_KEY, "not-a-real-key")
    base = {"llm": {**OFFLINE_CONFIGS[name], "ledger": {"path": str(tmp_path / "calls.jsonl")}}}
    try:
        production = get_provider(base)
        measured = build_provider(eval_config(base=base))
    except (ImportError, RuntimeError) as exc:
        if name in ALWAYS_CONSTRUCTIBLE:
            raise
        pytest.skip(f"{name} needs an extra this environment lacks: {exc}")

    assert isinstance(measured, Metered)
    assert isinstance(measured.provider, RecordingProvider)
    assert type(_real_provider(measured)) is type(_real_provider(production))
    for flag in FLAGS:
        assert getattr(measured, flag)() == getattr(production, flag)(), flag
    for decide in DECISIONS:
        assert decide(measured) == decide(production), decide.__name__


def test_the_model_flag_is_a_config_override_not_a_constructor(monkeypatch):
    """`--model` edits the llm block; `get_provider` still builds it."""
    import evals.provider as provider_module

    handed = []
    monkeypatch.setattr(
        provider_module, "get_provider", lambda config: handed.append(config) or object()
    )

    config = eval_config(model="openrouter/deepseek/deepseek-v4-flash", min_interval=2.0, base={})
    build_provider(config)

    assert handed == [config]
    assert config["llm"]["provider"] == "litellm"
    assert config["llm"]["model"] == "openrouter/deepseek/deepseek-v4-flash"
    assert config["llm"]["min_interval_seconds"] == 2.0


def test_the_interval_reaches_only_a_provider_that_paces_requests():
    """`min_interval_seconds` is a LiteLLM setting; nothing else reads it."""
    local = {"provider": "local", "model": "m", "base_url": "http://localhost:11434/v1"}

    config = eval_config(min_interval=2.0, base={"llm": local})

    assert "min_interval_seconds" not in config["llm"]


def test_eval_calls_land_in_their_own_ledger_file(tmp_path):
    """Beside the production ledger, wherever config put that."""
    moved = {
        "llm": {
            "provider": "local",
            "model": "m",
            "base_url": "http://localhost:11434/v1",
            "ledger": {"path": str(tmp_path / "custom" / "calls.jsonl")},
        }
    }

    assert eval_config(base=moved)["llm"]["ledger"]["path"] == str(
        tmp_path / "custom" / EVAL_LEDGER_NAME
    )
    # With nothing configured: beside wherever the ledger's default is —
    # read from the ledger rather than restated, since the test suite may
    # point that default somewhere other than output/.
    default = Path(ledger.ledger_path({}))
    assert eval_config(model="x", base={})["llm"]["ledger"]["path"] == str(
        default.with_name(EVAL_LEDGER_NAME)
    )


def test_a_ledger_the_config_turned_off_stays_off_under_the_harness():
    """Turning it off is the config's decision, and `--model` keeps it."""
    base = {
        "llm": {
            "provider": "local",
            "model": "m",
            "base_url": "http://localhost:11434/v1",
            "ledger": {"enabled": False},
        }
    }

    measured = build_provider(eval_config(base=base))

    assert not isinstance(measured.provider, RecordingProvider)
    assert eval_config(model="x", base=base)["llm"]["ledger"]["enabled"] is False


def test_no_config_and_no_model_is_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("POLICYFORGE_CONFIG", raising=False)

    with pytest.raises(FileNotFoundError):
        eval_config()


def test_a_case_is_recorded_under_its_suite_and_name(tmp_path, monkeypatch):
    """Not `(unattributed)`: the case it graded, at the site `eval`."""
    from evals import runner

    class Fake(LLMProvider):
        def generate(self, **kwargs):
            return LLMResponse(text="ok", model="fake")

        def check(self):
            return True

    def probe(case, provider, corpora):
        provider.generate(system="S", prompt=case["question"])
        return runner.Outcome(True, "")

    monkeypatch.setitem(runner.SUITES, "probe", probe)
    provider = Metered(
        RecordingProvider(
            Fake(),
            provider_name="fake",
            provider_class="local",
            path=tmp_path / EVAL_LEDGER_NAME,
        )
    )

    runner.run_case("probe", {"name": "the-case", "question": "q?"}, provider)

    (record,) = ledger.load(tmp_path / EVAL_LEDGER_NAME)
    assert record.subject == "probe/the-case"
    assert record.site == EVAL_SITE
    assert provider.calls == 1


# ---- the provider --model always builds, held to parity where CI runs -----
#
# The `litellm` case above skips wherever the extra is absent, and CI
# installs only the dev extras — so the one provider `--model` always builds
# would be the one CI never compared. `get_provider` takes no injected
# completion, so the module itself is stubbed: `LiteLLMProvider` reads
# `completion`, `BadRequestError`, `supports_response_schema` and sets
# `suppress_debug_info` on it, and nothing else. `monkeypatch.setitem`
# undoes the stub, so a real litellm elsewhere in the run is untouched.


@pytest.fixture
def litellm_stub(monkeypatch):
    import sys
    import types

    stub = types.ModuleType("litellm")
    stub.completion = lambda **kwargs: None
    stub.BadRequestError = type("BadRequestError", (Exception,), {})
    stub.supports_response_schema = lambda model: True
    stub.suppress_debug_info = False
    monkeypatch.setitem(sys.modules, "litellm", stub)
    return stub


def _assert_parity(measured, production):
    from policyforge.llm.litellm_provider import LiteLLMProvider

    assert isinstance(measured, Metered)
    assert isinstance(measured.provider, RecordingProvider)
    assert type(_real_provider(measured)) is LiteLLMProvider
    assert type(_real_provider(production)) is LiteLLMProvider
    for flag in FLAGS:
        assert getattr(measured, flag)() == getattr(production, flag)(), flag
    for decide in DECISIONS:
        assert decide(measured) == decide(production), decide.__name__
    # The flag this bug hit hardest: LiteLLM advertises effort, and both
    # paths must say so.
    assert effort.accepts_effort(measured) is True
    assert effort.accepts_effort(production) is True


def test_the_litellm_config_builds_the_same_both_ways_without_litellm(
    tmp_path, monkeypatch, litellm_stub
):
    monkeypatch.setenv(_KEY, "not-a-real-key")
    base = {"llm": {**OFFLINE_CONFIGS["litellm"], "ledger": {"path": str(tmp_path / "c.jsonl")}}}

    production = get_provider(base)
    measured = build_provider(eval_config(base=base))

    _assert_parity(measured, production)


def test_the_model_flag_builds_the_same_provider_production_would_without_litellm(
    tmp_path, monkeypatch, litellm_stub
):
    """`--model openrouter/…` against the config a user would write for it."""
    model = "openrouter/deepseek/deepseek-v4-flash"
    ledger_file = str(tmp_path / "c.jsonl")

    measured = build_provider(
        eval_config(model=model, min_interval=1.5, base={"llm": {"ledger": {"path": ledger_file}}})
    )
    production = get_provider(
        {
            "llm": {
                "provider": "litellm",
                "model": model,
                "min_interval_seconds": 1.5,
                "ledger": {"path": ledger_file},
            }
        }
    )

    _assert_parity(measured, production)
    assert _real_provider(measured).model == model
    assert _real_provider(measured).min_interval_seconds == 1.5
