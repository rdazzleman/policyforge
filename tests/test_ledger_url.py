"""Each model call's row records where it was sent (#328).

The SDKs honour base-URL overrides (`ANTHROPIC_BASE_URL` and the like), and
a row with no endpoint can't say where the text went. Every provider now
puts the base URL it resolved on the response, through `recordable_endpoint`,
and the ledger copies it into the row: scheme, host, port and path, with the
user name and password, the query, the fragment and path parameters dropped.

Nothing here reaches the network. The Anthropic call goes through the real
SDK into an `httpx.MockTransport`, so the expected endpoint is read off the
request the transport received rather than from the code under test.
"""

from __future__ import annotations

import json
import types
from urllib.parse import urlsplit

import pytest

from policyforge.llm.base import get_provider, recordable_endpoint


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("https://user:s3cret@proxy.example.com/v1", "https://proxy.example.com/v1"),
        ("https://token@proxy.example.com/v1", "https://proxy.example.com/v1"),
        (
            "https://proxy.example.com:8443/llm/v1?key=abc&x=1",
            "https://proxy.example.com:8443/llm/v1",
        ),
        ("https://proxy.example.com/v1#frag", "https://proxy.example.com/v1"),
        ("http://[::1]:11434/v1", "http://[::1]:11434/v1"),
        ("http://localhost:11434/v1/", "http://localhost:11434/v1/"),
        ("https://u:p%40ss@h.example/v1?sig=a%2Fb", "https://h.example/v1"),
        # A raw "@" in the password: the host follows the LAST "@" (1d, PR 353).
        ("https://u:p@ss@proxy.example:8443/v1", "https://proxy.example:8443/v1"),
        # Path parameters, on the last segment and on one before it (ba, PR 353).
        ("https://proxy.example/v1;api_key=SECRET", "https://proxy.example/v1"),
        ("https://proxy.example/v1;token=SECRET/chat", "https://proxy.example/v1/chat"),
        (None, None),
        ("", None),
        ("not a url", None),
        ("/v1/messages", None),
    ],
)
def test_what_an_endpoint_keeps(url, kept):
    assert recordable_endpoint(url) == kept


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _config(tmp_path, **llm):
    return {"llm": {**llm, "ledger": {"path": str(tmp_path / "calls.jsonl")}}}


@pytest.fixture
def anthropic_through_a_mock_transport(monkeypatch):
    """The real `anthropic.Anthropic`, built as the provider builds it, but
    sending into a transport that records each request and answers it."""
    anthropic = pytest.importorskip("anthropic")
    # The SDK's own HTTP package: anthropic 1.7.0 (the lock) is built on
    # httpx2 and refuses a plain httpx client.
    httpx = pytest.importorskip("httpx2")

    sent = []

    def answer(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-x",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    real = anthropic.Anthropic

    def build(**kwargs):
        return real(http_client=httpx.Client(transport=httpx.MockTransport(answer)), **kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", build)
    monkeypatch.setenv("PF_TEST_ANTHROPIC_KEY", "sk-test-not-a-key")
    return sent


def test_an_anthropic_base_url_override_is_the_endpoint_recorded(
    tmp_path, monkeypatch, anthropic_through_a_mock_transport
):
    """The issue's case: ANTHROPIC_BASE_URL set, so the call goes to the
    override, and the row says the override, without its credential."""
    monkeypatch.setenv(
        "ANTHROPIC_BASE_URL", "https://proxyuser:proxytoken@my.proxy.internal:8443/anthropic"
    )
    config = _config(
        tmp_path, provider="anthropic", model="claude-x", api_key_env="PF_TEST_ANTHROPIC_KEY"
    )

    get_provider(config).generate(system="s", prompt="p", max_tokens=16)

    (request,) = anthropic_through_a_mock_transport
    (row,) = _rows(tmp_path / "calls.jsonl")
    recorded = urlsplit(row["endpoint"])
    assert request.url.host == "my.proxy.internal", "the override is where the call went"
    assert (recorded.hostname, recorded.port) == (request.url.host, request.url.port)
    assert request.url.path.startswith(recorded.path), (request.url.path, recorded.path)
    assert "proxytoken" not in json.dumps(row) and "proxyuser" not in json.dumps(row)


def test_with_no_override_the_endpoint_is_anthropic_s_own(
    tmp_path, monkeypatch, anthropic_through_a_mock_transport
):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    config = _config(
        tmp_path, provider="anthropic", model="claude-x", api_key_env="PF_TEST_ANTHROPIC_KEY"
    )

    get_provider(config).generate(system="s", prompt="p", max_tokens=16)

    (request,) = anthropic_through_a_mock_transport
    (row,) = _rows(tmp_path / "calls.jsonl")
    assert request.url.host == "api.anthropic.com"
    assert urlsplit(row["endpoint"]).hostname == "api.anthropic.com"


def test_an_openai_compatible_base_url_is_recorded_without_credential_or_query(
    tmp_path, monkeypatch
):
    import requests

    posted = []

    class Session:
        trust_env = True

        def post(self, url, **kwargs):
            posted.append(url)
            body = {
                "id": "c1",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            }
            return types.SimpleNamespace(status_code=200, json=lambda: body, text=json.dumps(body))

    monkeypatch.setattr(requests, "Session", Session)
    config = _config(
        tmp_path,
        provider="openai-compat",
        model="m",
        base_url="https://svc:svcsecret@proxy.example:8443/custom/v1?token=t0k",
    )

    get_provider(config).generate(system="s", prompt="p", max_tokens=16)

    (row,) = _rows(tmp_path / "calls.jsonl")
    assert row["endpoint"] == "https://proxy.example:8443/custom/v1"
    assert urlsplit(posted[0]).hostname == "proxy.example"
    assert "svcsecret" not in json.dumps(row) and "t0k" not in json.dumps(row)


def _litellm_reply(**hidden):
    return types.SimpleNamespace(
        id="c1",
        model="openai/m",
        choices=[
            types.SimpleNamespace(message=types.SimpleNamespace(content="ok"), finish_reason="stop")
        ],
        usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=1),
        _hidden_params=hidden,
    )


def test_litellm_records_its_configured_api_base(tmp_path):
    from policyforge.llm.ledger import wrap
    from policyforge.llm.litellm_provider import LiteLLMProvider

    config = _config(tmp_path, provider="litellm", model="openai/m")
    provider = LiteLLMProvider(
        model="openai/m",
        api_base="https://k:litellmsecret@gw.example/v1?sig=abc",
        completion=lambda **_: _litellm_reply(
            api_base="https://k:litellmsecret@gw.example/v1?sig=abc"
        ),
    )

    wrap(provider, config).generate(system="s", prompt="p", max_tokens=16)

    (row,) = _rows(tmp_path / "calls.jsonl")
    assert row["endpoint"] == "https://gw.example/v1"
    assert "litellmsecret" not in json.dumps(row)


def test_litellm_with_no_api_base_records_nothing_rather_than_its_default(tmp_path):
    """Measured on LiteLLM 1.103.0: with OPENAI_BASE_URL set and no api_base,
    it sent the request to the override and reported https://api.openai.com
    as its `api_base`. A row naming that host would say the text went
    somewhere it didn't, so the row says nothing."""
    from policyforge.llm.ledger import wrap
    from policyforge.llm.litellm_provider import LiteLLMProvider

    config = _config(tmp_path, provider="litellm", model="openai/m")
    provider = LiteLLMProvider(
        model="openai/m", completion=lambda **_: _litellm_reply(api_base="https://api.openai.com")
    )

    wrap(provider, config).generate(system="s", prompt="p", max_tokens=16)

    (row,) = _rows(tmp_path / "calls.jsonl")
    assert row["endpoint"] is None


def test_gemini_records_its_base_url(monkeypatch):
    from policyforge.llm.gemini_provider import GeminiProvider

    monkeypatch.setenv("GEMINI_API_KEY", "not-a-key")
    provider = GeminiProvider(model="gemini-x", base_url="https://gem.proxy.example/g?key=leak")
    reply = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
    monkeypatch.setattr(provider, "_post", lambda payload: reply)

    assert provider.generate(system="s", prompt="p").endpoint == "https://gem.proxy.example/g"


def test_bedrock_records_the_endpoint_boto_resolved():
    from policyforge.llm.bedrock_provider import BedrockProvider

    client = types.SimpleNamespace(
        meta=types.SimpleNamespace(endpoint_url="https://bedrock.proxy.example/route"),
        converse=lambda **_: {"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {}},
    )

    assert BedrockProvider(model="m", client=client).generate(system="s", prompt="p").endpoint == (
        "https://bedrock.proxy.example/route"
    )


def test_vertex_records_its_client_base_url():
    from policyforge.llm.vertex_provider import VertexProvider

    message = types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text="ok")],
        usage=types.SimpleNamespace(input_tokens=3, output_tokens=1),
        stop_reason="end_turn",
    )
    client = types.SimpleNamespace(
        base_url="https://europe-west1-aiplatform.googleapis.com/v1/",
        messages=types.SimpleNamespace(create=lambda **_: message),
    )

    response = VertexProvider(model="claude-x", project_id="p", client=client).generate(
        system="s", prompt="p", max_tokens=16
    )

    assert response.endpoint == "https://europe-west1-aiplatform.googleapis.com/v1/"
