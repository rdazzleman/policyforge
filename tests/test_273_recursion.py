"""A Confluence page too deep to convert is refused by name on every path (#273).

80's ruling: one place (`confluence_importer.convert_page`) turns the
converter's `RecursionError` into `PageTooDeepError`; a caller that loops over
pages refuses that page as it refuses any page it can't take and goes on; a
caller of one page exits 2 with one line. Every page here is converted by the
real converter, 1,200 deep, never a mock raising `RecursionError`.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from policyforge.export.confluence_importer import (
    ConfluencePage,
    PageTooDeepError,
    confluence_to_markdown,
    convert_page,
)

DEPTH = 1200
NL = chr(10)


def _nested(depth: int) -> str:
    return "<div>" * depth + "<p>x</p>" + "</div>" * depth


def _page(title: str, body: str) -> ConfluencePage:
    return ConfluencePage(
        id=f"id-{title}", title=title, version=3, storage_body=body, webui_url=f"https://x/{title}"
    )


def _fetch_with_one_deep_page(monkeypatch, deep_title: str):
    """Fake only the network: every page converts through the real converter."""

    def _fetch(*, space, title, host, **kwargs):
        return _page(title, _nested(DEPTH) if title == deep_title else "<p>Staff must act.</p>")

    monkeypatch.setattr("policyforge.export.confluence_importer.fetch_confluence_page", _fetch)


def test_the_real_converter_raises_at_this_depth_and_converts_a_shallow_page():
    """The premise: if the converter ever stops recursing, these tests would
    pass for the wrong reason, so the premise is pinned."""
    assert "x" in confluence_to_markdown(_nested(200))
    with pytest.raises(RecursionError):
        confluence_to_markdown(_nested(DEPTH))


def test_convert_page_names_the_page_and_raises_only_its_own_error():
    with pytest.raises(PageTooDeepError, match="'Deep Page' nested too deeply to convert"):
        convert_page(_nested(DEPTH), title="Deep Page")
    assert "x" in convert_page(_nested(200), title="Shallow Page")


def test_a_too_deep_page_is_a_value_error_as_callers_catch_those():
    assert issubclass(PageTooDeepError, ValueError)


# ---- the callers of one page: one line, exit 2 ---------------------------------


def test_import_confluence_refuses_a_too_deep_page_in_one_line(tmp_path, monkeypatch):
    import policyforge.cli as cli_mod

    _fetch_with_one_deep_page(monkeypatch, "Deep Page")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        cli_mod.cli,
        ["import-confluence", "--space", "SEC", "--title", "Deep Page", "--host", "https://x",
         "--tier", "standard", "--name", "deep"],
    )  # fmt: skip

    assert result.exit_code == 2
    assert result.output.strip().splitlines() == [
        "Error: page 'Deep Page' nested too deeply to convert"
    ]
    assert not (tmp_path / "output").exists(), "nothing written"


def test_import_from_confluence_raises_the_named_error(monkeypatch):
    from policyforge.export.confluence_importer import import_from_confluence

    _fetch_with_one_deep_page(monkeypatch, "Deep Page")

    with pytest.raises(PageTooDeepError, match="'Deep Page'"):
        import_from_confluence(space="SEC", title="Deep Page", host="https://x")


# ---- the edit session: fails up front, as for an unsupported macro (80) ----------


def test_an_edit_session_over_a_too_deep_page_fails_up_front_and_writes_nothing(
    tmp_path, monkeypatch
):
    from test_edit import _registry

    import policyforge.cli as cli_mod

    _fetch_with_one_deep_page(monkeypatch, "Deep Page")
    published = []
    monkeypatch.setattr(
        "policyforge.export.confluence_exporter.update_page_body",
        lambda *a, **k: published.append(k) or "https://x",
    )

    class NoModel:
        def generate(self, **_):
            pytest.fail("the model was asked before the run failed")

    monkeypatch.setattr(cli_mod, "load_config", lambda: {"llm": {"model": "fake-model"}})
    monkeypatch.setattr(cli_mod, "get_provider", lambda config: NoModel())
    registry = _registry(tmp_path, pages={"policy": "Fine Page", "standard": "Deep Page"})

    result = CliRunner().invoke(
        cli_mod.cli,
        ["edit-topic", "--instruction", "Access reviews move to monthly.",
         "--topic-name", "Access Review", "--topics", str(registry),
         "--host", "https://x", "--out-dir", str(tmp_path / "edits"),
         "--history-dir", str(tmp_path / "history")],
    )  # fmt: skip

    assert result.exit_code == 2, result.output
    assert result.output.strip().splitlines()[-1] == (
        "Error: page 'Deep Page' nested too deeply to convert"
    )
    assert "Usage:" not in result.output
    assert published == []
    assert not (tmp_path / "edits").exists() and not (tmp_path / "history").exists()


# ---- the callers that loop: refuse the page by name, go on with the rest ---------


def test_a_corpus_sync_skips_a_too_deep_page_by_name_and_syncs_the_rest(tmp_path, monkeypatch):
    from test_zardoz_corpus import _topics

    from policyforge.zardoz.corpus import sync_corpus

    _fetch_with_one_deep_page(monkeypatch, "Access Control Standard")
    monkeypatch.setattr("policyforge.export.confluence_search.search_pages", lambda **k: [])
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **k: {}
    )

    report = sync_corpus(_topics(), host="https://x", corpus_dir=tmp_path)

    assert [name for name, _ in report.skipped] == ["Access Control Standard (standard)"]
    assert "nested too deeply to convert" in report.skipped[0][1]
    assert len(report.synced) == 1, "the procedure page still syncs"


def test_a_supporting_page_too_deep_is_skipped_by_name_too(tmp_path, monkeypatch):
    from test_zardoz_corpus import _topics

    from policyforge.zardoz.corpus import sync_corpus

    _fetch_with_one_deep_page(monkeypatch, "nothing is deep in the registry")
    monkeypatch.setattr(
        "policyforge.export.confluence_search.search_pages",
        lambda **k: [
            _page("Deep Supporting Note", _nested(DEPTH)),
            _page("Fine Note", "<p>ok</p>"),
        ],
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_search.fetch_user_names", lambda ids, **k: {}
    )
    report = sync_corpus(_topics(), host="https://x", supporting_space="NOTES", corpus_dir=tmp_path)

    assert [name for name, _ in report.skipped] == ["Deep Supporting Note"]
    assert "nested too deeply to convert" in report.skipped[0][1]
    assert len(report.synced) == 3, "both registry pages and the fine note still sync"
