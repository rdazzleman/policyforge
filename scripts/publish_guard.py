#!/usr/bin/env python3
"""Refuse to publish anything to GitHub that carries a licensed parser (#33).

WHY. From 1.7 the public tool is "generator only" (the user, 2026-09-28):
the five licensed-framework parsers move to a private repository (#32), and
past releases stay as published. The team's Forgejo `release/1.7` still
carries all five in its HISTORY, and deleting a file in a later commit does
not remove it from history. So a plain push mirror, or any push of a branch
descended from the train, would republish them. Only a filtered commit P
may go to GitHub, and this refuses P unless it is clean.

WHAT IT CHECKS. Two populations, together: every commit and object in
`B..P` (what a push of P would ADD), and every file in P's TREE (what
becomes public `main`). The second is not redundant: B is 1.6.1, which
already carries the HITRUST parsers, so a byte-identical renamed copy of one
is reachable from B, is in no object `B..P` adds, and passed every layer
until P's tree was scanned too. Four layers, three independent
instruments, because each misses what another catches:

    L1 paths    a licensed module's path, or anything but `.gitkeep` under
                `local_content/`, in the tree of ANY commit in the range.
                A tree-only check passes a head that deleted the file while
                its parent still carries it; this reads every commit.
    L2 blobs    any object in the range that is byte-identical to a blob
                which ever stood at a licensed path in the SOURCE history.
                Catches a pure rename, which L1 cannot (9b on #33).
    L3 content  a blob that imports a licensed module, or that defines a
                name only the licensed modules define (derived from their
                source by AST, minus any name the SOURCE's public code
                defines, so assembly that moved public under #32 is not a
                hit, and a copy in P cannot exempt itself).
                Catches a renamed AND edited copy that keeps the parser's
                structure, which L2 cannot.
    L4 data     a `.json` blob that PARSES as a licensed catalog envelope
                (`{"licence": "licensed", ...}`, #10). Parsed, not grepped:
                the marker's text appears in seven public files (docs,
                `schema.py`, tests), and 0 blobs parse as an envelope.
                Every `.json` blob is parsed, at any size and in any
                encoding `json` detects; there is no size cap to hide under.

THE ONE CONSTANT. `LICENSED_MODULES`, from `scripts/licensed_modules.py`
(#48), names the seven modules #32 part 3 moved: that list IS the user's
decision, and `release_check` (so `release.py`) reads the same one.
Everything else is derived at run time from the source ref, never kept by
hand beside the check (9b on #33: a hand list written today would already
miss #34's new test file).

WHAT IT CANNOT SEE, stated so it is not read as proof (1d; b5 on #35):
- a copy renamed AND with its imports and defined names rewritten passes
  all four layers. A test pins this limit as passing, so the sentence
  stays true;
- a RAW vendor export (a MyCSF `.csv` or `.mhtml`, a GovRAMP `.xlsx`, a
  TSC text copy) outside `local_content/` is not a catalog, carries no
  envelope and no framework rows, and passes;
- a parser embedded in a notebook (`.ipynb`) or other non-source text,
  edited so that no definition line survives as written, passes.
The guard stops accidental publication of the parsers and of catalogs
built from licensed content; it cannot prove a deliberate rewrite or an
unrecognisable format absent. Reviewing P is what covers those.

L4 ALSO refuses a JSON catalog (a list, or an envelope's `controls`) whose
rows name a licensed framework, by the parsers' own `FRAMEWORK` constants
(b5 on #35): a catalog written before #10 has no envelope, and a user's own
HITRUST export dropped into `data/frameworks/` is the ordinary accident.

ONE IMPLEMENTATION OF EACH CONTENT LAYER (80's ruling on #237). L2 to L4
also run on a downloaded release archive: `release_check`'s assertion 5
reads the artefact users get, after publishing. So the layers are a core
over CONTENTS, never a copy beside the original: `scan_contents` is L3 and
L4 over `(path, bytes)` pairs, and `check_blobs` is L2 over blob ids.
`check_content` feeds the core blobs read through `git cat-file`.
`check_files` feeds it an archive's members, computing each one's git blob
id from its bytes, so a byte-identical copy matches whatever it is called.
`reference` derives what the layers compare against, once for both callers,
and refuses an empty derivation.

THE FORMAT HINTS ARE NOT MARKERS. The user ruled that format knowledge
(SSRS textbox headers, caption conventions, level-label shapes) stays
public in `generate-parser` (#32, comment 501). Keying on it would refuse
the public generator, so it is deliberately not a layer.

EXIT: 0 clean; 1 refused, every finding named; 2 nothing to check (an empty
range, an unknown ref, or a source that carries none of the modules). A
derivation that found nothing is not a pass. The push is conditioned on
this exit code, never on reading the output.

    python scripts/publish_guard.py --candidate P --base B --source S
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The licensed modules, from the ONE list every check reads (#48). Read
#: from this tool's own checkout, never from the tree being checked.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from licensed_modules import LICENSED_MODULES, LICENSED_PATHS, MODULE_DIR  # noqa: E402


def _schema():
    """This checkout's own `ingest/schema.py`, by file path (#320): the one
    licence rule, from the tool's checkout and never from the tree being
    checked, as `licensed_modules` is read. It imports only the standard
    library, so no install is needed."""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "src" / "policyforge" / "ingest" / "schema.py"
    spec = importlib.util.spec_from_file_location("_publish_guard_schema", path)
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses look their module up there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SCHEMA = _schema()


def _licensed_envelope(data: dict) -> bool:
    """L4's licence test (#320). An envelope (an object with a `controls`
    list, `parse_catalog`'s own definition) is judged by `is_licensed`, fail
    closed: `Licensed`, `licenced` or the key `license` all count. Any other
    object counts when its licence names a licensed spelling (`licen...`,
    any case), not merely any value: a `package.json`'s `"license": "MIT"`
    is not a catalog."""
    licence = _SCHEMA.envelope_licence(data)
    if isinstance(data.get("controls"), list):
        return _SCHEMA.is_licensed(licence)
    return licence.strip().casefold().startswith("licen")


#: An import of a licensed module, in code or in a string (monkeypatch
#: targets and generated-code templates import by dotted text too).
_IMPORT = re.compile(
    r"(?:policyforge\.ingest\.|from\s+\.\s*|from\s+policyforge\.ingest\s+import\s+[^\n]*\b)"
    rf"({'|'.join(LICENSED_MODULES)})\b"
)

#: Every blob is read for content, whatever its size (9b on #35: a 2 MB
#: cap skipped a 2.36 MB licensed catalog silently, exit 0, and the train
#: already holds a 3.7 MB `.json`). A blob with a NUL in its first 8000
#: bytes is not scanned by L3 as text; a `.json` one is still parsed by L4,
#: from bytes, so a UTF-16 catalog is read too.


@dataclass(frozen=True)
class Finding:
    layer: str
    where: str
    why: str

    def __str__(self) -> str:
        return f"{self.layer}  {self.where}: {self.why}"


class NothingToCheck(RuntimeError):
    """The range or the derivation is empty: refused, never passed."""


def _git(*args: str, root: Path = REPO_ROOT, check: bool = True) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and done.returncode != 0:
        raise NothingToCheck(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _read_blobs(oids: list[str], root: Path) -> dict[str, bytes]:
    """{oid: content} for every blob, in ONE `git cat-file --batch` pass: the
    full-tree scan reads every file of P, and a process per file is slow."""
    if not oids:
        return {}
    out = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=("\n".join(oids) + "\n").encode("ascii"),
        capture_output=True,
        check=True,
    ).stdout
    found: dict[str, bytes] = {}
    at = 0
    while at < len(out):
        end = out.index(b"\n", at)
        header = out[at:end].decode("ascii").split()
        at = end + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        found[header[0]] = out[at : at + size]
        at += size + 1  # the content, then its trailing newline
    return found


def tree_blobs(candidate: str, root: Path) -> dict[str, str]:
    """{blob id: path} for every file in P's TREE: what becomes public `main`."""
    blobs: dict[str, str] = {}
    for line in _git("ls-tree", "-r", candidate, root=root).splitlines():
        meta, _, path = line.partition("\t")
        _mode, kind, oid = meta.split()
        if kind == "blob":
            blobs.setdefault(oid, path)
    return blobs


def range_commits(candidate: str, base: str | None, root: Path) -> list[str]:
    spec = [candidate] + ([f"^{base}"] if base else [])
    return _git("rev-list", *spec, root=root).split()


def range_blobs(candidate: str, base: str | None, root: Path) -> dict[str, str]:
    """{blob id: a path it appears at} for every blob a push of P would add."""
    spec = [candidate] + ([f"^{base}"] if base else [])
    blobs: dict[str, str] = {}
    for line in _git("rev-list", "--objects", *spec, root=root).splitlines():
        oid, _, path = line.partition(" ")
        if path:
            blobs.setdefault(oid, path)
    typed = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        cwd=root,
        input="\n".join(blobs),
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.split("\n")
    kind = dict(line.split(" ", 1) for line in typed if " " in line)
    return {oid: path for oid, path in blobs.items() if kind.get(oid) == "blob"}


def licensed_blobs(source: str, root: Path) -> set[str]:
    """Every blob that ever stood at a licensed path in the source history."""
    found: set[str] = set()
    for path in LICENSED_PATHS:
        for commit in _git("log", "--format=%H", source, "--", path, root=root).split():
            oid = _git(
                "rev-parse", "--verify", "--quiet", f"{commit}:{path}", root=root, check=False
            )
            if oid.strip():
                found.add(oid.strip())
    return found


def _defined_names(code: str) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _last_version(source: str, path: str, root: Path) -> str:
    """`path`'s content at the last commit in `source`'s history that touched
    it; at that commit's parent if the commit deleted it; "" if never there."""
    last = _git("log", "-1", "--format=%H", source, "--", path, root=root).strip()
    if not last:
        return ""
    for commit in (last, f"{last}^"):
        code = _git("show", f"{commit}:{path}", root=root, check=False)
        if code:
            return code
    return ""


def licensed_only_names(source: str, root: Path) -> set[str]:
    """Top-level names the licensed modules define at the source, minus any
    name the SOURCE's own public Python defines: what only a parser carries.

    **The exemption comes from the source, never from P** (the same
    instrument on both sides of the comparison agrees with itself). The
    first version subtracted P's own definitions, so a renamed-and-edited
    parser in P exempted its own names and passed: the thing being checked
    defined what was allowed. The test for that arm caught it. Assembly that
    moves public under #32 (`levelled.py`) lands on the train before P is
    built, so it is exempted here; a copy that exists only in P is not.

    **The licensed names come from the source's HISTORY, as L2's blobs do**
    (9b on #35). The first version read each module from the source's TREE.
    After #32 the tree no longer holds them, so the names came back empty and
    L3 went silent while L1, L2 and L4 kept reporting: a renamed-and-edited
    copy passed with exit 0, and the control (a pre-#32 source) refused it.
    Each module is read at the last commit in the source's history that
    touched it, or at that commit's parent when the commit deleted it."""
    licensed: set[str] = set()
    for path in LICENSED_PATHS:
        licensed |= _defined_names(_last_version(source, path, root))
    public: set[str] = set()
    for path in _git("ls-tree", "-r", "--name-only", source, root=root).splitlines():
        if path.endswith(".py") and path not in LICENSED_PATHS:
            public |= _defined_names(_git("show", f"{source}:{path}", root=root, check=False))
    # Short or generic names would match everywhere; a parser's own names are specific.
    return {name for name in licensed - public if len(name) > 6 and not name.startswith("_")}


def check_paths(commits: list[str], root: Path) -> list[Finding]:
    """One finding per offending PATH, naming how many commits in the range
    carry it and the newest: every commit is read, the report is grouped."""
    carried: dict[str, list[str]] = {}
    why: dict[str, str] = {}
    for commit in commits:  # rev-list order: newest first
        for path in _git("ls-tree", "-r", "--name-only", commit, root=root).splitlines():
            if path in LICENSED_PATHS:
                why[path] = "a licensed parser"
            elif path.startswith("local_content/") and path != "local_content/.gitkeep":
                why[path] = "local_content is data"
            else:
                continue
            carried.setdefault(path, []).append(commit)
    return [
        Finding(
            "L1 path",
            path,
            f"{why[path]}, in {len(hits)} commit(s) of the range, newest {hits[0][:12]}",
        )
        for path, hits in carried.items()
    ]


def blob_id(data: bytes) -> str:
    """The git object id of a blob holding `data`, as `git hash-object` gives
    it in this SHA-1 repository: how L2 knows an archive member that never
    passed through git."""
    header = b"blob %d\0" % len(data)
    # SHA-1 because it is git's object id in this repository, to be compared with
    # git's own ids, not a security choice.
    # nosemgrep: python.lang.security.insecure-hash-algorithms.insecure-hash-algorithm-sha1
    digest = hashlib.sha1(header + data, usedforsecurity=False)  # nosec B324
    return digest.hexdigest()


def check_blobs(blobs: dict[str, str], forbidden: set[str]) -> list[Finding]:
    return [
        Finding("L2 blob", f"{oid[:12]} at {path}", "byte-identical to a licensed parser's blob")
        for oid, path in blobs.items()
        if oid in forbidden
    ]


def _framework_key(name: str) -> str:
    """Case and punctuation folded, so `HITRUST CSF` matches `HITRUST-CSF`."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


#: The public, fixed list of licensed framework names (#36; 80's ruling 562):
#: a second instrument beside the parsers' own constants.
PUBLIC_NAMES_PATH = "src/policyforge/plugins.py"
PUBLIC_NAMES_CONSTANT = "LICENSED_FRAMEWORK_NAMES"


def _assigned_constant(tree: ast.Module, name: str) -> list[str]:
    """String values assigned to `name` at module level: a string, or the
    string values of a dict or the items of a list/tuple/set literal."""
    values: list[str] = []
    for node in tree.body:
        value = getattr(node, "value", None)
        targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values.append(value.value)
        elif isinstance(value, ast.Dict):
            elements = value.values
            values += [e.value for e in elements if isinstance(e, ast.Constant)]
        elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            values += [e.value for e in value.elts if isinstance(e, ast.Constant)]
    return [v for v in values if isinstance(v, str)]


def _parse(code: str) -> ast.Module | None:
    try:
        return ast.parse(code)
    except SyntaxError:
        return None


def licensed_frameworks(source: str, root: Path) -> set[str]:
    """The framework names the licensed parsers write into their rows, from
    TWO instruments, unioned, so one changing form cannot silently drop a name:

    1. each licensed module's `FRAMEWORK` at its last version in the source's
       history, following ONE re-export (`from .levelled import FRAMEWORK`):
       #38 moved HITRUST's constant into the public assembler, and reading
       only the assignment form lost `HITRUST-CSF` from this set while
       GovRAMP and SOC 2 kept the floor quiet (1d, reviewing #38);
    2. the public fixed list `plugins.LICENSED_FRAMEWORK_NAMES` at the source
       (#36; 80's ruling 562 keeps it public), read from history too."""
    found: set[str] = set()
    for path in LICENSED_PATHS:
        tree = _parse(_last_version(source, path, root))
        if tree is None:
            continue
        found.update(_framework_key(v) for v in _assigned_constant(tree, "FRAMEWORK"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and any(a.name == "FRAMEWORK" for a in node.names):
                module = node.module or ""
                if node.level:  # relative to the licensed modules' own package
                    target = f"{MODULE_DIR}/{module.replace('.', '/')}.py"
                else:
                    target = f"src/{module.replace('.', '/')}.py"
                imported = _parse(_last_version(source, target, root))
                if imported is not None:
                    found.update(
                        _framework_key(v) for v in _assigned_constant(imported, "FRAMEWORK")
                    )
    public = _parse(_last_version(source, PUBLIC_NAMES_PATH, root))
    if public is not None:
        found.update(_framework_key(v) for v in _assigned_constant(public, PUBLIC_NAMES_CONSTANT))
    return found


def _licensed_rows(data: object, frameworks: set[str]) -> set[str]:
    """The licensed framework names a parsed catalog's rows declare."""
    rows = data.get("controls") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return set()
    return {
        str(row.get("framework"))
        for row in rows
        if isinstance(row, dict) and _framework_key(row.get("framework", "")) in frameworks
    }


def check_content(
    blobs: dict[str, str], names: set[str], root: Path, frameworks: set[str] = frozenset()
) -> list[Finding]:
    """L3 and L4 over git blobs ({blob id: path}), read in one `cat-file` pass."""
    contents = _read_blobs(list(blobs), root)
    return scan_contents(
        [(path, contents.get(oid, b"")) for oid, path in blobs.items()], names, frameworks
    )


def scan_contents(
    files: list[tuple[str, bytes]], names: set[str], frameworks: set[str] = frozenset()
) -> list[Finding]:
    """L3 and L4 over `(path, bytes)` pairs: the one implementation, whoever
    supplies the bytes."""
    definitions = (
        re.compile(rf"^\s*(?:def|class)\s+({'|'.join(map(re.escape, sorted(names)))})\b", re.M)
        if names
        else None
    )
    findings = []
    for path, raw in files:
        if b"\0" not in raw[:8000]:
            text = raw.decode("utf-8", errors="replace")
            if m := _IMPORT.search(text):
                findings.append(
                    Finding("L3 content", path, f"imports licensed module {m.group(1)!r}")
                )
            if definitions and (m := definitions.search(text)):
                findings.append(
                    Finding("L3 content", path, f"defines {m.group(1)!r}, a licensed parser's name")
                )
        if path.endswith(".json"):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and _licensed_envelope(data):
                findings.append(Finding("L4 data", path, "a licensed catalog envelope (#10)"))
            elif named := _licensed_rows(data, frameworks):
                # b5 on #35: a catalog written before #10 carries no envelope,
                # and a user's own export dropped into data/frameworks/ is the
                # ordinary accident. Its rows still name the framework.
                findings.append(
                    Finding(
                        "L4 data",
                        path,
                        f"a catalog whose rows name licensed framework(s) {sorted(named)}",
                    )
                )
    return findings


@dataclass(frozen=True)
class Reference:
    """What L2 to L4 compare against, derived from a source's history."""

    blobs: frozenset[str]
    names: frozenset[str]
    frameworks: frozenset[str]


def reference(source: str, root: Path = REPO_ROOT) -> Reference:
    """The content layers' reference set from `source`'s history; raises
    NothingToCheck when the ref is unknown or any part comes back empty: a
    derivation that found nothing is not a pass."""
    forbidden = licensed_blobs(source, root)
    if not forbidden:
        raise NothingToCheck(
            f"source {source!r} carries none of {', '.join(LICENSED_PATHS)}: the guard would "
            "have nothing to compare against. Name the ref that still holds the parsers."
        )
    names = licensed_only_names(source, root)
    if not names:
        # The floor under L3 (9b on #35): a derivation that found nothing is
        # not a pass, for this layer as for L2's blobs above.
        raise NothingToCheck(
            f"no parser-only names could be derived from {source!r}'s history, so L3's "
            "definitions check would be empty. Name a ref whose history holds the parsers."
        )
    frameworks = licensed_frameworks(source, root)
    if not frameworks:
        raise NothingToCheck(
            f"no licensed framework names (FRAMEWORK constants) could be derived from "
            f"{source!r}'s history, so L4 could not recognise an unmarked catalog."
        )
    return Reference(frozenset(forbidden), frozenset(names), frozenset(frameworks))


def check_files(files: list[tuple[str, bytes]], ref: Reference) -> list[Finding]:
    """L2 to L4 over `(path, bytes)` pairs that never passed through git (a
    downloaded archive's members, #237): L2 by each one's computed blob id,
    and L3 and L4 by `scan_contents`, the implementations `guard` uses."""
    ids: dict[str, str] = {}
    for path, raw in files:
        ids.setdefault(blob_id(raw), path)
    return [
        *check_blobs(ids, set(ref.blobs)),
        *scan_contents(files, set(ref.names), set(ref.frameworks)),
    ]


def guard(candidate: str, base: str | None, source: str, root: Path = REPO_ROOT) -> list[Finding]:
    """Every finding for a push of `candidate` over `base`; raises NothingToCheck."""
    commits = range_commits(candidate, base, root)
    if not commits:
        raise NothingToCheck(f"{base}..{candidate} holds no commit: nothing would be published")
    ref = reference(source, root)
    # P's whole TREE as well as the objects the push adds (1d, reviewing #37):
    # `rev-list P ^B` leaves out anything already reachable from B, and B is
    # 1.6.1, which carries the HITRUST parsers. A byte-identical renamed copy
    # of one was therefore scanned by no layer and passed as "clean", while
    # P's tree, which becomes public main, carried the parser's code.
    blobs = {**tree_blobs(candidate, root), **range_blobs(candidate, base, root)}
    return [
        *check_paths(commits, root),
        *check_blobs(blobs, set(ref.blobs)),
        *check_content(blobs, set(ref.names), root, set(ref.frameworks)),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", required=True, help="the commit to publish (P)")
    parser.add_argument("--base", help="what GitHub already has (B); omit on a first publish")
    parser.add_argument("--source", required=True, help="a ref still carrying the parsers")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="the repository to read")
    args = parser.parse_args(argv)
    try:
        findings = guard(args.candidate, args.base, args.source, args.root)
    except NothingToCheck as exc:
        print(f"publish_guard: NOTHING TO CHECK, refused: {exc}")
        return 2
    scope = f"{args.base + '..' if args.base else ''}{args.candidate}"
    if findings:
        print(f"publish_guard: REFUSED, {len(findings)} finding(s) in {scope}:")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print(f"publish_guard: clean. {scope} carries no licensed parser, blob, import or envelope.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
