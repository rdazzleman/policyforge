"""A traceable library of a licensed catalog's versions (443, part 1).

The user's request (to 80, 2026-10-11): *"I want a local place where i can
store a traceable version of HITRUST CSF. As I get new versions of the CSF, I
want to store my own copy with change log analysis."* The library is a git
repository of its own, private and never mirrored, laid out as:

    framework.yaml            what the library holds and may hold (below)
    versions/<version>/
      export/<file>           the export as received, byte for byte
      controls.json           what the framework's licensed parser made of it
      provenance.json         where each of those came from
    changes/<old>-to-<new>.md the change log between two versions
    CURRENT                   the version projects build from
    .gitattributes            ending `* -text`: no line-ending conversion (461)

and each version is a commit and a tag, `v<version>`. A project builds from a
version by naming its `controls.json` (`--controls`, or a path in
`programme.catalogs`), and every document generated from it records that
version and its export's hash (`catalog_trace`, part 2).

**The root `framework.yaml` is the library's own declaration** (80's ruling
R1 on 443): `licence: licensed`, the `parser:` (an `etl-*` command a licensed
plugin provides), and `allow_licensed_in_repo: true`, the repository saying
of itself that it may hold this content. Optional: `export_option:` (the
parser's argument the export fills, `export_path` by default) and
`parser_options:` (fixed arguments, e.g. a GovRAMP `impact_level`).

**Nothing licensed is parsed here.** The public tool asks the plugin for the
framework's parser, as `etl-hitrust` does, and writes what it returns in the
licence envelope; a plugin never sees a path it writes to.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "framework.yaml"
CURRENT = "CURRENT"
#: The library's own attributes, and the rule it must end with (461): every
#: path stored and checked out as received, with no line-ending conversion.
#: Without it, Git for Windows' default `core.autocrlf=true` stores a CRLF
#: export as LF (so `provenance.json`'s sha256 isn't the blob's) and checks
#: `controls.json` out as CRLF in a clone (so `catalog_trace` finds no version).
ATTRIBUTES = ".gitattributes"
NO_CONVERSION = "* -text"
#: A version is a folder name and a tag: `11.7`, `v11.8-beta`.
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class LibraryError(ValueError):
    """A refusal: said by name, before anything is written."""


@dataclass
class Library:
    root: Path
    parser: str
    export_option: str = "export_path"
    parser_options: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 B607: fixed argv, no shell
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _inside_policyforge(path: Path) -> Path | None:
    """The PolicyForge checkout `path` sits in, if any: a directory at or
    above it holding the package's source or naming it in its pyproject."""
    for directory in (path, *path.parents):
        if (directory / "src" / "policyforge" / "__init__.py").is_file():
            return directory
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file() and re.search(
            r'(?m)^name\s*=\s*"policyforge"\s*$', pyproject.read_text(encoding="utf-8")
        ):
            return directory
    return None


def open_library(root: Path) -> Library:
    """The library at `root`, or a refusal naming what's missing.

    Refused: a path that isn't the top of its own git repository; one inside
    a PolicyForge checkout (licensed content never enters the tool's own
    tree); a root `framework.yaml` that is missing, isn't licensed, names no
    `parser:`, or doesn't declare `allow_licensed_in_repo: true`.
    """
    import yaml

    root = root.resolve()
    if not root.is_dir():
        raise LibraryError(f"{root} is not a directory.")
    checkout = _inside_policyforge(root)
    if checkout is not None:
        raise LibraryError(
            f"{root} is inside a PolicyForge checkout ({checkout}). A library holds licensed "
            "content, which never enters the tool's own tree: keep it in a repository of its own."
        )
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise LibraryError(f"{root} is not a git repository. A library is one: `git init` it.")
    if Path(top.stdout.strip()).resolve() != root:
        raise LibraryError(
            f"{root} is inside the git repository {top.stdout.strip()}, not the top of its own. "
            "A library is a repository of its own."
        )
    path = root / MANIFEST
    if not path.is_file():
        raise LibraryError(f"{root} has no {MANIFEST} saying what it holds and may hold.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LibraryError(f"{path} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise LibraryError(f"{path} is not a mapping.")
    if str(data.get("licence") or "").strip().lower() != "licensed":
        raise LibraryError(f"{path} doesn't say `licence: licensed`.")
    if data.get("allow_licensed_in_repo") is not True:
        raise LibraryError(
            f"{path} doesn't declare `allow_licensed_in_repo: true`: the library hasn't said it "
            "may hold this content. Add it if your licence permits this repository to."
        )
    parser = str(data.get("parser") or "").strip()
    if not parser:
        raise LibraryError(f"{path} names no `parser:` (the `etl-*` command that reads exports).")
    options = data.get("parser_options") or {}
    if not isinstance(options, dict):
        raise LibraryError(f"{path}: `parser_options` is not a mapping.")
    return Library(
        root=root,
        parser=parser,
        export_option=str(data.get("export_option") or "export_path"),
        parser_options=dict(options),
        manifest=data,
    )


def versions(library: Library) -> list[str]:
    folder = library.root / "versions"
    return sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []


def current(library: Library) -> str | None:
    path = library.root / CURRENT
    return path.read_text(encoding="utf-8").strip() or None if path.is_file() else None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: What a catalog outside any library records as its library.
NO_LIBRARY = "none"


def catalog_trace(path: Path, name: str) -> dict:
    """What a generated document records about one catalog it was drawn from
    (443 part 2, 80's ruling R2): its name, the sha256 of its controls.json,
    and its library version.

    **A library version is claimed only when the file is that version's own:**
    a `controls.json` in a library's `versions/<V>/`, beside a `provenance.json`
    that names `<V>` and this file's sha256. Then `library` holds the version
    and the export's sha256, so the trace runs from the document to the
    version to the raw export. Anywhere else `library` is `none`, and a file
    in a version folder that doesn't match its provenance says why, rather
    than naming a version whose export it wasn't parsed from."""
    digest = sha256_of(path)
    entry = {"catalog": name, "sha256": digest, "library": NO_LIBRARY}
    folder = path.parent
    provenance = folder / "provenance.json"
    if path.name != "controls.json" or folder.parent.name != "versions":
        return entry
    if not provenance.is_file():
        return entry
    try:
        recorded = json.loads(provenance.read_text(encoding="utf-8"))
        export_sha = recorded["export"]["sha256"]
        matches = recorded["version"] == folder.name and recorded["controls"]["sha256"] == digest
    except (OSError, ValueError, KeyError, TypeError):
        entry["library"] = f"{NO_LIBRARY}: the provenance.json beside it can't be read"
        return entry
    if not matches:
        entry["library"] = (
            f"{NO_LIBRARY}: this controls.json isn't the one version {folder.name} recorded"
        )
        return entry
    entry["library"] = {"version": folder.name, "export_sha256": export_sha}
    return entry


def check_add(library: Library, export: Path, version: str) -> tuple[str, str]:
    """(the export's sha256, the git author), or a refusal: a version that
    isn't a safe folder and tag name, or already exists; an export whose
    content is already a version's; a working tree with other changes in it;
    no git identity to commit as."""
    # A folder and a tag both: git's own rule for the tag refuses `1..2`,
    # `x.lock` and a trailing dot, which Windows would also drop from a
    # folder's name (1d on PR 458).
    if (
        not _VERSION_RE.fullmatch(version)
        or _git(library.root, "check-ref-format", f"refs/tags/v{version}").returncode != 0
    ):
        raise LibraryError(
            f"{version!r} can't name a folder and a tag: use letters, digits, `.`, `_` or `-`, "
            "with no `..`, no trailing `.` and no `.lock` ending."
        )
    # By case-folded name, not by the filesystem: on a case-insensitive one
    # `11.7-A` IS `11.7-a`'s folder, and a tag in packed-refs isn't seen by
    # a path lookup (b5 on PR 458).
    taken = {v.casefold() for v in versions(library)} | {
        t.removeprefix("v").casefold() for t in _git(library.root, "tag", "--list").stdout.split()
    }
    if version.casefold() in taken:
        raise LibraryError(f"Version {version} is already in {library.root} (case ignored).")
    if not export.is_file():
        raise LibraryError(f"{export} is not a file.")
    digest = sha256_of(export)
    for existing in versions(library):
        provenance = library.root / "versions" / existing / "provenance.json"
        if provenance.is_file():
            recorded = json.loads(provenance.read_text(encoding="utf-8"))
            if recorded.get("export", {}).get("sha256") == digest:
                raise LibraryError(
                    f"{export.name} is the same export as version {existing} "
                    f"(sha256 {digest[:12]}), under a new name. Nothing was added."
                )
    status = _git(library.root, "status", "--porcelain")
    if status.returncode != 0 or status.stdout.strip():
        raise LibraryError(
            f"{library.root} has changes not committed. A version is added as one commit of "
            "its own: commit or set them aside first."
        )
    who = _git(library.root, "config", "user.name").stdout.strip()
    if not who:
        raise LibraryError(
            f"{library.root} has no git `user.name`, so there is no one to record as adding "
            "this version. Set it in that repository."
        )
    return digest, who


@dataclass
class Added:
    version: str
    previous: str | None
    folder: Path
    change_log: Path | None
    tag: str
    commit: str
    #: "written" or "extended" when this add put `* -text` into the
    #: library's `.gitattributes`, None when it was already its last rule.
    attributes: str | None = None


def _ensure_no_conversion(library: Library) -> str | None:
    """Make `* -text` the last rule of the library's `.gitattributes` (461).

    Last, because a later line overrides an earlier one for the same
    attribute: a file ending in `* -text` covers every path whatever it says
    above. Anything else (no file, or a last rule that isn't this one) gets
    the line appended, and the person's own lines are kept. Returns
    "written", "extended" or None. Whether it took effect is measured after
    `git add` (`_check_no_conversion`), not assumed from this reading."""
    path = library.root / ATTRIBUTES
    if not path.exists():
        path.write_bytes((NO_CONVERSION + "\n").encode("utf-8"))
        return "written"
    data = path.read_bytes()
    rules = [
        line.strip()
        for line in data.decode("utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if rules and rules[-1].split() == NO_CONVERSION.split():
        return None
    ending = b"" if not data or data.endswith(b"\n") else b"\n"
    path.write_bytes(data + ending + (NO_CONVERSION + "\n").encode("utf-8"))
    return "extended"


def _match_tree_to_stored(library: Library) -> None:
    """Rewrite each tracked file whose bytes aren't the ones git stored.

    `check_add` found the tree clean under the old attributes, so where a
    file differs from its blob it differs only by a conversion: a
    `framework.yaml` written with CRLF and committed under `autocrlf` is LF
    in git. With conversion off, git compares bytes, so that file would read
    as modified and the NEXT add would refuse the library as uncommitted
    (measured: 12 of 30 runs on this machine, because `git status` re-reads
    a file only when its timestamp is ambiguous). Compared byte for byte
    here, since a timestamp check (`git checkout-index`) skips such a file.

    Each rewritten file is staged again: the same blob, but the index's
    recorded size is the old one, and git counts a size change as modified
    without reading the file. `.gitattributes` is skipped: it is the one
    file this add has just changed on purpose."""
    listed = _git(library.root, "ls-files", "-z")
    if listed.returncode != 0:
        raise LibraryError(f"git ls-files failed in {library.root}: {listed.stderr.strip()[:300]}")
    rewritten = []
    for name in filter(None, listed.stdout.split("\0")):
        if name == ATTRIBUTES:
            continue
        stored = subprocess.run(  # nosec B603 B607: fixed argv, no shell
            ["git", "-C", str(library.root), "cat-file", "blob", f":{name}"],
            capture_output=True,
            check=False,
        )
        path = library.root / name
        if stored.returncode == 0 and path.is_file() and path.read_bytes() != stored.stdout:
            path.write_bytes(stored.stdout)
            rewritten.append(name)
    if rewritten:
        done = _git(library.root, "add", "--", *rewritten)
        if done.returncode != 0:
            raise LibraryError(f"git add failed in {library.root}: {done.stderr.strip()[:300]}")


def _check_no_conversion(library: Library, paths: list[str]) -> None:
    """Refuse unless git itself reads `text` as unset on every path being
    added: a nested `.gitattributes` under `versions/` would override the
    root's, and only git's answer covers that."""
    staged = _git(library.root, "ls-files", "-z", "--", *paths).stdout.split("\0")
    staged = [p for p in staged if p]
    answer = _git(library.root, "check-attr", "-z", "text", "--", *staged)
    fields = answer.stdout.split("\0")
    converted = [fields[i] for i in range(0, len(fields) - 2, 3) if fields[i + 2] != "unset"]
    if answer.returncode != 0 or not staged or converted:
        raise LibraryError(
            "Git would convert line endings in "
            + (", ".join(converted[:5]) or "the files being added")
            + f", so the library wouldn't hold them as received. Its {ATTRIBUTES} must leave "
            f"`text` unset ({NO_CONVERSION}) on every path. Nothing was added."
        )


def add_version(
    library: Library,
    export: Path,
    version: str,
    *,
    parse: Callable[[Path, str], tuple[list, dict]],
    content_root: Path | None,
    now: str,
    tool: dict,
) -> Added:
    """Add `export` as `version`: copy it, parse it through `parse` (the
    plugin call, which returns the controls and what to record about the
    parser), write the catalog in its licence envelope and the provenance,
    write the change log against CURRENT, then commit and tag.

    Every refusal comes before the first write (`check_add`); a parse that
    fails leaves nothing behind."""
    import dataclasses

    from policyforge.ingest.schema import LICENSED_MARKER, catalog_json
    from policyforge.textfile import replace_text_lf

    digest, who = check_add(library, export, version)
    previous = current(library)
    controls, parser_record = parse(export, version)

    folder = library.root / "versions" / version
    change_log = (
        library.root / "changes" / f"{previous}-to-{version}.md" if previous is not None else None
    )
    created: list[Path] = []
    # The library's own attributes file, byte for byte, to put back on a
    # failure: a `git checkout` of it would convert its line endings too.
    attributes_path = library.root / ATTRIBUTES
    restore = {attributes_path: attributes_path.read_bytes()} if attributes_path.exists() else {}
    try:
        # First, before anything is copied: `git add` reads the attributes
        # from the working tree, so the export is stored as received (461).
        attributes = _ensure_no_conversion(library)
        if attributes == "written":
            created.append(library.root / ATTRIBUTES)
        if attributes is not None:
            _match_tree_to_stored(library)
        # Never `exist_ok`: a folder already there is someone else's, so it is
        # refused here, before this call owns anything (b5 on PR 458).
        folder.parent.mkdir(exist_ok=True)
        folder.mkdir()
        created.append(folder)
        (folder / "export").mkdir()
        copied = folder / "export" / export.name
        shutil.copyfile(export, copied)
        if sha256_of(copied) != digest:
            raise LibraryError(f"{copied} doesn't match {export} after the copy.")
        catalog = folder / "controls.json"
        replace_text_lf(
            catalog,
            catalog_json([dataclasses.asdict(c) for c in controls], licence=LICENSED_MARKER),
        )
        provenance = {
            "version": version,
            "added": now,
            "added_by": who,
            "export": {"file": export.name, "bytes": copied.stat().st_size, "sha256": digest},
            "controls": {
                "file": "controls.json",
                "sha256": sha256_of(catalog),
                "count": len(controls),
            },
            "parser": parser_record,
            "policyforge": tool,
        }
        replace_text_lf(folder / "provenance.json", json.dumps(provenance, indent=2) + "\n")
        if change_log is not None:
            from policyforge.ingest.schema import load_controls

            old = load_controls(library.root / "versions" / previous / "controls.json")
            change_log.parent.mkdir(exist_ok=True)
            if change_log.exists():
                raise LibraryError(f"{change_log} is already there.")
            created.append(change_log)
            replace_text_lf(
                change_log,
                change_log_text(old, controls, previous, version, content_root=content_root),
            )
        replace_text_lf(library.root / CURRENT, version + "\n")
        paths = [str(folder.relative_to(library.root)), CURRENT]
        if change_log is not None:
            paths.append(str(change_log.relative_to(library.root)))
        if attributes is not None:
            paths.append(ATTRIBUTES)
        message = f"Add {library.parser} version {version}" + (
            f" (from {previous})" if previous else ""
        )
        for step in (("add", "--", *paths), ("commit", "-q", "-m", message)):
            if step[0] == "commit":
                _check_no_conversion(library, paths)
            done = _git(library.root, *step)
            if done.returncode != 0:
                raise LibraryError(
                    f"git {step[0]} failed in {library.root}: {done.stderr.strip()[:300]}"
                )
    except BaseException:
        _undo(library, created, restore)
        raise
    # Committed: a tag that fails leaves the version in, and says so.
    tagged = _git(library.root, "tag", f"v{version}")
    if tagged.returncode != 0:
        raise LibraryError(
            f"Version {version} is committed, but `git tag v{version}` failed: "
            f"{tagged.stderr.strip()[:300]}"
        )
    commit = _git(library.root, "rev-parse", "HEAD").stdout.strip()
    return Added(version, previous, folder, change_log, f"v{version}", commit, attributes)


def _undo(library: Library, created: list[Path], restore: dict[Path, bytes]) -> None:
    """Put the library back as `check_add` found it: **only what this call
    created** is removed (a folder or file already there is never touched,
    b5 on PR 458), each file in `restore` gets its own bytes back, and
    CURRENT and the index are as committed."""
    _git(library.root, "reset", "-q", "--", "versions", "changes", CURRENT, ATTRIBUTES)
    for path in reversed(created):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    for folder in (library.root / "changes", library.root / "versions"):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    for path, data in restore.items():
        path.write_bytes(data)
    if _git(library.root, "ls-files", "--error-unmatch", CURRENT).returncode == 0:
        _git(library.root, "checkout", "-q", "--", CURRENT)
    else:
        (library.root / CURRENT).unlink(missing_ok=True)


def change_log_text(old, new, previous: str, version: str, *, content_root: Path | None) -> str:
    """The change log from `previous` to `version`: what drift reports, each
    item by id (and level), with the documents that cite it. It says what to
    re-read; it never recommends regenerating."""
    from policyforge.frameworks.drift import ADDED, REMOVED, analyze_drift

    report = analyze_drift(old, new, content_root=content_root)
    lines = [
        f"# Changes from {previous} to {version}",
        "",
        f"{len(report.substantive)} change(s) to review, {len(report.editorial)} editorial.",
        "",
    ]
    if content_root is None:
        lines += ["No content tree was given, so no document is named below.", ""]

    held = {
        c.control_id: list(c.requirements)
        for side in (old, new)
        for c in side
        if getattr(c, "requirements", None)
    }

    def entry(change) -> str:
        what = change.control_id + (f" ({change.level})" if change.level else "")
        fields = f": {', '.join(change.fields)}" if change.fields else ""
        documents = report.impacts.get(change.control_id)
        cited = ", ".join(documents.documents) if documents and documents.documents else ""
        line = f"- **{what}**{fields}"
        if cited:
            line += f". Cited by: {cited}"
        if change.detail:
            line += "\n" + "\n".join(f"  {row}" for row in change.detail.splitlines())
        if change.kind in (ADDED, REMOVED) and change.control_id in held:
            # A control that arrives or leaves brings or takes its level
            # requirements: each is named, with its level.
            line += "".join(
                f"\n  - {r.requirement_id}" + (f" ({r.level})" if r.level else "")
                for r in held[change.control_id]
            )
        return line

    for title, kind in (("Added", ADDED), ("Removed", REMOVED)):
        found = [c for c in report.changes if c.kind == kind]
        if found:
            lines += [f"## {title}", "", *(entry(c) for c in found), ""]
    changed = [c for c in report.substantive if c.kind not in (ADDED, REMOVED)]
    if changed:
        lines += ["## Changed", "", *(entry(c) for c in changed), ""]
    editorial = [c for c in report.editorial if c.kind not in (ADDED, REMOVED)]
    if editorial:
        lines += ["## Editorial", "", *(entry(c) for c in editorial), ""]
    if not report.changes:
        lines += ["Nothing changed between the two versions.", ""]
    documents = report.affected_documents
    if documents:
        lines += ["## Documents to re-read", "", *(f"- {d}" for d in documents), ""]
    return "\n".join(lines).rstrip() + "\n"
