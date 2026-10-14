"""The suite's `slow` set, derived from a measured run (#292).

    python scripts/slow_tests.py <junit.xml> --source "<what run it was>"

A test is slow when one serial run measured it at `THRESHOLD` seconds or
more, and not within `MARGIN` of that line: one run's time is good to about
20%, so a test near the line stays unmarked and the list errs towards
running (80 on #292). The time is junit's: setup, call and teardown together. This script
writes `tests/slow_tests.txt` from that run's junit XML, each test beside its
measured time, and conftest marks exactly what the list names. Nothing is
marked slow by hand, so `slow` can't become a place to put a flaky test:
`tests/test_slow_tests.py` refuses a `pytest.mark.slow` written in a test, an
entry under the threshold, and an entry naming no test.

Who leaves them out: carriers and readers may run `-m "not slow"` over the
files OUTSIDE their change (the codebase scanners and the importers), never
over the test files the change touches. The batch gate and CI run everything.

Measure serially (`-p no:xdist`) with nothing else running: under xdist a
test's time includes waiting on the workers beside it.
"""

from __future__ import annotations

import argparse
import sys

# The one input is a JUnit report pytest wrote from this repository's own
# tests, as for skip_gate.py: never a file from outside, so `defusedxml`
# would be a new dependency to parse our own output.
# nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
import xml.etree.ElementTree as ET  # nosec B405
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIST = REPO_ROOT / "tests" / "slow_tests.txt"
#: Seconds (80's ruling on #292).
THRESHOLD = 5.0
#: A measured time this close above THRESHOLD isn't marked.
MARGIN = 0.20
#: What is marked: 34 tests on 2026-10-06, 33% of a 23.7 min serial run.
CUT = THRESHOLD * (1 + MARGIN)


def node_id(classname: str, name: str, root: Path = REPO_ROOT) -> str:
    """pytest's node id for a junit case: `tests.test_x.TestY` + `name` is
    `tests/test_x.py::TestY::name`. The module is the longest dotted prefix
    that is a file under `root`; the rest are classes."""
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        module = Path(*parts[:cut]).with_suffix(".py")
        if (root / module).is_file():
            return "::".join([module.as_posix(), *parts[cut:], name])
    raise ValueError(f"no module under {root} for junit class {classname!r}")


def measured(junit: Path, root: Path = REPO_ROOT) -> list[tuple[float, str]]:
    """Every case at `CUT` or more, slowest first. Skipped cases are
    left out: their time measures nothing that ran."""
    rows = []
    for case in ET.parse(junit).getroot().iter("testcase"):  # nosec B314  # nosemgrep
        if case.find("skipped") is not None:
            continue
        seconds = float(case.get("time") or 0)
        if seconds >= CUT:
            rows.append((seconds, node_id(case.get("classname", ""), case.get("name", ""), root)))
    return sorted(rows, key=lambda row: (-row[0], row[1]))


def render(rows: list[tuple[float, str]], source: str) -> str:
    head = [
        "# The suite's `slow` set: every test one serial run measured at",
        f"# {CUT:g} s or more ({THRESHOLD:g} s and {MARGIN:.0%} margin), with its time.",
        "# Written by scripts/slow_tests.py;",
        "# don't edit by hand, re-run it from a new measurement.",
        f"# Source: {source}",
    ]
    return "\n".join(head + [f"{seconds:6.2f}  {node}" for seconds, node in rows]) + "\n"


def read(path: Path = LIST) -> dict[str, float]:
    """The list as {node id: measured seconds}."""
    listed = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            seconds, node = line.split(maxsplit=1)
            listed[node] = float(seconds)
    return listed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("junit", type=Path, help="the junit XML of a serial run")
    parser.add_argument("--source", required=True, help="what run it was, for the list's header")
    args = parser.parse_args(argv)
    rows = measured(args.junit)
    LIST.write_text(render(rows, args.source), encoding="utf-8", newline="\n")
    print(f"{len(rows)} test(s) at {CUT:g} s or more written to {LIST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
