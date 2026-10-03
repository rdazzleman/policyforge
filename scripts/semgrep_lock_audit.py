"""Audit semgrep's lock for CVEs, in advisory mode (#82; 80's ruling 1774).

    python scripts/semgrep_lock_audit.py

WHY. semgrep is the static analyser the gate and CI run over this repository.
It never ships to users, and it pins its own dependencies exactly:
semgrep 1.177.0 (and 1.178.0, the newest) requires `pyjwt<2.14`. On
2026-09-29, eight advisories against pyjwt 2.13.0 reached the database in an
afternoon, all fixed in 2.14.0, which can't be installed beside semgrep.
Downgrading doesn't help either: 1.156.0, the newest semgrep that allows
pyjwt 2.14, fails the audit on click and mcp instead (1d measured it on #82).
A named exception per advisory (#72, #82) had become a queue, so 80 ruled
that semgrep's lock is audited in ADVISORY mode, bounded as follows. The user
chose this too ("Report only, for now"; 80 recorded it as the relay, 1776).

WHAT THIS DOES. It runs pip-audit over semgrep's lock, reads its JSON, and
classifies every result, so nothing is dropped:

    an advisory on a package the product can't install
    at that version                                  REPORTED by id, counted,
                                                     not blocking
    an advisory on a package AND version that
    requirements/runtime.txt also pins               FAILS: the product shares
                                                     it (80's condition 3)
    a dependency pip-audit skipped, or none audited  FAILS

and it prints one summary line, `semgrep lock: N advisories (advisory, not
blocking; #82)`, which `check.py` repeats in its summary (condition 2).

**An audit that can't run FAILS; it never reports empty** (condition 2):
exit 2 when pip-audit wrote no readable report, exited with a code it hasn't
been measured to use, wrote a report of an unmeasured shape, or wrote one
that disagrees with its own exit code.

SCOPE, pinned by path (condition 1). Only `requirements/semgrep/semgrep.txt`
is advisory, and this script audits nothing else. Every other audit (the
project environment in `check.py` and CI, the runtime lock in
`ci_in_docker.py`) is plain `pip-audit` and still blocks.

It's temporary: #83 (1.8) runs semgrep from a pinned container image, and
the PR that lands it removes this advisory mode.

EXIT: 0 no blocking finding (advisories may be listed); 1 a blocking
finding, each named; 2 the audit couldn't run or its report can't be trusted.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The ONE lock audited in advisory mode (80's condition 1).
LOCK = REPO_ROOT / "requirements" / "semgrep" / "semgrep.txt"
#: What the installed product pins: an advisory it shares stays blocking.
RUNTIME_LOCK = REPO_ROOT / "requirements" / "runtime.txt"
#: The issue the summary line names.
ISSUE = "#82"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned(lock_text: str) -> dict[str, set[str]]:
    """{normalised package: every version pinned} for each `name==version`.

    A SET per name, because a universal lock can pin one package twice under
    different markers (`pyjwt==2.13.0 ; python_full_version < '3.12'`, then
    2.14.0 for the rest). Keeping only the last pin would read the product as
    installing 2.14.0 alone, and a shared advisory on 2.13.0 would pass as
    advisory: the unsafe direction of 80's condition 3 (ba on #84, #85)."""
    pins: dict[str, set[str]] = {}
    for line in lock_text.splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", line)
        if match:
            pins.setdefault(_norm(match.group(1)), set()).add(match.group(2))
    return pins


def _report_problem(report: object) -> str | None:
    """Why a pip-audit JSON report can't be read as one, or None: the shape
    measured on 2.10.1 is a mapping whose `dependencies` is a list of
    mappings, each with an optional `vulns` list of mappings."""
    if not isinstance(report, dict):
        return f"is a {type(report).__name__}, not a mapping"
    deps = report.get("dependencies")
    if not isinstance(deps, list):
        return f"has dependencies as {type(deps).__name__}, not a list"
    for dep in deps:
        if not isinstance(dep, dict):
            return f"has a dependency that is a {type(dep).__name__}, not a mapping"
        vulns = dep.get("vulns", [])
        if not isinstance(vulns, list) or not all(isinstance(v, dict) for v in vulns):
            return f"has vulns for {dep.get('name', '?')!r} that are not a list of mappings"
    return None


def classify(report: dict, runtime_pins: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    """(blocking, advisories) for a pip-audit JSON report of semgrep's lock.
    Every vulnerability lands in exactly one of the two."""
    blocking: list[str] = []
    advisories: list[str] = []
    deps = report.get("dependencies", [])
    if not deps:
        blocking.append("pip-audit audited ZERO dependencies: a broken run, not a clean lock")
    for dep in deps:
        name, version = _norm(dep.get("name", "")), dep.get("version", "?")
        if dep.get("skip_reason"):
            blocking.append(f"NOT AUDITED  {name}: {dep['skip_reason']}")
        for vuln in dep.get("vulns", []):
            vuln_id = vuln.get("id", "?")
            fixes = ", ".join(vuln.get("fix_versions", [])) or "none"
            line = f"{name} {version}: {vuln_id} (fixed in {fixes})"
            if version in runtime_pins.get(name, set()):
                blocking.append(
                    f"SHARED WITH THE PRODUCT  {line}: requirements/runtime.txt pins "
                    f"{name} {version} too, so this blocks"
                )
            else:
                advisories.append(f"ADVISORY  {line}")
    return blocking, advisories


def summary_line(advisories: list[str]) -> str:
    """The line `check.py` repeats in its summary (80's condition 2)."""
    return f"semgrep lock: {len(advisories)} advisories (advisory, not blocking; {ISSUE})"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="semgrep-lock-audit-") as scratch:
        out = Path(scratch) / "report.json"
        cmd = [
            sys.executable,
            "-m",
            "pip_audit",
            "--disable-pip",
            "--require-hashes",
            "-r",
            str(LOCK),
            "-f",
            "json",
            "-o",
            str(out),
        ]
        # pip-audit 2.10.1's exit codes, measured (#74): 0 for a clean audit
        # AND for one whose only problem is a skipped dependency (a report is
        # written; `classify` fails the skip); 1 for vulnerabilities found
        # (a report is written) AND for an input error (a missing file, a pin
        # with no hash: NO report, so this exits 2 below). No other code was
        # seen, and a report beside one is not trusted.
        done = subprocess.run(cmd, cwd=REPO_ROOT, check=False)
        if done.returncode not in (0, 1):
            print(
                f"semgrep_lock_audit: pip-audit exited {done.returncode}, which it has not "
                "been measured to use; its report is not trusted",
                file=sys.stderr,
            )
            return 2
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(
                f"semgrep_lock_audit: COULD NOT READ pip-audit's report (exit {done.returncode}): "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 2
    shape = _report_problem(report)
    if shape:
        # Checked before ANY line reads the report, so a shape pip-audit was
        # not seen to write is exit 2, never a crash reading as exit 1 (b5 on
        # #76).
        print(f"semgrep_lock_audit: pip-audit's report {shape}; not trusted", file=sys.stderr)
        return 2
    found = any(dep.get("vulns") for dep in report["dependencies"])
    if found != (done.returncode == 1):
        # Measured: exit 1 with a report always listed a vulnerability, and
        # exit 0 never did. A report that disagrees with its own exit code
        # is a shape nobody has seen, so it is not trusted (b5 on #76).
        print(
            f"semgrep_lock_audit: pip-audit exited {done.returncode} but its report lists "
            f"{'a' if found else 'no'} vulnerability; the two disagree, so neither is trusted",
            file=sys.stderr,
        )
        return 2
    try:
        blocking, advisories = classify(report, pinned(RUNTIME_LOCK.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - a crash must not read as exit 1, a verdict
        print(
            f"semgrep_lock_audit: COULD NOT CLASSIFY the report: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(
        f"pip-audit over {LOCK.relative_to(REPO_ROOT).as_posix()}: "
        f"{len(report['dependencies'])} dependencies"
    )
    for line in advisories + blocking:
        print(f"  {line}")
    print(summary_line(advisories))
    if blocking:
        print(f"semgrep_lock_audit: {len(blocking)} blocking problem(s); the step FAILS")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
