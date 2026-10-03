"""Local version history for generated documents — a lightweight, offline
changelog of every markdown snapshot this tool has produced (or imported
from Confluence) for a given document over time.

This is NOT a replacement for your org's actual system of record —
Confluence's own page version history, git history if you commit output/
somewhere private, or a GRC platform. Treat it as a local,
tool-side audit trail: "what did this Standard actually say two synthesis
runs ago, and what changed." It exists because those other systems only
see what got *published*; this also captures drafts you regenerated but
never pushed, and — via `export/confluence_importer.py` writing into the
same stream with `source="confluence-import"` — lets you diff the last
thing this tool generated against whatever is actually live right now.

Snapshots for one document ("slug", e.g. "standard/authenticator-mgmt")
live under `history_dir/<slug>/`: `v{N}.md` (full content), `v{N}.diff`
(unified diff against v{N-1}), and `index.jsonl` (one JSON line per
version — number, timestamp, content hash, diff stats, source, and any
caller-supplied metadata).
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from policyforge.textfile import write_text_lf


@dataclass
class VersionRecord:
    version: int
    timestamp: str
    content_hash: str
    source: str
    lines_added: int
    lines_removed: int
    metadata: dict = field(default_factory=dict)


def _slug_dir(history_dir: Path, slug: str) -> Path:
    """`history_dir / slug`, refusing a slug that would leave the store.

    The CLI checks `--name` before it gets here (`_checked_slug`), but that
    protects the CLI only. Zardoz's `/history` builds the slug from arguments
    a model fills in and reached this unchecked (found by policyforge-ba), so
    the store checks for itself. It checks traversal rather than the CLI's
    slug grammar: the `confluence/` stream is keyed by page titles through a
    different slugify, and refusing those here would break writes that are
    not a traversal.
    """
    parts = slug.replace("\\", "/").split("/")
    if (
        slug.startswith(("/", "\\"))
        or Path(slug).is_absolute()
        or any(part in {".", ".."} or ":" in part for part in parts)
    ):
        raise ValueError(
            f"{slug!r} is not a document in the history store: a slug is "
            "'<tier>/<name>', and this one would resolve outside it."
        )
    return history_dir / slug


def _index_path(history_dir: Path, slug: str) -> Path:
    return _slug_dir(history_dir, slug) / "index.jsonl"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]


def load_history(history_dir: Path, slug: str) -> list[VersionRecord]:
    """Every recorded version for `slug`, oldest first. Empty list if
    nothing's been recorded yet."""
    index_path = _index_path(history_dir, slug)
    if not index_path.exists():
        return []
    records = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(VersionRecord(**json.loads(line)))
    return records


def load_version_content(history_dir: Path, slug: str, version: int) -> str:
    path = _slug_dir(history_dir, slug) / f"v{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"No recorded v{version} for {slug!r} in {history_dir}.")
    return path.read_text(encoding="utf-8")


def record_version(
    history_dir: Path,
    slug: str,
    content: str,
    *,
    source: str,
    metadata: dict | None = None,
) -> VersionRecord | None:
    """Snapshot `content` as the next version of `slug`, unless it's
    byte-identical to the current latest version — regenerating the same
    output shouldn't pad the history with no-op entries, so this returns
    None and writes nothing in that case.

    `source` should say what produced this version, e.g. "generate" or
    "confluence-import", so history readers can tell a fresh draft apart
    from an imported/reconciled one.
    """
    history = load_history(history_dir, slug)
    new_hash = _content_hash(content)

    if history and history[-1].content_hash == new_hash:
        return None

    previous_version = history[-1].version if history else 0
    previous_content = load_version_content(history_dir, slug, previous_version) if history else ""

    diff_lines = list(
        difflib.unified_diff(
            previous_content.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"v{previous_version}",
            tofile=f"v{previous_version + 1}",
        )
    )
    lines_added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    lines_removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))

    version_number = previous_version + 1
    record = VersionRecord(
        version=version_number,
        timestamp=datetime.now(timezone.utc).isoformat(),
        content_hash=new_hash,
        source=source,
        lines_added=lines_added,
        lines_removed=lines_removed,
        metadata=metadata or {},
    )

    slug_dir = _slug_dir(history_dir, slug)
    slug_dir.mkdir(parents=True, exist_ok=True)
    # The stored copy is what `history --diff` compares against later, and
    # a CRLF copy of an LF document diffs on every line.
    write_text_lf(slug_dir / f"v{version_number}.md", content)
    write_text_lf(slug_dir / f"v{version_number}.diff", "".join(diff_lines))
    with _index_path(history_dir, slug).open("a", encoding="utf-8") as f:
        f.write(json.dumps(dataclasses.asdict(record)) + "\n")

    return record


def diff_versions(history_dir: Path, slug: str, v1: int, v2: int) -> str:
    """Unified diff between two already-recorded versions of `slug`."""
    content1 = load_version_content(history_dir, slug, v1)
    content2 = load_version_content(history_dir, slug, v2)
    return "".join(
        difflib.unified_diff(
            content1.splitlines(keepends=True),
            content2.splitlines(keepends=True),
            fromfile=f"v{v1}",
            tofile=f"v{v2}",
        )
    )
