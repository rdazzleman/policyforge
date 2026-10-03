"""Find pages in Confluence, rather than fetching one you can already name.

`confluence_importer.fetch_confluence_page` looks a page up by exact title,
which is all the rest of the pipeline ever needs: every command that touches
Confluence is told which page it is operating on. Zardoz is the first thing
here that has to *discover* pages — a supporting space is defined by its key,
not by a list of titles somebody maintained — so it needs the search half of
the API.

Kept separate from confluence_importer.py because that module is about
converting one page's storage format back to markdown, and this one makes no
conversion decisions at all. They share the auth helper and the page shape.

Two guards worth knowing about:

* **Paging is bounded.** A space can hold thousands of pages, and a sync that
  discovers it has fetched nine thousand of them is a bad way to find out.
  `max_results` stops and says so rather than running until the API does.
* **Body is opt-in.** Listing a space to see what is in it is a different
  request from pulling every page's full text; asking for bodies you then
  discard is slow enough to be worth not doing by accident.
"""

from __future__ import annotations

from ._confluence_auth import confluence_auth
from .confluence_importer import _PAGE_EXPAND, ConfluencePage, _parse_page

API_SEARCH_PATH = "rest/api/content/search"
API_USER_PATH = "rest/api/user"

#: Confluence caps a single page of results well below this; it is the loop
#: increment, not a promise about what the server returns.
_PAGE_SIZE = 50

#: Refuse to walk a space larger than this without being asked to. Chosen to
#: sit an order of magnitude above a plausible policy space, so that hitting
#: it means the CQL selected more than intended.
DEFAULT_MAX_RESULTS = 500


class SearchLimitExceeded(RuntimeError):
    """Raised when a query matches more pages than the caller allowed.

    Deliberately an error rather than a silent truncation: a corpus that
    quietly contains the first 500 of 4,000 pages would answer questions
    confidently from an arbitrary subset of the documentation.
    """


def space_cql(space: str) -> str:
    """CQL selecting every current page in one space.

    `type=page` excludes blog posts, comments and attachments, which are not
    documents in the sense this tool means.
    """
    escaped = space.replace('"', '\\"')
    return f'space = "{escaped}" AND type = page'


def search_pages(
    *,
    host: str,
    cql: str,
    with_body: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS,
    username_env: str = "CONFLUENCE_USERNAME",
    token_env: str = "CONFLUENCE_API_TOKEN",
) -> list[ConfluencePage]:
    """Every page matching `cql`, paged through to the end.

    Set `with_body=True` to get storage-format bodies in the same pass;
    leave it off to list what exists before deciding what to pull.
    """
    import requests

    auth, headers = confluence_auth(username_env=username_env, token_env=token_env)
    base = host.rstrip("/")
    expand = _PAGE_EXPAND if with_body else "version,ancestors,metadata.labels"

    pages: list[ConfluencePage] = []
    start = 0
    while True:
        response = requests.get(
            f"{base}/{API_SEARCH_PATH}",
            params={"cql": cql, "expand": expand, "limit": _PAGE_SIZE, "start": start},
            auth=auth,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        pages.extend(_parse_page(page, base=base) for page in results)

        if len(pages) > max_results:
            raise SearchLimitExceeded(
                f"CQL query matched more than {max_results} pages: {cql!r}. Narrow the "
                "query, or raise the limit if the space really is that large."
            )
        # Confluence stops sending `_links.next` on the final page; falling
        # back to a short result set covers deployments that omit it.
        if not payload.get("_links", {}).get("next") or len(results) < _PAGE_SIZE:
            return pages
        start += _PAGE_SIZE


def fetch_user_names(
    account_ids: set[str],
    *,
    host: str,
    username_env: str = "CONFLUENCE_USERNAME",
    token_env: str = "CONFLUENCE_API_TOKEN",
) -> dict[str, str]:
    """Resolve account ids (Cloud) or user keys (Server/DC) to display names.

    A mention is stored as an opaque id, so "Owner: @Jane" is only a name
    after a second request. Failures are omitted rather than raised: one
    deactivated account should not fail a sync, and the caller renders what
    it could not resolve as `@unresolved-user`, which is visible.
    """
    import requests

    if not account_ids:
        return {}

    auth, headers = confluence_auth(username_env=username_env, token_env=token_env)
    base = host.rstrip("/")

    names: dict[str, str] = {}
    for account_id in sorted(account_ids):
        # Cloud keys the lookup on accountId and Server/DC on key; which one
        # a given id is cannot be told by looking at it, so try both.
        for param in ("accountId", "key"):
            try:
                response = requests.get(
                    f"{base}/{API_USER_PATH}",
                    params={param: account_id},
                    auth=auth,
                    headers=headers,
                    timeout=30,
                )
            except requests.RequestException:
                break
            if response.ok:
                display = response.json().get("displayName")
                if display:
                    names[account_id] = display
                break
    return names
