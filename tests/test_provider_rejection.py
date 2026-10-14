"""A provider's rejection reaches the user as a sentence naming the call.

With document budgets at 16384, a model whose output cap is lower rejects
every draft, and that used to arrive as an SDK traceback ending in
"HTTP 400". Now each provider raises `ProviderRejected` with the vendor's
own words, `llm/effort.py` adds the ledger subject, the site and the budget
the request carried, and the CLI prints that and exits non-zero with
nothing written. Nothing is clamped silently.

Driven from each provider's own SDK or transport boundary, as
`test_stop_reason_contract.py` does, because a fake provider that raises
the right type proves nothing about the provider that has to map it.
"""

from __future__ import annotations

import json
import sys
import types

import pytest
from click.testing import CliRunner

from policyforge.llm import effort, ledger
from policyforge.llm.base import ProviderRejected

VENDOR = "max_tokens: 16384 > 8192, which is the maximum allowed number of output tokens"
_KEY = "POLICYFORGE_REJECT_KEY"


# ---- the annotation --------------------------------------------------------


class Refusing:
    """A provider that has already mapped the vendor's 400."""

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2, **kwargs):
        raise ProviderRejected(VENDOR, status=400, model="capped-model")

    def check(self):
        return True


def test_the_helper_names_the_subject_site_and_budget_on_the_vendors_message():
    with (
        ledger.about("standard/incident-response", site="generate"),
        pytest.raises(ProviderRejected) as caught,
    ):
        effort.call(Refusing(), system="S", prompt="P", max_tokens=16384)

    exc = caught.value
    assert exc.vendor_message == VENDOR
    assert exc.subject == "standard/incident-response"
    assert exc.site == "generate"
    assert exc.budget == 16384
    message = str(exc)
    assert message.startswith("standard/incident-response (generate): the request at 16384 output")
    assert "rejected by capped-model (HTTP 400)" in message
    assert VENDOR in message
    assert "Nothing was written" in message
    assert "lower the budget" in message


def test_a_rejection_is_not_retried_at_a_larger_budget():
    """A retry at twice the budget would be rejected harder."""
    calls = []

    class Counting(Refusing):
        def generate(self, **kwargs):
            calls.append(kwargs["max_tokens"])
            return super().generate(**kwargs)

    with pytest.raises(ProviderRejected):
        effort.call(Counting(), system="S", prompt="P", max_tokens=4096)

    assert calls == [4096]


# ---- the writers ------------------------------------------------------------


def test_generate_prints_the_rejection_and_writes_nothing(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    synthesis = tmp_path / "incident-response.md"
    synthesis.write_text("- Incidents must be reported. [NIST IR-6]\n", encoding="utf-8")
    out_path = tmp_path / "standards" / "incident-response.md"
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"org": {"name": "Acme"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: Refusing())

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(synthesis),
            "--out",
            str(out_path),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        "a rejection must be a clean exit, not a traceback"
    )
    assert "standard/incident-response (generate)" in result.output
    # The Standard budget, as a literal on purpose: a test that read
    # LONG_DOCUMENT_TOKENS would pass under any change to it (32768 since #3).
    assert "32768 output tokens" in result.output
    assert VENDOR in result.output
    assert not out_path.exists()


# ---- each provider, from its own boundary ------------------------------------


@pytest.fixture
def stubbed_sdks(monkeypatch):
    monkeypatch.setenv(_KEY, "not-a-real-key")

    anthropic = types.ModuleType("anthropic")

    class BadRequestError(Exception):
        status_code = 400

    class _Messages:
        def create(self, **kwargs):
            raise BadRequestError(VENDOR)

    class _Client:
        def __init__(self, *, api_key=None, **kwargs):
            self.messages = _Messages()

    anthropic.BadRequestError = BadRequestError
    anthropic.Anthropic = _Client
    anthropic.AnthropicVertex = _Client
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)

    litellm = types.ModuleType("litellm")

    class LiteLLMBadRequest(Exception):
        status_code = 400

    def completion(**kwargs):
        raise LiteLLMBadRequest(VENDOR)

    litellm.BadRequestError = LiteLLMBadRequest
    litellm.completion = completion
    litellm.supports_response_schema = lambda model: False
    litellm.suppress_debug_info = False
    # What `llm.litellm_map.load()` touches the first time it runs in a
    # process, and its guard reset per test (monkeypatch restores it), so
    # the result doesn't depend on what ran first (#86; #248's shape).
    litellm.model_cost = {}
    litellm.register_model = lambda model_cost: None
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    from policyforge.llm import litellm_map

    monkeypatch.setattr(litellm_map, "_registered", None)
    return anthropic


def _rejecting_provider(name: str, stub):
    if name == "anthropic":
        from policyforge.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model="claude-sonnet-5", api_key_env=_KEY)
    if name == "vertex":
        from policyforge.llm.vertex_provider import VertexProvider

        return VertexProvider(model="claude-sonnet-5", project_id="p", client=stub.Anthropic())
    if name == "litellm":
        from policyforge.llm.litellm_provider import LiteLLMProvider

        return LiteLLMProvider(model="openrouter/some/model")
    if name == "openai-compat":
        from policyforge.llm.openai_compat_provider import OpenAICompatProvider

        class Session:
            def post(self, url, *, json, headers, timeout):
                class Response:
                    status_code = 400
                    text = json_module.dumps({"error": {"message": VENDOR}})

                return Response()

        json_module = json
        return OpenAICompatProvider(
            model="qwen3:14b", base_url="http://localhost:11434/v1", session=Session()
        )
    if name == "bedrock":
        from policyforge.llm.bedrock_provider import BedrockProvider

        class ClientError(Exception):
            response = {"Error": {"Code": "ValidationException", "Message": VENDOR}}

        class Client:
            def converse(self, **kwargs):
                raise ClientError(VENDOR)

        return BedrockProvider(model="anthropic.claude-sonnet-5", client=Client())
    raise AssertionError(name)


@pytest.mark.parametrize("name", ["anthropic", "vertex", "litellm", "openai-compat", "bedrock"])
def test_each_provider_maps_its_vendors_400_to_a_rejection(name, stubbed_sdks):
    provider = _rejecting_provider(name, stubbed_sdks)

    with (
        ledger.about("synthesis/x", site="synthesize"),
        pytest.raises(ProviderRejected) as caught,
    ):
        effort.call(provider, system="S", prompt="P", max_tokens=16384)

    assert VENDOR in caught.value.vendor_message
    assert caught.value.budget == 16384
    assert caught.value.site == "synthesize"


def test_a_server_failure_is_not_a_rejection():
    """A 5xx is the server failing, not the request being refused."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    class Session:
        def post(self, url, *, json, headers, timeout):
            class Response:
                status_code = 503
                text = "overloaded"

            return Response()

    provider = OpenAICompatProvider(
        model="m", base_url="http://localhost:11434/v1", session=Session()
    )

    with pytest.raises(RuntimeError) as caught:
        provider.generate(system="S", prompt="P")
    assert not isinstance(caught.value, ProviderRejected)
    assert "503" in str(caught.value)


def test_the_temperature_workaround_still_retries_rather_than_rejecting(stubbed_sdks):
    """The one 400 that is handled, not surfaced: a model that rejects
    `temperature` gets the request again without it."""
    from policyforge.llm.litellm_provider import LiteLLMProvider

    sent = []

    def completion(**kwargs):
        sent.append(kwargs)
        if "temperature" in kwargs:
            raise sys.modules["litellm"].BadRequestError("temperature is deprecated for this model")

        class Message:
            content = "fine"

        class Choice:
            message = Message()
            finish_reason = "stop"

        class Usage:
            prompt_tokens = 1
            completion_tokens = 1
            prompt_tokens_details = None

        class Response:
            choices = [Choice()]
            model = "m"
            usage = Usage()
            _hidden_params = {}

        return Response()

    provider = LiteLLMProvider(model="openrouter/some/model", completion=completion)

    assert provider.generate(system="S", prompt="P").text == "fine"
    assert [("temperature" in k) for k in sent] == [True, False]


@pytest.mark.parametrize("status, body", [(429, "rate limited"), (401, "invalid api key")])
def test_a_rate_limit_or_an_auth_failure_is_not_a_rejection(status, body):
    """A 429 clears by waiting and a 401 is the key; neither is the request
    being refused as written, and budget advice on either would send the
    user to change the wrong thing. They stay the plain error they were."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    class Session:
        def post(self, url, *, json, headers, timeout):
            class Response:
                status_code = status
                text = body

            return Response()

    provider = OpenAICompatProvider(
        model="m", base_url="http://localhost:11434/v1", session=Session()
    )

    with pytest.raises(RuntimeError) as caught:
        provider.generate(system="S", prompt="P")
    assert not isinstance(caught.value, ProviderRejected)
    assert str(status) in str(caught.value)
    assert "budget" not in str(caught.value)


@pytest.mark.parametrize("status", [400, 404, 413, 422])
def test_the_statuses_that_mean_refused_as_written(status):
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    class Session:
        def post(self, url, *, json, headers, timeout):
            class Response:
                status_code = status
                text = VENDOR

            return Response()

    provider = OpenAICompatProvider(
        model="m", base_url="http://localhost:11434/v1", session=Session()
    )

    with pytest.raises(ProviderRejected) as caught:
        provider.generate(system="S", prompt="P")
    assert caught.value.status == status
