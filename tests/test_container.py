"""The container files, held to what they claim. No Docker needed.

The runtime image, the dev container and scripts/ci_in_docker.py each rest
on a property that nothing else enforces. The image must not contain
licensed or private content. It must run the dependency versions CI tests.
All three must run on the same base image. And every install must be hashed.
Building the image proves some of this once. These tests keep it true for
changes that never build it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIGEST_FROM = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)


def _pins(lock: str) -> dict[str, str]:
    text = (ROOT / lock).read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_.\-]+)==(\S+)", text, re.MULTILINE))


def _dockerignore() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


# ---- one base image, pinned ---------------------------------------------


def test_every_base_image_is_pinned_by_digest_and_they_agree():
    """A tag moves, and a digest does not. Dependabot moves the digest, and when
    it does, the dev container and the CI-in-Docker script must move with the
    runtime image, or the "same environment as CI" claim quietly stops holding."""
    froms = []
    for dockerfile in ("Dockerfile", ".devcontainer/Dockerfile"):
        text = (ROOT / dockerfile).read_text(encoding="utf-8")
        found = [ref.split(" ")[0] for ref in DIGEST_FROM.findall(text)]
        assert found, f"{dockerfile} has no FROM line"
        froms += [(dockerfile, ref) for ref in found]

    for dockerfile, ref in froms:
        assert re.fullmatch(r"python:3\.12-slim@sha256:[0-9a-f]{64}", ref), (
            f"{dockerfile}: {ref} is not python:3.12-slim pinned by digest"
        )
    assert len({ref for _, ref in froms}) == 1, f"base images differ: {froms}"

    script = (ROOT / "scripts" / "ci_in_docker.py").read_text(encoding="utf-8")
    assert 'read_text(encoding="utf-8")' in script and "def base_image()" in script, (
        "ci_in_docker.py should read the base image from the Dockerfile, not repeat it"
    )


# ---- nothing private reaches an image -----------------------------------


def test_the_build_context_is_an_allowlist():
    """Fail closed. A denylist has to foresee every private file, and it fails
    open the day someone adds a new config.<env>.yaml."""
    patterns = _dockerignore()
    assert patterns[0] == "*", ".dockerignore must start by excluding everything"


def test_nothing_private_is_let_back_in():
    """The content this project promises never to redistribute. An image is
    something people push, so for this purpose it is a public path."""
    allowed = [p[1:].rstrip("/") for p in _dockerignore() if p.startswith("!")]
    private = (
        "local_content",
        "output",
        ".env",
        "config/config.yaml",
        "config/topics.yaml",
        ".git",
        ".venv",
        ".tools",
    )
    for pattern in allowed:
        for path in private:
            assert not (pattern == path or pattern.startswith(path + "/")), (
                f".dockerignore lets {pattern!r} into the build context"
            )
        assert pattern not in ("*", "**", "data", "data/frameworks", "config"), (
            f".dockerignore re-includes a whole tree: {pattern!r}"
        )


def test_licensed_frameworks_contribute_only_their_readmes():
    """HITRUST and GovRAMP are bring-your-own. Their directories hold a README
    in the repository, and a catalog someone copies in by hand must not be
    swept into an image."""
    allowed = [p[1:] for p in _dockerignore() if p.startswith("!")]
    for licensed in ("hitrust-csf", "govramp"):
        matching = [p for p in allowed if licensed in p]
        assert matching == [], f"{licensed} is named in .dockerignore: {matching}"
    wildcard = [p for p in allowed if p.startswith("data/frameworks/*")]
    assert wildcard in ([], ["data/frameworks/*/README.md"]), wildcard


def test_the_image_does_not_copy_the_whole_context():
    """`COPY . .` would make the allowlist the only guard. Each COPY naming
    what it takes is a second one."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert not re.search(r"^COPY\s+(--\S+\s+)*\.\s", text, re.MULTILINE)


# ---- the image runs what CI tests ----------------------------------------


def test_runtime_lock_pins_what_ci_tests():
    """The image installs requirements/runtime.txt, and CI tests
    requirements/ci.txt. Every package in the image must be at the version CI
    ran, or a green build says nothing about the image."""
    runtime, ci = _pins("requirements/runtime.txt"), _pins("requirements/ci.txt")
    assert runtime, "requirements/runtime.txt pins nothing"
    differs = {name: (version, ci.get(name)) for name, version in runtime.items()}
    differs = {k: v for k, v in differs.items() if v[0] != v[1]}
    assert differs == {}, f"runtime.txt versions not tested by CI (runtime, ci): {differs}"


def test_runtime_lock_carries_no_dev_tools():
    runtime = _pins("requirements/runtime.txt")
    dev = {"pytest", "ruff", "bandit", "pip-audit", "pre-commit", "semgrep"}
    assert dev.isdisjoint(runtime), sorted(dev & set(runtime))


# ---- every install hashed -------------------------------------------------


def test_every_pip_install_from_a_file_is_hashed():
    sources = {
        "Dockerfile": (ROOT / "Dockerfile").read_text(encoding="utf-8"),
        ".devcontainer/devcontainer.json": json.loads(
            re.sub(
                r"^\s*//.*$",
                "",
                (ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8"),
                flags=re.MULTILINE,
            )
        )["postCreateCommand"],
    }
    for name, text in sources.items():
        installs = re.findall(r"pip install[^&\n]*-r\s+\S+", text)
        assert installs, f"{name}: no pip install -r found"
        for install in installs:
            assert "--require-hashes" in install, f"{name}: unhashed install: {install}"
