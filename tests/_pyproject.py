"""Read pyproject.toml in the test suite, on every Python the project supports.

`tomllib` arrived in Python 3.11 and the project's floor is 3.10, so a
test that imports it cannot run on the floor — and one that
`importorskip`s it skips the version and packaging checks on exactly the
interpreter least likely to have been tried. Adding `tomli` to the dev
extra would work but moves the hashed lock for a test-only need.

So: `tomllib` where it exists, `tomli` where it is installed (it comes in
transitively on most dev environments), and otherwise a reader small
enough to audit that handles the subset pyproject.toml uses — table
headers, bare and quoted keys, string values, and arrays of strings across
lines with comments. `tests/test_pyproject_reader.py` holds the fallback
equal to `tomllib` on the real file wherever `tomllib` is available, so a
divergence fails on 3.12 before the fallback can mislead anyone on 3.10.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

_HEADER = re.compile(r"^\[(?P<path>[^\]]+)\]\s*$")
#: A quoted key may be empty: `"" = "src"` is how package-dir names the
#: root package, and it is in this repository's pyproject.
_KEY = re.compile(r'^(?:"(?P<quoted>[^"]*)"|(?P<bare>[A-Za-z0-9_.-]+))\s*=\s*(?P<rest>.*)$')
_STRING = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*(?:#.*)?$')


def _strip_comment(line: str) -> str:
    """Drop a trailing comment that is not inside a string."""
    out, in_string = [], False
    for ch in line:
        if ch == '"':
            in_string = not in_string
        if ch == "#" and not in_string:
            break
        out.append(ch)
    return "".join(out).strip()


def _split_key(path: str) -> list[str]:
    """`tool.setuptools.package-dir` -> parts; quoted segments stay whole."""
    parts, current, in_string = [], [], False
    for ch in path:
        if ch == '"':
            in_string = not in_string
        elif ch == "." and not in_string:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append("".join(current).strip())
    return parts


def _parse_strings(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', text)]


def _minimal_loads(text: str) -> dict:
    """The subset of TOML this repository's pyproject uses, and no more.

    Anything else — inline tables, numbers, booleans, multi-line strings —
    is left out rather than guessed at. The tests that use this read
    strings and arrays of strings, and `tests/test_pyproject_reader.py`
    proves that is all they need.
    """
    root: dict = {}
    table = root
    lines = iter(text.splitlines())
    for raw in lines:
        line = _strip_comment(raw)
        if not line:
            continue
        header = _HEADER.match(line)
        if header:
            table = root
            for part in _split_key(header.group("path")):
                table = table.setdefault(part, {})
            continue
        key_match = _KEY.match(line)
        if not key_match:
            continue
        quoted = key_match.group("quoted")
        key = quoted if quoted is not None else key_match.group("bare")
        rest = key_match.group("rest").strip()
        if rest.startswith("["):
            body = rest[1:]
            while "]" not in body:
                body += " " + _strip_comment(next(lines))
            table[key] = _parse_strings(body[: body.index("]")])
            continue
        string = _STRING.match(rest)
        if string:
            table[key] = string.group(1)
    return root


def loads(text: str) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            return _minimal_loads(text)
    return tomllib.loads(text)


def load_pyproject() -> dict:
    return loads(PYPROJECT.read_text(encoding="utf-8"))
