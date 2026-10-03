"""Refuse a gate run on libraries other than the ones CI installs (#44).

    python scripts/lock_drift.py [--semgrep PATH]

CI installs `requirements/ci.txt`, a hashed lock, and semgrep from its own
lock, `requirements/semgrep/semgrep.txt`. `scripts/check.py` runs in whatever
environment the caller has, and the shared `.venv` drifted: b5 measured 34
of 126 pins different and 7 missing (#44), and the gate's semgrep was 1.174.0
where CI's is 1.177.0 (1d). So a local exit 0 and a CI green were answers
about different libraries, which is how #31's 87 CI failures stayed
invisible locally. This makes the gate say which libraries it measured, and
refuse when they are not CI's.

WHAT IT COMPARES. Every pin in `requirements/ci.txt` whose environment
marker holds for this interpreter, against `importlib.metadata`, by
canonical name. Derived from the whole lock, never from a list of packages
someone judged important. Each pin is classified, so none can drop out:

    same            installed at the locked version
    DIFFERENT       installed at another version       -> refused
    MISSING         not installed                      -> refused
    not for here    its marker is false on this platform (e.g. pywin32 on
                    Linux), so CI on this platform would not install it

Installed packages the lock doesn't name are counted and listed, not refused:
the project itself, and semgrep with its dependencies when semgrep lives in
the same environment.

SEMGREP is compared by its OWN version only, against `semgrep==` in its lock.
Its dependencies are pinned for a separate environment and may legitimately
disagree with `ci.txt`, so comparing them here would refuse a correct setup.

EXIT: 0 every applicable pin is the same; 1 drift, every difference named;
2 nothing to compare (no pins parsed, or semgrep's version unreadable).
Zero pins parsed is a broken parse, not a clean environment.

WHAT IT CANNOT SEE: a package installed at the locked version from other
bytes (the lock's hashes are not re-checked here); an interpreter different
from CI's 3.12 (markers are evaluated for THIS interpreter); and gitleaks,
which is a binary pinned by version in `ci.yml` (`GITLEAKS_VERSION`), not a
package in either lock, so a local gitleaks of another version is not
refused (ba on #46).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

try:
    from packaging.markers import Marker
    from packaging.utils import canonicalize_name
except ImportError:  # it is in the lock; without it nothing here can be measured
    print(
        "lock_drift: `packaging` is not installed, so nothing could be compared. That is\n"
        "an environment not built from requirements/ci.txt, not a clean one.",
        file=sys.stderr,
    )
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_LOCK = REPO_ROOT / "requirements" / "ci.txt"
SEMGREP_LOCK = REPO_ROOT / "requirements" / "semgrep" / "semgrep.txt"

#: A pin line as `uv pip compile --generate-hashes` writes it:
#: `name==version`, an optional `; marker`, then ` \` before the hashes.
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?\s*$")


class NothingToCompare(RuntimeError):
    """The lock gave no pins, or a version could not be read: exit 2."""


@dataclass
class Report:
    same: list[str] = field(default_factory=list)
    different: dict[str, tuple[str, str]] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    not_here: list[str] = field(default_factory=list)
    unlocked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.different and not self.missing


def pins(text: str) -> dict[str, tuple[str, str | None]]:
    """{canonical name: (version, marker or None)} for every pin line."""
    found: dict[str, tuple[str, str | None]] = {}
    for line in text.splitlines():
        m = _PIN.match(line)
        if m:
            found[canonicalize_name(m.group(1))] = (m.group(2), m.group(3))
    return found


def installed(distributions=None) -> dict[str, str]:
    """{canonical name: version} of the running interpreter's environment.

    When a name is installed twice, the FIRST on `sys.path` is kept, because
    that is the one an import and `metadata.version()` resolve to. Keeping the
    last let a stale copy found first hide behind a correct one found later:
    lock_drift said exit 0 while `metadata.version('click')` was 0.0.1 (ba on
    #46)."""
    found: dict[str, str] = {}
    for d in metadata.distributions() if distributions is None else distributions:
        if d.metadata["Name"]:
            found.setdefault(canonicalize_name(d.metadata["Name"]), d.version)
    return found


def classify(
    locked: dict[str, tuple[str, str | None]], have: dict[str, str], env: dict | None = None
) -> Report:
    if not locked:
        raise NothingToCompare("the lock gave ZERO pins: a broken parse, not a clean environment")
    report = Report()
    for name, (version, marker) in sorted(locked.items()):
        if marker and not Marker(marker).evaluate(env):
            report.not_here.append(name)
        elif name not in have:
            report.missing[name] = version
        elif have[name] != version:
            report.different[name] = (version, have[name])
        else:
            report.same.append(name)
    report.unlocked = sorted(set(have) - set(locked))
    return report


def semgrep_version(exe: str) -> str:
    out = subprocess.run(  # nosec B603: our own tool, argv list, no shell
        [exe, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    lines = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    version = lines[-1] if lines else ""
    if not re.fullmatch(r"\d+(\.\d+)+", version):
        raise NothingToCompare(f"could not read semgrep's version from {exe!r}: {version!r}")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--semgrep", help="the semgrep the gate will run; omitted: not compared")
    args = parser.parse_args(argv)
    try:
        report = classify(pins(CI_LOCK.read_text(encoding="utf-8")), installed())
        semgrep = None
        if args.semgrep:
            want = pins(SEMGREP_LOCK.read_text(encoding="utf-8")).get("semgrep")
            if want is None:
                raise NothingToCompare(f"{SEMGREP_LOCK.name} pins no semgrep")
            semgrep = (want[0], semgrep_version(args.semgrep))
    except NothingToCompare as exc:
        print(f"lock_drift: {exc}", file=sys.stderr)
        return 2

    applicable = len(report.same) + len(report.different) + len(report.missing)
    print(
        f"{CI_LOCK.name}: {applicable} pin(s) for this platform: {len(report.same)} same, "
        f"{len(report.different)} DIFFERENT, {len(report.missing)} MISSING; "
        f"{len(report.not_here)} not for this platform; "
        f"{len(report.unlocked)} installed and not in the lock"
    )
    for name, (want, have) in report.different.items():
        print(f"  DIFFERENT  {name}  lock {want}, installed {have}")
    for name, want in report.missing.items():
        print(f"  MISSING    {name}  lock {want}")
    if report.unlocked:
        print(f"  not in the lock (listed, not refused): {', '.join(report.unlocked)}")
    drift = not report.ok
    if semgrep:
        want, have = semgrep
        same = want == have
        print(f"semgrep: lock {want}, the gate's {have}: {'same' if same else 'DIFFERENT'}")
        drift = drift or not same
    if drift:
        print(
            "This environment is not CI's, so the gate would measure other libraries.\n"
            "Re-install from the lock when no run depends on this environment:\n"
            "  pip install --require-hashes -r requirements/ci.txt && pip install --no-deps -e .\n"
            "and semgrep from requirements/semgrep/semgrep.txt (see check.py's docstring)."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
