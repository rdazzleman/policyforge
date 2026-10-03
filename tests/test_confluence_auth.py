"""Credential derivation for the Confluence REST API.

Thirty-two lines with no tests, which is a poor trade for what they decide:
whether a token is sent as HTTP Basic or as a Bearer header, and what
happens when it is missing. Confluence Cloud wants an email plus an API
token; Server and Data Center want a personal access token as a Bearer.
Sending the wrong shape fails with a 401 that says nothing useful, and the
person debugging it has no reason to suspect this function.
"""

from __future__ import annotations

import pytest

from policyforge.export._confluence_auth import confluence_auth


def test_a_username_means_cloud_and_basic_auth(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_USERNAME", "ryan@example.com")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")

    auth, headers = confluence_auth()

    assert auth == ("ryan@example.com", "tok")
    assert headers == {}, "Basic auth must not also send a Bearer header"


def test_no_username_means_a_bearer_token(monkeypatch):
    """Server and Data Center personal access tokens."""
    monkeypatch.delenv("CONFLUENCE_USERNAME", raising=False)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")

    auth, headers = confluence_auth()

    assert auth is None
    assert headers == {"Authorization": "Bearer tok"}


def test_an_empty_username_is_not_a_username(monkeypatch):
    """`CONFLUENCE_USERNAME=` in a shell profile or a CI variable left blank
    is the same as unset. Treating it as set produces Basic auth with an
    empty user, which fails as a 401 that looks like a bad token."""
    monkeypatch.setenv("CONFLUENCE_USERNAME", "")
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")

    auth, headers = confluence_auth()

    assert auth is None
    assert headers == {"Authorization": "Bearer tok"}


def test_a_missing_token_says_which_variable_to_set(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError) as caught:
        confluence_auth()

    assert "CONFLUENCE_API_TOKEN" in str(caught.value)


def test_the_error_never_repeats_the_token(monkeypatch):
    """A credential in an exception reaches a traceback, a log aggregator
    and a pasted bug report. The one case that raises here has no token to
    leak, so this pins that it stays that way under a custom variable name
    whose value happens to be set."""
    monkeypatch.setenv("MY_TOKEN_VAR", "s3cret-value")
    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError) as caught:
        confluence_auth(token_env="CONFLUENCE_API_TOKEN")

    assert "s3cret-value" not in str(caught.value)


def test_the_variable_names_can_be_overridden(monkeypatch):
    """Two Confluence instances in one shell, or a CI job that names its
    secrets differently."""
    monkeypatch.setenv("OTHER_USER", "ci@example.com")
    monkeypatch.setenv("OTHER_TOKEN", "tok2")

    auth, headers = confluence_auth(username_env="OTHER_USER", token_env="OTHER_TOKEN")

    assert auth == ("ci@example.com", "tok2")
    assert headers == {}
