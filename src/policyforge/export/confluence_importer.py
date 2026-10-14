"""Pull an existing page's content back out of Confluence, converting its
storage format (XHTML) back to this project's canonical markdown — the
reverse of confluence_exporter.py's markdown_to_confluence /
export_to_confluence.

Two things this exists for:
- Bootstrapping: bring a policy that already lives in Confluence (written
  by hand before this tool existed) into the pipeline so it can be tracked
  going forward — see history/version_store.py.
- Drift detection: the `import-confluence` command (cli/content.py) records the
  imported content into the *same* version stream as `generate` for that
  tier/name, so you can diff "what this tool last generated" against
  "what's actually live" in case someone hand-edited the published page.

Conversion is not guaranteed lossless for Confluence-native macros beyond
the `code` macro this project's own exporter emits (see
`_restore_code_fences`) — round-trip fidelity is only guaranteed for
documents this tool itself published. A panel, expand block, or page
property macro will pass through as raw HTML rather than vanishing or
crashing, but it won't come back as clean markdown either.

Reading a *foreign* page — one a team hand-wrote years before this tool
existed — asks more of the conversion than round-tripping our own does, and
that is what `_restore_links` is for. Confluence writes cross-page links,
user mentions and images as `<ac:link>` / `<ac:image>` elements whose payload
lives entirely in attributes, so markdownify, which knows only HTML, renders
them as *nothing at all*: a page-properties row reading `Owner: @Jane`
arrives as an empty table cell, and "see the Access Review Procedure"
arrives as "see .". For the existing use — drift detection against documents
this tool published, which contain none of those elements — that never
mattered. For anything that reads someone else's page and answers questions
about it, a silently blank owner is worse than a missing one, because it
reads as an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ._confluence_auth import API_CONTENT_PATH, confluence_auth, reports_as_confluence


@dataclass
class ConfluencePage:
    id: str
    title: str
    version: int
    storage_body: str
    webui_url: str
    #: Ancestor page titles, outermost first. Confluence hierarchy is the
    #: cheapest structural signal there is — a Standard filed under a Policy
    #: says which topic it belongs to without anyone declaring it.
    ancestors: list[str] = field(default_factory=list)
    #: Page labels. Teams that already label their space have effectively
    #: pre-classified it; asking costs nothing on the same request.
    labels: list[str] = field(default_factory=list)
    #: The message on the latest version. `publish` reads it to tell this
    #: tool's own last write — stamped with `PUBLISH_MARKER` — from somebody's
    #: hand edit, which a bare version number cannot distinguish.
    version_message: str = ""


#: Everything worth having from one page request. `ancestors` and
#: `metadata.labels` are free on a call already being made, and are the two
#: signals that let a page be placed in a document set without a human
#: declaring where it goes.
_PAGE_EXPAND = "body.storage,version,ancestors,metadata.labels"


def _parse_page(page: dict, *, base: str) -> ConfluencePage:
    """Build a ConfluencePage from one REST result.

    Tolerant of the expansions being absent: the same shape comes back from
    a search that asked for less, and a missing ancestor list should mean
    "unknown", not an exception.
    """
    metadata = page.get("metadata") or {}
    labels = (metadata.get("labels") or {}).get("results") or []
    body = (page.get("body") or {}).get("storage") or {}
    version = page.get("version") or {}
    return ConfluencePage(
        id=page["id"],
        title=page["title"],
        version_message=str(version.get("message") or ""),
        version=version.get("number", 0),
        storage_body=body.get("value", ""),
        webui_url=f"{base}{(page.get('_links') or {}).get('webui', '')}",
        ancestors=[a["title"] for a in page.get("ancestors") or [] if a.get("title")],
        labels=[lbl["name"] for lbl in labels if lbl.get("name")],
    )


@reports_as_confluence
def fetch_confluence_page(
    *,
    space: str,
    title: str,
    host: str,
    username_env: str = "CONFLUENCE_USERNAME",
    token_env: str = "CONFLUENCE_API_TOKEN",
) -> ConfluencePage:
    """Look up a page by title within a space and return its current
    storage-format body and version number. Raises LookupError if no page
    with that exact title exists in that space."""
    import requests

    auth, headers = confluence_auth(username_env=username_env, token_env=token_env)
    base = host.rstrip("/")

    response = requests.get(
        f"{base}/{API_CONTENT_PATH}",
        params={"title": title, "spaceKey": space, "expand": _PAGE_EXPAND},
        auth=auth,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    results = response.json().get("results", [])
    if not results:
        raise LookupError(f"No Confluence page titled {title!r} found in space {space!r}.")

    return _parse_page(results[0], base=base)


#: Attributes allowed on every element, in any order: a page as the server
#: returns it carries `ac:schema-version` and `ac:macro-id`, which this
#: project's exporter doesn't write (b5 on PR 431), and the strict pattern
#: restored nothing from such a page.
_CODE_MACRO_RE = re.compile(
    r'<ac:structured-macro\b[^>]*\bac:name="code"[^>]*>\s*'
    r'(?:<ac:parameter\b[^>]*\bac:name="language"[^>]*>(?P<language>[^<]*)</ac:parameter>\s*)?'
    r"<ac:plain-text-body><!\[CDATA\[(?P<code>.*?)\]\]></ac:plain-text-body>\s*"
    r"</ac:structured-macro>",
    re.DOTALL,
)


def _restore_code_fences(storage_html: str) -> str:
    """Turn this project's own `code` structured macro (see
    confluence_exporter.py's `_ConfluenceRenderer.fence`) back into a plain
    `<pre><code>` block markdownify already knows how to render as a fenced
    code block, before handing the rest of the document to markdownify —
    markdownify has no Confluence-macro-specific knowledge of its own."""

    from html import escape

    def _replace(match: re.Match[str]) -> str:
        language = match.group("language") or ""
        # Escaped: CDATA holds the code raw, and markdownify parses what it is
        # handed as HTML, so `<b>weekly</b>` in a code block came back as
        # `weekly` (found by #17's fidelity check on a round-tripped quote).
        code = escape(match.group("code").replace("]]]]><![CDATA[>", "]]>"), quote=False)
        lang_attr = f' class="language-{language}"' if language else ""
        return f"<pre><code{lang_attr}>{code}</code></pre>"

    return _CODE_MACRO_RE.sub(_replace, storage_html)


#: `<ac:link>...</ac:link>` and `<ac:image>...</ac:image>` wrappers. Matched
#: as an outer shell whose contents are then inspected, rather than as one
#: monolithic pattern per shape: the payload element (`ri:page`, `ri:user`,
#: `ri:attachment`) and the optional link body can appear in either order and
#: with attributes in any order, which a single regex handles badly.
_LINK_RE = re.compile(r"<ac:link\b[^>]*>(?P<inner>.*?)</ac:link>", re.DOTALL)
_IMAGE_RE = re.compile(r"<ac:image\b[^>]*>(?P<inner>.*?)</ac:image>", re.DOTALL)

_RI_PAGE_RE = re.compile(r'<ri:page\b[^>]*ri:content-title="(?P<title>[^"]*)"')
_RI_ATTACHMENT_RE = re.compile(r'<ri:attachment\b[^>]*ri:filename="(?P<filename>[^"]*)"')
_RI_URL_RE = re.compile(r'<ri:url\b[^>]*ri:value="(?P<url>[^"]*)"')
#: Cloud identifies a person by `ri:account-id`, Server/DC by `ri:userkey`.
_RI_USER_RE = re.compile(r'<ri:user\b[^>]*ri:(?:account-id|userkey)="(?P<user>[^"]*)"')
#: An explicit label on a link, which overrides the target's own title.
_LINK_BODY_RE = re.compile(
    r"<ac:(?:plain-text-link-body|link-body)>(?:<!\[CDATA\[)?(?P<label>.*?)(?:\]\]>)?"
    r"</ac:(?:plain-text-link-body|link-body)>",
    re.DOTALL,
)

#: Shown where a mention names someone the sync could not resolve to a
#: display name. Deliberately conspicuous: the failure this exists to
#: prevent is a blank owner field reading as "nobody owns this".
UNRESOLVED_USER = "@unresolved-user"


def _restore_links(storage_html: str, user_names: dict[str, str] | None = None) -> str:
    """Render Confluence's attribute-only elements as text markdownify can see.

    Cross-page links, user mentions and images carry everything in
    attributes, so an HTML-only converter drops them entirely. Each is
    replaced with the plain text a reader would have seen on the page:

    * a page or attachment link becomes its label, or the target's title
    * a mention becomes the person's display name, when `user_names` has it
    * an image becomes `[image: filename]`, so a diagram is visibly present
      rather than silently absent

    Links deliberately become plain text rather than markdown links: there
    is no URL to point at, and a page's *outbound references* are more
    useful collected separately (see `extract_references`) than inlined into
    prose that then reads badly.
    """
    names = user_names or {}

    def _link(match: re.Match[str]) -> str:
        inner = match.group("inner")
        body = _LINK_BODY_RE.search(inner)
        if body and body.group("label").strip():
            return body.group("label").strip()
        page = _RI_PAGE_RE.search(inner)
        if page:
            return page.group("title")
        user = _RI_USER_RE.search(inner)
        if user:
            return names.get(user.group("user"), UNRESOLVED_USER)
        attachment = _RI_ATTACHMENT_RE.search(inner)
        if attachment:
            return attachment.group("filename")
        url = _RI_URL_RE.search(inner)
        if url:
            return url.group("url")
        return ""

    def _image(match: re.Match[str]) -> str:
        inner = match.group("inner")
        attachment = _RI_ATTACHMENT_RE.search(inner)
        if attachment:
            return f"[image: {attachment.group('filename')}]"
        url = _RI_URL_RE.search(inner)
        if url:
            return f"[image: {url.group('url')}]"
        return "[image]"

    return _IMAGE_RE.sub(_image, _LINK_RE.sub(_link, storage_html))


def extract_user_ids(storage_html: str) -> set[str]:
    """Every account id or user key mentioned on a page.

    Collected before conversion so a caller can resolve them all in one
    batch and hand the names back to `confluence_to_markdown`, rather than
    making one API call per mention mid-render.
    """
    return {match.group("user") for match in _RI_USER_RE.finditer(storage_html)}


def extract_references(storage_html: str) -> list[str]:
    """Titles of other Confluence pages this page links to, in order.

    Kept out of the converted markdown and returned separately: as prose,
    an inlined link target reads badly, but as a graph it answers "what else
    should I read alongside this?" — which is exactly the question a policy
    set generates, since a Standard and its Procedure reference each other.
    """
    seen: list[str] = []
    for link in _LINK_RE.finditer(storage_html):
        page = _RI_PAGE_RE.search(link.group("inner"))
        if page and page.group("title") not in seen:
            seen.append(page.group("title"))
    return seen


def _code_language_callback(el) -> str | None:
    """markdownify only emits a bare ``` fence by default; pull the
    language back out of the `class="language-X"` attribute
    `_restore_code_fences` set on the `<code>` tag, so a fenced code block
    keeps its language on the round trip."""
    code = el.find("code")
    if code is None:
        return None
    for css_class in code.get("class", []):
        if css_class.startswith("language-"):
            return css_class[len("language-") :]
    return None


def confluence_to_markdown(storage_html: str, *, user_names: dict[str, str] | None = None) -> str:
    """Convert a Confluence storage-format page body back to CommonMark
    markdown.

    `user_names` maps account ids (or Server user keys) to display names, so
    that mentions render as the person rather than as `@unresolved-user`.
    Collect the ids with `extract_user_ids` and resolve them in one batch.
    """
    from markdownify import markdownify

    storage_html, blocks = _lift_source_blocks(storage_html, user_names)
    prepared = _restore_links(_restore_code_fences(storage_html), user_names)
    markdown = (
        markdownify(
            prepared,
            heading_style="ATX",
            bullets="-",
            code_language_callback=_code_language_callback,
        ).strip()
        + "\n"
    )
    for placeholder, block in blocks.items():
        markdown = markdown.replace(placeholder, block)
    return markdown


#: The `expand` macro `confluence_exporter` writes for a Standard's source
#: block (#17, part 2b). Its body holds no `rich-text-body` of its own (a
#: quote is a `code` macro, whose body is `plain-text-body`), so the lazy
#: match ends at the macro's own close.
_EXPAND_RE = re.compile(
    r'<ac:structured-macro\b[^>]*\bac:name="expand"[^>]*>\s*'
    r'<ac:parameter\b[^>]*\bac:name="title"[^>]*>(?P<title>[^<]*)</ac:parameter>\s*'
    r"<ac:rich-text-body>(?P<body>.*?)</ac:rich-text-body>\s*"
    r"</ac:structured-macro>",
    re.DOTALL,
)


def _source_block_title(match: re.Match[str]) -> str | None:
    """The title of an `_EXPAND_RE` match when it is a source block's
    (`Source: <catalog> (<n>)`), else None. **The one rule** for which
    `expand` macro is the exporter's own: `_lift_source_blocks` reads these
    back into the block, and `unwrap_source_blocks` lets the macro check
    through them (449)."""
    from html import unescape

    from policyforge.content.sources import SUMMARY_RE

    title = unescape(match.group("title")).strip()
    return title if SUMMARY_RE.match(f"<summary>{title}</summary>") else None


def unwrap_source_blocks(storage_html: str) -> str:
    """`storage_html` with each source block's `expand` macro replaced by its
    body (449). The wrapper is what `_lift_source_blocks` turns back into the
    block, so it survives a round trip; what is inside it is read like the
    rest of the page, so a macro a person put in there is still seen.

    An `expand` with any other title, or with a parameter the exporter never
    writes, doesn't match and stays, so it is still reported."""
    return _EXPAND_RE.sub(
        lambda m: m.group("body") if _source_block_title(m) is not None else m.group(0),
        storage_html,
    )


def _lift_source_blocks(storage_html: str, user_names) -> tuple[str, dict[str, str]]:
    """Each `expand` macro titled as a source block (`Source: <catalog>
    (<n>)`), taken out of `storage_html` and converted back into the block
    `generate` wrote, so `pull` keeps the marker `check` and `deontic` read.
    Any other `expand` macro is left for markdownify, as before."""
    from policyforge.content.sources import SOURCE_BLOCK_CLOSE, SOURCE_BLOCK_OPEN

    blocks: dict[str, str] = {}
    marker = "PFSOURCEBLOCK"
    while marker in storage_html:
        marker += "X"

    def lift(match: re.Match[str]) -> str:
        title = _source_block_title(match)
        if title is None:
            return match.group(0)
        placeholder = f"{marker}{len(blocks)}Z"
        inner = confluence_to_markdown(match.group("body"), user_names=user_names).strip()
        blocks[placeholder] = (
            f"{SOURCE_BLOCK_OPEN}\n<summary>{title}</summary>\n\n{inner}\n\n{SOURCE_BLOCK_CLOSE}"
        )
        return f"<p>{placeholder}</p>"

    return _EXPAND_RE.sub(lift, storage_html), blocks


class PageTooDeepError(ValueError):
    """A Confluence page nested too deeply to convert (#273).

    The converter recurses once per nested element, so a page nested about
    500 deep exhausts the stack (#252). Every caller converts through
    `convert_page`, which turns that `RecursionError` into this, naming the
    page, so each caller refuses the page as it refuses any page it can't
    take: one page by name in a loop, or one line for a single page.
    """


def convert_page(storage_html: str, *, title: str, user_names: dict[str, str] | None = None) -> str:
    """`confluence_to_markdown`, with a page too deep to convert raised as
    `PageTooDeepError` naming `title` (80's ruling on #273: one place, not one
    `except RecursionError` per caller). Only `RecursionError` is caught; the
    recursion limit is left alone, since raising it moves the cliff."""
    try:
        # Called as each caller called it before (#273): `user_names` only
        # where the caller had names to give.
        if user_names is None:
            return confluence_to_markdown(storage_html)
        return confluence_to_markdown(storage_html, user_names=user_names)
    except RecursionError:
        raise PageTooDeepError(f"page {title!r} nested too deeply to convert") from None


@reports_as_confluence
def import_from_confluence(
    *,
    space: str,
    title: str,
    host: str,
    username_env: str = "CONFLUENCE_USERNAME",
    token_env: str = "CONFLUENCE_API_TOKEN",
) -> str:
    """Fetch a page from Confluence and convert it to markdown — the
    reverse of confluence_exporter.py's `export_to_confluence`. A page too
    deep to convert raises `PageTooDeepError` (#273)."""
    page = fetch_confluence_page(
        space=space, title=title, host=host, username_env=username_env, token_env=token_env
    )
    return convert_page(page.storage_body, title=page.title)
