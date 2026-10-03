"""zardoz corpus tests (milestone M1) — reading foreign Confluence pages.

The interesting cases here are all about *loss*. Any page converts to
something; the question is whether what went missing on the way is visible.
A blank owner field is the motivating example: it reads as an answer
("nobody owns this") while actually being a conversion failure.

No test makes a network call. The Confluence fetch, search and user-lookup
functions are all faked at the module level, the same way the edit tests
fake them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from policyforge.export.confluence_importer import (
    UNRESOLVED_USER,
    ConfluencePage,
    confluence_to_markdown,
    extract_references,
    extract_user_ids,
)
from policyforge.topics.registry import Topic

# A page as a security team would actually have written it years ago, with
# the Confluence furniture such pages accumulate.
HAND_WRITTEN_PAGE = """
<ac:structured-macro ac:name="details">
  <ac:rich-text-body>
    <table><tbody>
      <tr><th>Owner</th><td><ac:link><ri:user ri:account-id="557058:jane"/></ac:link></td></tr>
      <tr><th>Review cycle</th><td>Annual</td></tr>
    </tbody></table>
  </ac:rich-text-body>
</ac:structured-macro>
<h1>Access Control Standard</h1>
<h2>Requirements</h2>
<table><tbody>
  <tr><th>ID</th><th>Requirement</th></tr>
  <tr><td>AC-1</td><td>Accounts are reviewed quarterly.</td></tr>
</tbody></table>
<h2>Related</h2>
<p>See <ac:link><ri:page ri:content-title="Access Review Procedure"/></ac:link>.</p>
<p><ac:image><ri:attachment ri:filename="access-flow.png"/></ac:image></p>
"""


# --------------------------------------------------------------------------
# Conversion: what survives reading a page this tool did not write
# --------------------------------------------------------------------------


def test_a_user_mention_is_visible_even_when_it_cannot_be_resolved():
    """The motivating bug. Confluence stores a mention as an opaque id in an
    attribute, so an HTML-only converter renders it as nothing — and an
    Owner row that arrives blank reads as "nobody owns this", which is an
    answer rather than a gap."""
    markdown = confluence_to_markdown(HAND_WRITTEN_PAGE)

    assert UNRESOLVED_USER in markdown
    assert "| Owner |  |" not in markdown


def test_a_resolved_mention_renders_as_the_person():
    markdown = confluence_to_markdown(HAND_WRITTEN_PAGE, user_names={"557058:jane": "Jane Okafor"})

    assert "Jane Okafor" in markdown
    assert UNRESOLVED_USER not in markdown


def test_a_cross_page_link_keeps_its_text():
    markdown = confluence_to_markdown(HAND_WRITTEN_PAGE)

    assert "See Access Review Procedure." in markdown


def test_an_explicit_link_label_beats_the_target_title():
    html = (
        '<p><ac:link><ri:page ri:content-title="Access Review Procedure"/>'
        "<ac:plain-text-link-body><![CDATA[the quarterly review]]></ac:plain-text-link-body>"
        "</ac:link></p>"
    )

    assert "the quarterly review" in confluence_to_markdown(html)


def test_a_server_style_user_key_resolves_too():
    """Cloud identifies a person by account id, Server/DC by user key. Which
    one a deployment uses is not something the caller should have to know."""
    html = '<p><ac:link><ri:user ri:userkey="ff8081"/></ac:link></p>'

    assert "Dana Reyes" in confluence_to_markdown(html, user_names={"ff8081": "Dana Reyes"})


def test_an_image_is_announced_rather_than_dropped():
    """Zardoz cannot read a diagram, but it should be able to say one is
    there rather than answering as though the page had no diagram."""
    markdown = confluence_to_markdown(HAND_WRITTEN_PAGE)

    assert "[image: access-flow.png]" in markdown


def test_headings_and_tables_survive_intact():
    """Chunking and citation both depend on headings, and compliance
    requirements very often live in tables."""
    markdown = confluence_to_markdown(HAND_WRITTEN_PAGE)

    assert "# Access Control Standard" in markdown
    assert "## Requirements" in markdown
    assert "| AC-1 | Accounts are reviewed quarterly. |" in markdown


def test_conversion_of_our_own_documents_is_unchanged():
    """The link restoration must not disturb the round trip this module
    already guaranteed for pages PolicyForge itself published."""
    html = (
        "<h1>T</h1><p>Body with <strong>bold</strong>.</p>"
        '<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">bash'
        "</ac:parameter><ac:plain-text-body><![CDATA[echo hi]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )
    markdown = confluence_to_markdown(html)

    assert "```bash" in markdown
    assert "echo hi" in markdown


def test_user_ids_are_collected_for_one_batched_lookup():
    """Resolving mid-render would mean one API call per mention."""
    html = (
        '<p><ac:link><ri:user ri:account-id="a"/></ac:link>'
        '<ac:link><ri:user ri:account-id="b"/></ac:link>'
        '<ac:link><ri:user ri:account-id="a"/></ac:link></p>'
    )

    assert extract_user_ids(html) == {"a", "b"}


def test_references_are_extracted_as_a_graph_not_inlined():
    """Inlined, a link target reads badly in prose; collected, it answers
    'what should I read alongside this?'."""
    assert extract_references(HAND_WRITTEN_PAGE) == ["Access Review Procedure"]


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


def _fake_response(monkeypatch, pages, *, has_next=True):
    """Fake `requests.get` for the search endpoint."""
    import requests

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self.ok = True

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    calls = []

    def _get(url, params=None, **kwargs):
        calls.append(params)
        start = (params or {}).get("start", 0)
        limit = (params or {}).get("limit", 50)
        window = pages[start : start + limit]
        links = {"next": "/more"} if has_next and start + limit < len(pages) else {}
        return _Resp({"results": window, "_links": links})

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "t")
    monkeypatch.setenv("CONFLUENCE_USERNAME", "u")
    return calls


def _raw_page(index):
    return {
        "id": str(index),
        "title": f"Page {index}",
        "version": {"number": 1},
        "body": {"storage": {"value": f"<p>body {index}</p>"}},
        "_links": {"webui": f"/x/{index}"},
        "ancestors": [{"title": "Parent"}],
        "metadata": {"labels": {"results": [{"name": "security"}]}},
    }


def test_search_pages_through_to_the_end(monkeypatch):
    from policyforge.export.confluence_search import search_pages

    _fake_response(monkeypatch, [_raw_page(i) for i in range(120)])

    pages = search_pages(host="https://x", cql="type = page")

    assert len(pages) == 120
    assert pages[0].ancestors == ["Parent"]
    assert pages[0].labels == ["security"]


def test_search_refuses_a_space_bigger_than_the_cap(monkeypatch):
    """Silently truncating would leave a corpus holding an arbitrary subset
    of the documentation while answering as though it were complete."""
    from policyforge.export.confluence_search import SearchLimitExceeded, search_pages

    _fake_response(monkeypatch, [_raw_page(i) for i in range(300)])

    with pytest.raises(SearchLimitExceeded, match="Narrow the query"):
        search_pages(host="https://x", cql="type = page", max_results=100)


def test_space_cql_selects_pages_only():
    from policyforge.export.confluence_search import space_cql

    cql = space_cql("SEC")

    assert 'space = "SEC"' in cql
    assert "type = page" in cql


# --------------------------------------------------------------------------
# Sync
# --------------------------------------------------------------------------


def _topics():
    return [
        Topic(
            name="Access Review",
            owner="IAM Engineering",
            nist_controls=["AC-2"],
            confluence={
                "space": "SEC",
                "pages": {
                    "standard": "Access Control Standard",
                    "procedure": "Access Review Procedure",
                },
            },
        ),
        Topic(name="Vendor Risk", owner="Third-Party Risk", nist_controls=["SR-3"]),
    ]


def _patch_sync(monkeypatch, *, missing=(), supporting=(), user_names=None):
    """Fake the three network functions sync_corpus reaches for."""

    def _fetch(*, space, title, host):
        if title in missing:
            raise LookupError(f"No Confluence page titled {title!r} found in space {space!r}.")
        return ConfluencePage(
            id=f"id-{title}",
            title=title,
            version=4,
            storage_body=HAND_WRITTEN_PAGE,
            webui_url=f"https://x/{title}",
            ancestors=["Security"],
            labels=["policy"],
        )

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr(
        "policyforge.export.confluence_search.search_pages",
        lambda **kwargs: list(supporting),
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names",
        lambda ids, **kwargs: dict(user_names or {}),
    )


def test_sync_writes_a_manifest_and_one_file_per_document(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch)

    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert len(report.synced) == 2
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["documents"]) == 2
    assert (tmp_path / "docs" / "sec-access-control-standard.md").exists()
    # The body lives in its own file, not in the manifest.
    assert "body" not in manifest["documents"][0]


def test_synced_registry_pages_know_their_topic_owner_and_tier(tmp_path, monkeypatch):
    """This is the whole reason registry pages are 'trusted': they arrive
    already knowing what an answer needs to attribute them."""
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch)

    doc = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path).synced[0]

    assert doc.is_trusted
    assert doc.topic == "Access Review"
    assert doc.owner == "IAM Engineering"
    assert doc.tier == "standard"
    assert doc.references == ["Access Review Procedure"]


def test_one_renamed_page_does_not_cost_the_whole_sync(tmp_path, monkeypatch):
    """Twenty topics is a lot of titles to keep exactly in step; a single
    stale one should be reported, not fatal."""
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch, missing={"Access Review Procedure"})

    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert len(report.synced) == 1
    assert len(report.skipped) == 1
    what, why = report.skipped[0]
    assert "Access Review Procedure" in what
    assert "No Confluence page titled" in why


def test_unresolved_owners_are_counted_and_surfaced(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch, user_names={})

    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert report.unresolved_users == 2  # one per page
    assert "could not be resolved" in report.format_report()
    assert "usually the Owner field" in report.format_report()


def test_resolved_owners_are_not_reported_as_a_problem(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch, user_names={"557058:jane": "Jane Okafor"})

    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert report.unresolved_users == 0
    assert "could not be resolved" not in report.format_report()


def test_pages_that_read_but_cannot_be_edited_are_flagged(tmp_path, monkeypatch):
    """Zardoz can answer from a macro-heavy legacy page, but `edit-topic`
    would refuse to touch it. Saying so at sync time beats discovering it
    when an edit is proposed."""
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch)

    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert "details" in report.synced[0].unsupported_macros
    assert "cannot be edited" in report.format_report()


def test_supporting_pages_arrive_without_an_owner(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import sync_corpus

    extra = ConfluencePage(
        id="r1",
        title="Access Review Runbook",
        version=2,
        storage_body="<h1>Runbook</h1><p>Steps.</p>",
        webui_url="https://x/r1",
    )
    _patch_sync(monkeypatch, supporting=[extra])

    report = sync_corpus(
        _topics(), host="https://x", supporting_space="RUNBOOKS", corpus_dir=tmp_path
    )

    doc = next(d for d in report.synced if not d.is_trusted)
    assert doc.title == "Access Review Runbook"
    assert doc.owner == ""
    assert doc.topic == ""
    assert doc.space == "RUNBOOKS"


def test_a_declared_page_beats_a_supporting_copy_of_itself(tmp_path, monkeypatch):
    """Otherwise the same requirement could be cited twice at two different
    confidence levels, which is worse than citing it once."""
    from policyforge.zardoz.corpus import sync_corpus

    duplicate = ConfluencePage(
        id="dup",
        title="Access Control Standard",
        version=9,
        storage_body="<h1>Stale copy</h1>",
        webui_url="https://x/dup",
    )
    _patch_sync(monkeypatch, supporting=[duplicate])

    report = sync_corpus(
        _topics(), host="https://x", supporting_space="RUNBOOKS", corpus_dir=tmp_path
    )

    titles = [d.title for d in report.synced]
    assert titles.count("Access Control Standard") == 1
    assert all(d.is_trusted for d in report.synced if d.title == "Access Control Standard")


def test_a_removed_page_does_not_linger_in_the_corpus(tmp_path, monkeypatch):
    """A stale file left behind would keep being answered from after the
    page was deliberately taken out of the registry."""
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch)
    sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)
    assert (tmp_path / "docs" / "sec-access-review-procedure.md").exists()

    trimmed = _topics()
    trimmed[0].confluence["pages"].pop("procedure")
    sync_corpus(trimmed, host="https://x", corpus_dir=tmp_path)

    assert not (tmp_path / "docs" / "sec-access-review-procedure.md").exists()
    assert (tmp_path / "docs" / "sec-access-control-standard.md").exists()


# --------------------------------------------------------------------------
# Loading it back
# --------------------------------------------------------------------------


def test_load_corpus_round_trips_bodies_and_metadata(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    _patch_sync(monkeypatch)
    sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    corpus = load_corpus(tmp_path)

    assert len(corpus) == 2
    assert corpus.host == "https://x"
    doc = corpus.get("sec-access-control-standard")
    assert doc is not None
    assert "# Access Control Standard" in doc.body
    assert doc.owner == "IAM Engineering"
    assert corpus.by_topic("access review") == corpus.documents


def test_loading_an_unsynced_corpus_says_what_to_run(tmp_path):
    from policyforge.zardoz.corpus import load_corpus

    with pytest.raises(FileNotFoundError, match="zardoz sync"):
        load_corpus(tmp_path / "nothing")


# --------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------


def _write_registry(tmp_path):
    path = tmp_path / "topics.yaml"
    path.write_text(
        "topics:\n"
        "  - name: Access Review\n"
        "    owner: IAM Engineering\n"
        "    nist_controls: [AC-2]\n"
        "    confluence:\n"
        "      space: SEC\n"
        "      pages:\n"
        '        standard: "Access Control Standard"\n',
        encoding="utf-8",
    )
    return path


def test_sync_command_reports_what_it_did(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    _patch_sync(monkeypatch, user_names={"557058:jane": "Jane Okafor"})
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"zardoz": {"host": "https://x"}})

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "zardoz",
            "--topics",
            str(_write_registry(tmp_path)),
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "sync",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Synced 1 document(s)" in result.output
    assert "1 trusted" in result.output


def test_sync_without_a_host_says_where_to_put_one(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    result = CliRunner().invoke(
        cli_mod.cli, ["zardoz", "--topics", str(_write_registry(tmp_path)), "sync"]
    )

    assert result.exit_code != 0
    assert "config/config.yaml" in result.output
    assert "zardoz:" in result.output


def test_the_host_flag_overrides_the_config(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    seen = {}

    def _fetch(*, space, title, host):
        seen["host"] = host
        raise LookupError("nope")

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"zardoz": {"host": "https://config"}})

    CliRunner().invoke(
        cli_mod.cli,
        [
            "zardoz",
            "--topics",
            str(_write_registry(tmp_path)),
            "--corpus-dir",
            str(tmp_path / "c"),
            "sync",
            "--host",
            "https://flag",
        ],
    )

    assert seen["host"] == "https://flag"


def test_the_shell_reports_a_synced_corpus(tmp_path, monkeypatch):
    from policyforge.cli import cli
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch)
    sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path / "corpus")

    result = CliRunner().invoke(
        cli,
        [
            "zardoz",
            "--no-art",
            "--topics",
            str(_write_registry(tmp_path)),
            "--corpus-dir",
            str(tmp_path / "corpus"),
        ],
        input="/corpus\n/quit\n",
    )

    assert result.exit_code == 0, result.output
    assert "2 document(s)" in result.output
    assert "[standard] Access Control Standard" in result.output
    assert "IAM Engineering" in result.output


def test_the_shell_opens_without_a_corpus_and_says_so(tmp_path):
    from policyforge.cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "zardoz",
            "--no-art",
            "--topics",
            str(_write_registry(tmp_path)),
            "--corpus-dir",
            str(tmp_path / "absent"),
        ],
        input="/corpus\nwhat is the cadence?\n/quit\n",
    )

    assert result.exit_code == 0, result.output
    assert "no documents synced yet" in result.output
    assert "No corpus synced" in result.output
    assert "nothing to answer from" in result.output


# --------------------------------------------------------------------------
# Not losing a working corpus
# --------------------------------------------------------------------------


def test_a_sync_that_resolves_nothing_leaves_the_old_corpus_alone(tmp_path, monkeypatch):
    """A typo'd space key used to empty the corpus. The report said "Synced 0"
    and nothing said the previous snapshot had just been deleted, so the way
    you found out was by getting worse answers."""
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    _patch_sync(monkeypatch)
    sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)
    assert len(load_corpus(tmp_path)) == 2

    _patch_sync(monkeypatch, missing=("Access Control Standard", "Access Review Procedure"))
    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert report.refused_empty is True
    assert len(load_corpus(tmp_path)) == 2, "the working corpus survived"
    assert "left in place" in report.format_report()
    assert "--allow-empty" in report.format_report()


def test_allow_empty_clears_the_corpus_on_purpose(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    _patch_sync(monkeypatch)
    sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    _patch_sync(monkeypatch, missing=("Access Control Standard", "Access Review Procedure"))
    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path, allow_empty=True)

    assert report.refused_empty is False
    assert len(load_corpus(tmp_path)) == 0


def test_an_empty_first_sync_is_not_refused(tmp_path, monkeypatch):
    """There is nothing to protect yet, and refusing would leave no manifest
    for the shell to distinguish 'unsynced' from 'synced nothing'."""
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    _patch_sync(monkeypatch, missing=("Access Control Standard", "Access Review Procedure"))
    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert report.refused_empty is False
    assert len(load_corpus(tmp_path)) == 0


def test_the_sync_command_exits_nonzero_when_it_refuses(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    _patch_sync(monkeypatch)
    monkeypatch.setattr(cli_mod, "load_config", lambda: {"zardoz": {"host": "https://x"}})
    args = [
        "zardoz",
        "--topics",
        str(_write_registry(tmp_path)),
        "--corpus-dir",
        str(tmp_path / "corpus"),
        "sync",
    ]
    assert CliRunner().invoke(cli_mod.cli, args).exit_code == 0

    _patch_sync(monkeypatch, missing=("Access Control Standard",))
    result = CliRunner().invoke(cli_mod.cli, args)

    assert result.exit_code == 1
    assert "left in place" in result.output


# --------------------------------------------------------------------------
# Document identity
# --------------------------------------------------------------------------


def test_titles_in_a_non_latin_script_stay_distinct(tmp_path, monkeypatch):
    """`slugify` keeps only ASCII, so these titles used to slugify to nothing,
    collapse onto one id, and overwrite each other's files. The corpus then
    served one document's text under another's title."""
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    bodies = {"アクセス制御基準": "<p>Japanese body</p>", "Политика доступа": "<p>Russian body</p>"}

    def _fetch(*, space, title, host):
        return ConfluencePage(
            id=f"id-{title}",
            title=title,
            version=1,
            storage_body=bodies[title],
            webui_url=f"https://x/{title}",
            ancestors=[],
            labels=[],
        )

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **kw: {}
    )

    topics = [
        Topic(
            name="Access",
            owner="Sec",
            confluence={"space": "ENG", "pages": {"standard": "アクセス制御基準"}},
        ),
        Topic(
            name="Dostup",
            owner="Sec",
            confluence={"space": "ENG", "pages": {"standard": "Политика доступа"}},
        ),
    ]
    sync_corpus(topics, host="https://x", corpus_dir=tmp_path)
    corpus = load_corpus(tmp_path)

    ids = [doc.doc_id for doc in corpus.documents]
    assert len(set(ids)) == 2, f"ids collided: {ids}"
    by_title = {doc.title: doc.body for doc in corpus.documents}
    assert "Japanese" in by_title["アクセス制御基準"]
    assert "Russian" in by_title["Политика доступа"]


def test_two_titles_that_slugify_alike_get_their_own_files(tmp_path, monkeypatch):
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    def _fetch(*, space, title, host):
        return ConfluencePage(
            id=f"id-{title}",
            title=title,
            version=1,
            storage_body=f"<p>Body of {title}</p>",
            webui_url="",
            ancestors=[],
            labels=[],
        )

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **kw: {}
    )

    topics = [
        Topic(
            name="One",
            owner="Sec",
            confluence={"space": "ENG", "pages": {"standard": "A/B Testing Policy"}},
        ),
        Topic(
            name="Two",
            owner="Sec",
            confluence={"space": "ENG", "pages": {"standard": "A B Testing Policy"}},
        ),
    ]
    sync_corpus(topics, host="https://x", corpus_dir=tmp_path)
    corpus = load_corpus(tmp_path)

    assert len({doc.doc_id for doc in corpus.documents}) == 2
    assert len({doc.body for doc in corpus.documents}) == 2


def test_a_page_two_topics_both_declare_is_reported_not_duplicated(tmp_path, monkeypatch):
    """Two teams claiming one document is the contested-ownership problem
    `coverage` exists to surface. Syncing it twice under two owners would
    bury exactly that, and cite the same requirement twice."""
    from policyforge.zardoz.corpus import sync_corpus

    _patch_sync(monkeypatch)
    shared = {"space": "SEC", "pages": {"policy": "Information Security Policy"}}
    topics = [
        Topic(name="Access Review", owner="IAM Engineering", confluence=shared),
        Topic(name="Vendor Risk", owner="Third-Party Risk", confluence=shared),
    ]

    report = sync_corpus(topics, host="https://x", corpus_dir=tmp_path)

    assert len(report.synced) == 1
    assert report.synced[0].owner == "IAM Engineering"
    reasons = " ".join(why for _, why in report.skipped)
    assert "already claimed by topic 'Access Review'" in reasons
    assert "Vendor Risk" in reasons


# --------------------------------------------------------------------------
# Manifest compatibility
# --------------------------------------------------------------------------


def test_a_field_this_version_does_not_know_is_dropped(tmp_path):
    """The manifest gains fields as the milestones land. Loading one written
    by a newer sync used to raise a bare TypeError out of the dataclass,
    which reached the user as a traceback on shell launch."""
    from policyforge.zardoz.corpus import CORPUS_SCHEMA, load_corpus

    (tmp_path / "docs").mkdir()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema": CORPUS_SCHEMA,
                "synced_at": "2026-08-31T00:00:00+00:00",
                "documents": [
                    {
                        "doc_id": "a",
                        "title": "T",
                        "space": "S",
                        "confidence": "trusted",
                        "chunk_count": 12,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    corpus = load_corpus(tmp_path)

    assert len(corpus) == 1
    assert corpus.documents[0].title == "T"


def test_a_corpus_from_a_newer_version_says_to_re_sync(tmp_path):
    from policyforge.zardoz.corpus import CORPUS_SCHEMA, load_corpus

    (tmp_path / "docs").mkdir()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schema": CORPUS_SCHEMA + 1, "documents": []}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="zardoz sync"):
        load_corpus(tmp_path)


# --------------------------------------------------------------------------
# Resolving owners once, not once per page
# --------------------------------------------------------------------------


def test_user_names_are_resolved_once_per_sync(tmp_path, monkeypatch):
    """Every page in a policy set names the same handful of owners, and each
    id costs up to two HTTP requests. Sixty pages used to mean sixty lookups
    of the same two people."""
    from policyforge.zardoz.corpus import sync_corpus

    calls = []

    def _counting(ids, **kwargs):
        calls.append(sorted(ids))
        return {i: i.title() for i in ids}

    _patch_sync(monkeypatch)
    monkeypatch.setattr("policyforge.export.confluence_search.fetch_user_names", _counting)

    topics = [
        Topic(
            name=f"Topic {n}",
            owner="Sec",
            confluence={"space": "SEC", "pages": {"standard": f"Standard {n}"}},
        )
        for n in range(8)
    ]
    report = sync_corpus(topics, host="https://x", corpus_dir=tmp_path)

    assert len(report.synced) == 8
    assert len(calls) == 1, f"looked the same people up {len(calls)} times"


def test_an_unresolvable_id_is_not_retried_on_every_page(tmp_path, monkeypatch):
    """A negative result is a result. Retrying it once per page only makes a
    sync slower without making the name any more resolvable."""
    from policyforge.zardoz.corpus import sync_corpus

    calls = []
    _patch_sync(monkeypatch)
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names",
        lambda ids, **kw: (calls.append(sorted(ids)), {})[1],
    )

    topics = [
        Topic(
            name=f"Topic {n}",
            owner="Sec",
            confluence={"space": "SEC", "pages": {"standard": f"Standard {n}"}},
        )
        for n in range(5)
    ]
    report = sync_corpus(topics, host="https://x", corpus_dir=tmp_path)

    assert len(calls) == 1
    assert report.unresolved_users == 5, "still counted and reported on every page"


# --------------------------------------------------------------------------
# Markdown: the content tree as a source
# --------------------------------------------------------------------------


def _content_tree(tmp_path):
    root = tmp_path / "docs"
    (root / "standards").mkdir(parents=True)
    (root / "policies").mkdir(parents=True)
    (root / "standards" / "access-review.md").write_text(
        "# Access Review Standard\n\nAccounts are reviewed quarterly. "
        "See [the policy](../policies/access-review.md).\n",
        encoding="utf-8",
    )
    (root / "policies" / "access-review.md").write_text(
        "---\ntopic: Access Review\nowner: IAM Engineering\n"
        "confluence:\n  space: SEC\n  title: Access Control Standard\n---\n\n"
        "# Access Review Policy\n",
        encoding="utf-8",
    )
    (root / "unowned.md").write_text("# A Runbook Nobody Claimed\n", encoding="utf-8")
    return root


def test_markdown_syncs_with_no_host_and_no_credentials(tmp_path):
    """The whole point: a repo-backed document set is answerable offline, and
    trying Zardoz at all no longer needs an Atlassian account."""
    from policyforge.zardoz.corpus import MARKDOWN, load_corpus, sync_corpus

    report = sync_corpus(
        _topics(), content_dir=_content_tree(tmp_path), corpus_dir=tmp_path / "corpus"
    )

    assert len(report.synced) == 3
    corpus = load_corpus(tmp_path / "corpus")
    assert len(corpus.from_markdown) == 3
    assert all(doc.source == MARKDOWN for doc in corpus.documents)


def test_a_markdown_document_is_cited_by_its_repo_path(tmp_path):
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    sync_corpus(_topics(), content_dir=_content_tree(tmp_path), corpus_dir=tmp_path / "corpus")
    corpus = load_corpus(tmp_path / "corpus")

    doc = next(d for d in corpus.documents if d.title == "Access Review Standard")
    assert doc.location == "standards/access-review.md"
    assert doc.tier == "standard"


def test_ownership_comes_from_the_registry_when_frontmatter_is_silent(tmp_path):
    """An unannotated tree still resolves: `synthesize` names files after the
    registry topic, so the slug is the join."""
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    topics = [Topic(name="Access Review", owner="IAM Engineering", nist_controls=["AC-2"])]
    sync_corpus(topics, content_dir=_content_tree(tmp_path), corpus_dir=tmp_path / "corpus")
    corpus = load_corpus(tmp_path / "corpus")

    doc = next(d for d in corpus.documents if d.title == "Access Review Standard")
    assert doc.topic == "Access Review"
    assert doc.owner == "IAM Engineering"
    assert doc.is_trusted


def test_a_file_nobody_owns_is_supporting_not_trusted(tmp_path):
    """Same rule as a page from the extra space: real content, but nothing
    says who is accountable, so an answer must not imply somebody is."""
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    sync_corpus(_topics(), content_dir=_content_tree(tmp_path), corpus_dir=tmp_path / "corpus")
    corpus = load_corpus(tmp_path / "corpus")

    unowned = next(d for d in corpus.documents if d.title == "A Runbook Nobody Claimed")
    assert not unowned.is_trusted
    assert unowned.owner == ""


def test_a_markdown_document_is_always_editable(tmp_path):
    """A file is a file; the review gate is the diff. That is the difference
    the repo-backed arrangement buys over editing a page full of macros."""
    from policyforge.zardoz.corpus import load_corpus, sync_corpus

    sync_corpus(_topics(), content_dir=_content_tree(tmp_path), corpus_dir=tmp_path / "corpus")
    corpus = load_corpus(tmp_path / "corpus")

    assert all(doc.is_editable for doc in corpus.from_markdown)


def test_markdown_wins_over_the_published_copy_of_itself(tmp_path, monkeypatch):
    """Where a repo publishes to Confluence the file is the source of truth
    and the page is a copy. Holding both would cite one requirement twice and
    invite an answer that quotes the stale half."""
    from policyforge.zardoz.corpus import MARKDOWN, sync_corpus

    _patch_sync(monkeypatch)

    report = sync_corpus(
        _topics(),
        host="https://x",
        content_dir=_content_tree(tmp_path),
        corpus_dir=tmp_path / "corpus",
    )

    titles = [doc.title for doc in report.synced]
    assert titles.count("Access Control Standard") == 0, "the wiki copy was not also synced"
    policy = next(d for d in report.synced if d.title == "Access Review Policy")
    assert policy.source == MARKDOWN
    reasons = " ".join(why for _, why in report.skipped)
    assert "content tree, which wins" in reasons


def test_sync_needs_at_least_one_source(tmp_path):
    from policyforge.zardoz.corpus import sync_corpus

    with pytest.raises(ValueError, match="Nothing to sync from"):
        sync_corpus(_topics(), corpus_dir=tmp_path)


def test_the_content_dir_flag_syncs_without_any_confluence_config(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "zardoz",
            "--topics",
            str(_write_registry(tmp_path)),
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "sync",
            "--content-dir",
            str(_content_tree(tmp_path)),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Synced 3 document(s)" in result.output
    assert "3 from markdown" in result.output


def test_a_content_dir_that_is_not_there_says_so(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_config", lambda: {})

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "zardoz",
            "--topics",
            str(_write_registry(tmp_path)),
            "sync",
            "--content-dir",
            str(tmp_path / "nope"),
        ],
    )

    assert result.exit_code != 0
    assert "No content directory" in result.output


# --------------------------------------------------------------------------
# Staleness, and picking up a re-sync without restarting
# --------------------------------------------------------------------------


def test_an_old_snapshot_is_flagged_as_stale(tmp_path):
    from policyforge.zardoz.corpus import STALE_AFTER_DAYS, Corpus, CorpusDocument

    fresh = Corpus(synced_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    old = Corpus(
        documents=[CorpusDocument(doc_id="a", title="T", space="S", confidence="trusted")],
        synced_at=(datetime.now(timezone.utc) - timedelta(days=STALE_AFTER_DAYS + 1)).isoformat(
            timespec="seconds"
        ),
    )

    assert not fresh.is_stale
    assert old.is_stale
    assert old.age_days > STALE_AFTER_DAYS
    assert Corpus().age_days is None


def test_corpus_command_warns_when_the_snapshot_is_old(tmp_path):
    from policyforge.zardoz.corpus import STALE_AFTER_DAYS, Corpus, CorpusDocument
    from policyforge.zardoz.shell import ShellState, dispatch

    corpus = Corpus(
        documents=[
            CorpusDocument(doc_id="a", title="T", space="S", confidence="trusted", owner="Sec")
        ],
        synced_at=(datetime.now(timezone.utc) - timedelta(days=STALE_AFTER_DAYS + 5)).isoformat(
            timespec="seconds"
        ),
    )

    output = dispatch("/corpus", ShellState(corpus=corpus))

    assert "days old" in output
    assert "/reload" in output


def test_reload_picks_up_a_re_sync_without_ending_the_session(tmp_path):
    """Ask, notice the document is wrong, edit the markdown, ask again. Before
    this, "ask again" meant quitting the shell."""
    from policyforge.zardoz.corpus import load_corpus, sync_corpus
    from policyforge.zardoz.shell import ShellState, dispatch

    root = _content_tree(tmp_path)
    corpus_dir = tmp_path / "corpus"
    sync_corpus(_topics(), content_dir=root, corpus_dir=corpus_dir)
    state = ShellState(corpus=load_corpus(corpus_dir), corpus_dir=corpus_dir)
    assert len(state.corpus) == 3

    (root / "standards" / "new-one.md").write_text("# Backup Standard\n", encoding="utf-8")
    sync_corpus(_topics(), content_dir=root, corpus_dir=corpus_dir)

    output = dispatch("/reload", state)

    assert len(state.corpus) == 4
    assert "Reloaded 4 document(s)" in output
    assert "4 from markdown" in output


def test_reload_without_a_corpus_says_what_to_run(tmp_path):
    from policyforge.zardoz.shell import ShellState, dispatch

    output = dispatch("/reload", ShellState(corpus_dir=tmp_path / "nothing"))

    assert "zardoz sync" in output


def test_the_corpus_listing_does_not_scroll_the_terminal(tmp_path):
    """A twenty-topic registry publishes sixty pages, and a command whose job
    is to orient you should not be the thing you have to scroll back through."""
    from policyforge.zardoz.corpus import Corpus, CorpusDocument
    from policyforge.zardoz.shell import ShellState, dispatch

    corpus = Corpus(
        documents=[
            CorpusDocument(
                doc_id=f"d{n}", title=f"Standard {n}", space="SEC", confidence="trusted", owner="S"
            )
            for n in range(40)
        ],
        synced_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    output = dispatch("/corpus", ShellState(corpus=corpus))

    assert "and 25 more" in output
    assert len(output.splitlines()) < 30
