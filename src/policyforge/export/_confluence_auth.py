"""Shared Confluence REST API auth for confluence_exporter.py (push) and
confluence_importer.py (pull) — both hit the same Content REST API with the
same credential pattern, so it's derived in one place.
"""

from __future__ import annotations

import os

API_CONTENT_PATH = "rest/api/content"


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
        raise RuntimeError(
            f"Environment variable {token_env} is not set. Export your "
            f"Confluence API token before running policyforge, e.g.:\n"
            f"  export {token_env}=..."
        )
    username = os.environ.get(username_env)
    auth = (username, token) if username else None
    headers = {} if auth else {"Authorization": f"Bearer {token}"}
    return auth, headers
