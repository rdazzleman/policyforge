#!/usr/bin/env python3
"""The candidate's Homebrew formula, with its `resource` blocks made from the lock.

The macOS job on the private test repo builds a formula for the CANDIDATE and
installs it from source on a real Mac (#381). A Homebrew `resource` needs an
sdist's url and the sha256 of that file; `requirements/runtime.txt` has the
pin and its hashes, wheels and sdist mixed, and no urls. So for each pin that
a Mac installs from the base dependencies, this asks PyPI for that exact name
and version, takes the sdist's url and sha256 from the answer, and **refuses**
unless that sha256 is one of the pin's hashes in the lock. PyPI supplies the
url; the lock decides (CONTRIBUTING, the release steps).

Refused, never worked around (80's conditions on #381):

- a pin with no sdist, and a pin with more than one: **no wheel fallback**;
- an sdist whose sha256 the lock doesn't list for that pin;
- a marker that gives a different answer on a Mac's two architectures, or
  depends on what isn't known about the Mac (`macos_wheels.Unclear`);
- an answer that doesn't name `files.pythonhosted.org`.

Which pins: the ones whose origin reaches a direct dependency of
`pyproject.toml` (`macos_wheels.origins`), and that hold on a Mac under every
Python the formula depends on. Extras' pins (mcp) are not in the formula, as
in the published one: the formula `depends_on` libraries Homebrew supplies
(certifi) and those get no resource. Measured on the 1.7.0 formula and this
lock: 36 pins reach the base dependencies on a Mac; 35 are the published
formula's resources and the 36th is certifi.

Output is byte-stable: blocks sorted by name, LF, no timestamps.

    python scripts/formula_resources.py --lock requirements/runtime.txt \\
        --pyproject pyproject.toml --tap --archive /tmp/p.tar.gz \\
        --version 1.8.0 --out /tmp/policyforge.rb

Not used for the release formula (80, #381): that is a later question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import macos_wheels as mw
import release
import release_check

REPO_ROOT = Path(__file__).resolve().parent.parent

#: PyPI's JSON for one release. The only network read this makes besides the
#: tap's formula.
PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"

#: Where an sdist's url must live. A url anywhere else is refused, so a
#: changed answer can't point a build at another host.
PYTHONHOSTED = "https://files.pythonhosted.org/"

#: The architectures a Mac is, for the markers the lock carries.
MACHINES = ("arm64", "x86_64")

_SHA256 = re.compile(r"[0-9a-f]{64}")
_DEPENDS_ON = re.compile(r'^\s*depends_on "([^"]+)"', re.MULTILINE)
_RESOURCE_BLOCK = re.compile(r'^  resource "[^"]+" do\n.*?^  end\n\n?', re.MULTILINE | re.DOTALL)

Fetch = Callable[[str, str], dict]


class Refused(Exception):
    """The formula can't be made from this lock. Exit 1, nothing written."""


def fetch_pypi(name: str, version: str) -> dict:
    """PyPI's JSON for `name==version`. A failure is a refusal that says so."""
    url = PYPI_JSON.format(name=name, version=version)
    try:
        # https to a fixed host; `name` and `version` come from a parsed lock pin
        # (macos_wheels.parse_lock accepts only `name==version`). Both
        # suppressions sit on the line: semgrep honours `nosemgrep` there only.
        with urllib.request.urlopen(url, timeout=30) as response:  # nosec B310  # nosemgrep
            return json.load(response)
    except Exception as exc:
        raise Refused(
            f"{name}=={version}: PyPI could not be read ({type(exc).__name__}: {exc})"
        ) from exc


def wanted_pins(lock_text: str, pyproject_text: str, formula: str) -> list[mw.Pin]:
    """The pins that become resources, sorted by name."""
    try:
        pins = mw.parse_lock(lock_text)
        direct, extras = mw.pyproject_names(pyproject_text)
        origin = mw.origins(pins, direct, extras)
        pythons = mw.formula_pythons(formula)
        homebrew = {mw.canonical(name) for name in _DEPENDS_ON.findall(formula)}
        chosen = []
        for pin in pins:
            holds = {mw.on_a_mac(pin, py, machine) for py in pythons for machine in MACHINES}
            if len(holds) > 1:
                raise Refused(f"{pin.spec}: its marker differs between a Mac's architectures")
            reaches_base = "base" in origin[pin.name].split(" (", 1)[0].split(", ")
            if holds == {True} and reaches_base and pin.name not in homebrew:
                chosen.append(pin)
    except mw.Unclear as exc:
        raise Refused(str(exc)) from exc
    if not chosen:
        raise Refused("no pin reaches the base dependencies on a Mac: the lock or pyproject moved")
    return sorted(chosen, key=lambda pin: pin.name)


def sdist_for(pin: mw.Pin, fetch: Fetch) -> tuple[str, str]:
    """(url, sha256) of the pin's one sdist, if the lock lists its hash."""
    name, _, version = pin.spec.partition("==")
    data = fetch(name, version)
    sdists = [u for u in data.get("urls", []) if u.get("packagetype") == "sdist"]
    if not sdists:
        raise Refused(f"{pin.spec}: PyPI lists no sdist for it (no wheel fallback)")
    if len(sdists) > 1:
        raise Refused(f"{pin.spec}: PyPI lists {len(sdists)} sdists for it; which is the lock's?")
    url = str(sdists[0].get("url", ""))
    digest = str(sdists[0].get("digests", {}).get("sha256", ""))
    if not url.startswith(PYTHONHOSTED):
        raise Refused(f"{pin.spec}: its sdist url is not on {PYTHONHOSTED}: {url[:80]}")
    if not _SHA256.fullmatch(digest):
        raise Refused(f"{pin.spec}: its sdist's sha256 is not a sha256: {digest[:20]!r}")
    if f"sha256:{digest}" not in pin.hashes:
        raise Refused(
            f"{pin.spec}: its sdist's sha256 is not one of the lock's hashes for this pin"
        )
    return url, digest


def resource_blocks(pins: list[mw.Pin], fetch: Fetch) -> str:
    """The `resource` blocks for `pins`, in name order, as brew writes them."""
    blocks = []
    for pin in pins:
        url, digest = sdist_for(pin, fetch)
        blocks.append(
            f'  resource "{pin.name}" do\n    url "{url}"\n    sha256 "{digest}"\n  end\n'
        )
    return "\n".join(blocks)


def with_resources(formula: str, blocks: str) -> str:
    """`formula` (LF) with every existing `resource` block replaced by `blocks`,
    placed before `def install`."""
    text = formula.replace("\r\n", "\n")
    text = _RESOURCE_BLOCK.sub("", text)
    marker = "  def install\n"
    if text.count(marker) != 1:
        raise Refused("the base formula has no single `def install` to put the resources before")
    return text.replace(marker, blocks + "\n" + marker, 1)


def candidate_formula(
    base: str,
    lock_text: str,
    pyproject_text: str,
    archive: Path,
    version: str,
    fetch: Fetch | None = None,
) -> str:
    """The base formula with the lock's resources, and the candidate archive's
    `file://` url and sha256 (set by `release._formula`, the one definition)."""
    # Looked up at call time, so a test can replace the one network read.
    blocks = resource_blocks(wanted_pins(lock_text, pyproject_text, base), fetch or fetch_pypi)
    text = with_resources(base, blocks)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "base.rb"
        path.write_text(text, encoding="utf-8", newline="\n")
        ctx = SimpleNamespace(formula_path=path, version=version)
        return release._formula(ctx, archive.resolve().as_uri(), digest)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lock", type=Path, default=REPO_ROOT / "requirements" / "runtime.txt")
    parser.add_argument("--pyproject", type=Path, default=REPO_ROOT / "pyproject.toml")
    base = parser.add_mutually_exclusive_group(required=True)
    base.add_argument("--base", type=Path, help="a formula file to edit")
    base.add_argument("--tap", action="store_true", help="the public tap's formula")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        base_text = (
            args.base.read_text(encoding="utf-8") if args.base else release_check.fetch_formula()
        )
        formula = candidate_formula(
            base_text,
            args.lock.read_text(encoding="utf-8"),
            args.pyproject.read_text(encoding="utf-8"),
            args.archive,
            args.version,
        )
    except Refused as exc:
        print(f"formula_resources: refused: {exc}", file=sys.stderr)
        return 1
    args.out.write_text(formula, encoding="utf-8", newline="\n")
    print(f"formula_resources: wrote {args.out} ({formula.count('  resource ')} resources)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
