"""Output read from a child process is UTF-8, decoded where it is used.

`subprocess.run(..., text=True)` with no `encoding` decodes with the locale
codec -- cp1252 on most Windows installs -- and git writes UTF-8. Measured
on #287: a page named `café.md` came back from `git status -z` as
`cafÃ©.md`, never matched its own path, and `uncommitted()` reported a
modified file as clean; a reviewer named José was recorded as `JosÃ©`.

**Passing `encoding="utf-8", errors="strict"` does not make it loud.**
`communicate()` decodes in a reader thread on Windows, so a bad byte kills
that thread and the call RETURNS, with `stdout=None` and the traceback on
stderr only -- the caller then fails somewhere else, or not at all
(measured on #287; the same shape as #285's `TypeError`). So a site whose
output is DATA captures bytes and decodes here, in the caller's thread,
where a bad byte raises.

Which sites are data and which are only shown to a person is 80's ruling on
#287: parsed into a catalog, a path, a SHA or a decision -> `strict_text`;
shown in a message -> `encoding="utf-8", errors="replace"` on the call,
which cannot raise.
"""

from __future__ import annotations

from collections.abc import Sequence


class UndecodableOutput(RuntimeError):
    """A child wrote bytes that are not UTF-8 where the output is data.

    Deliberately not a `subprocess.SubprocessError`: the call sites catch
    that to mean "git could not answer" and degrade to None, and a byte that
    would have become U+FFFD inside a catalog, a path or a SHA must not be
    folded into "unknown".
    """


def strict_text(data: bytes | None, *, site: str, argv: Sequence[str]) -> str:
    """`data` decoded as UTF-8, or `UndecodableOutput` naming where and what.

    `None` (nothing captured) reads as empty, the same as a child that wrote
    nothing.
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        command = " ".join(str(a) for a in argv[:3])
        raise UndecodableOutput(
            f"{site}: `{command}` wrote output that is not UTF-8 "
            f"(byte 0x{data[exc.start]:02x} at position {exc.start}). It is read as data "
            "here, so it is refused rather than read with the bad byte replaced."
        ) from exc
