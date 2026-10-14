"""Tests for port retention and sanitization in one-line HTTP error reporting (#425)."""

from __future__ import annotations

import requests

from policyforge.cli import _http_failure
from policyforge.llm.base import ProviderHttpError


def _refused(status: int, reason: str, url: str) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    response.reason = reason
    response.url = url
    return requests.exceptions.HTTPError(f"{status} {reason}", response=response)


def test_http_error_preserves_port_for_non_default_ports():
    """http://localhost:11434/v1/chat/completions keeps :11434."""
    error = _refused(404, "Not Found", "http://localhost:11434/v1/chat/completions")
    line = _http_failure(error)
    assert "http://localhost:11434/v1/chat/completions" in line


def test_http_error_preserves_port_while_stripping_credentials_and_query():
    """https://user:tok@wiki.example:8090/rest/api?token=x gives
    https://wiki.example:8090/rest/api, with no credentials and no query."""
    error = _refused(401, "Unauthorized", "https://user:tok@wiki.example:8090/rest/api?token=x")
    line = _http_failure(error)
    assert "https://wiki.example:8090/rest/api" in line
    assert "user" not in line
    assert "tok" not in line
    assert "token=x" not in line


def test_http_error_remains_portless_when_url_has_no_port():
    """https://wiki.example/rest stays portless."""
    error = _refused(500, "Internal Server Error", "https://wiki.example/rest")
    line = _http_failure(error)
    assert "https://wiki.example/rest" in line
    assert "https://wiki.example:" not in line


def test_http_error_handles_malformed_port_without_exception():
    """A malformed port gives the host only, with no exception."""
    error = _refused(502, "Bad Gateway", "http://example.com:badport/rest")
    line = _http_failure(error)
    assert "http://example.com/rest" in line


def test_provider_http_error_preserves_port():
    """ProviderHttpError carrying a URL with a port retains it in the output."""
    error = ProviderHttpError(
        "failed", status=401, url="http://localhost:11434/v1/chat/completions"
    )
    line = _http_failure(error)
    assert "http://localhost:11434/v1/chat/completions" in line


def test_an_ipv6_literal_keeps_its_brackets_with_and_without_a_port():
    """b5 on 425: `urlsplit(...).hostname` drops the brackets, and
    `http://::1:11434/v1` is not the address `http://[::1]:11434/v1` names."""
    with_port = _http_failure(_refused(404, "Not Found", "http://[::1]:11434/v1/chat/completions"))
    assert "http://[::1]:11434/v1/chat/completions" in with_port
    without = _http_failure(_refused(404, "Not Found", "http://[::1]/v1"))
    assert "http://[::1]/v1" in without


def test_port_zero_is_a_port_the_url_carries():
    """`:0` is falsy and still the port the URL named."""
    line = _http_failure(_refused(404, "Not Found", "http://localhost:0/v1"))
    assert "http://localhost:0/v1" in line
