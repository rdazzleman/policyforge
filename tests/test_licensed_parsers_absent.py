"""The licensed parsers are not in this tree, and nothing here points at them (#32).

The owner's decision (2026-09-28): the HITRUST, GovRAMP and SOC 2 parsers
expose too much of what those frameworks contain to ship in the
Apache-licensed tool. Part 3 moved them to the separately distributed
licensed-framework plugin. This holds the tree to that: the modules can't
come back, and no code or doc may send a reader to a file that isn't here.

The moved modules are hand-written from part 3's diff, not derived from the
tree being checked. Whether the figures and titles from the owner's exports
are gone is checked by the plugin's own suite, because the list of what they
were is itself content (#32, 80's ruling 798).

**What it cannot see:** a moved module's code brought back under another
name, with its imports and pointers stripped. Nothing here names it, so
nothing matches. That is the publish guard's job (`scripts/publish_guard.py`
L2 byte-identical blobs, L3 licensed-only names), not this test's (1d, #50).
"""

from __future__ import annotations

import json
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Moved by #32 part 3 (and `byoc_loader`, their shared entry point, and the
#: in-tree stand-in that adapted them to the plugin contract).
MOVED = (
    "hitrust",
    "hitrust_export",
    "govramp",
    "govramp_export",
    "soc2_tsc",
    "byoc_loader",
    "licensed_builtin",
)
#: Functions that lived only in those modules.
MOVED_FUNCTIONS = ("load_hitrust_export", "load_govramp_export")

#: A pointer to a moved module: its path under `ingest/`, its dotted name
#: under `policyforge.ingest`, or a from-import of the package naming it among
#: others (the form this missed until a full run found two). Anywhere in the
#: tree. (Spelled here without a moved name: the publish guard refuses any
#: public blob whose text does, comments included.)
_MOVED = "|".join(MOVED)
POINTER = re.compile(
    rf"ingest[/.](?:{_MOVED})\b(?:\.py)?"
    rf"|from\s+policyforge\.ingest\s+import\s+[^\n]*?(?<![\w])(?:{_MOVED})(?![\w])"
)
#: A moved function's name. Outside `tests/` only: in a test it is the name a
#: GENERATED parser defines (`generate-parser` asks for `load_hitrust_export`),
#: which is public. So a test that calls the moved function is found by its
#: import, not its name; one that only names it in prose is not found.
FUNCTION_POINTER = re.compile(r"\b(?:" + "|".join(MOVED_FUNCTIONS) + r")\b")

#: 80's ruling (#40, #32 comment 798): the changelog records releases already
#: public, as published.
HISTORY = {"CHANGELOG.md"}
#: This file and its plant fixture name them to forbid them. The plants are
#: data, not strings in this file, so #37's publish step (which reads *.py)
#: does not classify this test as carrying an import and drop it from P.
PLANTS = ROOT / "tests" / "fixtures" / "licensed_absent_plants.json"
SELF = {"tests/test_licensed_parsers_absent.py", "tests/fixtures/licensed_absent_plants.json"}
#: Files that name them on purpose, keyed by file with the reason. Each must
#: still hold a pointer (test below), so an entry can't outlive its reason.
PLANTED = {
    "tests/test_publish_guard.py": (
        "plants licensed-parser paths to prove the publish guard refuses them"
    ),
    "tests/test_release_public.py": (
        "plants licensed-module imports in synthetic repos to prove the public "
        "build's classifier refuses them (#37)"
    ),
    "tests/test_public_rehearsal.py": (
        "plants a licensed parser and its importers in a synthetic history to prove "
        "the pre-cut rehearsal builds P without them and refuses P with them (#49)"
    ),
}


def test_no_moved_module_is_importable():
    """Asked of the package's own module listing, not with a loader call: a
    public test that loads a licensed module by a computed name is one the
    publish step removes (#37), and this one has to ship."""
    import policyforge.ingest

    present = {m.name for m in pkgutil.iter_modules(policyforge.ingest.__path__)}
    # Its public neighbours must be listed, or the listing is broken, not clean.
    neighbours = {"levelled", "schema", "parser_codegen", "parser_gate", "nist_rev4_rev5"}
    assert neighbours <= present, sorted(neighbours - present)
    assert present.isdisjoint(MOVED), sorted(present & set(MOVED))


def test_no_file_of_that_name_is_anywhere_in_src():
    names = {f"{m}.py" for m in MOVED}
    found = [
        p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py") if p.name in names
    ]
    assert found == []


def _tree() -> dict[str, str]:
    """Every tracked or new (not ignored) text file: what the next commit publishes."""
    names = (
        subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
        )
        .stdout.decode("utf-8")
        .split("\0")
    )
    texts = {}
    for name in filter(None, names):
        try:
            texts[name] = (ROOT / name).read_bytes().decode("utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
    return texts


def pointers(texts: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    """Every pointer found, classified: none is dropped."""
    found: dict[str, list[tuple[str, str]]] = {"history": [], "self": [], "planted": [], "live": []}
    for name, text in sorted(texts.items()):
        kind = (
            "history"
            if name in HISTORY
            else "self"
            if name in SELF
            else "planted"
            if name in PLANTED
            else "live"
        )
        found[kind].extend((name, m.group(0)) for m in POINTER.finditer(text))
        if not name.startswith("tests/"):
            found[kind].extend((name, m.group(0)) for m in FUNCTION_POINTER.finditer(text))
    return found


def test_nothing_in_the_tree_points_at_a_moved_module():
    texts = _tree()
    assert len(texts) > 500, f"only {len(texts)} text files: a broken listing, not a clean tree"
    assert pointers(texts)["live"] == []


def test_every_planted_exemption_still_holds_a_pointer():
    planted = {name for name, _ in pointers(_tree())["planted"]}
    assert planted == set(PLANTED), set(PLANTED) - planted


def test_a_planted_pointer_is_found_and_history_is_classified_not_dropped():
    """The plants are templates filled here with moved names, so no public
    byte spells an import of one. They test the pattern's SHAPES; whether
    MOVED is complete is held by the tests above and #48's constant."""
    plants = json.loads(PLANTS.read_text(encoding="utf-8"))
    names = {"m0": MOVED[0], "m1": MOVED[3], "m2": MOVED[4], "f0": MOVED_FUNCTIONS[1]}

    def fill(value):
        return value.format(**names)

    found = pointers({name: fill(text) for name, text in plants["texts"].items()})
    assert found["live"] == [(name, fill(hit)) for name, hit in plants["live"]]
    assert found["history"] == [(name, fill(hit)) for name, hit in plants["history"]]
    # Every kind this file tells apart is planted, or a pattern could stop
    # matching one and this would still pass on the rest.
    kinds = {text for _, text in found["live"]}
    assert any(k.startswith("from policyforge.ingest import") for k in kinds)
    assert any(k.startswith("ingest/") for k in kinds) and any(
        k.startswith("ingest.") for k in kinds
    )
    assert any(k.startswith("load_") for k in kinds)


def test_moved_is_the_list_every_guard_reads():
    """#48: MOVED is hand-written from part 3's diff; the guards read
    `scripts/licensed_modules.py`. Two instruments, so they must agree: a
    module moved private without the guards knowing it would pass them all."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from licensed_modules import LICENSED_MODULES

    assert set(MOVED) == set(LICENSED_MODULES), set(MOVED) ^ set(LICENSED_MODULES)
