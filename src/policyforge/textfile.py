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
