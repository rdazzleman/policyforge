"""An HTTP failure names the service that refused it (#413).

The one line a failed request becomes gave a 401 the Confluence advice (check
the space and the token) even when the 401 came from the model provider, so
the user was sent to fix the wrong credential. 80's ruling: the line names the
service that answered, from where the request was made, never guessed from the
status code alone. A fake model-provider 401 names the provider and its key
variable; a fake Confluence 401 names Confluence and its token variable;
neither gets the other's advice; one nobody tagged is described without a
guess.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge.llm import ledger

pytestmark = pytest.mark.own_config


@pytest.fixture(autouse=True)
def _in_the_project_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _refused(status: int, reason: str, url: str) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.reason = reason
    response.url = url
    return requests.exceptions.HTTPError(f"{status} {reason}", response=response)


def _error_line(result) -> str:
    lines = [line for line in result.output.splitlines() if line.startswith("Error:")]
    assert len(lines) == 1, result.output
    return lines[0]


def _controls(tmp_path: Path) -> Path:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    entry = {
        "control_id": "AC-1",
        "title": "Policy",
        "framework": "NIST 800-53",
        "framework_version": "Rev 5",
        "family": None,
        "family_abbr": None,
        "baseline": None,
        "control_statement": "Have a policy.",
        "discussion": "",
        "enhancements": [],
        "related_controls": [],
        "source_crosswalk": {},
        "source_path": None,
    }
    path = catalog / "controls.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")
    return path


class _ModelProvider:
    """A provider whose every request is refused with a status."""

    model = "some-model"

    def __init__(self, status: int, reason: str):
        self.error = _refused(status, reason, "https://api.model.example/v1/chat/completions")

    def generate(self, **kwargs):
        raise self.error

    def check(self):
        return True


def _run_ssp(tmp_path, monkeypatch, provider):
    config = {
        "llm": {
            "provider": "openai-compat",
            "model": "some-model",
            "base_url": "https://api.model.example/v1",
            "api_key_env": "MY_MODEL_KEY",
        }
    }
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: ledger.wrap(provider, cfg))
    return CliRunner().invoke(
        cli_mod.cli,
        ["ssp", "--controls", str(_controls(tmp_path)), "--out", str(tmp_path / "s.xlsx"), "--yes"],
    )


# ---- the model provider ---------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_model_key_names_the_provider_and_its_key_variable(tmp_path, monkeypatch, status):
    result = _run_ssp(tmp_path, monkeypatch, _ModelProvider(status, "Unauthorized"))

    assert result.exit_code == 1, result.output
    line = _error_line(result)
    assert "openai-compat model provider" in line
    assert "$MY_MODEL_KEY" in line
    assert "API key" in line
    # Neither Confluence's service nor its advice.
    assert "Confluence" not in line
    assert "space" not in line and "username" not in line


def test_an_unknown_model_names_the_provider_and_the_config_key(tmp_path, monkeypatch):
    result = _run_ssp(tmp_path, monkeypatch, _ModelProvider(404, "Not Found"))

    line = _error_line(result)
    assert "model provider" in line and "llm.model" in line
    assert "Confluence" not in line and "space and title" not in line


# ---- Confluence -------------------------------------------------------------------------


def _import_confluence(monkeypatch, error, *args):
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "a-token")

    def refuse(*_, **__):
        raise error

    monkeypatch.setattr(requests, "get", refuse)
    return CliRunner().invoke(
        cli_mod.cli,
        [
            "import-confluence",
            "--tier",
            "standard",
            "--name",
            "a-page",
            "--space",
            "SEC",
            "--title",
            "A Page",
            "--host",
            "https://wiki.example.com/wiki",
            *args,
        ],
    )


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_confluence_token_names_confluence_and_its_token_variable(
    tmp_path, monkeypatch, status
):
    error = _refused(status, "Unauthorized", "https://wiki.example.com/wiki/rest/api/content")

    result = _import_confluence(monkeypatch, error)

    assert result.exit_code == 1, result.output
    line = _error_line(result)
    assert "(Confluence)" in line
    assert "$CONFLUENCE_API_TOKEN" in line and "can see the space" in line
    # Not the model provider's service or its advice.
    assert "model provider" not in line and "API key" not in line and "llm." not in line


def test_the_token_variable_named_is_the_one_the_call_was_given():
    from policyforge.export._confluence_auth import reports_as_confluence
    from policyforge.http_service import CONFLUENCE, service_of

    @reports_as_confluence
    def fetch(*, token_env: str = "CONFLUENCE_API_TOKEN"):
        raise _refused(401, "Unauthorized", "https://wiki.example.com/x")

    with pytest.raises(requests.exceptions.HTTPError) as raised:
        fetch(token_env="MY_WIKI_TOKEN")
    assert service_of(raised.value).credential == "MY_WIKI_TOKEN"
    assert service_of(raised.value).kind == CONFLUENCE

    with pytest.raises(requests.exceptions.HTTPError) as default:
        fetch()
    assert service_of(default.value).credential == "CONFLUENCE_API_TOKEN"


# ---- one nobody tagged ---------------------------------------------------------------------


def test_a_failure_nobody_tagged_is_described_without_a_guess():
    from policyforge.cli import _http_failure

    line = _http_failure(_refused(401, "Unauthorized", "https://somewhere.example.com/api"))

    assert line.startswith("401 Unauthorized from https://somewhere.example.com/api.")
    assert "Confluence" not in line and "space" not in line and "API key" not in line
    assert "credential" in line


def test_the_first_tagger_is_the_service_that_made_the_request():
    from policyforge.http_service import CONFLUENCE, LLM, Service, service_of, tag

    error = _refused(401, "Unauthorized", "https://x.example.com/")
    tag(error, Service(LLM, "the p model provider", "K"))
    tag(error, Service(CONFLUENCE, "Confluence", "T"))

    assert service_of(error).kind == LLM


# ---- the shipped providers (ba on PR 414): their own error, through the real classes ------


class _Session:
    """Only the HTTP session is faked: the provider class is the real one."""

    def __init__(self, status: int, reason: str, body: str):
        self.status, self.reason, self.body = status, reason, body

    def post(self, *args, **kwargs):
        outer = self

        class _Response:
            status_code = outer.status
            reason = outer.reason
            text = outer.body

            def json(self):
                raise ValueError("not json")

        return _Response()


def _real_provider(kind: str, session):
    from policyforge.llm.gemini_provider import GeminiProvider
    from policyforge.llm.openai_compat_provider import OpenAICompatProvider

    if kind == "openai-compat":
        return OpenAICompatProvider(
            model="some-model",
            base_url="https://api.model.example/v1",
            api_key_env="MY_MODEL_KEY",
            session=session,
        )
    return GeminiProvider(model="some-model", api_key_env="MY_MODEL_KEY", session=session)


@pytest.mark.parametrize("kind", ["openai-compat", "gemini"])
@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_from_a_shipped_provider_names_it_and_the_variable(
    tmp_path, monkeypatch, kind, status
):
    monkeypatch.setenv("MY_MODEL_KEY", "k-123")
    body = '{"error": "Incorrect API key provided: k-123"}'
    provider = _real_provider(kind, _Session(status, "Unauthorized", body))
    config = {"llm": {"provider": kind, "model": "some-model", "api_key_env": "MY_MODEL_KEY"}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: ledger.wrap(provider, cfg))

    result = CliRunner().invoke(
        cli_mod.cli,
        ["ssp", "--controls", str(_controls(tmp_path)), "--out", str(tmp_path / "s.xlsx"), "--yes"],
    )

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    line = _error_line(result)
    assert f"{status} Unauthorized from" in line
    assert f"({kind} model provider)" in line or f"the {kind} model provider" in line
    assert "$MY_MODEL_KEY" in line and "API key" in line
    assert "Confluence" not in line and "space" not in line
    assert "k-123" not in result.output, "a vendor's reply can echo the key: it isn't printed"


def test_a_provider_http_error_is_still_the_runtime_error_it_was():
    from policyforge.llm.base import ProviderHttpError

    error = ProviderHttpError("https://x/y returned HTTP 401: no", status=401, url="https://x/y")
    assert isinstance(error, RuntimeError)
    assert str(error) == "https://x/y returned HTTP 401: no"


def test_a_server_failure_from_a_shipped_provider_says_to_try_again(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_MODEL_KEY", "k-123")
    provider = _real_provider("openai-compat", _Session(503, "Service Unavailable", "busy"))
    config = {"llm": {"provider": "openai-compat", "model": "m", "api_key_env": "MY_MODEL_KEY"}}
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)
    monkeypatch.setattr(cli_mod, "get_provider", lambda cfg: ledger.wrap(provider, cfg))

    result = CliRunner().invoke(
        cli_mod.cli,
        ["ssp", "--controls", str(_controls(tmp_path)), "--out", str(tmp_path / "s.xlsx"), "--yes"],
    )

    line = _error_line(result)
    assert "503 Service Unavailable" in line and "try again later" in line
    assert "model provider" in line and "API key" not in line
