"""The fallback pyproject reader agrees with tomllib on the real file.

`tests/_pyproject.py` uses `tomllib` where it exists and a minimal reader
where it does not. The minimal reader only matters on Python 3.10 without
`tomli`, which is the environment least likely to be checked by hand — so
it is checked here, on every interpreter that has `tomllib`, against the
sections the suite actually reads.
"""

from __future__ import annotations

import sys

import pytest

from tests import _pyproject

SECTIONS = (
    ("project", "version"),
    ("project", "license"),
    ("project", "scripts"),
    ("tool", "setuptools", "packages"),
    ("tool", "setuptools", "package-dir"),
    ("tool", "setuptools", "package-data"),
)


def _dig(data: dict, path: tuple[str, ...]):
    for key in path:
        data = data[key]
    return data


def test_the_fallback_reads_what_the_suite_reads_exactly_as_tomllib_does():
    tomllib = pytest.importorskip("tomllib")
    text = _pyproject.PYPROJECT.read_text(encoding="utf-8")

    reference = tomllib.loads(text)
    minimal = _pyproject._minimal_loads(text)

    for path in SECTIONS:
        assert _dig(minimal, path) == _dig(reference, path), ".".join(path)


def test_loads_falls_back_when_neither_toml_module_exists(monkeypatch):
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)

    data = _pyproject.load_pyproject()

    assert data["project"]["scripts"]["policyforge"] == "policyforge.cli:cli"
    assert "policyforge" in data["tool"]["setuptools"]["packages"]


def test_the_minimal_reader_handles_the_shapes_pyproject_uses():
    text = (
        '[project]\nname = "x"  # trailing comment\nversion = "1.2.0"\n'
        "[project.scripts]\n"
        'policyforge = "policyforge.cli:cli"\n'
        "[tool.setuptools]\n"
        "packages = [\n"
        '    "a",  # first\n'
        '    "b.c",\n'
        "]\n"
        '[tool.setuptools."package-dir"]\n'
        '"" = "src"\n'
        '"pkg.sub" = "data/dir"\n'
        "[tool.setuptools.package-data]\n"
        '"pkg.sub" = ["one/f.json", "two/g.yaml"]\n'
    )

    data = _pyproject._minimal_loads(text)

    assert data["project"] == {
        "name": "x",
        "version": "1.2.0",
        "scripts": {"policyforge": "policyforge.cli:cli"},
    }
    assert data["tool"]["setuptools"]["packages"] == ["a", "b.c"]
    assert data["tool"]["setuptools"]["package-dir"] == {"": "src", "pkg.sub": "data/dir"}
    assert data["tool"]["setuptools"]["package-data"] == {"pkg.sub": ["one/f.json", "two/g.yaml"]}
