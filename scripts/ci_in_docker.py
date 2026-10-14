#!/usr/bin/env python3
"""Run CI's checks on Linux with Python 3.12, from any machine with Docker.

    python scripts/ci_in_docker.py              # everything
    python scripts/ci_in_docker.py --no-image   # skip building the runtime image

`scripts/check.py` runs the checks where you are, and on Windows that is not
where CI runs. Three problems reached this repository through that gap: provenance
hashes taken from CRLF files, a fixture that relied on Python 3.13's
docstring dedent, and an mcp release that broke `policyforge mcp` while every
test passed. Each would have failed here.

What it tests is the committed HEAD, cloned fresh inside the container, not
your working tree. That is what CI will see, and it keeps untracked and
ignored files (a `.env`, `local_content/`) out of the run. Uncommitted
changes are reported and left out.

Inside the container, in CI's order: the hashed install, ruff, pip-audit,
bandit, mdformat, pytest. Not semgrep: it runs from its own pinned image
(#83), which this container can't start (it has no Docker), and the local
gate already runs that image, on Linux, through Docker.
Then three checks CI does not run yet:

- every lock reproduces byte-for-byte from the command in its header, which
  is how a Dependabot edit made for Linux only gets caught;
- the runtime lock pins exactly the versions CI tests;
- `policyforge mcp` answers a real client (scripts/mcp_smoke.py).

Then, on the host, from a clean export of HEAD with private files planted in
it (a licensed catalog, a `.env`, an org config): none may reach Docker's
build context or the image, and the MCP server in the image must answer the
same client.

Requires Docker and git. The only third-party download not pinned by hash is
`uv`, pinned by version, used only to re-run the lock commands.
"""

from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Matches what regenerated the committed locks. Output formatting can change
#: between uv releases, so a different version can report a lock as stale.
#: 0.12.18, not 0.12.15, which carries CVE-2026-104843 (#282). Measured before
#: the bump: 0.12.18 regenerates all three locks from their headers with no
#: change, as 0.12.15 does.
UV_VERSION = "0.12.18"

IMAGE_TAG = "policyforge:ci-local"

#: Files that must never reach the runtime image, planted in the build context
#: to prove it. Paths relative to the repository root.
PRIVATE_DECOYS = [
    "local_content/hitrust/CSFLibraryReport.csv",
    ".env",
    "config/config.yaml",
    "config/topics.yaml",
    "data/frameworks/hitrust-csf/controls.json",
    "data/frameworks/govramp/controls.json",
    "output/drafts/access-control-policy.md",
]


def base_image() -> str:
    """The digest-pinned base, read from the runtime Dockerfile.

    One source of truth: tests/test_container.py checks that the dev container
    uses the same one.
    """
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^FROM\s+(\S+@sha256:[0-9a-f]{64})", text, re.MULTILINE)
    if not match:
        raise SystemExit("Dockerfile has no digest-pinned FROM line")
    return match.group(1)


#: Runs inside the container, from a fresh clone at /repo. Each step is
#: recorded, not fatal, so one failure does not hide the rest.
INNER = r"""
set -u
results=()
step() {
  local name="$1"; shift
  echo; echo "============================================================"; echo "$name"
  echo "============================================================"
  if ( set -e; "$@" ); then results+=("PASS  $name"); else results+=("FAIL  $name"); fi
}

apt-get -qq update >/dev/null && apt-get -qq install -y --no-install-recommends git >/dev/null
git config --global user.email ci-in-docker@example.invalid
git config --global user.name ci-in-docker
git clone -q /work/repo.bundle /repo && cd /repo
echo "Testing $(git log --oneline -1)"

python -m venv /venv && . /venv/bin/activate
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore

install() {
  pip install -q --require-hashes -r requirements/ci.txt
  pip install -q --require-hashes --only-binary :all: -r requirements/build.txt
  pip install -q --no-deps --no-build-isolation -e .
}
audit_lock() { pip-audit --disable-pip --require-hashes -r "$1"; }
step "install (hashed lock)" install
step "ruff (lint)" ruff check src tests scripts
step "ruff (format)" ruff format --check src tests scripts
step "pip-audit (project)" pip-audit
step "pip-audit (runtime lock)" audit_lock requirements/runtime.txt
step "bandit" bandit -q -c pyproject.toml -r src
mdformat_check() { git ls-files -z '*.md' | xargs -0 mdformat --check; }
step "mdformat" mdformat_check
step "pytest" pytest -q -p no:cacheprovider -n auto

step "runtime lock pins what CI tests" \
  python -m pytest -q -p no:cacheprovider tests/test_container.py -k runtime_lock
step "MCP server answers a client" python scripts/mcp_smoke.py -- policyforge mcp
locks_reproduce() {
  python -m venv /uv && /uv/bin/pip install -q "uv==$UV_VERSION"
  for lock in requirements/ci.txt requirements/ci-py310.txt requirements/runtime.txt; do
    cmd=$(sed -n '2s/^#    //p' "$lock")
    echo "$cmd"
    /uv/bin/$cmd -q
  done
  git diff --stat -- requirements/
  local status=0
  git diff --quiet -- requirements/ || status=1
  # Regenerating rewrote the committed locks. Put them back, so nothing after
  # this reads a lock that was repaired rather than the one committed.
  git checkout -- requirements/
  return $status
}
step "locks reproduce from their headers" locks_reproduce

echo; echo "============================================================"
echo "Summary (inside the container)"
echo "============================================================"
failed=0
for r in "${results[@]}"; do echo "  $r"; [[ "$r" == FAIL* ]] && failed=1; done
exit $failed
"""


def run(cmd: list[str], **kwargs) -> int:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, **kwargs).returncode


def dirty_tree() -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def container_checks(work: Path) -> bool:
    subprocess.run(
        ["git", "bundle", "create", str(work / "repo.bundle"), "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    (work / "inner.sh").write_text(INNER, encoding="utf-8", newline="\n")
    code = run(
        [
            "docker",
            "run",
            "--rm",
            "-e",
            f"UV_VERSION={UV_VERSION}",
            "-v",
            f"{work}:/work:ro",
            base_image(),
            "bash",
            "/work/inner.sh",
        ]
    )
    return code == 0


def build_context(work: Path, root: Path = REPO_ROOT) -> Path:
    """The Docker build context: HEAD's tree with its stored line endings, plus
    the private decoys the checks below look for.

    `core.autocrlf=false` (#171, the same class as #158): `git archive` applies
    the checkout's `core.autocrlf`, which is `true` system-wide on this
    machine, and that wrote CRLF into 490 of 680 files of the context. The
    image would then be built from bytes that aren't the tree's.

    And `core.eol=lf` (b5 on #206): autocrlf off stops git guessing, but a path
    `.gitattributes` marks `text` (or `text=auto`) is still converted to
    `core.eol`, which is native: CRLF on Windows. Today only `*.md` is marked
    (and pinned `eol=lf`), so the first setting alone was enough, but only
    while `.gitattributes` says so. Both together keep the stored line
    endings; a path marked `eol=crlf` is still converted, by its own explicit
    choice. **Line endings only** (9b on #206): `ident`,
    `working-tree-encoding`, `filter` and `export-subst` also change archived
    bytes, and none is set in this tree's `.gitattributes` today."""
    context = work / "context"
    context.mkdir()
    archive = subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "archive",
            "--format=tar",
            "HEAD",
        ],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(context, filter="data")
    for decoy in PRIVATE_DECOYS:
        path = context / decoy
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE DECOY: must not reach the image\n", encoding="utf-8")
    return context


def image_checks(work: Path) -> dict[str, bool]:
    results: dict[str, bool] = {}
    context = build_context(work)

    # The build context as Docker itself sees it, after .dockerignore. The
    # image below only COPYs what it names, so a loosened .dockerignore would
    # not show up there. It would show up here, and the next COPY someone
    # adds would carry it.
    probe_file = work / "context-probe.Dockerfile"
    checks = " ; ".join(
        f'if [ -e "/ctx/{d}" ]; then echo "IN BUILD CONTEXT: {d}"; leak=1; fi'
        for d in PRIVATE_DECOYS
    )
    probe_file.write_text(
        f"FROM {base_image()}\nCOPY . /ctx\nRUN leak=0 ; {checks} ; exit $leak\n",
        encoding="utf-8",
        newline="\n",
    )
    results["private files stay out of the build context"] = (
        run(
            [
                "docker",
                "build",
                "--no-cache",
                "--progress=plain",
                "-f",
                str(probe_file),
                str(context),
            ]
        )
        == 0
    )

    results["image builds"] = run(["docker", "build", "-t", IMAGE_TAG, str(context)]) == 0
    if not results["image builds"]:
        return results

    probe = " ; ".join(f'test -e "/app/{d}" && echo "LEAKED {d}"' for d in PRIVATE_DECOYS)
    leaked = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", IMAGE_TAG, "-c", probe + " ; true"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    print(leaked or "No planted private file reached the image.")
    results["private files stay out of the image"] = not leaked

    results["MCP server in the image answers a client"] = (
        run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "mcp_smoke.py"),
                "--",
                "docker",
                "run",
                "-i",
                "--rm",
                IMAGE_TAG,
                "mcp",
            ]
        )
        == 0
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-image", action="store_true", help="skip the runtime image checks")
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("docker is not on PATH.")
        return 2
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("docker is installed but the engine is not running (start Docker Desktop).")
        return 2

    dirty = dirty_tree()
    if dirty:
        print(f"Note: {len(dirty)} uncommitted change(s) are not tested. This checks HEAD:")
        for line in dirty[:10]:
            print(f"  {line}")

    results: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="ci-in-docker-") as tmp:
        work = Path(tmp)
        results["checks inside Linux / Python 3.12 (summary above)"] = container_checks(work)
        if not args.no_image:
            results.update(image_checks(work))

    print(f"\n{'=' * 60}\nci_in_docker summary\n{'=' * 60}")
    for label, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")
    if args.no_image:
        print("  SKIP  runtime image (--no-image)")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
