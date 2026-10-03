"""The shared eCFR fetcher asks for exactly what the HIPAA loader asked.

`ingest/ecfr.py` exists because four more regulations are coming — 45 CFR
170.315, 45 CFR 171, 42 CFR Part 2, 32 CFR Part 170 — and the HIPAA loader
hardcoded title 45 and part 164 in three places. Copied per catalog, that
is four divergent fetchers.

This is a refactor, so the claim to prove is that nothing moved. The URL
builders as they stood at 886a41a are frozen below and driven through the
same inputs as the new ones, asserting identical strings. They must not be
"tidied" into calls to the new code: the moment they delegate, the test
proves nothing. The same technique as `test_publisher_equivalence.py`.

The second half is the reason the module exists: that the title is a real
parameter and not only the part. 42 CFR Part 2 is a different title, so a
fetcher generalising the part alone would look general and fail on the
second catalog.
"""

from __future__ import annotations

import pytest

from policyforge.ingest import ecfr, hipaa_loader

DATE = "2026-09-17"


# ---- frozen from hipaa_loader.py at 886a41a -----------------------------


def _old_source_url(date: str) -> str:
    return f"https://www.ecfr.gov/api/versioner/v1/full/{date}/title-45.xml?part=164"


_OLD_FETCH_URL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-45.xml"
_OLD_FETCH_PARAMS = {"part": "164"}
_OLD_TITLES_URL = "https://www.ecfr.gov/api/versioner/v1/titles.json"


# ---- nothing moved ------------------------------------------------------


@pytest.mark.parametrize("date", ["2026-09-17", "2024-01-01", "2020-12-31"])
def test_the_provenance_url_is_unchanged(date):
    assert hipaa_loader.ecfr_source_url(date) == _old_source_url(date)


def test_the_hipaa_wrappers_still_name_hipaa_s_own_coordinates():
    """A reader of the HIPAA loader should see that it fetches HIPAA."""
    assert hipaa_loader.TITLE == 45
    assert hipaa_loader.PART == "164"


class _Recorder:
    """Stands in for `requests`, recording what was asked for."""

    def __init__(self, payload=None, text="<xml/>"):
        self.calls: list[tuple] = []
        self._payload = payload
        self._text = text

    def get(self, url, *, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        recorder = self

        class Response:
            status_code = 200
            text = recorder._text

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return recorder._payload

        return Response()


@pytest.fixture
def requests_recorder(monkeypatch):
    import sys
    import types

    recorder = _Recorder(payload={"titles": [{"number": 45, "up_to_date_as_of": DATE}]})
    module = types.ModuleType("requests")
    module.get = recorder.get
    monkeypatch.setitem(sys.modules, "requests", module)
    return recorder


def test_the_fetch_asks_for_the_same_url_and_params(requests_recorder):
    hipaa_loader.fetch_ecfr_subpart_c_xml(date=DATE)

    url, params, timeout = requests_recorder.calls[0]
    assert url == _OLD_FETCH_URL.format(date=DATE)
    assert params == _OLD_FETCH_PARAMS
    assert timeout == 30


def test_the_date_lookup_asks_for_the_same_url_and_reads_title_45(requests_recorder):
    assert hipaa_loader.current_ecfr_date() == DATE

    url, _params, timeout = requests_recorder.calls[0]
    assert url == _OLD_TITLES_URL
    assert timeout == 30


def test_a_fetch_with_no_date_resolves_one_first_then_uses_it(requests_recorder):
    """Two calls in order, and the date fetched is the date asked for — the
    reason the lookup lives in the caller rather than inside the fetch."""
    hipaa_loader.fetch_ecfr_subpart_c_xml()

    assert requests_recorder.calls[0][0] == _OLD_TITLES_URL
    assert requests_recorder.calls[1][0] == _OLD_FETCH_URL.format(date=DATE)


# ---- the title is a real parameter --------------------------------------


@pytest.mark.parametrize(
    "title,part,expected",
    [
        (45, "164", "title-45.xml?part=164"),
        (45, "170", "title-45.xml?part=170"),
        (45, "171", "title-45.xml?part=171"),
        (42, "2", "title-42.xml?part=2"),
        (32, "170", "title-32.xml?part=170"),
    ],
)
def test_every_regulation_this_project_is_adding_builds_its_own_url(title, part, expected):
    """42 CFR Part 2 is the case that matters: a fetcher generalising only
    the part would look general and fail on the second catalog."""
    assert ecfr.source_url(DATE, title=title, part=part).endswith(expected)


def test_two_parts_of_one_title_are_distinguishable_in_provenance():
    """Provenance records the URL, and two catalogs drawn from eCFR on the
    same date must not look identical in `framework.yaml`."""
    assert ecfr.source_url(DATE, title=45, part="164") != ecfr.source_url(
        DATE, title=45, part="170"
    )


def test_the_date_lookup_reads_the_title_it_was_asked_for(monkeypatch):
    import sys
    import types

    recorder = _Recorder(
        payload={
            "titles": [
                {"number": 42, "up_to_date_as_of": "2026-01-01"},
                {"number": 45, "up_to_date_as_of": DATE},
            ]
        }
    )
    module = types.ModuleType("requests")
    module.get = recorder.get
    monkeypatch.setitem(sys.modules, "requests", module)

    assert ecfr.current_date(42) == "2026-01-01"
    assert ecfr.current_date(45) == DATE


def test_an_unknown_title_raises_rather_than_returning_a_wrong_date(monkeypatch):
    """`next()` over a filtered list returns the *first* match; with no
    match it must raise rather than fall through to something plausible."""
    import sys
    import types

    recorder = _Recorder(payload={"titles": [{"number": 45, "up_to_date_as_of": DATE}]})
    module = types.ModuleType("requests")
    module.get = recorder.get
    monkeypatch.setitem(sys.modules, "requests", module)

    with pytest.raises(StopIteration):
        ecfr.current_date(99)
