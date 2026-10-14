"""A Standard this tool published is accepted by its own publish, pull and
edit paths (449, a regression from PR 431).

`confluence_exporter` writes each source block as an `expand` macro, and
`detect_unsupported_macros` reported `expand` on the tool's own output, so a
1.8 Standard couldn't be republished, pulled or edited without
`--allow-macros`. The invariant: a macro is accepted when the importer reads
it back to the same markdown. Storage bodies below are written by hand, or
are the exporter's own output served as a Confluence server returns it.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from policyforge.content.sources import add_source_blocks, normalize_tags, source_index
from policyforge.edit.apply import detect_unsupported_macros
from policyforge.export.confluence_exporter import PUBLISH_MARKER, markdown_to_confluence
from policyforge.export.publish import CREATED, SKIPPED, publish_tree
from policyforge.export.publisher import UNCHANGED
from policyforge.export.pull import pull_pages
from policyforge.ingest.schema import load_controls
from policyforge.topics.satisfies import catalog_scope

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
STARTER = ("nist-800-53-r5", "fedramp", "arc-ampe", "hipaa-security-rule")

#: A Standard as `generate` writes it: blocks after a bulleted list, after a
#: paragraph, and one between two items of a numbered requirement list.
DOC = """# Incident Response Standard

## Plan

- Keep a plan. [NIST 800-53 IR-8 | ARC-AMPE IR-8]
- Report incidents. [HIPAA Security Rule 164.308(a)(6)(ii) | NIST 800-53 IR-6]

## Training

Train staff. [NIST 800-53 IR-2]

## Testing

1. Test the plan every year. [NIST 800-53 IR-3]
"""

#: The numbered requirement goes on after its block.
AFTER = "2. Record what the test found.\n"

TITLE = "Incident Response Standard"


@pytest.fixture(scope="module")
def standard():
    controls = [c for d in STARTER for c in load_controls(FRAMEWORKS / d / "controls.json")]
    scope = catalog_scope(controls)
    body, added = add_source_blocks(normalize_tags(DOC, scope), source_index(controls), scope)
    assert added == 5
    body = body.rstrip("\n") + "\n\n" + AFTER
    return SimpleNamespace(body=body, storage=markdown_to_confluence(body))


def _served(storage: str, *, first: bool = False) -> str:
    """The page as a Confluence server returns it: every macro carries
    `ac:schema-version` and its own `ac:macro-id` (b5 on PR 431), after its
    name, or before it with `first`."""
    ids = itertools.count(1)

    def stamp(match: re.Match) -> str:
        extra = f'ac:schema-version="1" ac:macro-id="m{next(ids):04d}"'
        if first:
            return f"<ac:structured-macro {extra} {match.group(1)}"
        return f"<ac:structured-macro {match.group(1)} {extra}"

    return re.sub(r'<ac:structured-macro (ac:name="[^"]*")', stamp, storage)


def _expand(title: str, body: str = "<p>Text.</p>", extra: str = "") -> str:
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>{extra}'
        f"<ac:rich-text-body>{body}</ac:rich-text-body></ac:structured-macro>"
    )


# ---- the detector -------------------------------------------------------------


def test_the_exporters_own_source_blocks_are_accepted(standard):
    assert standard.storage.count('ac:name="expand"') == 5
    assert detect_unsupported_macros(standard.storage) == []
    assert detect_unsupported_macros(_served(standard.storage)) == []
    assert detect_unsupported_macros(_served(standard.storage, first=True)) == []


def test_a_macro_is_seen_wherever_its_name_stands_among_its_attributes():
    """The detector wanted `ac:name` first, and saw nothing on a page whose
    macros carried a server's attributes ahead of it."""
    info = (
        '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>x</p>'
        "</ac:rich-text-body></ac:structured-macro>"
    )
    assert detect_unsupported_macros(_served(info)) == ["info"]
    assert detect_unsupported_macros(_served(info, first=True)) == ["info"]
    assert detect_unsupported_macros(_served(_expand("Details"), first=True)) == ["expand"]


@pytest.mark.parametrize(
    ("storage", "reported"),
    [
        (_expand("Source: NIST 800-53 (2)"), []),
        (_expand("Source: HIPAA Security Rule (1)"), []),
        # A person's own expand block: the importer flattens it.
        (_expand("Details"), ["expand"]),
        (_expand("Source"), ["expand"]),
        (_expand("Sources: NIST 800-53 (2)"), ["expand"]),
        # A parameter the exporter never writes: the importer doesn't lift it.
        (
            _expand(
                "Source: NIST 800-53 (2)",
                extra='<ac:parameter ac:name="breakoutMode">wide</ac:parameter>',
            ),
            ["expand"],
        ),
        # A macro a person put inside a source block is still seen.
        (
            _expand(
                "Source: NIST 800-53 (1)",
                '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>x</p>'
                "</ac:rich-text-body></ac:structured-macro>",
            ),
            ["info"],
        ),
        ('<p>a</p><ac:structured-macro ac:name="status"/>', ["status"]),
        (_expand("Source: NIST 800-53 (1)") + _expand("Details"), ["expand"]),
    ],
)
def test_an_expand_is_judged_by_whether_the_importer_reads_it_back(storage, reported):
    assert detect_unsupported_macros(storage) == reported


# ---- the round trips ----------------------------------------------------------


@dataclass
class _Page:
    id: str
    title: str
    storage_body: str
    version: int = 1
    webui_url: str = "https://x/wiki/page"
    labels: list = field(default_factory=list)
    ancestors: list = field(default_factory=list)
    version_message: str = PUBLISH_MARKER


def _confluence(monkeypatch):
    """A Confluence that stores what `publish` exports and serves it back as a
    server does. Returns the store, by title."""
    store: dict[str, _Page] = {}

    def _fetch(*, space, title, host, **kwargs):
        if title not in store:
            raise LookupError(f"No Confluence page titled {title!r} in space {space!r}.")
        return store[title]

    def _export(markdown_text, *, space, title, host, **kwargs):
        old = store.get(title)
        store[title] = _Page(
            id=f"id-{title}",
            title=title,
            storage_body=_served(markdown_to_confluence(markdown_text)),
            version=(old.version + 1) if old else 1,
        )
        return f"https://x/wiki/{title}"

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr("policyforge.export.confluence_exporter.export_to_confluence", _export)
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **kw: {}
    )
    return store


def _file(root: Path, body: str) -> Path:
    path = root / "standards" / "incident-response.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {TITLE}\ntier: standard\nconfluence:\n  space: SEC\n  title: {TITLE}\n"
        f"---\n\n{body}",
        encoding="utf-8",
    )
    return path


def _body(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("---\n", 2)[2].lstrip("\n")


def test_a_published_standard_publishes_again_with_nothing_to_change(
    standard, tmp_path, monkeypatch
):
    store = _confluence(monkeypatch)
    _file(tmp_path, standard.body)

    first = publish_tree(tmp_path, host="https://x", dry_run=False)
    assert [r.action for r in first.results] == [CREATED]
    assert 'ac:schema-version="1" ac:macro-id="m0001"' in store[TITLE].storage_body

    again = publish_tree(tmp_path, host="https://x", dry_run=False)
    assert [(r.action, r.reason) for r in again.results] == [(UNCHANGED, "")]
    assert SKIPPED not in [r.action for r in again.results]
    assert store[TITLE].version == 1


def test_a_published_standard_pulls_back_to_the_same_file(standard, tmp_path, monkeypatch):
    _confluence(monkeypatch)
    path = _file(tmp_path, standard.body)
    publish_tree(tmp_path, host="https://x", dry_run=False)

    report = pull_pages(
        [("SEC", TITLE, "standard")], root=tmp_path, host="https://x", dry_run=False
    )

    assert report.refused == []
    assert _body(path) == standard.body
    assert list(tmp_path.rglob("*.md")) == [path]


def test_a_published_standard_can_be_planned_against_by_edit_topic(standard, tmp_path, monkeypatch):
    import policyforge.cli as cli_mod
    from tests.test_edit import ScriptedProvider, _plan_json

    _confluence(monkeypatch)
    _file(tmp_path, standard.body)
    publish_tree(tmp_path, host="https://x", dry_run=False)

    registry = tmp_path / "topics.yaml"
    registry.write_text(
        "topics:\n  - name: Incident Response\n    owner: Security\n"
        "    nist_controls: [IR-8]\n"
        f"    confluence: {{space: SEC, pages: {{standard: {TITLE}}}}}\n",
        encoding="utf-8",
    )
    provider = ScriptedProvider(
        _plan_json(
            steps=[
                {
                    "kind": "modify",
                    "target": "Training",
                    "summary": "Train staff every year.",
                    "rationale": "The instruction asks for it.",
                }
            ]
        ),
        standard.body.replace("Train staff.", "Train staff every year."),
    )
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: provider)

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "edit-topic",
            "--instruction",
            "Train staff every year.",
            "--topic-name",
            "Incident Response",
            "--topics",
            str(registry),
            "--host",
            "https://x",
            "--out-dir",
            str(tmp_path / "edits"),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )

    assert "cannot round-trip" not in result.output, result.output
    assert result.exit_code == 0, result.output
    assert provider.calls, "nothing was planned"


def test_a_servers_macro_bookkeeping_is_not_an_edit():
    """What a server stamps on each macro doesn't make the page differ, and
    text that merely spells such an attribute still does."""
    from policyforge.export.publisher import _storage_key

    page = (
        '<p>a</p><ac:structured-macro ac:name="code"><ac:plain-text-body>'
        "<![CDATA[x]]></ac:plain-text-body></ac:structured-macro>"
    )
    assert _storage_key(_served(page)) == _storage_key(page)
    assert _storage_key(_served(page)) != _storage_key(page.replace("<p>a</p>", "<p>b</p>"))
    spelled = '<p>Set ac:macro-id="7" in the template.</p>'
    assert _storage_key(spelled) != _storage_key("<p>Set in the template.</p>")
