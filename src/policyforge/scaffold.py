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

from policyforge.topics.scope_facts import ScopeFacts

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


#: What `init` writes into `config/config.yaml` for each provider it can start
#: from (#176). The same figures as the blocks in `config.example.yaml`, which
#: `tests/test_scaffold.py` holds this table to. `local` is the default: it
#: needs no key and sends nothing off the machine.
LLM_STARTERS: dict[str, dict[str, str]] = {
    "local": {
        "provider": "local",
        "model": "qwen3:14b",
        "base_url": "http://localhost:11434/v1",
    },
    "anthropic": {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "gemini": {
        "provider": "gemini",
        "model": "gemini-3.6-flash",
        "api_key_env": "GEMINI_API_KEY",
    },
    "litellm": {
        "provider": "litellm",
        "model": "openrouter/z-ai/glm-5.3-flash",
        "api_key_env": "OPENROUTER_API_KEY",
    },
}

#: The catalogs the starter registry's anchors need: 800-53 for the controls,
#: AI RMF for its AI topics, and the three HIPAA rules for its two PHI topics,
#: which otherwise read as missing (the review that filed #176 measured
#: exactly that for the AI topics, and #420 for these). All are bundled and
#: public. The three HIPAA rules go together (80's ruling on #420): they are
#: one regulation, 45 CFR Part 164, and Privacy and Breach Notification
#: without the Security Rule is a partial HIPAA that looks complete. A
#: registry with no PHI topics drops them out of its numbers (adoption).
STARTER_CATALOGS = (
    "nist-800-53-r5",
    "nist-ai-rmf",
    "hipaa-security-rule",
    "hipaa-privacy-rule",
    "hipaa-breach-notification-rule",
)


@dataclass
class ScaffoldReport:
    written: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    #: What the written `config/config.yaml` names, for the next-steps text.
    provider: str = ""
    api_key_env: str = ""
    #: The scope facts the registry was built from, and what the rule did with
    #: them (22, part 3). `selection` is None when `topics.yaml` was kept.
    facts: object = None
    selection: object = None
    #: Lines to print after the file list.
    notes: list[str] = field(default_factory=list)


@dataclass
class RefreshReport:
    """What `init --refresh` did to the packaged files of an existing project."""

    updated: list[tuple[Path, str, str]] = field(
        default_factory=list
    )  # (file, old, new) sha256[:8]
    added: list[Path] = field(default_factory=list)
    unchanged: int = 0
    #: Where the replaced copies went, or None when nothing was replaced.
    backup: Path | None = None


def _scalar(value: str) -> str:
    """A YAML plain scalar when it is safe as one, a quoted string otherwise."""
    import json
    import re

    return value if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value) else json.dumps(value)


def starter_config(
    provider: str = "local",
    model: str | None = None,
    api_key_env: str | None = None,
    facts: ScopeFacts | None = None,
) -> tuple[str, dict[str, str]]:
    """The text of a starter `config/config.yaml`, and the `llm:` values in it.

    A provider `init` has a starter for gets its model and key variable; any
    other name is written as given, with `model` required, and a pointer to
    the example's block for it. The key itself is never asked for or written:
    config names the VARIABLE that holds it.
    """
    base = dict(LLM_STARTERS.get(provider, {"provider": provider}))
    if model:
        base["model"] = model
    if api_key_env:
        base["api_key_env"] = api_key_env
    if "model" not in base:
        raise ValueError(
            f"No starter model for provider {provider!r}: pass --model, and see the "
            "block for it in config/config.example.yaml."
        )
    lines = [
        "# Written by `policyforge init`. Every option, and a block for each other",
        "# provider, is in config/config.example.yaml.",
        "",
        "llm:",
    ]
    for key in ("provider", "model", "base_url", "api_key_env"):
        if key in base:
            lines.append(f"  {key}: {_scalar(base[key])}")
    lines += [
        "",
        "# The catalogs every command that takes --controls uses when it isn't given",
        "# (`policyforge frameworks` lists the names). `--controls` replaces it.",
        "programme:",
        "  catalogs:",
        *[f"    - {name}" for name in STARTER_CATALOGS],
        "",
    ]
    lines += _scope_fact_lines(facts or ScopeFacts())
    return "\n".join(lines), base


#: What each scope fact asks, and what answers it takes, for the lines `init`
#: writes under `programme:` (22, part 3).
_FACT_HELP = (
    ("builds_software", "true", "does it build software? (turns Secure Development on)"),
    ("own_facilities", "true", "does it run its own facilities? (turns Physical on)"),
    ("personal_data", "pii", "none | pii | phi (turns Privacy on, and for phi the HIPAA topics)"),
    ("baseline", "800-53", "none | 800-53 | fedramp | govramp (keeps Personnel, Maintenance)"),
    ("ai", "user", "none | user | builder (sizes the AI topics)"),
)


def _scope_fact_lines(facts: ScopeFacts) -> list[str]:
    """The scope facts under `programme:`: each declared one as a value, each
    other as a commented example, so the file says what to declare."""
    declared = facts.declared()
    lines = [
        "  # Scope facts: what the organisation is. `policyforge init` builds",
        "  # config/topics.yaml from them. Unset is not `none`: an undeclared fact",
        "  # leaves its topics out and the registry's first lines say so.",
    ]
    for name, example, question in _FACT_HELP:
        if name in declared:
            value = declared[name]
            text = str(value).lower() if isinstance(value, bool) else str(value)
            lines.append(f"  {name}: {text}   # {question}")
        else:
            lines.append(f"  # {name}: {example}   # {question}")
    lines.append("")
    return lines


def _facts_in_config(path: Path) -> ScopeFacts:
    """The scope facts in an existing `config.yaml`. A file that isn't YAML
    or has an unknown value refuses, naming what is wrong, rather than being
    read as no facts: a registry built from facts that weren't read would be
    a wrong one that looks right."""
    import yaml

    from policyforge.topics.scope_facts import ScopeFactError, load_scope_facts

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{path} could not be read as YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a mapping at its top level.")
    try:
        return load_scope_facts(data)
    except ScopeFactError as exc:
        raise ValueError(f"{path}: {exc}") from exc


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


def init_project(
    directory: Path,
    *,
    provider: str = "local",
    model: str | None = None,
    api_key_env: str | None = None,
    facts: ScopeFacts | None = None,
) -> ScaffoldReport:
    """Lay out a project in `directory`, creating it if needed. Never overwrites.

    Besides the catalogs and the examples it writes `config/config.yaml` (the
    chosen provider and model, or the local-model starter; the catalog set
    the starter registry needs, and the scope facts) and `config/topics.yaml`
    (built from the scope facts by the topic rule, 22 part 3), each only where
    there is none (#176). The facts are those in an existing `config.yaml`,
    with any given here taking their place; a `config.yaml` that is kept isn't
    changed, and the report says which lines would add them.
    """
    import yaml

    from policyforge.topics.build import render
    from policyforge.topics.rule import select

    report = ScaffoldReport()
    root = bundled_root()
    config_path = directory / "config" / "config.yaml"
    given = facts or ScopeFacts()
    config_existed = config_path.exists()
    existing = _facts_in_config(config_path) if config_existed else ScopeFacts()
    effective = existing.overlaid_with(given)
    text, llm = starter_config(provider, model, api_key_env, effective)

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
    place(Path("config") / "config.yaml", text.encode("utf-8"))
    topics_path = Path("config") / "topics.yaml"
    if (directory / topics_path).exists():
        report.kept.append(topics_path)
    else:
        pool = yaml.safe_load(root.joinpath("config", "topics.example.yaml").read_text("utf-8"))
        selection = select(effective)
        place(topics_path, render(pool, effective, selection).encode("utf-8"))
        report.selection = selection
    report.facts = effective
    differing = {k: v for k, v in given.declared().items() if existing.declared().get(k) != v}
    lines = ", ".join(f"{k}: {str(v).lower()}" for k, v in differing.items())
    if report.selection is None and given.declared():
        # `topics.yaml` was kept, so nothing was built from the facts given.
        if config_existed and differing:
            report.notes.append(
                "config/topics.yaml and config/config.yaml were already there, so the scope "
                "facts you gave were neither used nor written. To use them, add under "
                f"`programme:`: {lines}; then delete config/topics.yaml and run "
                "`policyforge init` again."
            )
        else:
            report.notes.append(
                "config/topics.yaml was already there, so it was not rebuilt from the scope "
                "facts you gave. To rebuild it, delete it and run `policyforge init` again."
            )
    elif config_existed and differing:
        report.notes.append(
            "config/config.yaml was already there, so the scope facts you gave were used "
            "for config/topics.yaml but not written. To keep them, add under `programme:`: " + lines
        )
    place(Path("local_content") / ".gitkeep", b"")
    place(Path(".gitignore"), GITIGNORE.encode("utf-8"))
    report.provider = llm.get("provider", "")
    report.api_key_env = llm.get("api_key_env", "")
    return report


def refresh_project(directory: Path) -> RefreshReport:
    """Bring an existing project's packaged files up to this version (#176).

    The bundled catalogs, their READMEs and the example configs are replaced
    where they differ from what this version ships, and added where this
    version ships one the project doesn't have. `config/config.yaml`,
    `config/topics.yaml`, `local_content/` and `.gitignore` are the project's
    own and are never touched. A replaced file's previous copy is kept under
    `output/.init-backup/<UTC time>/`, so nothing a person re-fetched or
    edited is lost to an upgrade.
    """
    import hashlib
    from datetime import datetime, timezone

    report = RefreshReport()
    root = bundled_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = directory / "output" / ".init-backup" / stamp
    for source, relative in _plan(root):
        packaged = source.read_bytes()
        target = directory / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(packaged)
            report.added.append(relative)
            continue
        current = target.read_bytes()
        if current == packaged:
            report.unchanged += 1
            continue
        kept_copy = backup / relative
        kept_copy.parent.mkdir(parents=True, exist_ok=True)
        kept_copy.write_bytes(current)
        target.write_bytes(packaged)
        report.updated.append(
            (
                relative,
                hashlib.sha256(current).hexdigest()[:8],
                hashlib.sha256(packaged).hexdigest()[:8],
            )
        )
    if report.updated:
        report.backup = backup.relative_to(directory)
    return report
