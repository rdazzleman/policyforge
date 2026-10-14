"""The build backend an editable install uses is hashed, like every other
package (173, CR-14).

`pip install --no-deps -e .` builds the project, and build isolation fetched
pyproject's `setuptools>=77` fresh and unhashed in every job that installs
it. Each such install now takes setuptools from `requirements/build.txt`
(hashed, `--only-binary`) and builds with `--no-build-isolation`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "requirements" / "build.txt"

#: Installs left resolving fresh ON PURPOSE, keyed by their text: the
#: `unpinned` CI leg is what `pip install` gives a user today, with no lock.
FRESH_ON_PURPOSE = ('pip install -e ".[dev,mcp,litellm,bedrock,vertex]"',)


def _commands(text: str) -> list[str]:
    """`text` as the commands it runs, in order: each line, split on `&&`,
    with comment lines (`#` in YAML and shell, `//` in JSONC) left out."""
    out: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(("#", "//")):
            continue
        out += [part.strip() for part in line.split("&&") if part.strip()]
    return out


def _checked(name: str, text: str) -> int:
    """Every editable install in `text` builds without isolation, after a
    hashed install of the build lock earlier in `text`. Returns how many it
    checked."""
    built = False
    count = 0
    for command in _commands(text):
        if "pip install" not in command:
            continue
        if "requirements/build.txt" in command and "--require-hashes" in command:
            built = True
            continue
        if not re.search(r"\s-e\s", command) or any(f in command for f in FRESH_ON_PURPOSE):
            continue
        count += 1
        assert "--no-build-isolation" in command, f"{name}: builds in isolation: {command}"
        assert built, f"{name}: no hashed install of requirements/build.txt before: {command}"
    return count


def test_the_build_lock_pins_setuptools_inside_pyprojects_range_with_hashes():
    text = BUILD.read_text(encoding="utf-8")
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", text, re.M)
    assert [name for name, _ in pins] == ["setuptools"], pins
    version = tuple(int(p) for p in pins[0][1].split("."))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    floor = re.search(r'^requires = \["setuptools>=(\d+)"\]$', pyproject, re.M)
    assert floor and version >= (int(floor.group(1)),), (pins, floor)
    assert re.findall(r"--hash=sha256:[0-9a-f]{64}", text)


def test_the_scripts_and_the_devcontainer_build_from_the_hashed_lock():
    for name in ("scripts/ci_in_docker.py", "scripts/publish_public.py",
                 ".devcontainer/devcontainer.json"):  # fmt: skip
        assert _checked(name, (ROOT / name).read_text("utf-8")), f"{name}: no install checked"


@pytest.mark.github_config
def test_every_workflow_builds_from_the_hashed_lock():
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    checked = {p.name: _checked(p.name, p.read_text("utf-8")) for p in workflows}
    # The population: the jobs that install the project, by hand from 173.
    for name in ("ci.yml", "content.yml", "framework-drift.yml", "export.yml"):
        assert checked.get(name), f"{name}: no editable install checked"


@pytest.mark.parametrize(
    "text, ok",
    [
        # Hand-written: what passes, and each way to get it wrong.
        ("pip install --require-hashes -r requirements/build.txt\n"
         "pip install --no-deps --no-build-isolation -e .\n", True),
        ("x && pip install --require-hashes -r requirements/build.txt && "
         "pip install --no-deps --no-build-isolation -e . && y", True),
        ("pip install --no-deps -e .\n", False),
        ("pip install --require-hashes -r requirements/build.txt\n"
         "pip install --no-deps -e .\n", False),
        ("pip install -r requirements/build.txt\n"
         "pip install --no-deps --no-build-isolation -e .\n", False),
        ("pip install --no-deps --no-build-isolation -e .\n"
         "pip install --require-hashes -r requirements/build.txt\n", False),
    ],
)  # fmt: skip
def test_the_check_reads_an_install_as_ci_would_run_it(text, ok):
    if ok:
        assert _checked("t", text) == 1
    else:
        with pytest.raises(AssertionError):
            _checked("t", text)
