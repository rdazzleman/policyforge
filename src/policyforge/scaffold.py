"""Starting a PolicyForge project directory outside a clone of this repository.

Every command resolves its inputs relative to where it runs:
`config/config.yaml`, `config/topics.yaml`, `data/frameworks/*/controls.json`,
`output/`. From a clone those are all already there. From an installed
package — `pip install`, `pipx`, Homebrew — none of them are, and the first
command a new user runs fails looking for a catalog they were never given.

So the redistributable catalogs and the example configs ship inside the
package (`policyforge._bundled`, mapped from `data/frameworks/` and `config/`
in pyproject.toml rather than copied, so there is one source of truth), and
`policyforge init` lays them out as a project directory.

What ships is decided by licence, not by what happens to be on disk. The
package data in pyproject.toml names the bundled catalogs one by one: a glob
would package a licensed HITRUST export somebody had dropped into their own
checkout's `data/frameworks/`. `tests/test_scaffold.py` holds that list to
the catalogs whose manifests say `public-domain`.

Nothing here overwrites. A file that already exists is kept and reported, so
running `init` again — or in a clone — is safe, and a catalog someone has
re-fetched or a config they have edited is never replaced by the packaged one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

#: Catalogs whose licence lets them ship. Mirrors the package-data list in
#: pyproject.toml; the test compares the two.
BUNDLED_CATALOGS = (
    "arc-ampe",
    "cfr-170-315-onc-certification",
    "cfr-171-information-blocking",
    "cfr-42-part-2-sud-records",
    "fedramp",
    "hipaa-breach-notification-rule",
    "hipaa-privacy-rule",
    "hipaa-security-rule",
    "nist-800-171-r3",
    "nist-800-53-r5",
    "nist-ai-rmf",
    "nist-ai-rmf-playbook",
    "nist-csf-2-0",
)

#: Bring-your-own catalogs: only the README explaining how to supply one.
BYOC_CATALOGS = ("govramp", "hitrust-ai", "hitrust-csf", "soc2-tsc")

CATALOG_FILES = ("README.md", "controls.json", "framework.yaml")

CONFIG_EXAMPLES = ("config.example.yaml", "topics.example.yaml")

#: Written only when the directory has no .gitignore. The same entries this
#: repository ignores for the same reasons: the config names your model
#: account and org, the registry names your teams, `local_content/` holds
#: licensed exports, and `output/` holds drafts about your organization.
GITIGNORE = """\
# Written by `policyforge init`.

# Secrets and local config
.env
.env.*
config/config.yaml
config/config.*.yaml
!config/config.example.yaml
# Topic registry: names your internal teams
config/topics.yaml
# Crosswalk overlays: your organization's mapping decisions
config/crosswalks/

# Licensed / bring-your-own content: never redistribute this
local_content/*
!local_content/.gitkeep

# Drafts generated for your organization
output/

# Rebuilt from the controls.json files by `policyforge map`
data/frameworks/crosswalk.json
data/frameworks/crosswalk.overlays.json
"""


@dataclass
class ScaffoldReport:
    written: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)


def bundled_root():
    """The packaged catalogs and example configs.

    Installed from a wheel, `policyforge._bundled` is real package data. An
    editable install maps it back to `data/` and `config/` in the checkout;
    if an older editable install predates that mapping, the checkout itself
    is the fallback, since that is what the mapping points at.
    """
    try:
        root = resources.files("policyforge._bundled")
        if root.joinpath("frameworks", "nist-800-53-r5", "controls.json").is_file():
            return root
    except ModuleNotFoundError:
        pass
    checkout = Path(__file__).resolve().parents[2]
    if not (checkout / "data" / "frameworks" / "nist-800-53-r5" / "controls.json").is_file():
        raise RuntimeError(
            "This PolicyForge installation has no bundled catalogs, so there is "
            "nothing to lay out. Reinstall it from a release, or run from a clone of "
            "the repository, where data/frameworks/ already holds them."
        )
    return _CheckoutLayout(checkout)


class _CheckoutLayout:
    """`data/frameworks` and `config` in a clone, addressed like the package."""

    def __init__(self, checkout: Path):
        self._dirs = {"frameworks": checkout / "data" / "frameworks", "config": checkout / "config"}

    def joinpath(self, first: str, *rest: str) -> Path:
        return self._dirs[first].joinpath(*rest)


def _plan(root) -> list[tuple[object, Path]]:
    """(source, destination relative to the project) for every packaged file."""
    plan: list[tuple[object, Path]] = []
    for name in CONFIG_EXAMPLES:
        plan.append((root.joinpath("config", name), Path("config") / name))
    for catalog in BUNDLED_CATALOGS:
        for name in CATALOG_FILES:
            plan.append(
                (
                    root.joinpath("frameworks", catalog, name),
                    Path("data") / "frameworks" / catalog / name,
                )
            )
    for catalog in BYOC_CATALOGS:
        plan.append(
            (
                root.joinpath("frameworks", catalog, "README.md"),
                Path("data") / "frameworks" / catalog / "README.md",
            )
        )
    return plan


def init_project(directory: Path) -> ScaffoldReport:
    """Lay out a project in `directory`, creating it if needed. Never overwrites."""
    report = ScaffoldReport()
    root = bundled_root()

    def place(relative: Path, content: bytes) -> None:
        target = directory / relative
        if target.exists():
            report.kept.append(relative)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        report.written.append(relative)

    for source, relative in _plan(root):
        # Bytes, not text: a catalog is compared against its recorded hash,
        # and a newline translation would make an honest copy look tampered.
        place(relative, source.read_bytes())
    place(Path("local_content") / ".gitkeep", b"")
    place(Path(".gitignore"), GITIGNORE.encode("utf-8"))
    return report
