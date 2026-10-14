"""`scripts/macos_wheels.py`: every runtime pin a Mac would install has a
hashed wheel for it (#325; 80's ruling, 5263: arm64 blocks, x86_64 is advisory).

Real pip, offline: each case writes small but valid wheels to a directory and
points pip at it with `--no-index --find-links`, so what answers is the same
`pip download` the check runs against PyPI, not a stand-in for it. The
wheels' platforms are written from what PyPI serves (an arm64-only package is
cryptography 50's shape), not from the script's own target list.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import macos_wheels as mw

ARM = "cp312-cp312-macosx_11_0_arm64"
INTEL = "cp312-cp312-macosx_10_15_x86_64"
ANY = "py3-none-any"
FORMULA = (
    'class Policyforge < Formula\n  depends_on "rust" => :build\n  depends_on "python@3.12"\nend\n'
)
PYPROJECT = """
[project]
name = "policyforge"
dependencies = ["alpha>=1", "beta"]

[project.optional-dependencies]
ext = ["delta"]
"""


SDIST = "sdist"

#: An in-tree PEP 517 backend that needs nothing installed, so pip can read
#: the sdist's metadata OFFLINE, as it reads a real one from PyPI. Without it
#: an sdist would fail for an unrelated reason (no setuptools on the offline
#: index), and a pip that falls back to sdists would look like it refused.
_BACKEND = """
import os


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    info = os.path.join(metadata_directory, "{name}-{version}.dist-info")
    os.makedirs(info)
    with open(os.path.join(info, "METADATA"), "w") as f:
        f.write("Metadata-Version: 2.1\\nName: {name}\\nVersion: {version}\\n")
    return "{name}-{version}.dist-info"


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    raise NotImplementedError("metadata only")
"""


def _sdist(where: Path, name: str, version: str) -> Path:
    import io
    import tarfile

    path = where / f"{name}-{version}.tar.gz"
    files = {
        "PKG-INFO": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        "pyproject.toml": '[build-system]\nrequires = []\nbuild-backend = "backend"\n'
        'backend-path = ["."]\n',
        "backend.py": _BACKEND.format(name=name, version=version),
    }
    with tarfile.open(path, "w:gz") as tar:
        for member, text in files.items():
            data = text.encode("utf-8")
            entry = tarfile.TarInfo(f"{name}-{version}/{member}")
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    return path


def _wheel(where: Path, name: str, version: str, tag: str) -> Path:
    if tag == SDIST:
        return _sdist(where, name, version)
    path = where / f"{name}-{version}-{tag}.whl"
    info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as whl:
        whl.writestr(
            f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        )
        whl.writestr(f"{info}/WHEEL", f"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: {tag}\n")
        whl.writestr(f"{info}/RECORD", "")
    return path


def _world(
    tmp_path: Path, pins: list[tuple[str, str, list[str], str, list[str]]]
) -> tuple[str, mw.Download]:
    """(lock text, an offline pip) for pins of (name, version, wheel tags,
    marker, via). The lock carries every wheel's sha256, as uv writes it."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    out = []
    for name, version, tags, marker, via in pins:
        hashes = [
            hashlib.sha256(_wheel(wheels, name, version, tag).read_bytes()).hexdigest()
            for tag in tags
        ] or ["0" * 64]
        head = f"{name}=={version}" + (f" ; {marker}" if marker else "")
        out.append(head + " \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes))
        out.append(
            "    # via " + via[0]
            if len(via) == 1
            else "    # via\n" + "\n".join(f"    #   {v}" for v in via)
        )
    return "\n".join(out) + "\n", mw.pip_download(("--no-index", "--find-links", str(wheels)))


ROOT = mw.ROOT
#: The shape of the real lock: pure wheels, a two-platform binary, and an
#: extra whose dependency ships arm64 only (cryptography <- pyjwt <- mcp).
BASE = [
    ("alpha", "1.0", [ANY], "", [ROOT]),
    ("beta", "2.0", [ARM, INTEL], "", [ROOT]),
    ("delta", "4.0", [ANY], "", [ROOT]),
    ("gamma", "3.0", [ARM], "", ["delta"]),
    ("winonly", "1.0", ["cp312-cp312-win_amd64"], "sys_platform == 'win32'", ["alpha"]),
]


def _check(tmp_path, pins, targets=mw.TARGETS):
    lock, download = _world(tmp_path, pins)
    return mw.check(lock, FORMULA, PYPROJECT, download, targets)


def test_an_intel_only_gap_is_named_counted_and_passes(tmp_path):
    code, lines = _check(tmp_path, BASE)
    report = "\n".join(lines)
    assert code == 0, report
    assert (
        "cp3.12 macosx_11_0_arm64 (blocking): 4 of 4 pins have a wheel (1 don't apply on a Mac)"
        in report
    )
    assert "cp3.12 macosx_10_15_x86_64 (advisory): 3 of 4 pins" in report
    assert "ADVISORY gamma==3.0: no wheel; pulled in by extra ext (gamma <- delta)" in report
    assert lines[-1] == "result: passed: 0 missing on a blocking platform, 1 advisory"


def test_an_arm64_miss_fails_and_every_miss_is_named(tmp_path):
    """pip stops at its first miss; the check names both."""
    pins = [
        *BASE,
        ("epsilon", "5.0", ["cp312-cp312-manylinux_2_17_x86_64"], "", [ROOT]),
        ("zeta", "6.0", [INTEL], "", ["beta"]),
    ]
    code, lines = _check(tmp_path, pins)
    report = "\n".join(lines)
    assert code == 1, report
    assert "MISSING epsilon==5.0: no wheel; pulled in by nothing the pyproject names" in report
    assert "MISSING zeta==6.0: no wheel; pulled in by base (zeta <- beta)" in report
    assert lines[-1] == "result: FAILED: 2 missing on a blocking platform, 2 advisory"


def test_intel_made_blocking_fails_the_cryptography_shape(tmp_path):
    """80's arm, as a test: the same world, x86_64 blocking, goes red."""
    targets = (mw.TARGETS[0], mw.Target("macosx_10_15_x86_64", "x86_64", blocking=True))
    code, lines = _check(tmp_path, BASE, targets)
    assert code == 1 and "MISSING gamma==3.0" in "\n".join(lines), lines


def test_a_pin_pypi_serves_only_as_an_sdist_is_missing_not_built(tmp_path):
    """Every real project serves an sdist beside its wheels, and pip, given
    `--platform` with `--no-deps`, takes the sdist when no wheel fits unless
    told wheels only. Then a missing wheel passes silently (1d's arm on PR
    361: cryptography's advisory vanished on real PyPI). Here eta has an sdist
    pip can read offline, and an arm64 wheel only."""
    pins = [
        *BASE,
        ("eta", "5.0", [SDIST, ARM], "", ["beta"]),
        ("theta", "6.0", [SDIST], "", [ROOT]),
    ]
    code, lines = _check(tmp_path, pins)
    report = "\n".join(lines)
    assert code == 1, report
    assert "MISSING theta==6.0: no wheel" in report
    assert "ADVISORY eta==5.0: no wheel; pulled in by base (eta <- beta)" in report


def test_x86_64_is_the_advisory_one_at_10_15():
    """The ruling's two targets, pinned (an edit to either is a ruling change)."""
    assert [(t.platform, t.blocking) for t in mw.TARGETS] == [
        ("macosx_11_0_arm64", True),
        ("macosx_10_15_x86_64", False),
    ]


def test_markers_are_a_macs_not_the_hosts(tmp_path):
    """A darwin-only pin is checked on every host, and a win32-only one never:
    pip would evaluate both against the interpreter running it."""
    pins = [*BASE, ("macdep", "1.0", ["cp312-cp312-win_amd64"], "sys_platform == 'darwin'", [ROOT])]
    code, lines = _check(tmp_path, pins)
    report = "\n".join(lines)
    assert code == 1 and "MISSING macdep==1.0" in report, report
    assert "winonly" not in report


def test_a_marker_on_what_isnt_known_about_the_mac_refuses(tmp_path):
    pins = [*BASE, ("kern", "1.0", [ANY], "platform_release >= '20'", [ROOT])]
    code, lines = _check(tmp_path, pins)
    assert code == 2 and "kern==1.0: its marker depends on" in lines[0], lines


def test_a_wheel_whose_hash_the_lock_lacks_refuses(tmp_path):
    lock, download = _world(tmp_path, BASE)
    first = lock.index("--hash=sha256:", lock.index("beta==")) + len("--hash=sha256:")
    lock = lock[:first] + "f" * 64 + lock[first + 64 :]
    code, lines = mw.check(lock, FORMULA, PYPROJECT, download)
    assert code == 2 and "hash isn't in the lock" in lines[0], lines


@pytest.mark.parametrize(
    "stderr, says",
    [
        (
            "WARNING: Retrying ... NewConnectionError(...)\n"
            "ERROR: No matching distribution found for alpha==1.0",
            "couldn't reach the index",
        ),
        ("ERROR: something else entirely", "not a missing wheel"),
    ],
)
def test_a_pip_failure_that_isnt_a_missing_wheel_is_never_a_pass(stderr, says):
    lock = (
        "alpha==1.0 \\\n    --hash=sha256:"
        + "a" * 64
        + "\n    # via policyforge (pyproject.toml)\n"
    )

    def download(requirements, platform, python):
        return subprocess.CompletedProcess([], 1, "", stderr)

    code, lines = mw.check(lock, FORMULA, PYPROJECT, download)
    assert code == 2 and says in lines[0], lines


def test_every_python_the_formula_names_is_checked():
    asked = []

    def download(requirements, platform, python):
        asked.append((platform, python))
        return subprocess.CompletedProcess([], 0, "", "")

    lock = (
        "alpha==1.0 \\\n    --hash=sha256:"
        + "a" * 64
        + "\n    # via policyforge (pyproject.toml)\n"
    )
    formula = FORMULA + '  depends_on "python@3.13"\n'
    code, _ = mw.check(lock, formula, PYPROJECT, download)
    assert code == 0
    assert sorted({p for _, p in asked}) == ["3.12", "3.13"] and len(asked) == 4


def test_a_formula_without_a_python_refuses():
    code, lines = mw.check(
        "alpha==1.0 --hash=sha256:" + "a" * 64 + "\n", "class X\nend\n", PYPROJECT, None
    )
    assert code == 2 and "python@X.Y" in lines[0], lines


def test_a_lock_line_that_isnt_a_hashed_pin_refuses():
    for lock in ("alpha>=1.0 --hash=sha256:" + "a" * 64 + "\n", "alpha==1.0\n", "-e .\n"):
        code, lines = mw.check(lock, FORMULA, PYPROJECT, None)
        assert code == 2, (lock, lines)


def test_the_real_lock_parses_whole():
    """Every pin, against a count taken by a different instrument: the
    unindented `name==` lines, counted by a plain line scan."""
    text = mw.LOCK.read_text(encoding="utf-8")
    expected = sum(1 for line in text.splitlines() if line[:1].isalnum() and "==" in line)
    pins = mw.parse_lock(text)
    assert len(pins) == expected > 40
    assert all(p.hashes and p.parents for p in pins)
