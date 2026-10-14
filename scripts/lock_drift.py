"""Refuse a gate run on libraries other than the ones CI installs (#44).

    python scripts/lock_drift.py

CI installs `requirements/ci.txt`, a hashed lock. `scripts/check.py` runs in
whatever environment the caller has, and the shared `.venv` drifted: b5
measured 34 of 126 pins different and 7 missing (#44), and the gate's semgrep
was 1.174.0 where CI's was 1.177.0 (1d). So a local exit 0 and a CI green were answers
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

An installed package the lock doesn't name is an EXTRA, and REFUSED (#116),
unless it is in `ALLOWED_EXTRAS` (the project itself, and `uv`). The gate
audits the installed environment, so an extra is a package CI never sees: a
stray pypdf with new advisories turned every local gate red on 2026-10-02.
semgrep's dependencies are extras too if semgrep is installed here. It isn't
installed anywhere now: the gate and CI run one container image, pinned by
digest (`semgrep_gate.SEMGREP_IMAGE`, #83), so its version can't drift from
CI's and there is nothing of it to compare here.

EXIT: 0 every applicable pin is the same; 1 drift, every difference named;
2 nothing to compare (no pins parsed).
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
#: Installed but in no lock, and allowed (#116). Anything else installed is an
#: EXTRA and refused: the gate audits the installed environment, so a stray
#: package (pypdf 6.16.2, on 2026-10-02) turned every local gate red for a reason
#: CI, built from the locks, never sees.
#:   policyforge  the project itself, installed editable
#:   uv           the tool that writes these locks (`uv pip compile`)
#: Not `gh`: that pip package is unrelated to GitHub CLI and shadows it
#: (CLAUDE.md, "gh is shadowed by the venv"), so it is removed, not allowed.
ALLOWED_EXTRAS = frozenset({"policyforge", "uv"})
CI_LOCK = REPO_ROOT / "requirements" / "ci.txt"

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
    unallowed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.different and not self.missing and not self.unallowed


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
    report.unallowed = sorted(set(report.unlocked) - ALLOWED_EXTRAS)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    try:
        report = classify(pins(CI_LOCK.read_text(encoding="utf-8")), installed())
    except NothingToCompare as exc:
        print(f"lock_drift: {exc}", file=sys.stderr)
        return 2

    applicable = len(report.same) + len(report.different) + len(report.missing)
    print(
        f"{CI_LOCK.name}: {applicable} pin(s) for this platform: {len(report.same)} same, "
        f"{len(report.different)} DIFFERENT, {len(report.missing)} MISSING; "
        f"{len(report.not_here)} not for this platform; "
        f"{len(report.unallowed)} EXTRA"
    )
    for name, (want, have) in report.different.items():
        print(f"  DIFFERENT  {name}  lock {want}, installed {have}")
    for name, want in report.missing.items():
        print(f"  MISSING    {name}  lock {want}")
    for name in report.unallowed:
        print(f"  EXTRA      {name}  installed and not in the lock")

    allowed = [u for u in report.unlocked if u in ALLOWED_EXTRAS]
    if allowed:
        print(f"  not in the lock (allowed): {', '.join(allowed)}")
    drift = not report.ok
    if drift:
        print(
            "This environment is not CI's, so the gate would measure other libraries.\n"
            "Re-install from the lock when no run depends on this environment:\n"
            "  pip install --require-hashes -r requirements/ci.txt"
            " && pip install --require-hashes -r requirements/build.txt"
            " && pip install --no-deps --no-build-isolation -e ."
        )
        if report.unallowed:
            # An install can't remove a package, so name the command that does.
            print(
                "An EXTRA is removed, not re-installed (say first if anyone uses it):\n"
                f"  pip uninstall -y {' '.join(report.unallowed)}"
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
