"""Run semgrep for the gate, and refuse anything it did not check (#43).

    python scripts/semgrep_gate.py --image      (the local gate: the pinned container)
    python scripts/semgrep_gate.py --semgrep PATH   (CI, already inside that container)

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

UNDER LOAD (#348). The 30 s was measured on a quiet machine. On 2026-10-06,
with full test runs sharing the machine, two taint rules timed out on
`scripts/release.py` at 30 s, and the step failed on a tree that the
previous gate had passed. Alone, that file scans in 15 to 18 s, with no
timeout at 30 s or at 300 s (1d measured). So a timeout is RETRIED ONCE:

- only the files with a timed-out rule are scanned again, with every rule,
  at `RETRY_TIMEOUT_SECONDS`;
- what the retry reports for those files replaces the first run's timeouts
  there, and nothing else changes;
- a rule that times out again FAILS, as before, and so does any finding the
  retry makes, any other error it reports, and any retried file it didn't
  scan;
- every run that retries prints how many timeouts it retried and how many
  remain, so load is visible rather than absorbed.

FROM A PINNED CONTAINER (#83). semgrep pins its dependencies exactly, and
installed beside the project its pins became the project's, so its lock got
its own environment and an ADVISORY audit (#82): pyjwt advisories landed on
it that the product didn't share. Now semgrep runs only from SEMGREP_IMAGE,
pinned by its index digest, so its dependencies sit outside every audited
Python environment, and that lock and its audit are gone.
- **CI** runs the job inside that image and calls this with `--semgrep
  semgrep`.
- **The local gate** calls it with `--image`. The tracked files' working-tree
  contents are streamed into the container as a tar, extracted, `git add`ed,
  and scanned as `.`. So semgrep makes its own git-aware choice of files, as
  it does natively. Measured on the 1.8 train (1d, PR for 83): 343 files
  scanned, 343 natively, the same set, 0 findings both ways, the same two
  partial parses, and 32 s against 33 s.

Two simpler routes measured worse:
- **Mounting the worktree:** its `.git` names a Windows path, so git can't
  read it inside the container, and semgrep scans every file under the mount
  (ba on 83).
- **Passing the tracked files as arguments:** explicit targets skip semgrep's
  default ignore, so tests/ was scanned too, and the list is 35,132
  characters, over Windows' command-line limit (1d, on 83).

SCOPE, stated so the zero is not read as more: semgrep's own default ignore
(`tests/`, vendored and generated paths; there is no `.semgrepignore` here)
decides which files it scans at all; this refuses what semgrep SKIPPED
WITHIN that scope, not the scope itself.

EXIT: 0 clean; 1 a finding, a timeout, an unlisted partial parse, an
unknown error, or an oversized unscanned file, each named; 2 semgrep could
not run or its report could not be read, on the first scan or the retry.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = ("p/python", "p/security-audit", "p/owasp-top-ten")
#: The one semgrep there is (#83): its version tag, and the multi-arch INDEX
#: digest Docker refuses any other content for. ci.yml's semgrep job runs in
#: exactly this reference, and a test holds them equal. To move it, read the
#: new tag's index digest (`docker buildx imagetools inspect <tag>`) and
#: change it here and in ci.yml together.
SEMGREP_IMAGE = (
    "semgrep/semgrep:1.177.0"
    "@sha256:acaac22ffc7b7cc5926de0751b223bce0b2491c33d18422fa72f632c78d81198"
)
#: Run inside the container: the streamed tree becomes a git repository
#: whose index is the host's tracked set, so `semgrep scan .` chooses files
#: as it would natively. The semgrep command follows as "$@".
IN_CONTAINER = 'set -e; mkdir /w; cd /w; tar -xf -; git init -q; git add -A; exec "$@"'
TIMEOUT_SECONDS = 30
#: Per rule per file, for the one retry of files that timed out at
#: TIMEOUT_SECONDS. Ten times the first limit: the file that timed out under
#: load scans in 15 to 18 s on a quiet machine.
RETRY_TIMEOUT_SECONDS = 300
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


def timed_out_paths(report: dict) -> list[str]:
    """The files on which at least one rule timed out, as posix paths."""
    return sorted(
        {_error_path(e) for e in report.get("errors", []) if _error_type(e) == "Timeout"} - {""}
    )


def merge_retry(first: dict, retry: dict, retried: list[str]) -> dict:
    """`first`, with its timeouts on the `retried` files replaced by what the
    retry reported. Everything else in `first` stands. The retry's findings
    and errors are added (a finding both runs made is counted once), and a
    retried file the retry didn't scan becomes an error, so its rules can't
    pass as checked."""
    again = set(retried)
    errors = [
        e
        for e in first.get("errors", [])
        if not (_error_type(e) == "Timeout" and _error_path(e) in again)
    ]
    errors += retry.get("errors", [])
    rescanned = {_posix(p) for p in retry.get("paths", {}).get("scanned", [])}
    for path in retried:
        if path not in rescanned:
            errors.append(
                {
                    "type": "NotRescanned",
                    "path": path,
                    "message": "the retry did not scan it, so its timed-out rules are unchecked",
                }
            )

    def key(result):
        line = result.get("start", {}).get("line")
        return result.get("check_id"), _posix(result.get("path", "")), line

    results = list(first.get("results", []))
    seen = {key(r) for r in results}
    results += [r for r in retry.get("results", []) if key(r) not in seen]
    return {**first, "errors": errors, "results": results}


def tracked_tree(root: Path) -> bytes:
    """A tar of every tracked file's working-tree contents, for the container
    (#83). Tracked by git's index, read from disk, so an edit not yet
    committed is scanned, as natively."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for raw in filter(None, out.stdout.split(b"\0")):
            rel = raw.decode("utf-8", "replace")
            if (root / rel).is_file():
                tar.add(root / rel, arcname=rel, recursive=False)
    return buffer.getvalue()


def _scan(
    semgrep: str | None,
    targets: list[str],
    timeout: int,
    scratch: Path,
    name: str,
    *,
    image: str | None = None,
    tree: bytes | None = None,
):
    """Run semgrep on `targets`, natively or in `image` over `tree`; return
    its JSON report, or None after saying why."""
    out = scratch / f"{name}.json"
    flags = [
        *(f"--config={c}" for c in CONFIGS),
        f"--timeout={timeout}",
        f"--max-target-bytes={MAX_TARGET_BYTES}",
        "--metrics=off",
        "--json",
    ]
    if image:
        cmd = [
            *("docker", "run", "--rm", "-i", "-v", f"{scratch}:/out", image),
            *("sh", "-c", IN_CONTAINER, "semgrep_gate"),
            *("semgrep", "scan", *flags, "--output", f"/out/{name}.json", *targets),
        ]
        done = subprocess.run(cmd, input=tree, check=False)
    else:
        cmd = [semgrep, "scan", *flags, "--output", str(out), *targets]
        done = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
    try:
        return json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(
            f"semgrep_gate: COULD NOT READ semgrep's {name} report "
            f"(semgrep exit {done.returncode}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    how = parser.add_mutually_exclusive_group(required=True)
    how.add_argument("--semgrep", help="a semgrep executable to run (CI, inside SEMGREP_IMAGE)")
    how.add_argument(
        "--image",
        nargs="?",
        const=SEMGREP_IMAGE,
        help="run semgrep in this container image (default: the pinned SEMGREP_IMAGE)",
    )
    args = parser.parse_args(argv)
    where = {"image": args.image, "tree": tracked_tree(REPO_ROOT)} if args.image else {}
    retry_line = ""
    with tempfile.TemporaryDirectory(prefix="semgrep-gate-") as scratch:
        report = _scan(args.semgrep, ["."], TIMEOUT_SECONDS, Path(scratch), "first", **where)
        if report is None:
            return 2
        retried = timed_out_paths(report)
        if retried:
            timeouts = sum(1 for e in report.get("errors", []) if _error_type(e) == "Timeout")
            retry = _scan(
                args.semgrep, retried, RETRY_TIMEOUT_SECONDS, Path(scratch), "retry", **where
            )
            if retry is None:
                return 2
            report = merge_retry(report, retry, retried)
            left = sum(1 for e in report.get("errors", []) if _error_type(e) == "Timeout")
            retry_line = (
                f"  RETRIED  {timeouts} rule timeout(s) at {TIMEOUT_SECONDS} s on "
                f"{len(retried)} file(s), re-scanned at {RETRY_TIMEOUT_SECONDS} s: "
                f"{left} timeout(s) remain ({', '.join(retried)})"
            )
    failures, notes = classify(report, tracked_sizes(REPO_ROOT))
    scanned = len(report.get("paths", {}).get("scanned", []))
    print(f"semgrep {report.get('version', '?')}: scanned {scanned} file(s)")
    if retry_line:
        print(retry_line)
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
