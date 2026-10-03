"""Every provider carries the vendor's cut-off signal into `stop_reason`.

The truncation fix (#74) passed its tests and review while
`openai_compat_provider` never set `stop_reason`: the fakes set the field
directly, which tested the fakes. On Ollama, vLLM or any OpenAI-compatible
server - the local setup the docs recommend for licensed content - a
non-empty reply cut off at `max_tokens` was still written silently.

So these tests do not trust a fake provider. For every provider the
factory can build, a fake is injected at the SDK or transport boundary
that returns that vendor's own cut-off wire shape with non-empty text, and
the provider must report `.truncated` and `effort.call` must refuse after
its retry. The provider list is read from `_build_provider`'s source, so a
new provider fails here until it has a mapping below.
"""

from __future__ import annotations

import ast
import inspect
import sys
import types

import pytest

from policyforge.llm import base, effort
from policyforge.llm.base import TruncatedResponse

CUT_TEXT = "- Implement an incident handling capability [IR-4]\n- Require personnel to"
_KEY = "POLICYFORGE_CONTRACT_KEY"


def factory_provider_names() -> list[str]:
    """Every `provider_name == "..."` / `in (...)` the factory dispatches on."""
    tree = ast.parse(inspect.getsource(base._build_provider))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)):
            continue
        if node.left.id != "provider_name":
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant):
                names.add(comparator.value)
            elif isinstance(comparator, ast.Tuple):
                names.update(c.value for c in comparator.elts if isinstance(c, ast.Constant))
    return sorted(names)


# ---- one fake per vendor, at the boundary the provider actually calls ----


class _AnthropicMessages:
    """`client.messages.create(...)` returning a message cut off at max_tokens."""

    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class Block:
            type = "text"
            text = CUT_TEXT

        class Usage:
            input_tokens = 40
            output_tokens = kwargs.get("max_tokens", 0)

        class Message:
            content = [Block()]
            usage = Usage()
            stop_reason = "max_tokens"

        return Message()


class _AnthropicClient:
    def __init__(self):
        self.messages = _AnthropicMessages()


class _BedrockClient:
    """`client.converse(...)` returning a Converse response with stopReason."""

    def __init__(self):
        self.calls: list[dict] = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": {"message": {"content": [{"text": CUT_TEXT}]}},
            "usage": {"inputTokens": 40, "outputTokens": kwargs["inferenceConfig"]["maxTokens"]},
            "stopReason": "max_tokens",
        }


class _OpenAISession:
    """`session.post(...)` returning a chat completion with finish_reason "length"."""

    def __init__(self):
        self.calls: list[dict] = []

    def post(self, url, *, json, headers, timeout):
        self.calls.append(json)
        payload = {
            "model": json["model"],
            "choices": [{"message": {"content": CUT_TEXT}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 40, "completion_tokens": json["max_tokens"]},
        }

        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return payload

        return Response()


class _GeminiSession:
    """`session.post(...)` returning a generateContent reply cut off.

    Gemini spells the cut-off signal `finishReason: "MAX_TOKENS"` inside
    the first candidate, not beside the choice like the OpenAI shape and
    not as `stop_reason` like Anthropic's, so this fake exists to drive
    that spelling through the real parsing path.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def post(self, url, *, json, headers, timeout):
        self.calls.append(json)
        payload = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": CUT_TEXT}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 40,
                "candidatesTokenCount": json["generationConfig"]["maxOutputTokens"],
            },
            "modelVersion": "gemini-3.6-flash",
        }

        class Response:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return payload

        return Response()


def _litellm_completion(**kwargs):
    """`litellm.completion(...)` returning a ModelResponse cut off at length."""

    class Message:
        content = CUT_TEXT

    class Choice:
        message = Message()
        finish_reason = "length"

    class Usage:
        prompt_tokens = 40
        completion_tokens = kwargs.get("max_tokens", 0)
        prompt_tokens_details = None

    class ModelResponse:
        choices = [Choice()]
        model = kwargs.get("model", "?")
        usage = Usage()
        _hidden_params = {"response_cost": 0.0001}

    return ModelResponse()


@pytest.fixture
def stubbed_sdks(monkeypatch):
    """The two SDKs a provider imports lazily, stood in for at import time."""
    monkeypatch.setenv(_KEY, "not-a-real-key")

    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = lambda *, api_key: _AnthropicClient()
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)

    litellm = types.ModuleType("litellm")
    litellm.completion = _litellm_completion
    litellm.BadRequestError = type("BadRequestError", (Exception,), {})
    litellm.supports_response_schema = lambda model: False
    litellm.suppress_debug_info = False
    monkeypatch.setitem(sys.modules, "litellm", litellm)


def _build(name: str):
    """The provider, with the vendor fake behind it. None means no mapping."""
    if name == "anthropic":
        from policyforge.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(model="claude-sonnet-5", api_key_env=_KEY)
    if name == "vertex":
        from policyforge.llm.vertex_provider import VertexProvider

        return VertexProvider(model="claude-sonnet-5", project_id="p", client=_AnthropicClient())
    if name == "bedrock":
        from policyforge.llm.bedrock_provider import BedrockProvider

        return BedrockProvider(model="anthropic.claude-sonnet-5", client=_BedrockClient())
    if name in ("openai-compat", "local"):
        from policyforge.llm.openai_compat_provider import OpenAICompatProvider

        return OpenAICompatProvider(
            model="qwen3:14b", base_url="http://localhost:11434/v1", session=_OpenAISession()
        )
    if name == "gemini":
        from policyforge.llm.gemini_provider import GeminiProvider

        return GeminiProvider(model="gemini-3.6-flash", api_key_env=_KEY, session=_GeminiSession())
    if name == "litellm":
        from policyforge.llm.litellm_provider import LiteLLMProvider

        # No injected completion: bound from the stubbed module, the way a
        # real run binds it.
        return LiteLLMProvider(model="openrouter/deepseek/deepseek-v4-flash")
    if name == "cascade":
        from policyforge.llm.cascade_provider import CascadeProvider

        return CascadeProvider(primary=_build("local"), escalate_to=_build("anthropic"))
    return None


@pytest.mark.parametrize("name", factory_provider_names())
def test_the_vendors_cut_off_signal_reaches_stop_reason(name, stubbed_sdks):
    provider = _build(name)
    if provider is None:
        pytest.fail(f"provider {name!r} is built by the factory but has no contract mapping here")

    response = provider.generate(system="S", prompt="P", max_tokens=4096, temperature=0.0)

    assert response.text == CUT_TEXT, "the fake must hand back non-empty text"
    assert response.stop_reason, f"{name} dropped the vendor's stop reason"
    assert response.truncated is True, f"{name}: {response.stop_reason!r} did not read as cut off"


@pytest.mark.parametrize("name", factory_provider_names())
def test_a_cut_off_reply_is_refused_through_the_real_provider(name, stubbed_sdks):
    provider = _build(name)
    if provider is None:
        pytest.fail(f"provider {name!r} is built by the factory but has no contract mapping here")

    with pytest.raises(TruncatedResponse) as caught:
        effort.call(provider, system="S", prompt="P", max_tokens=4096, temperature=0.0)

    assert caught.value.first_budget == 4096
    assert caught.value.budget == 8192
    assert caught.value.text == CUT_TEXT


def test_the_factory_list_is_the_one_everybody_knows():
    """A guard on the guard: an empty or surprising discovery would let the
    parametrised tests above pass vacuously."""
    assert factory_provider_names() == [
        "anthropic",
        "bedrock",
        "cascade",
        "gemini",
        "litellm",
        "local",
        "openai-compat",
        "vertex",
    ]


def test_the_openai_compatible_retry_carries_the_larger_budget(stubbed_sdks):
    """The one provider that dropped the field: both requests are observed
    at the transport, and the second asks for twice the first."""
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    session = _OpenAISession()
    provider = OpenAICompatProvider(
        model="m", base_url="http://localhost:11434/v1", session=session
    )

    with pytest.raises(TruncatedResponse):
        effort.call(provider, system="S", prompt="P", max_tokens=1000)

    assert [call["max_tokens"] for call in session.calls] == [1000, 2000]
