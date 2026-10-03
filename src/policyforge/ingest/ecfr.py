"""Reading a part of the Code of Federal Regulations from eCFR.

One fetcher for every regulation this project ingests, because there is
about to be more than one. The HIPAA Security Rule was the first, and its
loader hardcoded title 45 and part 164 in three places — fine while it was
the only regulation, and the wrong shape the moment 45 CFR 170.315, 45 CFR
171, 42 CFR Part 2 and 32 CFR Part 170 arrive. Copied per catalog, that is
four divergent fetchers and four places to fix the next time eCFR changes
its API.

The title is a parameter, not only the part: 42 CFR Part 2 is a different
title, so a fetcher that generalised the part alone would have looked
general and failed on the second catalog.

**What this module does not do is parse.** Every regulation has its own
structure — 164 Subpart C nests `(a)(1)(i)`, 170.315 is a flat list of
lettered criteria with deep sub-paragraphs — and a shared parser would be
a worse lie than a shared fetcher is a truth. Fetching is the easy half;
the parse is where a catalog goes quietly wrong.

A note on the effective date, inherited from the HIPAA loader and true for
every title. eCFR is a live source with no tags: the effective date is the
only thing identifying a revision, which makes it the equivalent of an
OSCAL release tag and the right value to record as `source_ref`. Resolving
it in the caller rather than inside the fetch means the date that gets
stamped is provably the date that was fetched, not a second lookup that
could land on the other side of an eCFR publication.
"""

from __future__ import annotations

_API = "https://www.ecfr.gov/api/versioner/v1"


def current_date(title: int) -> str:
    """The effective date eCFR publishes `title` as up to date to."""
    import requests

    response = requests.get(f"{_API}/titles.json", timeout=30)
    response.raise_for_status()
    entry = next(t for t in response.json()["titles"] if t["number"] == title)
    return entry["up_to_date_as_of"]


def source_url(date: str, *, title: int, part: str) -> str:
    """The exact URL a given effective date and part is read from.

    Recorded as provenance, so it carries the title and the part: two
    catalogs drawn from eCFR on the same date are otherwise
    indistinguishable in `framework.yaml`, and a provenance record that
    cannot tell two sources apart is the kind that looks complete and is
    ambiguous.
    """
    return f"{_API}/full/{date}/title-{title}.xml?part={part}"


def fetch_part_xml(*, title: int, part: str, date: str | None = None) -> str:
    """The full XML of one CFR part, at `date` or at the current revision.

    The only network-touching function here, kept apart from every parser
    so the parsers stay pure and testable offline against fixtures.
    """
    import requests

    if date is None:
        date = current_date(title)

    response = requests.get(
        f"{_API}/full/{date}/title-{title}.xml",
        params={"part": part},
        timeout=30,
    )
    response.raise_for_status()
    return response.text
