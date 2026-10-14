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

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIGEST_FROM = re.compile(r"^FROM\s+(\S+)", re.MULTILINE)


def _pins(lock: str) -> dict[str, str]:
    text = (ROOT / lock).read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_.\-]+)==(\S+)", text, re.MULTILINE))


def _dockerignore() -> list[str]:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _map_controls(dockerfile: str) -> list[str]:
    """The catalogs the build stage runs `policyforge.cli map` over, as the
    Dockerfile spells them: the `--controls` of the `RUN` that runs it, with
    its continuation lines joined. A `--controls` anywhere else is not read."""
    joined = re.sub(r"\\\n", " ", dockerfile)
    runs = [
        line
        for line in joined.splitlines()
        if line.lstrip().startswith("RUN") and "policyforge.cli map" in line
    ]
    assert len(runs) == 1, f"expected one RUN that runs `map`, found {len(runs)}"
    return re.findall(r"--controls[ =]\s*(\S+)", runs[0])


def _docker_pattern(pattern: str) -> re.Pattern:
    """A .dockerignore pattern as Docker matches it: `*` and `?` stay within
    one path segment, `**` crosses any number of them, `[...]` is a class."""
    pattern = pattern.strip().lstrip("/").rstrip("/")
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif c == "*":
            out, i = out + "[^/]*", i + 1
        elif c == "?":
            out, i = out + "[^/]", i + 1
        elif c == "[" and "]" in pattern[i + 1 :]:
            end = pattern.index("]", i + 1)
            body = pattern[i + 1 : end]
            out += "[" + ("^" + body[1:] if body.startswith("^") else body) + "]"
            i = end + 1
        else:
            out, i = out + re.escape(c), i + 1
    return re.compile(out)


def _lets_in(pattern: str, path: str) -> bool:
    """Whether re-including `pattern` brings `path` into the build context:
    it matches the path, or a directory above it (a directory comes in whole)."""
    parts = path.split("/")
    matcher = _docker_pattern(pattern)
    return any(matcher.fullmatch("/".join(parts[:n])) for n in range(1, len(parts) + 1))


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


def test_public_catalogs_are_allowed_and_licensed_catalogs_are_not():
    """Derive the expected set from the framework.yaml files (#444): every
    catalog declaring licence: public-domain must be allowed in by name, and
    no allowed pattern may let in a catalog file of one without that
    declaration (only its README comes in)."""
    import yaml

    frameworks_dir = ROOT / "data" / "frameworks"
    public_catalogs: set[str] = set()
    licensed_catalogs: set[str] = set()

    for path in frameworks_dir.iterdir():
        if not path.is_dir():
            continue
        yaml_path = path / "framework.yaml"
        if yaml_path.is_file():
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if data.get("licence") == "public-domain":
                public_catalogs.add(path.name)
            else:
                licensed_catalogs.add(path.name)
        else:
            licensed_catalogs.add(path.name)

    assert public_catalogs, "no public catalogs found under data/frameworks"

    allowed = [p[1:] for p in _dockerignore() if p.startswith("!")]
    allowed_dirs = {
        p.removeprefix("data/frameworks/").rstrip("/")
        for p in allowed
        if p.startswith("data/frameworks/") and not p.startswith("data/frameworks/*")
    }

    missing_public = sorted(public_catalogs - allowed_dirs)
    assert not missing_public, f"public catalogs missing from .dockerignore: {missing_public}"

    # By pattern, as Docker matches it, not by name: `!data/frameworks/h*/`
    # names no catalog and still lets HITRUST's controls.json in (b5 on PR 447).
    for licensed in sorted(licensed_catalogs):
        catalog = f"data/frameworks/{licensed}/controls.json"
        letting_in = [p for p in allowed if _lets_in(p, catalog)]
        assert letting_in == [], f".dockerignore lets {catalog} in: {letting_in}"


@pytest.mark.parametrize(
    ("pattern", "lets_in"),
    [
        # Hand-written from Docker's .dockerignore rules, not from _lets_in.
        ("data/frameworks/hitrust-csf/", True),
        ("data/frameworks/hitrust-csf", True),
        ("data/frameworks/hitrust-*/", True),
        ("data/frameworks/h*/", True),
        ("data/frameworks/hitrust-?sf/", True),
        ("data/frameworks/[h]itrust-csf/", True),
        ("data/*/hitrust-csf/", True),
        ("**/controls.json", True),
        ("data/frameworks/*/controls.json", True),
        ("data/frameworks/", True),
        ("/data/frameworks/hitrust-csf/", True),
        ("data/frameworks/*/README.md", False),
        ("data/frameworks/hitrust-csf/README.md", False),
        ("data/frameworks/nist-800-53-r5/", False),
        ("data/frameworks/hitrust-ai/", False),
        ("data/frameworks/hitrust", False),
        ("h*/", False),
        # `*` stays within one segment: these name files one or two levels up.
        ("data/*.json", False),
        ("*/controls.json", False),
        ("data/*", True),
    ],
)
def test_a_pattern_is_judged_by_what_it_lets_in(pattern, lets_in):
    """`.dockerignore` matching, held to Docker's rules on a licensed catalog's
    own file. The globs b5 found on PR 447 are among the cases."""
    assert _lets_in(pattern, "data/frameworks/hitrust-csf/controls.json") is lets_in


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


# ---- the image's crosswalk (448) --------------------------------------------


def test_the_image_crosswalk_is_built_from_every_public_catalog():
    """`addresses`, `coverage` and `synthesize` read the crosswalk the build
    stage makes. It was built from four catalogs while the image held thirteen,
    so a topic anchored in one of the other nine found no mapping (448). The
    expected set comes from the manifests, not from the Dockerfile or from
    `.dockerignore`: every catalog declaring `licence: public-domain` and
    holding a controls.json."""
    import yaml

    frameworks_dir = ROOT / "data" / "frameworks"
    public: set[str] = set()
    for path in frameworks_dir.iterdir():
        manifest = path / "framework.yaml"
        if not manifest.is_file() or not (path / "controls.json").is_file():
            continue
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if data.get("licence") == "public-domain":
            public.add(path.name)
    assert len(public) >= 13, (
        f"fewer public catalogs than the thirteen this was written for: {public}"
    )

    listed = _map_controls((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    names = [p.removeprefix("data/frameworks/").removesuffix("/controls.json") for p in listed]
    assert [f"data/frameworks/{n}/controls.json" for n in names] == listed, (
        f"a --controls that is not data/frameworks/<catalog>/controls.json: {listed}"
    )
    assert len(set(names)) == len(names), f"a catalog listed twice: {names}"
    assert sorted(set(names) ^ public) == [], (
        f"the build stage's map is not over the public catalogs: "
        f"missing {sorted(public - set(names))}, extra {sorted(set(names) - public)}"
    )


def test_every_catalog_the_crosswalk_is_built_from_is_in_the_build_context():
    """A catalog the map names but `.dockerignore` keeps out fails the build
    at that step, after the dependencies are installed."""
    allowed = [p[1:] for p in _dockerignore() if p.startswith("!")]
    for path in _map_controls((ROOT / "Dockerfile").read_text(encoding="utf-8")):
        assert any(_lets_in(p, path) for p in allowed), f"{path} is not let into the context"


@pytest.mark.parametrize(
    ("dockerfile", "expected"),
    [
        # Hand-written Dockerfiles: how the list is read.
        (
            "RUN python -m policyforge.cli map \\\n  --controls a/controls.json \\\n"
            "  --controls b/controls.json \\\n  --out x.json\n",
            ["a/controls.json", "b/controls.json"],
        ),
        (
            "RUN python -m policyforge.cli map --controls=a/controls.json --out x\n",
            ["a/controls.json"],
        ),
        (
            "RUN pip install x\nRUN python -m policyforge.cli synthesize --controls z.json\n"
            "RUN python -m policyforge.cli map --controls a.json\n",
            ["a.json"],
        ),
    ],
)
def test_the_maps_catalogs_are_read_as_the_dockerfile_writes_them(dockerfile, expected):
    assert _map_controls(dockerfile) == expected
