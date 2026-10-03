"""A loopback model is reached directly, never through the environment's proxy (#69).

`local` promises that nothing leaves the host. Measured on #69 (1589), every
client PolicyForge builds for a loopback endpoint sent its call through
HTTP_PROXY, the text in cleartext: openai-compat, the embed and rerank
channels (all `requests`), and litellm's ollama, ollama_chat and openai
routes (httpx). The requests clients now ignore the environment for a
loopback endpoint (80's (b), 1455). litellm can't be pinned (its model-info
calls use a module-level client), so licensed content to a litellm loopback
route is refused while httpx would proxy it (80's ruling 1591).

Every "did it reach the proxy" below is a real listener hearing a real
request, not a transport lookup (80's must-fail arm).
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import socket
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.config import load_config
from policyforge.ingest.schema import catalog_json
from policyforge.llm import boundary
from policyforge.llm.boundary import BoundaryViolation, enforce

pytestmark = pytest.mark.own_config

CANARY = "CANARY-69-licensed-text"

#: Replies every client can at least parse, so a call completes when it
#: reaches the model listener.
_REPLY = json.dumps(
    {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "embeddings": [[0.0]],
        "results": [{"index": 0, "relevance_score": 0.5}],
    }
).encode()


@pytest.fixture(autouse=True)
def _no_ambient_proxy(monkeypatch):
    """Each test sets the proxy environment it means to; the runner's own is
    removed by a rule of this file's, not by the code under test."""
    for name, value in list(os.environ.items()):
        upper = name.upper()
        if "://" in value or any(w in upper for w in ("URL", "BASE", "ENDPOINT", "PROXY")):
            monkeypatch.delenv(name, raising=False)


class _Listener:
    """An HTTP server on a loopback address that records every request
    (method, path, whether the canary was in the body) until it is closed."""

    def __init__(self, host: str = "127.0.0.1"):
        heard: list[tuple[str, str, bool]] = []

        class Record(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                heard.append((self.command, self.path, CANARY.encode() in body))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_REPLY)))
                self.end_headers()
                self.wfile.write(_REPLY)

            do_GET = do_POST

            def do_CONNECT(self):
                heard.append(("CONNECT", self.path, False))
                self.send_response(502)
                self.end_headers()

            def log_message(self, *args):
                pass

        server_class = http.server.ThreadingHTTPServer
        if ":" in host:
            server_class = type("V6", (http.server.ThreadingHTTPServer,), {})
            server_class.address_family = socket.AF_INET6
        self.server = server_class((host, 0), Record)
        self.heard = heard
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def listeners():
    opened: list[_Listener] = []

    def make(for_host: str = "127.0.0.1") -> _Listener:
        """A listener reachable as `for_host`. `localhost` binds ::1 where it
        can: Windows tries ::1 first and takes about 2 s to give up on it, and
        whether a proxy applies is decided by the NAME, not the address."""
        if for_host in ("localhost", "::1"):
            try:
                opened.append(_Listener("::1"))
                return opened[-1]
            except OSError:
                if for_host == "::1":
                    raise
        opened.append(_Listener("127.0.0.1"))
        return opened[-1]

    yield make
    for listener in opened:
        listener.close()


# ---- the requests clients reach a loopback endpoint directly (80's (b)) ---------------


def _openai_compat(base_url: str):
    from policyforge.llm.base import _build_provider

    return _build_provider(
        {"llm": {"provider": "openai-compat", "model": "m", "base_url": f"{base_url}/v1"}}
    )


def _embedder(base_url: str):
    from policyforge.embed.ollama_provider import OllamaEmbedder

    return OllamaEmbedder(model="e", base_url=base_url)


def _reranker(base_url: str):
    from policyforge.rerank.llamacpp_provider import LlamaCppReranker

    return LlamaCppReranker(base_url=base_url)


_CLIENTS = {
    "openai-compat": (
        _openai_compat,
        lambda c: c.generate(system="s", prompt=CANARY, max_tokens=8),
    ),
    "embed": (_embedder, lambda c: c.embed([CANARY])),
    "rerank": (_reranker, lambda c: c.score(CANARY, ["doc"])),
}


@pytest.mark.parametrize("client", sorted(_CLIENTS))
@pytest.mark.parametrize(
    "url, direct",
    [
        pytest.param("http://localhost:1", True, id="localhost"),
        pytest.param("http://127.0.0.1:1", True, id="127.0.0.1"),
        pytest.param("http://127.9.9.9:1", True, id="127/8"),
        pytest.param("http://[::1]:1", True, id="::1"),
        pytest.param("http://models.localhost:1", True, id="*.localhost"),
        pytest.param("https://api.example.com", False, id="hosted https"),
        pytest.param("http://10.0.0.5:1", False, id="a LAN host"),
        pytest.param("http://localhost.example.com:1", False, id="a look-alike"),
    ],
)
def test_each_requests_client_pins_whether_it_reads_the_proxy_environment(
    tmp_path, monkeypatch, client, url, direct
):
    """The pin 1458 asks for: an SDK or requests default changing can't
    silently reopen it, because this reads the built session's setting."""
    monkeypatch.chdir(tmp_path)
    build, _ = _CLIENTS[client]
    assert build(url)._session.trust_env is (not direct)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
@pytest.mark.parametrize("client", sorted(_CLIENTS))
def test_a_loopback_call_never_reaches_the_proxy(tmp_path, monkeypatch, listeners, client, host):
    """80's must-fail arm, through each real client: HTTP_PROXY points at a
    listener, and it must hear nothing while the model hears the call."""
    monkeypatch.chdir(tmp_path)
    model, proxy = listeners(host), listeners()
    monkeypatch.setenv("HTTP_PROXY", proxy.url())
    build, call = _CLIENTS[client]
    call(build(model.url(host)))
    assert proxy.heard == [], f"the proxy heard {proxy.heard}"
    assert [canary for *_, canary in model.heard] == [True]


def test_a_hosted_https_call_still_goes_through_the_proxy(tmp_path, monkeypatch, listeners):
    """80's passing arm: corporate egress isn't broken. The listener stands in
    for the proxy and refuses the tunnel, so nothing leaves this machine."""
    monkeypatch.chdir(tmp_path)
    proxy = listeners()
    monkeypatch.setenv("HTTPS_PROXY", proxy.url())
    with pytest.raises(RuntimeError):
        _openai_compat("https://api.example.test").generate(system="s", prompt="x", max_tokens=8)
    assert [(m, p) for m, p, _ in proxy.heard][:1] == [("CONNECT", "api.example.test:443")]


def test_a_lan_http_call_still_goes_through_the_proxy(tmp_path, monkeypatch, listeners):
    """Only loopback is exempt from the environment: a LAN host is not this
    machine, and the proxy is the organisation's route to it."""
    monkeypatch.chdir(tmp_path)
    proxy = listeners()
    monkeypatch.setenv("HTTP_PROXY", proxy.url())
    _openai_compat("http://llm.lan.example").generate(system="s", prompt=CANARY, max_tokens=8)
    assert [(p, canary) for _, p, canary in proxy.heard] == [
        ("http://llm.lan.example/v1/chat/completions", True)
    ]


# ---- litellm: refused for licensed content while httpx would proxy it (1591) ----------


def _project(tmp_path: Path, monkeypatch, llm_yaml: str) -> dict:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text("llm:\n  " + llm_yaml, encoding="utf-8")
    (tmp_path / "local_content" / "hitrust").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return load_config()


def _licensed(tmp_path: Path) -> Path:
    path = tmp_path / "local_content" / "hitrust" / "controls.json"
    path.write_text(catalog_json([{"control_id": "01.a", "title": CANARY}]), encoding="utf-8")
    return Path("local_content/hitrust/controls.json")


def _generate_parser(tmp_path, monkeypatch, llm_yaml):
    """The real generate-parser and the REAL provider: nothing is faked."""
    import policyforge.cli as cli_mod

    _project(tmp_path, monkeypatch, llm_yaml)
    sample = tmp_path / "local_content" / "hitrust" / "sample.csv"
    sample.write_text(f"Control Reference,Level\n01.a {CANARY},Level 1\n", encoding="utf-8")
    return CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            "local_content/hitrust/sample.csv",
            "--out",
            str(tmp_path / "out_loader.py"),
            "--yes",
        ],
    )


_LITELLM_ROUTES = [
    pytest.param("ollama/q", "", id="ollama"),
    pytest.param("ollama_chat/q", "", id="ollama_chat"),
    pytest.param("openai/m", "/v1", id="openai"),
]


@pytest.mark.parametrize("model_name, suffix", _LITELLM_ROUTES)
def test_litellm_to_a_loopback_model_under_a_proxy_is_refused_and_sends_nothing(
    tmp_path, monkeypatch, listeners, model_name, suffix
):
    """The must-fail arm through the real path: the licensed sample carries
    the canary, and neither the proxy nor the model hears anything."""
    pytest.importorskip("litellm")
    model, proxy = listeners("localhost"), listeners()
    monkeypatch.setenv("HTTP_PROXY", proxy.url())
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    result = _generate_parser(
        tmp_path,
        monkeypatch,
        f"provider: litellm\n  model: {model_name}\n  api_base: {model.url('localhost')}{suffix}\n",
    )
    assert proxy.heard == [] and model.heard == [], (proxy.heard, model.heard)
    assert result.exit_code != 0
    assert "NO_PROXY" in result.output and "HTTP_PROXY" in result.output
    assert proxy.url() not in result.output, "the proxy's value is never printed"
    assert "licensed_agreement" not in result.output, (
        "an agreement can't fix a proxied route, so the refusal doesn't offer one"
    )


def test_litellm_under_a_proxy_that_no_proxy_bypasses_reaches_the_model_directly(
    tmp_path, monkeypatch, listeners
):
    """The must-pass arm: with NO_PROXY naming the host, the call is allowed,
    and the real litellm sends it to the model and nothing to the proxy."""
    pytest.importorskip("litellm")
    model, proxy = listeners("localhost"), listeners()
    monkeypatch.setenv("HTTP_PROXY", proxy.url())
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    _generate_parser(
        tmp_path,
        monkeypatch,
        f"provider: litellm\n  model: openai/m\n  api_base: {model.url('localhost')}/v1\n",
    )
    assert proxy.heard == []
    assert any(canary for *_, canary in model.heard), model.heard


def test_ollama_model_info_to_its_default_port_is_held_to_the_proxy_too(tmp_path, monkeypatch):
    """litellm's ollama routes send /api/show to localhost:11434 whatever the
    api_base (#69, 1589: 4 of 5). NO_PROXY naming only the api_base's host
    leaves those proxied, so the route is still refused, naming the default."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    config = _project(
        tmp_path,
        monkeypatch,
        "provider: litellm\n  model: ollama/q\n  api_base: http://127.0.0.1:11434\n",
    )
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    assert "http://localhost:11434" in str(refused.value)
    assert "http://127.0.0.1:11434" not in str(refused.value), "the api_base itself is bypassed"


@pytest.mark.parametrize(
    "api_base", ["http://localhost:11434", "http://localhost:11434/", "http://LOCALHOST:11434"]
)
def test_an_api_base_at_the_ollama_default_is_named_once(tmp_path, monkeypatch, api_base):
    """80's 1649: the default is the same server as an api_base spelled this
    way, so the refusal lists it once."""
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.chdir(tmp_path)
    provider = boundary.classify_provider(
        {"provider": "litellm", "model": "ollama/q", "api_base": api_base}
    )
    assert boundary.proxied_loopback(provider) == [api_base]


def test_organisation_content_to_litellm_under_a_proxy_is_unchanged(tmp_path, monkeypatch):
    """80's 1591: non-licensed content is not refused by this rule."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    config = _project(
        tmp_path,
        monkeypatch,
        "provider: litellm\n  model: ollama/q\n  api_base: http://localhost:11434\n",
    )
    draft = tmp_path / "output" / "draft.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# Our own draft\n", encoding="utf-8")
    assert enforce(Path("output/draft.md"), config).allowed


def test_a_cascade_with_a_proxied_litellm_half_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    config = _project(
        tmp_path,
        monkeypatch,
        "provider: cascade\n"
        "  primary:\n    provider: openai-compat\n    model: m\n"
        "    base_url: http://localhost:11434/v1\n"
        "  escalate_to:\n    provider: litellm\n    model: ollama/q\n"
        "    api_base: http://localhost:11434\n",
    )
    with pytest.raises(BoundaryViolation, match="NO_PROXY"):
        enforce(_licensed(tmp_path), config)


# ---- NO_PROXY: the answer is httpx's, measured by a real request (1458) ---------------


_SHAPES = [
    "localhost",
    "127.0.0.1",
    ".localhost",
    "127.0.0.0/8",
    "*",
    "localhost , 127.0.0.1",
    "example.com",
    "calhost",
    "::1",
    "[::1]",
]


def _send_with_httpx(url: str) -> bool:
    """Send one real request with httpx, reading the environment as litellm's
    clients do. Which listener hears it is the measurement the boundary's
    decision must agree with. False when httpx refuses the environment
    itself and builds no client: measured for NO_PROXY=[::1] (InvalidURL),
    so litellm could send nothing under it either."""
    import httpx

    try:
        client = httpx.Client(timeout=5)
    except httpx.InvalidURL:
        return False
    with client, contextlib.suppress(httpx.HTTPError):
        client.post(url, content=b"{}")
    return True


#: Each shape as NO_PROXY, and the two a corporate config most often writes
#: in lower case as no_proxy too (Windows reads the environment without
#: case, so there the two are the same; elsewhere they are not).
_VARIABLES = [("NO_PROXY", shape) for shape in _SHAPES] + [
    ("no_proxy", "localhost"),
    ("no_proxy", "localhost , 127.0.0.1"),
]


@pytest.mark.parametrize("variable, shape", _VARIABLES)
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_the_decision_agrees_with_what_httpx_really_does(
    tmp_path, monkeypatch, listeners, variable, shape, host
):
    """Each NO_PROXY shape from 1458, lower-case too, against each loopback
    host form. Whatever httpx does with a real request, the boundary decides
    the same: refused exactly when the proxy hears the call."""
    monkeypatch.chdir(tmp_path)  # own_config, before any early return
    try:
        model = listeners(host)
    except OSError:
        pytest.skip("no IPv6 loopback on this machine")
    proxy = listeners()
    monkeypatch.setenv("HTTP_PROXY", proxy.url())
    monkeypatch.setenv(variable, shape)
    url = model.url(f"[{host}]" if host == "::1" else host)

    if not _send_with_httpx(url):
        assert boundary._proxied(url) is True, "a setting httpx can't read counts as proxied"
        return
    really = bool(proxy.heard)
    assert really != bool(model.heard), "exactly one listener hears the real request"

    assert boundary._proxied(url) is really
    config = _project(
        tmp_path, monkeypatch, f"provider: litellm\n  model: ollama/q\n  api_base: {url}\n"
    )
    provider = boundary.classify_provider(config["llm"])
    if provider.klass != boundary.LOCAL:
        pytest.skip(f"{host} is not classified local here")
    decision = boundary.check(
        boundary.ContentClassification(boundary.LICENSED, "a licensed passage"), provider, config
    )
    # The api_base is one of the URLs checked; the ollama default is the other.
    assert (url in (decision.note or "")) is really


# ---- b5's 1510: the #66 refusal's advice, followed to the letter ----------------------


def test_following_the_declared_local_refusal_to_the_letter_sends_nothing(
    tmp_path, monkeypatch, listeners
):
    """A declared-local ollama with no api_base is refused while HTTP_PROXY is
    set. Its advice (a loopback api_base) is followed with the proxy still
    set: refused again, and the listener hears nothing. Then NO_PROXY, which
    the advice now names, is added: allowed."""
    proxy = listeners()
    monkeypatch.setenv("HTTP_PROXY", proxy.url())
    first = _project(
        tmp_path, monkeypatch, "provider: litellm\n  model: ollama/q\n  classification: local\n"
    )
    with pytest.raises(ValueError, match="NO_PROXY") as refused:
        enforce(_licensed(tmp_path), first)
    assert "HTTP_PROXY" in str(refused.value)

    advised = "provider: litellm\n  model: ollama/q\n  api_base: http://localhost:11434\n"
    with pytest.raises(BoundaryViolation, match="NO_PROXY"):
        enforce(_licensed(tmp_path), _project(tmp_path, monkeypatch, advised))
    assert proxy.heard == []

    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    assert enforce(_licensed(tmp_path), _project(tmp_path, monkeypatch, advised)).allowed
