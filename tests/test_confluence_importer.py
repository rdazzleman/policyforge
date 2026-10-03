"""export/confluence_importer.py tests: fetching a page (via a monkeypatched
`requests.get`, so no network call), storage-format-to-markdown conversion
including this project's own `code` macro, and the missing-page error."""

from __future__ import annotations


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_confluence_page_returns_page_on_match(monkeypatch):
    from policyforge.export import confluence_importer

    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    monkeypatch.setenv("CONFLUENCE_USERNAME", "user@example.com")

    calls = []

    def fake_get(url, *, params, auth, headers, timeout):
        calls.append({"url": url, "params": params, "auth": auth})
        return FakeResponse(
            {
                "results": [
                    {
                        "id": "123",
                        "title": "Authenticator Management Standard",
                        "version": {"number": 4},
                        "body": {"storage": {"value": "<p>Hi</p>"}},
                        "_links": {"webui": "/spaces/ENG/pages/123"},
                    }
                ]
            }
        )

    monkeypatch.setattr("requests.get", fake_get)

    page = confluence_importer.fetch_confluence_page(
        space="ENG",
        title="Authenticator Management Standard",
        host="https://example.atlassian.net/wiki",
    )

    assert page.id == "123"
    assert page.version == 4
    assert page.storage_body == "<p>Hi</p>"
    assert page.webui_url == "https://example.atlassian.net/wiki/spaces/ENG/pages/123"
    assert calls[0]["params"]["title"] == "Authenticator Management Standard"
    assert calls[0]["params"]["spaceKey"] == "ENG"
    assert calls[0]["auth"] == ("user@example.com", "tok")


def test_fetch_confluence_page_raises_lookup_error_when_no_match(monkeypatch):
    from policyforge.export import confluence_importer

    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    monkeypatch.setattr("requests.get", lambda *a, **k: FakeResponse({"results": []}))

    try:
        confluence_importer.fetch_confluence_page(
            space="ENG", title="Nonexistent Page", host="https://example.atlassian.net/wiki"
        )
    except LookupError as exc:
        assert "Nonexistent Page" in str(exc)
    else:
        raise AssertionError("expected LookupError when no page matches")


def test_fetch_confluence_page_requires_api_token(monkeypatch):
    from policyforge.export import confluence_importer

    monkeypatch.delenv("CONFLUENCE_API_TOKEN", raising=False)

    try:
        confluence_importer.fetch_confluence_page(
            space="ENG", title="Anything", host="https://example.atlassian.net/wiki"
        )
    except RuntimeError as exc:
        assert "CONFLUENCE_API_TOKEN" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when no API token is configured")


def test_confluence_to_markdown_converts_headings_and_paragraphs():
    from policyforge.export.confluence_importer import confluence_to_markdown

    markdown = confluence_to_markdown(
        "<h1>Authenticator Management Standard</h1>"
        "<p>Staff must manage authenticators. [NIST IA-5]</p>"
    )

    assert markdown.startswith("# Authenticator Management Standard")
    assert "Staff must manage authenticators. [NIST IA-5]" in markdown


def test_confluence_to_markdown_restores_code_fence_with_language():
    from policyforge.export.confluence_importer import confluence_to_markdown

    storage = (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">bash</ac:parameter>'
        "<ac:plain-text-body><![CDATA[echo hello\n]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )

    markdown = confluence_to_markdown(storage)

    assert "```bash" in markdown
    assert "echo hello" in markdown


def test_roundtrip_through_exporter_and_importer_preserves_content():
    from policyforge.export.confluence_exporter import markdown_to_confluence
    from policyforge.export.confluence_importer import confluence_to_markdown

    original = (
        "# Title\n\n"
        "Body text. [NIST IA-5]\n\n"
        "## Section\n\n"
        "```python\nprint('hi')\n```\n\n"
        "- one\n- two\n"
    )

    roundtripped = confluence_to_markdown(markdown_to_confluence(original))

    assert "# Title" in roundtripped
    assert "Body text. [NIST IA-5]" in roundtripped
    assert "```python" in roundtripped
    assert "print('hi')" in roundtripped
    assert "- one" in roundtripped and "- two" in roundtripped


def test_import_from_confluence_fetches_and_converts(monkeypatch):
    from policyforge.export import confluence_importer

    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "tok")
    monkeypatch.setattr(
        "requests.get",
        lambda *a, **k: FakeResponse(
            {
                "results": [
                    {
                        "id": "1",
                        "title": "T",
                        "version": {"number": 1},
                        "body": {"storage": {"value": "<h1>T</h1><p>Body</p>"}},
                        "_links": {"webui": "/x"},
                    }
                ]
            }
        ),
    )

    result = confluence_importer.import_from_confluence(
        space="ENG", title="T", host="https://example.atlassian.net/wiki"
    )

    assert result.startswith("# T")
    assert "Body" in result
