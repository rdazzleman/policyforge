from policyforge.export.publisher import REFUSED, PullReport, PullResult


def test_a_missing_page_gives_no_macro_footer():
    report = PullReport(
        results=[
            PullResult(
                title="Missing", path="", action=REFUSED, reason="Not Found", kind="not-found"
            )
        ],
        dry_run=False,
    )
    text = report.format_report()
    assert "without those macros" not in text
    assert "Check the page title and space." in text


def test_a_macro_page_still_gives_the_macro_footer():
    report = PullReport(
        results=[
            PullResult(title="Macro", path="", action=REFUSED, reason="macros", kind="macros")
        ],
        dry_run=False,
    )
    text = report.format_report()
    assert "without those macros" in text
    assert "Check the page title and space." not in text


def test_both_together_give_the_macro_footer_once():
    report = PullReport(
        results=[
            PullResult(
                title="Missing", path="", action=REFUSED, reason="Not Found", kind="not-found"
            ),
            PullResult(title="Macro", path="", action=REFUSED, reason="macros", kind="macros"),
        ],
        dry_run=False,
    )
    text = report.format_report()
    assert "without those macros" in text
    assert "Check the page title and space." in text
    assert text.count("without those macros") == 1


def test_a_refusal_of_no_known_kind_is_refused_when_made():
    """1d, carrying ag's branch: with no kind, a refusal would print no advice."""
    import pytest

    with pytest.raises(ValueError, match="unknown kind"):
        PullResult(title="X", path="", action=REFUSED, reason="something new")
    # Only refusals carry a kind; a written page needs none.
    PullResult(title="W", path="w.md", action="written")
