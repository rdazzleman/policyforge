"""Shared Confluence REST API auth for confluence_exporter.py (push) and
confluence_importer.py (pull) — both hit the same Content REST API with the
same credential pattern, so it's derived in one place.
"""

from __future__ import annotations

import os

API_CONTENT_PATH = "rest/api/content"


def reports_as_confluence(function):
    """Tag an HTTP failure from `function` as Confluence's, with the token
    variable it was given (#413), so the one line a failed request becomes
    names Confluence and that variable and not another service's credential."""
    import functools
    import inspect

    import requests

    from policyforge.http_service import CONFLUENCE, Service, tag

    signature = inspect.signature(function)
    default = signature.parameters.get("token_env")

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except requests.exceptions.RequestException as exc:
            bound = signature.bind_partial(*args, **kwargs).arguments
            token_env = bound.get("token_env") or (default.default if default else "")
            tag(exc, Service(CONFLUENCE, "Confluence", str(token_env or "")))
            raise

    return wrapper


def confluence_auth(
    *,
    username_env: str = "CONFLUENCE_USERNAME",
    token_env: str = "CONFLUENCE_API_TOKEN",
) -> tuple[tuple[str, str] | None, dict[str, str]]:
    """Returns (auth, headers) for `requests`: HTTP Basic with
    (username, token) — an email + API token on Confluence Cloud — if
    `username_env` is set, otherwise `token_env` is sent as a Bearer token,
    for Confluence Server/Data Center personal access tokens."""
    token = os.environ.get(token_env)
    if not token:
        from policyforge.config import MissingCredentialError

        raise MissingCredentialError(
            f"Environment variable {token_env} is not set, so there is no Confluence API "
            f"token to send. Export it (`export {token_env}=...`), or name the variable "
            "that holds it with the command's token option."
        )
    username = os.environ.get(username_env)
    auth = (username, token) if username else None
    headers = {} if auth else {"Authorization": f"Bearer {token}"}
    return auth, headers
