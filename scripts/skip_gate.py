"""Fail CI when the tests it skipped aren't exactly the ones it expects to (#31).

    python scripts/skip_gate.py <pytest junit xml> [tests/ci_expected_skips.txt]

CI once skipped 15 tests where a local run skipped 2, and nobody could see it:
`pytest -q` names no skip. Twelve were extras missing from the CI lock, one
of them the credential guard, so a green run covered less than it seemed to.
This compares the run's skips with a committed list and fails on any
difference, in both directions:

- **a skip not on the list** is a test CI has stopped running;
- **a listed skip that didn't happen** means the list is stale, so it would
  wave through the next real one.

**Keyed by test function and reason, never by `path:line`.** Parametrised ids
can carry a `file.py:NNN` (the printed-command test's do), and a key built on
one would fail the next edit above that line. So a skip is identified by
its test function (parameters dropped) and its reason with every `*.py:NNN`
removed, and the list gives a COUNT per key. A new skip of an existing kind
still fails, because the count rises.
"""

from __future__ import annotations

import re
import sys

# The one input is the JUnit report pytest wrote moments earlier, in the same
# CI job or the same `check.py` run, from this repository's own tests: never
# a file from outside. `defusedxml` guards untrusted XML, and would be a new
# dependency to parse our own output, so it isn't used. If this ever reads a
# report it didn't just write, that reasoning stops holding.
# nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
import xml.etree.ElementTree as ET  # nosec B405
from collections import Counter
from pathlib import Path

DEFAULT_LIST = Path(__file__).resolve().parents[1] / "tests" / "ci_expected_skips.txt"
_LOCATION = re.compile(r"\S+\.py:\d+\s*")


def key(classname: str, name: str, reason: str) -> str:
    function = name.split("[", 1)[0]
    return f"{classname}::{function} | {_LOCATION.sub('', reason or '').strip()}"


def skips(junit: Path) -> Counter:
    found = Counter()
    # Our own pytest report (see the import). semgrep honours a suppression
    # only on the offending line, so it sits there, bare.
    tree = ET.parse(junit)  # nosec B314  # nosemgrep
    for case in tree.getroot().iter("testcase"):
        for child in case:
            if child.tag == "skipped" and child.get("type") != "pytest.xfail":
                k = key(case.get("classname", ""), case.get("name", ""), child.get("message", ""))
                found[k] += 1
    return found


def expected(listing: Path) -> Counter:
    want = Counter()
    for line in listing.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            count, rest = line.split(None, 1)
            want[rest.strip()] += int(count)
    return want


def main(argv: list[str]) -> int:
    junit = Path(argv[1])
    listing = Path(argv[2]) if len(argv) > 2 else DEFAULT_LIST
    got, want = skips(junit), expected(listing)
    extra = {k: got[k] - want[k] for k in got if got[k] > want[k]}
    missing = {k: want[k] - got[k] for k in want if want[k] > got[k]}
    print(
        f"{sum(got.values())} skip(s) in this run; {sum(want.values())} expected by {listing.name}"
    )
    for k, n in sorted(extra.items()):
        print(f"  NOT EXPECTED  +{n}  {k}")
    for k, n in sorted(missing.items()):
        print(f"  EXPECTED, DIDN'T SKIP  -{n}  {k}")
    if extra or missing:
        print(
            "The skips changed. A new one is a test CI no longer runs: make it run, or add it "
            f"to {listing.name} with its reason. One that stopped skipping: remove it "
            "from the list."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
