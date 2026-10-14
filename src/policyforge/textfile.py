"""Text this tool writes ends its lines with LF on every platform.

`Path.write_text` opens the file in text mode with universal newlines, and
on Windows that turns every `\\n` into `\\r\\n` on the way out. Every
markdown writer here passed `encoding="utf-8"` and none passed `newline`,
so a Standard drafted on Windows, a page pulled from Confluence on Windows,
or a revision `edit-topic --apply` wrote into `docs/` arrived with CRLF —
and `mdformat`, which the repository's own gate runs over every markdown
file, rejects any file containing a CR. The tool produced files its own
check then failed on, and only on the platform the check could not see
from CI. `.gitattributes` fixes what git checks *out*; it says nothing
about what a program writes, and `output/` is not tracked at all.

One function rather than a `newline=` argument at each site, because the
site that forgets is the one that ships. `tests/test_lf_output.py` holds
the writers to it.
"""

from __future__ import annotations

from pathlib import Path


def normalise_newlines(text: str) -> str:
    """`text` with every `\\r\\n` and bare `\\r` folded to `\\n`.

    What `write_text_lf` writes, exposed so a caller can compare text it is
    about to write against a file it reads back through universal newlines
    — `Path.read_text` folds CRLF on the way in, and a comparison against
    the unfolded string reads an unchanged file as changed every time.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def write_text_lf(path: Path, text: str) -> Path:
    """Write `text` to `path` as UTF-8 with LF line endings, whatever the OS.

    Incoming `\\r\\n` and bare `\\r` are folded to `\\n` first. Markdown that
    reaches this function has been through a model reply, a Confluence
    page, or a file a person edited, any of which can carry CRLF, and the
    guarantee callers want is "no CR in the file", not "no CR added".
    """
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalise_newlines(text))
    return path


def replace_text_lf(path: Path, text: str) -> Path:
    """`write_text_lf`, but written as a NEW file moved over `path` (#319).

    Opening `path` for writing writes through whatever it is: a hard link to
    another file, or a symlink, changes that other file too. A licensed
    catalog written to an `--out` hard-linked into the bundled
    `data/frameworks/` landed in the bundled file that way, with exit 0
    (measured on Windows and Linux). Moving a new file over the name replaces
    the directory entry only, so the other file keeps its bytes.
    """
    return replace_bytes(path, normalise_newlines(text).encode("utf-8"))


def replace_bytes(path: Path, data: bytes) -> Path:
    """Write `data` to a new file beside `path`, then move it over `path`.

    The new file is created in the same directory, so `os.replace` stays on
    one filesystem and doesn't fall back to a copy. It's opened like any
    other new file, so the umask applies. A `path` that already exists lends
    it its mode. If anything fails, the new file is removed and `path` is
    left as it was.
    """
    import contextlib
    import os
    import secrets
    import stat

    temporary = path.parent / f".{path.name}.{secrets.token_hex(6)}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
        with contextlib.suppress(OSError):
            os.chmod(temporary, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
    return path


#: The byte-order mark some editors and Windows PowerShell 5.1 (`Out-File`,
#: `Set-Content -Encoding utf8`) put at the start of a UTF-8 file.
BOM = "\ufeff"


def without_bom(text: str) -> str:
    """`text` without one leading U+FEFF, if it has one."""
    return text[1:] if text.startswith(BOM) else text


def loads_frontmatter(text: str):
    """`frontmatter.loads(text)`, with one leading BOM dropped first (#424).

    A BOM hides the `---` that opens the frontmatter, so the whole block was
    read as body: `export-confluence` published it as page text, and
    `read_synthesis` returned no metadata, losing a licensed synthesis's
    `content_class`. **The one frontmatter reader**: every module parses
    frontmatter through this, and `tests/test_frontmatter_bom.py` fails on a
    direct `frontmatter.loads` or `frontmatter.load` anywhere else. The bytes
    on disk are left as they are (80's ruling on #424)."""
    import frontmatter

    return frontmatter.loads(without_bom(text))
