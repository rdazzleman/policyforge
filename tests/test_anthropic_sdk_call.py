"""The Anthropic and Vertex providers through the REAL SDK (#352).

Every other Anthropic test replaces the client with a fake whose
`messages.create` takes `**kwargs`, so when anthropic 1.7.0 removed the
`temperature` keyword nothing noticed: every real call raised `TypeError`
before a request left, and the suite stayed green. These build the SDK's
own client, as the providers do, over an `httpx2.MockTransport`, so the
SDK's signature checks and request building run for real and nothing
reaches the network. What the providers must do (80's ruling, (a)): send a
request, and send no `temperature`.
"""

from __future__ import annotations

import json

import pytest

anthropic = pytest.importorskip("anthropic")
# The SDK's own HTTP package: anthropic 1.7.0 (the lock) is built on httpx2
# and refuses a plain httpx client.
httpx = pytest.importorskip("httpx2")

MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-x",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 3, "output_tokens": 1},
}


class Server:
    """Answers the Messages API's routes and keeps each request."""

    def __init__(self):
        self.requests = []
        #: What a `messages.create` call is answered with.
        self.message = MESSAGE

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/messages/batches") and request.method == "POST":
            return httpx.Response(200, json=self._batch("in_progress"))
        if path.endswith("/messages/batches/msgbatch_1") and request.method == "GET":
            return httpx.Response(200, json=self._batch("ended"))
        if path.endswith("/results"):
            line = {"custom_id": "a", "result": {"type": "succeeded", "message": MESSAGE}}
            return httpx.Response(200, content=json.dumps(line).encode() + b"\n")
        return httpx.Response(200, json=self.message)

    @staticmethod
    def _batch(status):
        return {
            "id": "msgbatch_1",
            "type": "message_batch",
            "processing_status": status,
            "request_counts": {
                "processing": 0,
                "succeeded": 1,
                "errored": 0,
                "canceled": 0,
                "expired": 0,
            },
            "created_at": "2026-10-06T00:00:00Z",
            "expires_at": "2026-10-07T00:00:00Z",
            "ended_at": None,
            "cancel_initiated_at": None,
            "archived_at": None,
            "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_1/results",
        }

    def bodies(self):
        return [json.loads(r.content) for r in self.requests if r.method == "POST"]


@pytest.fixture
def server(monkeypatch):
    """`anthropic.Anthropic`, built as AnthropicProvider builds it, over the
    mock transport."""
    answers = Server()
    real = anthropic.Anthropic
    monkeypatch.setattr(
        anthropic,
        "Anthropic",
        lambda **kwargs: real(
            http_client=httpx.Client(transport=httpx.MockTransport(answers)), **kwargs
        ),
    )
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("PF_TEST_ANTHROPIC_KEY", "sk-test-not-a-key")
    return answers


def _provider():
    from policyforge.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(model="claude-x", api_key_env="PF_TEST_ANTHROPIC_KEY")


def test_a_generate_call_is_sent_with_no_temperature(server):
    reply = _provider().generate(system="s", prompt="p", max_tokens=16, temperature=0.0)

    assert reply.text == "ok"
    (body,) = server.bodies()
    assert body["model"] == "claude-x" and body["max_tokens"] == 16
    assert "temperature" not in body


def test_a_json_call_is_sent_with_no_temperature(server):
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    server.message = dict(MESSAGE, content=[{"type": "text", "text": '{"a": "x"}'}])

    _provider().generate_json(system="s", prompt="p", schema=schema, max_tokens=16, temperature=0.0)

    (body,) = server.bodies()
    assert "temperature" not in body


def test_a_batch_is_sent_with_no_temperature(server):
    from policyforge.llm._anthropic_compat import submit_batch
    from policyforge.llm.batch import BatchRequest

    client = anthropic.Anthropic(api_key="sk-test-not-a-key")
    request = BatchRequest(custom_id="a", system="s", prompt="p", max_tokens=16, temperature=0.2)

    answers = submit_batch(client, [request], model="claude-x", sleep=lambda _: None)

    assert answers["a"].text == "ok"
    (body,) = server.bodies()
    (entry,) = body["requests"]
    assert entry["params"]["model"] == "claude-x"
    assert "temperature" not in entry["params"]


def test_vertex_is_sent_with_no_temperature():
    from policyforge.llm.vertex_provider import VertexProvider

    vertex = getattr(anthropic, "AnthropicVertex", None)
    if vertex is None:
        pytest.skip("this anthropic build has no Vertex client")
    answers = Server()
    try:
        client = vertex(
            region="us-east5",
            project_id="p",
            access_token="not-a-token",
            http_client=httpx.Client(transport=httpx.MockTransport(answers)),
        )
    except Exception as exc:  # the vertex extra's auth libraries missing
        pytest.skip(f"the Vertex client can't be built offline here: {exc}")

    reply = VertexProvider(model="claude-x", project_id="p", client=client).generate(
        system="s", prompt="p", max_tokens=16, temperature=0.0
    )

    assert reply.text == "ok"
    (body,) = answers.bodies()
    assert "temperature" not in body
