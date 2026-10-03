"""Run semgrep for the gate, and refuse anything it did not check (#43).

    python scripts/semgrep_gate.py --semgrep PATH

WHY. The gate ran `semgrep scan … --error .`, and `--error` fails only on
FINDINGS. Everything semgrep could not check was a warning, and the step
passed:

- a RULE TIMEOUT (5 s per rule per file by default) skipped that rule on that
  file. Measured on `scripts/release.py` (the script that pushes to GitHub):
  a taint rule timed out in 1 of 2 identical runs, on semgrep 1.174 and 1.177
  alike, and the step read PASS both times (9b found it; #43);
- a PARTIAL PARSE scanned only part of a file (both Dockerfiles today);
- a file over 1 MB (`--max-target-bytes`) was not scanned at all, and the JSON
  report does not even mention it (1d measured: `paths` holds only
  `scanned`). The 800-53 catalog crossed that line.

WHAT THIS DOES. It runs semgrep with `--json` and CLASSIFIES every error, so
nothing is dropped:

    Timeout          FAILS, naming the rule and the file
    PartialParsing   passes ONLY for a path in ALLOWED_PARTIAL (with its
                     reason), printed on every run; any other path FAILS
    anything else    FAILS, naming its type, so a new kind is never waved through

and it closes the other two gaps by construction: `--timeout 30` (measured:
0 timeouts in 2 full runs, at no extra wall time) and `--max-target-bytes
10 MB` (the catalog is scanned; 301 files instead of 300, no extra time).
Because the size skip is invisible in the report, it is DERIVED: any
tracked file over the limit that semgrep did not scan FAILS by name.
A finding FAILS, as before. A report with no scanned files at all FAILS:
that is a broken run, not a clean tree.

SCOPE, stated so the zero is not read as more: semgrep's own default ignore
(`tests/`, vendored and generated paths; there is no `.semgrepignore` here)
decides which files it scans at all; this refuses what semgrep SKIPPED
WITHIN that scope, not the scope itself.

EXIT: 0 clean; 1 a finding, a timeout, an unlisted partial parse, an
unknown error, or an oversized unscanned file, each named; 2 semgrep could
not run or its report could not be read.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ("p/python", "p/security-audit", "p/owasp-top-ten")
TIMEOUT_SECONDS = 30
MAX_TARGET_BYTES = 10_000_000

#: Partial parses accepted, keyed by PATH, never by line, each with why.
#: Anything semgrep only partly parses that is not here fails the gate.
ALLOWED_PARTIAL = {
    "Dockerfile": (
        "semgrep's Dockerfile parser rejects multi-line ENV and RUN continuations; "
        "the 4 Dockerfile rules run on the parts it reads"
    ),
    ".devcontainer/Dockerfile": "the same parser limit, on a multi-line RUN",
}


def _posix(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _error_type(error: dict) -> str:
    kind = error.get("type")
    return kind if isinstance(kind, str) else (kind[0] if kind else "unknown")


def _error_path(error: dict) -> str:
    if error.get("path"):
        return _posix(error["path"])
    spans = error.get("spans") or []
    return _posix(spans[0].get("file", "")) if spans else ""


def tracked_sizes(root: Path) -> dict[str, int]:
    """{posix path: size in the working tree} of every tracked file."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    sizes = {}
    for raw in filter(None, out.split(b"\0")):
        rel = raw.decode("utf-8", "replace")
        path = root / rel
        if path.is_file():
            sizes[rel] = path.stat().st_size
    return sizes


def classify(report: dict, sizes: dict[str, int]) -> tuple[list[str], list[str]]:
    """(failures, notes) for a semgrep JSON report. Every error is placed."""
    failures: list[str] = []
    notes: list[str] = []
    scanned = {_posix(p) for p in report.get("paths", {}).get("scanned", [])}
    if not scanned:
        failures.append("semgrep scanned ZERO files: a broken run, not a clean tree")
    for result in report.get("results", []):
        failures.append(
            f"FINDING  {result.get('check_id')}  "
            f"{_posix(result.get('path', ''))}:{result.get('start', {}).get('line', '?')}"
        )
    for error in report.get("errors", []):
        kind, path = _error_type(error), _error_path(error)
        if kind == "Timeout":
            failures.append(
                f"TIMEOUT  {error.get('rule_id') or '?'} did not finish on {path}: "
                "that rule was NOT checked there"
            )
        elif kind == "PartialParsing" and path in ALLOWED_PARTIAL:
            notes.append(f"partially scanned (allowed): {path}: {ALLOWED_PARTIAL[path]}")
        elif kind == "PartialParsing":
            failures.append(f"PARTIAL PARSE  {path} was only partly scanned, and is not allowed")
        else:
            failures.append(f"ERROR  {kind} on {path or '?'}: {(error.get('message') or '')[:160]}")
    for path, size in sorted(sizes.items()):
        if size > MAX_TARGET_BYTES and path not in scanned:
            failures.append(
                f"TOO LARGE  {path} ({size:,} bytes) is over {MAX_TARGET_BYTES:,} "
                "and was NOT scanned"
            )
    return failures, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--semgrep", required=True, help="the semgrep executable to run")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="semgrep-gate-") as scratch:
        out = Path(scratch) / "report.json"
        cmd = [
            args.semgrep,
            "scan",
            *(f"--config={c}" for c in CONFIGS),
            f"--timeout={TIMEOUT_SECONDS}",
            f"--max-target-bytes={MAX_TARGET_BYTES}",
            "--metrics=off",
            "--json",
            "--output",
            str(out),
            ".",
        ]
        done = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                f"semgrep_gate: COULD NOT READ semgrep's report (semgrep exit {done.returncode}): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2
    failures, notes = classify(report, tracked_sizes(REPO_ROOT))
    scanned = len(report.get("paths", {}).get("scanned", []))
    print(f"semgrep {report.get('version', '?')}: scanned {scanned} file(s)")
    for line in notes:
        print(f"  {line}")
    for line in failures:
        print(f"  {line}")
    if failures:
        print(f"semgrep_gate: {len(failures)} problem(s); the step FAILS")
        return 1
    print("semgrep_gate: every scanned file clean, no rule skipped, nothing oversize left out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
