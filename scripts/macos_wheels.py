"""Every runtime pin a Mac would install has a hashed wheel for it (#325).

macOS can't run in a container (Apple licenses it only on Apple hardware), so
this checks, from any host, what most often breaks a pure-Python tool's
`pip install` on a Mac: a dependency with no wheel for the platform. It is
part A of #165, as the user decided it, with 80's ruling on the targets
(#325, 5263).

For each Python version the Homebrew formula targets (its
`depends_on "python@X.Y"` lines), it selects the pins of
`requirements/runtime.txt` whose markers hold ON A MAC, then asks pip itself:
`pip download --no-deps --only-binary :all: --require-hashes` for each
platform below. Markers are evaluated here, for the target, because pip
evaluates them against the interpreter running it: on Windows it would pull
`pywin32 ; sys_platform == 'win32'` and fail on it, and on Linux it would
skip anything darwin-only. pip expands each platform tag to every tag that
machine accepts (`universal2`, older floors), so a tag below is a floor, not
a list.

    macosx_11_0_arm64    BLOCKING. 11.0 is the first macOS on Apple silicon,
                         so no arm64 Mac has an older floor.
    macosx_10_15_x86_64  ADVISORY: named, counted, never failing (80's
                         ruling). Upstream cryptography ships no Intel-Mac
                         wheel from 49.0.0, and holding a crypto library back
                         is worse than the gap; Intel Macs still install
                         through brew and through pip without the extra that
                         pulls it in. 10.15 is cffi 2.1.1's own floor.

What this does NOT cover, and the report says so: `brew install` (the formula
builds every resource from its sdist), and macOS runtime behaviour (APFS
normalisation, BSD tools, Apple's Python), which needs a real Mac (#165
part B). Case-insensitive filenames are exercised by every local gate on
Windows already.

Exit 0: every blocking platform has a wheel for every pin (advisories may be
listed). Exit 1: a blocking platform misses one, by name. Exit 2: it couldn't
tell (an unreadable lock, formula or pyproject; a marker that depends on what
can't be known about the Mac; a pip failure that isn't "no such wheel").
Never a pass.

    python scripts/macos_wheels.py [--formula PATH]   # default: the published formula
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK = REPO_ROOT / "requirements" / "runtime.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: What `# via` says of a direct dependency in a uv lock.
ROOT = "policyforge (pyproject.toml)"


@dataclass(frozen=True)
class Target:
    platform: str
    machine: str
    blocking: bool


TARGETS = (
    Target("macosx_11_0_arm64", "arm64", blocking=True),
    Target("macosx_10_15_x86_64", "x86_64", blocking=False),
)

COVERS = [
    "covers: pip installing the runtime lock on a Mac, wheel by wheel, hashes checked",
    "not covered: brew install (the formula builds every resource from its sdist), and "
    "macOS runtime behaviour (APFS normalisation, BSD tools, Apple's Python): a real Mac "
    "runs those (the private GitHub CI repo). Case-insensitive filenames: every local gate "
    "on Windows exercises them.",
]


class Unclear(Exception):
    """The check can't tell: exit 2, never a pass."""


@dataclass(frozen=True)
class Pin:
    name: str  # canonical
    spec: str  # name==version, as the lock writes it
    marker: str
    hashes: tuple[str, ...]
    parents: tuple[str, ...]  # canonical names, or ROOT

    def requirement(self) -> str:
        return " ".join([self.spec, *(f"--hash={h}" for h in self.hashes)])


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$")


def parse_lock(text: str) -> list[Pin]:
    """Every pin in a `uv pip compile --generate-hashes` lock, with its marker,
    hashes and `# via` parents. Parsed line by line, and the extent checked:
    every unindented, non-comment line must be a pin, or this refuses."""
    lines = text.replace("\r\n", "\n").split("\n")
    pins: list[Pin] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith("#"):
            i += 1
            continue
        if line[0].isspace():
            raise Unclear(f"lock line {i + 1} is indented outside a pin: {line.strip()[:60]}")
        logical = line
        while logical.endswith("\\"):
            i += 1
            logical = logical[:-1] + " " + lines[i].strip()
        head, _, rest = logical.partition(" --hash=")
        hashes = tuple(h.strip() for h in ("--hash=" + rest).split("--hash=") if h.strip())
        spec, _, marker = head.partition(";")
        match = _PIN.match(spec.strip())
        if not match or not hashes:
            raise Unclear(f"lock line {i + 1} isn't a hashed name==version pin: {head[:60]}")
        i += 1
        parents: list[str] = []
        if i < len(lines) and lines[i].strip().startswith("# via"):
            first = lines[i].strip()[len("# via") :].strip()
            if first:
                parents.append(first)
            i += 1
            while i < len(lines) and lines[i].strip().startswith("#   "):
                parents.append(lines[i].strip()[1:].strip())
                i += 1
        pins.append(
            Pin(
                name=canonical(match.group(1)),
                spec=spec.strip(),
                marker=marker.strip(),
                hashes=hashes,
                parents=tuple(p if p == ROOT else canonical(p) for p in parents),
            )
        )
    if not pins:
        raise Unclear("the lock holds no pins")
    return pins


def formula_pythons(formula: str) -> list[str]:
    found = re.findall(r'^\s*depends_on "python@(\d+\.\d+)"', formula, flags=re.M)
    if not found:
        raise Unclear('the formula has no depends_on "python@X.Y" line')
    return sorted(set(found), key=lambda v: tuple(int(p) for p in v.split(".")))


def _mac(python: str, machine: str, unknown: str) -> dict[str, str]:
    """A Mac's marker environment. EVERY key is given, because `Marker.evaluate`
    fills a missing one from the HOST. What a Mac's can't be known here (its
    release, kernel version and patch level) is `unknown`, so evaluating with
    two different values shows whether a marker depends on it."""
    patch = {"low": ".0", "high": ".99"}[unknown]
    return {
        "implementation_name": "cpython",
        "implementation_version": python + patch,
        "os_name": "posix",
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "platform_release": {"low": "0", "high": "99.0"}[unknown],
        "platform_system": "Darwin",
        "platform_version": {"low": "", "high": "Darwin Kernel Version 99.0"}[unknown],
        "python_full_version": python + patch,
        "python_version": python,
        "sys_platform": "darwin",
        "extra": "",
    }


def on_a_mac(pin: Pin, python: str, machine: str) -> bool:
    if not pin.marker:
        return True
    from packaging.markers import Marker

    marker = Marker(pin.marker)
    low, high = (marker.evaluate(_mac(python, machine, u)) for u in ("low", "high"))
    if low != high:
        raise Unclear(
            f"{pin.spec}: its marker depends on what isn't known about the Mac: {pin.marker}"
        )
    return low


def origins(pins: list[Pin], dependencies: set[str], extras: dict[str, set[str]]) -> dict[str, str]:
    """For each pin, what pulls it in: `base`, or `extra X` (several, sorted),
    with the shortest chain up to a direct dependency, e.g.
    `extra mcp (cryptography <- pyjwt <- mcp)`."""
    by_name = {p.name: p for p in pins}
    out: dict[str, str] = {}
    for pin in pins:
        chains: list[list[str]] = []
        frontier, seen = [[pin.name]], {pin.name}
        while frontier:
            step = []
            for chain in frontier:
                node = by_name.get(chain[-1])
                for parent in node.parents if node else ():
                    if parent == ROOT:
                        chains.append(chain)
                    elif parent not in seen:
                        seen.add(parent)
                        step.append([*chain, parent])
            frontier = step
        kinds: set[str] = set()
        for chain in chains:
            top = chain[-1]
            if top in dependencies:
                kinds.add("base")
            kinds.update(f"extra {x}" for x, names in extras.items() if top in names)
        shortest = min(chains, key=len) if chains else [pin.name]
        label = ", ".join(sorted(kinds)) or "nothing the pyproject names"
        out[pin.name] = f"{label} ({' <- '.join(shortest)})" if len(shortest) > 1 else label
    return out


def pyproject_names(text: str) -> tuple[set[str], dict[str, set[str]]]:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: the CI locks carry tomli
        import tomli as tomllib
    from packaging.requirements import Requirement

    project = tomllib.loads(text)["project"]
    names = lambda reqs: {canonical(Requirement(r).name) for r in reqs}  # noqa: E731
    return names(project.get("dependencies", [])), {
        x: names(reqs) for x, reqs in project.get("optional-dependencies", {}).items()
    }


#: pip's two ways of saying no distribution fits, then a hash the lock lacks.
_NO_WHEEL = re.compile(r"No matching distribution found for ([A-Za-z0-9._-]+)==")
_NETWORK = re.compile(
    r"NewConnectionError|Max retries exceeded|Could not fetch URL|ConnectionError"
)
_HASHES = "DO NOT MATCH THE HASHES"

Download = Callable[[list[str], str, str], subprocess.CompletedProcess]


def pip_download(index_args: tuple[str, ...] = ()) -> Download:
    """pip itself, as a Mac's pip would be asked: no deps (the lock is the
    whole set), wheels only, every hash checked."""

    def download(
        requirements: list[str], platform: str, python: str
    ) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("\n".join(requirements) + "\n", encoding="utf-8")
            argv = [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--no-deps",
                "--only-binary",
                ":all:",
                "--require-hashes",
                "--platform",
                platform,
                "--python-version",
                python,
                "--implementation",
                "cp",
                "--disable-pip-version-check",
                *index_args,
                "-r",
                str(req),
                "-d",
                str(Path(tmp) / "wheels"),
            ]
            return subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )

    return download


def misses(
    pins: list[Pin], target: Target, python: str, download: Download
) -> list[tuple[Pin, str]]:
    """Every pin pip finds no wheel for. pip stops at the first, so each one
    found is set aside and pip asked again: one call per miss, plus one."""
    left = {p.name: p for p in pins}
    found: list[tuple[Pin, str]] = []
    for _ in range(len(pins) + 1):
        done = download([p.requirement() for p in left.values()], target.platform, python)
        if done.returncode == 0:
            return found
        err = done.stderr or ""
        if _NETWORK.search(err):
            raise Unclear(
                f"pip couldn't reach the index for {target.platform}: {err.strip()[-300:]}"
            )
        if _HASHES in err:
            raise Unclear(
                f"a {target.platform} wheel's hash isn't in the lock: {err.strip()[-500:]}"
            )
        match = _NO_WHEEL.search(err)
        name = canonical(match.group(1)) if match else ""
        if name not in left:
            raise Unclear(
                f"pip exit {done.returncode} for {target.platform}, not a missing wheel: "
                f"{err.strip()[-500:]}"
            )
        found.append((left.pop(name), "no wheel"))
        if not left:
            return found
    raise Unclear("pip kept failing past one call per pin")


def check(
    lock: str, formula: str, pyproject: str, download: Download, targets=TARGETS
) -> tuple[int, list[str]]:
    """(exit code, report lines)."""
    try:
        pins = parse_lock(lock)
        pythons = formula_pythons(formula)
        dependencies, extras = pyproject_names(pyproject)
        whence = origins(pins, dependencies, extras)
        lines = [
            "macOS wheel readiness: every runtime pin a Mac would install, "
            "from requirements/runtime.txt",
            *COVERS,
        ]
        blocked = advisories = 0
        for python in pythons:
            for target in targets:
                chosen = [p for p in pins if on_a_mac(p, python, target.machine)]
                missed = misses(chosen, target, python, download)
                kind = "blocking" if target.blocking else "advisory"
                have = len(chosen) - len(missed)
                lines.append(
                    f"cp{python} {target.platform} ({kind}): {have} of {len(chosen)} pins "
                    f"have a wheel ({len(pins) - len(chosen)} don't apply on a Mac)"
                )
                for pin, why in missed:
                    flag = "MISSING" if target.blocking else "ADVISORY"
                    lines.append(f"    {flag} {pin.spec}: {why}; pulled in by {whence[pin.name]}")
                if target.blocking:
                    blocked += len(missed)
                else:
                    advisories += len(missed)
    except Unclear as why:
        return 2, [f"macOS wheel readiness: COULDN'T TELL, so not a pass: {why}"]
    lines.append(
        f"result: {'FAILED' if blocked else 'passed'}: {blocked} missing on a blocking "
        f"platform, {advisories} advisory"
    )
    return (1 if blocked else 0), lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--formula", type=Path, help="default: the published formula (release_check)"
    )
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--pyproject", type=Path, default=PYPROJECT)
    args = parser.parse_args(argv)
    try:
        if args.formula:
            formula = args.formula.read_text(encoding="utf-8")
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import release_check

            formula = release_check.fetch_formula()
        lock = args.lock.read_text(encoding="utf-8")
        pyproject = args.pyproject.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"macOS wheel readiness: COULDN'T TELL, so not a pass: {exc}")
        return 2
    code, lines = check(lock, formula, pyproject, pip_download())
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
