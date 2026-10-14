from unittest.mock import MagicMock

from policyforge.export.publisher import (
    REFUSED,
    TOO_DEEP,
    Publisher,
    PullReport,
    PullResult,
    pull_documents,
)


class FakeRecursionPublisher(Publisher):
    def fetch(self, tier: str, title: str):
        page = MagicMock()
        page.title = title
        page.page_id = "1"
        page.unsupported = ()
        return page

    def as_markdown(self, page) -> str:
        from policyforge.export.confluence_importer import PageTooDeepError

        if "nested" in page.title.lower():
            raise PageTooDeepError("nested too deeply")
        return "normal content"

    def write(self, *a, **k):
        pass

    def same_content(self, *a, **k):
        return False

    def recorded_version(self, *a, **k):
        return 1

    def title(self, *a, **k):
        return ""

    def location(self, *a, **k):
        return ""

    def reconcile_command(self, *a, **k):
        return ""

    def binding(self, *a, **k):
        return {"binding": "fake:"}


def test_pull_handles_recursion_error(tmp_path):
    publisher = FakeRecursionPublisher()

    def dummy_target(root, tier, slug):
        return root / f"{tier}s" / f"{slug}.md"

    report = pull_documents(
        publisher=publisher,
        pages=[("SEC", "Nested Page", "standard"), ("SEC", "Normal Page", "standard")],
        root=tmp_path,
        target_path=dummy_target,
        dry_run=False,
    )

    assert len(report.results) == 2

    nested_res = next(r for r in report.results if r.title == "Nested Page")
    assert nested_res.action == REFUSED
    assert nested_res.reason == TOO_DEEP

    normal_res = next(r for r in report.results if r.title == "Normal Page")
    assert normal_res.action != REFUSED

    # check that the normal one is written
    assert (tmp_path / "standards" / "normal-page.md").exists()
    assert "normal content" in (tmp_path / "standards" / "normal-page.md").read_text(
        encoding="utf-8"
    )


# --- 1d, carrying ag's branch: the real converter, not a fake that raises ---

DEPTH = 1200  # 80 measured N=600 and N=1200 raising; N=200 converted (#252)


def _nested(depth: int) -> str:
    return "<div>" * depth + "<p>x</p>" + "</div>" * depth


def test_the_real_converter_raises_on_a_page_this_deep():
    """The premise the handling rests on, measured on the real converter,
    so a converter that stops raising (made iterative, say) shows up here
    rather than leaving the handling untested."""
    import pytest

    from policyforge.export.confluence_importer import confluence_to_markdown

    assert "x" in confluence_to_markdown(_nested(200))
    with pytest.raises(RecursionError):
        confluence_to_markdown(_nested(DEPTH))


class RealConversionPublisher(FakeRecursionPublisher):
    """`as_markdown` is the real conversion of the page's storage body."""

    def fetch(self, tier: str, title: str):
        page = super().fetch(tier, title)
        page.body = _nested(DEPTH) if "nested" in title.lower() else "<p>normal content</p>"
        return page

    def as_markdown(self, page) -> str:
        from policyforge.export.confluence_importer import convert_page

        return convert_page(page.body, title=page.title)


def test_a_deeply_nested_real_page_is_refused_and_the_pull_carries_on(tmp_path):
    report = pull_documents(
        publisher=RealConversionPublisher(),
        pages=[("SEC", "Nested Page", "standard"), ("SEC", "Normal Page", "standard")],
        root=tmp_path,
        target_path=lambda root, tier, slug: root / f"{tier}s" / f"{slug}.md",
        dry_run=False,
    )
    nested = next(r for r in report.results if r.title == "Nested Page")
    assert nested.action == REFUSED and "nested too deeply" in nested.reason
    assert (tmp_path / "standards" / "normal-page.md").exists()
    # The report counts both: one refused, and the other not.
    assert len(report.refused) == 1 and len(report.results) == 2
    text = report.format_report()
    assert "Refused 1:" in text and f"Nested Page: {TOO_DEEP}" in text
    # The macro advice is about pages that read fine; this one never did.
    assert "without those macros" not in text and "nested too deeply to convert" in text


def test_each_kind_of_refusal_gets_its_own_advice():
    macro = PullResult(
        title="M", path="", action=REFUSED, reason="carries an unsupported macro", kind="macros"
    )
    deep = PullResult(title="D", path="", action=REFUSED, reason=TOO_DEEP, kind="too-deep")
    only_macro = PullReport(results=[macro], dry_run=False).format_report()
    both = PullReport(results=[macro, deep], dry_run=False).format_report()
    assert "without those macros" in only_macro and "Flatten their nesting" not in only_macro
    assert "without those macros" in both and "Flatten their nesting" in both
