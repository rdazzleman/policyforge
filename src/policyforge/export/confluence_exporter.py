"""Confluence export — a secondary, additional feature layered on top of
the canonical markdown output, not an independent generation path.

This module takes the *already-generated, already-quality-checked* markdown
from generate/policy_writer.py and converts it to Confluence storage
format, then optionally publishes via the Confluence REST API. It does not
call the LLM and does not regenerate content — that guarantees the
markdown and Confluence versions of a document can't drift from each other
in substance, only in formatting.

This module originally evaluated (and briefly used) the `md2cf` package for
both jobs, per this module's original TODO. It was dropped: md2cf pins
every one of its own dependencies to an exact, years-old version —
`mistune==0.8.4` (dozens of known CVEs) and `requests==2.31.0` (known
CVEs) among them — which is exactly what this repo's own `pip-audit` gate
exists to catch. Instead:

- `markdown_to_confluence` renders with `markdown-it-py` (already a
  maintained transitive dependency via `mdformat`) plus a small custom
  renderer subclass that only needs to special-case fenced code blocks
  (Confluence's `code` macro) — every other CommonMark/GFM construct this
  project's generated output uses (headings, paragraphs, emphasis, lists,
  tables, links, inline code) renders to valid Confluence storage format
  (an XHTML dialect) via markdown-it-py's default HTML output, unmodified.
- `export_to_confluence` calls the Confluence Content REST API directly via
  `requests`, with no dependency pinned to a specific vulnerable version.

Confluence API credentials are read from environment variables only,
matching the pattern in llm/anthropic_provider.py — never from a committed
config file.
"""

from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from mdit_py_plugins.gfm import gfm_plugin

from ._confluence_auth import API_CONTENT_PATH, confluence_auth, reports_as_confluence


class _ConfluenceRenderer(RendererHTML):
    """markdown-it-py's default HTML renderer already produces valid
    Confluence storage format for everything except fenced code blocks,
    which Confluence expects as its `code` structured macro rather than a
    bare `<pre><code>` (for syntax highlighting in the rendered page)."""

    def fence(self, tokens, idx, options, env):
        token = tokens[idx]
        info = token.info.strip()
        language = info.split()[0] if info else ""
        # Guard against the code content itself containing "]]>", which
        # would otherwise prematurely terminate the CDATA section.
        content = token.content.replace("]]>", "]]]]><![CDATA[>")
        language_param = (
            f'<ac:parameter ac:name="language">{language}</ac:parameter>' if language else ""
        )
        return (
            '<ac:structured-macro ac:name="code">'
            f"{language_param}"
            f"<ac:plain-text-body><![CDATA[{content}]]></ac:plain-text-body>"
            "</ac:structured-macro>\n"
        )


def markdown_to_confluence(markdown_text: str) -> str:
    """Convert already-generated canonical markdown to Confluence storage
    format (XHTML-based). Does not touch the LLM or regenerate content.

    **A Standard's source blocks become the `expand` macro** (#17, part 2b;
    80's 6048), titled with the block's summary (`Source: NIST 800-53 (2)`),
    with the block's own markdown rendered inside it. Without this a
    `<details>` block reached the page as raw HTML. `confluence_importer`
    turns the macro back into a block. A block that lost its close is left
    as it is, and `check` names it."""
    md = MarkdownIt("commonmark", renderer_cls=_ConfluenceRenderer).use(gfm_plugin)
    return _render_with_expand(md, markdown_text)


def _expand_macro(title: str, body_html: str) -> str:
    from html import escape

    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{escape(title, quote=False)}</ac:parameter>'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        "</ac:structured-macro>\n"
    )


def _render_with_expand(md: MarkdownIt, text: str) -> str:
    from policyforge.content.sources import SUMMARY_RE, source_block_spans

    spans = source_block_spans(text)
    if not spans:
        return md.render(text)
    # A placeholder paragraph per block, swapped for its macro after the
    # rest renders; chosen so it can't already be in the document.
    marker = "PFSOURCEBLOCK"
    while marker in text:
        marker += "X"
    lines = text.split("\n")
    out: list[str] = []
    macros: list[str] = []
    previous = 0
    for start, end in spans:
        out += lines[previous:start]
        summary = SUMMARY_RE.match(lines[start + 1].strip()) if start + 1 < end else None
        title = summary.group("title") if summary else "Source"
        inner = "\n".join(lines[(start + 2 if summary else start + 1) : end])
        macros.append(_expand_macro(title, md.render(inner)))
        out += ["", f"{marker}{len(macros) - 1}Z", ""]
        previous = end + 1
    out += lines[previous:]
    html = md.render("\n".join(out))
    # Each placeholder is a paragraph of its own: blank lines surround it, and
    # `source_block_spans` takes no block from inside a fence, the one place
    # it could be read as something else (b5 on PR 431).
    for index, macro in enumerate(macros):
        html = html.replace(f"<p>{marker}{index}Z</p>\n", macro)
    return html


#: Stamped into the version message of every page this tool writes. It is how
#: `publish` tells its own last write from somebody's hand edit without having
#: to store the page version in the repository — which a CI job, running on a
#: checkout it cannot commit to, could never keep current.
PUBLISH_MARKER = "Published by policyforge"

#: Stamped by a live edit (`edit-confluence`, `edit-topic --host`), which
#: writes the approved edit to the page and not to the repository. It is
#: NOT the publish stamp, and must not contain it (`publish` matches that
#: as a substring): a live edit is a change the repository hasn't seen, so
#: `publish` refuses the page, as for a hand edit, until `pull` brings it in.
#: It used to carry the publish stamp, and the next `publish --apply` from
#: an unchanged tree silently reverted the edit (#383).
LIVE_EDIT_MARKER = "Edited live by policyforge"


@reports_as_confluence
def export_to_confluence(
    markdown_text: str,
    *,
    space: str,
    title: str,
    host: str,
    parent_id: str | None = None,
    username_env: str = "CONFLUENCE_USERNAME",
    token_env: str = "CONFLUENCE_API_TOKEN",
) -> str:
    """Convert `markdown_text` to Confluence storage format and create (or,
    if a page with this title already exists in the space, update) the
    page. Returns the resulting page's URL.

    Auth: HTTP Basic with (`username_env`, `token_env`) — an email + API
    token on Confluence Cloud. If `username_env` isn't set, `token_env` is
    sent as a Bearer token instead, for Confluence Server/Data Center
    personal access tokens.
    """
    import requests

    auth, headers = confluence_auth(username_env=username_env, token_env=token_env)
    base = host.rstrip("/")

    body = markdown_to_confluence(markdown_text)
    body_payload = {"storage": {"value": body, "representation": "storage"}}
    ancestors = [{"id": parent_id}] if parent_id else None

    search = requests.get(
        f"{base}/{API_CONTENT_PATH}",
        params={"title": title, "spaceKey": space, "expand": "version"},
        auth=auth,
        headers=headers,
        timeout=30,
    )
    search.raise_for_status()
    results = search.json().get("results", [])

    if results:
        page = results[0]
        payload = {
            "id": page["id"],
            "type": "page",
            "title": title,
            "space": {"key": space},
            "body": body_payload,
            # Stamped, so the next publish can tell this write from a hand
            # edit made after it. `publish_tree` refuses to overwrite a page
            # whose latest version lacks the stamp.
            "version": {"number": page["version"]["number"] + 1, "message": PUBLISH_MARKER},
        }
        if ancestors:
            payload["ancestors"] = ancestors
        response = requests.put(
            f"{base}/{API_CONTENT_PATH}/{page['id']}",
            json=payload,
            auth=auth,
            headers=headers,
            timeout=30,
        )
    else:
        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space},
            "body": body_payload,
            "version": {"number": 1, "message": PUBLISH_MARKER},
        }
        if ancestors:
            payload["ancestors"] = ancestors
        response = requests.post(
            f"{base}/{API_CONTENT_PATH}", json=payload, auth=auth, headers=headers, timeout=30
        )

    response.raise_for_status()
    data = response.json()
    return f"{base}{data['_links']['webui']}"


class ConcurrentEditError(RuntimeError):
    """The page changed between being read and being written back."""


@reports_as_confluence
def update_page_body(
    markdown_text: str,
    *,
    page_id: str,
    title: str,
    space: str,
    host: str,
    expected_version: int,
    username_env: str = "CONFLUENCE_USERNAME",
    token_env: str = "CONFLUENCE_API_TOKEN",
) -> str:
    """Replace a known page's body, but only if it is still at
    `expected_version`. Returns the resulting page URL.

    This differs from `export_to_confluence` on purpose. That function
    re-reads the current version at publish time and increments it, so a
    publish always wins — which is right when you are pushing a freshly
    generated document that is the source of truth. It is wrong when you are
    *editing what is already there*: between reading the page and writing it
    back, someone else may have edited it, and blindly incrementing would
    discard their change without either party noticing.

    So the version read at fetch time is passed back in here and checked.
    Confluence itself rejects a stale version number, and that rejection is
    surfaced as `ConcurrentEditError` rather than a bare HTTP error, because
    the fix ("re-read the page and redo the edit") is specific and worth
    saying.
    """
    import requests

    auth, headers = confluence_auth(username_env=username_env, token_env=token_env)
    base = host.rstrip("/")

    current = requests.get(
        f"{base}/{API_CONTENT_PATH}/{page_id}",
        params={"expand": "version"},
        auth=auth,
        headers=headers,
        timeout=30,
    )
    current.raise_for_status()
    live_version = current.json()["version"]["number"]
    if live_version != expected_version:
        raise ConcurrentEditError(
            f"Page {title!r} is at version {live_version}, but the edit was planned "
            f"against version {expected_version}. Someone changed it in the meantime. "
            "Re-run to plan against the current content."
        )

    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "space": {"key": space},
        "body": {
            "storage": {"value": markdown_to_confluence(markdown_text), "representation": "storage"}
        },
        # Not the publish stamp: the repository hasn't seen this edit, so the
        # next `publish` must refuse the page until `pull` brings it in. With
        # the publish stamp, that publish reverted the edit (#383).
        "version": {"number": expected_version + 1, "message": LIVE_EDIT_MARKER},
    }
    response = requests.put(
        f"{base}/{API_CONTENT_PATH}/{page_id}",
        json=payload,
        auth=auth,
        headers=headers,
        timeout=30,
    )
    if response.status_code == 409:
        raise ConcurrentEditError(
            f"Confluence rejected the update to {title!r} as a version conflict — the "
            "page changed while the edit was being applied. Re-run to plan against "
            "the current content."
        )
    response.raise_for_status()
    return f"{base}{response.json()['_links']['webui']}"
