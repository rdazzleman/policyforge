"""Credentials and visibility for a GitHub wiki remote.

Two things the wiki adapter needs and neither belongs in it: how a token
reaches `git` without ever appearing on a command line, and whether the
repository being published to is public.

**The token never touches argv or disk.** The obvious spelling — putting it
in the remote URL, `https://x-access-token:TOKEN@github.com/...` — writes
the credential into `.git/config` inside the working clone, into the
process table for every `git` call, and into any error message that quotes
the remote. So it goes in the environment instead, through git's own
`GIT_CONFIG_*` variables, which set config for one process without writing
a file. `git` sends the header; nothing stores it.

Without `token_env`, nothing here is used at all: `git` falls back to the
user's credential helper, which is how a developer already authenticates
to GitHub and is the right default for a machine that has one.
"""

from __future__ import annotations

import base64
import os

#: The API host asked whether a repository is public. Declared in
#: `tests/test_no_undeclared_endpoints.py` beside the wiki remote itself.
GITHUB_API = "https://api.github.com"


def wiki_git_env(token_env: str = "") -> dict[str, str]:  # nosec B107 - a var name
    """Environment for `git` that authenticates as `token_env`'s token.

    Empty when no variable is named, which means the credential helper
    handles it. Raises when a variable is named and unset, rather than
    falling through to an anonymous clone that fails later with a message
    about the repository not existing — which is what GitHub returns for a
    private repository you did not authenticate to, and is a bad way to
    learn your token was missing.
    """
    if not token_env:
        return {}

    token = os.environ.get(token_env)
    if not token:
        raise RuntimeError(
            f"Environment variable {token_env} is not set. Export a GitHub token "
            f"with repository access before publishing to a wiki, e.g.:\n"
            f"  export {token_env}=..."
        )

    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def git_environment(token_env: str = "") -> dict[str, str]:  # nosec B107 - a var name
    """The full environment a `git` subprocess should run with.

    The inherited environment plus whatever `wiki_git_env` adds. It lives
    here rather than in the adapter so that `os.environ` is read in a module
    declared to read it — `tests/test_credential_containment.py` keeps that
    list, and the adapter reading the environment directly is exactly the
    drift that test exists to catch. It caught this one.
    """
    return {**os.environ, **wiki_git_env(token_env)}


def repository_visibility(repository: str, token_env: str = "") -> bool | None:  # nosec B107
    """True when the repository is public, False when private, None unknown.

    None is not an error to swallow: the caller refuses to publish on it,
    the same direction `llm/boundary.py` takes when it cannot classify.
    Every failure lands there — no network, no permission, a renamed
    repository, a response that is not the JSON this expects — because
    every one of them leaves the question unanswered, and the answer is
    what decides whether the organization's documents go somewhere the
    world can read.
    """
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a hard dependency
        return None

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get(token_env) if token_env else None
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(f"{GITHUB_API}/repos/{repository}", headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        private = response.json().get("private")
    except Exception:  # noqa: BLE001 - every failure means "unknown", not "public"
        return None

    # An explicit boolean or nothing. A response that omits the field, or
    # carries something that is not a boolean, is not an answer either.
    if isinstance(private, bool):
        return not private
    return None
