"""`policyforge library add` (443, part 1; 80's design rulings R1 and R2).

Everything here is invented: the export, the catalog, and the licensed plugin,
a stand-in installed through the plugin seam. No licensed content, no
`local_content/`. The library is a throwaway git repository.
"""

from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

import policyforge.cli as cli_mod
from policyforge import plugins
from policyforge.ingest.schema import Control, Requirement, read_catalog

MANIFEST = (
    "id: invented-csf\nname: Invented CSF\nlicence: licensed\nparser: etl-hitrust\n"
    "licensee: Example Org\nallow_licensed_in_repo: true\n"
)
V1 = (
    "01.a|01.a Level 1|Level 1|Keep an access policy.|NIST SP 800-53:AC-1\n"
    "01.b|01.b Level 1|Level 1|Register users.|NIST SP 800-53:AC-2\n"
    "01.b|01.b Level 2|Level 2|Review registrations quarterly.|\n"
)
#: 01.a Level 1 reworded, 01.b Level 2 removed, 01.c Level 1 added, and a
#: mapping added on 01.b Level 1.
V2 = (
    "01.a|01.a Level 1|Level 1|Keep and review an access policy.|NIST SP 800-53:AC-1\n"
    "01.b|01.b Level 1|Level 1|Register users.|NIST SP 800-53:AC-2,AC-3\n"
    "01.c|01.c Level 1|Level 1|Remove leavers' access.|\n"
)


def _parse(export_path, version, **_):
    """The stand-in parser: one requirement per line."""
    controls: dict[str, Control] = {}
    for line in Path(export_path).read_text(encoding="utf-8").splitlines():
        cid, rid, level, statement, mapping = line.split("|")
        control = controls.setdefault(cid, Control(cid, f"Invented {cid}", "Invented-CSF", version))
        mappings = {}
        if mapping:
            source, ids = mapping.split(":")
            mappings = {source: ids.split(",")}
        control.requirements.append(Requirement(rid, level, statement, mappings=mappings))
    return plugins.ParseResult(controls=list(controls.values()), report="invented report")


@pytest.fixture
def plugin(monkeypatch):
    calls = _Calls()

    def parse(**options):
        calls.append(options)
        if getattr(calls, "refuse", False):
            raise plugins.ParseRefused("not an export this parser reads")
        return _parse(**options)

    module = types.SimpleNamespace(
        CONTRACT=plugins.CONTRACT_VERSION,
        PARSERS=[plugins.Parser("etl-hitrust", "Invented-CSF", parse)],
    )

    class _Entry:
        name, value = "invented", "invented.plugin:PLUGIN"

        def load(self):
            return module

    monkeypatch.setattr(plugins, "BUILTIN", [])
    monkeypatch.setattr(plugins, "_entry_points", lambda: [_Entry()])
    plugins.reset()
    yield calls
    plugins.reset()


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _library(tmp_path, manifest=MANIFEST, *, name="Example Reviewer") -> Path:
    root = tmp_path / "csf-library"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    if name:
        _git(root, "config", "user.name", name)
    _git(root, "config", "user.email", "reviewer@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "framework.yaml").write_text(manifest, encoding="utf-8")
    _git(root, "add", "framework.yaml")
    _git(root, "-c", "user.name=setup", "commit", "-q", "-m", "manifest")
    return root


def _export(tmp_path, name, text) -> Path:
    path = tmp_path / "exports" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _add(root, export, version, *extra):
    return CliRunner().invoke(
        cli_mod.cli,
        [
            "library",
            "add",
            "--library",
            str(root),
            "--export",
            str(export),
            "--version",
            version,
            *extra,
        ],
    )


def _clean(root) -> bool:
    return _git(root, "status", "--porcelain").stdout.strip() == ""


# ---- adding versions -----------------------------------------------------------------


def test_the_first_version_is_copied_parsed_recorded_committed_and_tagged(tmp_path, plugin):
    root = _library(tmp_path)
    export = _export(tmp_path, "MyCSF export v11.7.csv", V1)
    result = _add(root, export, "11.7")
    assert result.exit_code == 0, result.output

    folder = root / "versions" / "11.7"
    assert (folder / "export" / export.name).read_bytes() == export.read_bytes()
    rows, licence = read_catalog(folder / "controls.json")
    assert licence == "licensed"
    assert [r["requirement_id"] for c in rows for r in c["requirements"]] == [
        "01.a Level 1",
        "01.b Level 1",
        "01.b Level 2",
    ]
    provenance = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    import hashlib

    assert provenance["export"]["sha256"] == hashlib.sha256(export.read_bytes()).hexdigest()
    assert provenance["export"]["file"] == export.name
    assert provenance["controls"]["count"] == 2
    assert provenance["parser"]["command"] == "etl-hitrust"
    assert provenance["added_by"] == "Example Reviewer"
    assert (root / "CURRENT").read_text(encoding="utf-8") == "11.7\n"
    assert not (root / "changes").exists()
    assert _git(root, "tag", "--list").stdout.split() == ["v11.7"]
    assert _clean(root)
    assert plugin[0]["export_path"] == export and plugin[0]["version"] == "11.7"


def test_a_second_version_gets_a_change_log_naming_items_levels_and_documents(tmp_path, plugin):
    root = _library(tmp_path)
    content = tmp_path / "docs" / "standards"
    content.mkdir(parents=True)
    (content / "access.md").write_text(
        "---\ntitle: Access\ntier: standard\n---\n\n# Access\n\nKeep it. [Invented-CSF 01.a]\n",
        encoding="utf-8",
    )
    assert _add(root, _export(tmp_path, "v11.7.csv", V1), "11.7").exit_code == 0
    result = _add(
        root, _export(tmp_path, "v11.8.csv", V2), "11.8", "--content-dir", str(tmp_path / "docs")
    )
    assert result.exit_code == 0, result.output

    log = (root / "changes" / "11.7-to-11.8.md").read_text(encoding="utf-8")
    assert "# Changes from 11.7 to 11.8" in log
    added = log.split("## Added")[1].split("##")[0]
    removed = log.split("## Removed")[1].split("##")[0]
    changed = log.split("## Changed")[1].split("##")[0]
    assert "**01.c**" in added and "  - 01.c Level 1 (Level 1)" in added
    assert "**01.b Level 2 (Level 2)**" in removed
    assert "**01.a Level 1 (Level 1)**: statement" in changed
    assert "Cited by: standards/access.md" in changed
    assert "**01.b Level 1 (Level 1)**: mappings" in changed and "AC-3" in changed
    assert "regenerat" not in log.casefold()
    assert (root / "CURRENT").read_text(encoding="utf-8") == "11.8\n"
    assert sorted(_git(root, "tag", "--list").stdout.split()) == ["v11.7", "v11.8"]
    assert _clean(root)


# ---- refusals, each before anything is written ----------------------------------------


def _unchanged(root, before) -> bool:
    return _git(root, "rev-parse", "HEAD").stdout == before and _clean(root)


@pytest.mark.parametrize(
    ("manifest", "said"),
    [
        (MANIFEST.replace("allow_licensed_in_repo: true\n", ""), "allow_licensed_in_repo: true"),
        (
            MANIFEST.replace("allow_licensed_in_repo: true", "allow_licensed_in_repo: yes please"),
            "allow_licensed_in_repo: true",
        ),
        (MANIFEST.replace("licence: licensed", "licence: public-domain"), "licence: licensed"),
        (MANIFEST.replace("parser: etl-hitrust\n", ""), "names no `parser:`"),
    ],
)
def test_a_library_that_hasnt_declared_what_it_holds_is_refused(tmp_path, plugin, manifest, said):
    root = _library(tmp_path, manifest)
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "v.csv", V1), "11.7")
    assert (
        result.exit_code == 1 and said in result.output and "Nothing was written" in result.output
    )
    assert _unchanged(root, before) and not (root / "versions").exists()
    assert plugin == []


def test_a_version_that_exists_is_refused(tmp_path, plugin):
    root = _library(tmp_path)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "b.csv", V2), "11.7")
    assert result.exit_code == 1 and "already in" in result.output
    assert _unchanged(root, before)


def test_the_same_export_under_a_new_name_is_refused(tmp_path, plugin):
    root = _library(tmp_path)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "renamed.csv", V1), "11.8")
    assert result.exit_code == 1 and "same export as version 11.7" in result.output
    assert _unchanged(root, before) and not (root / "versions" / "11.8").exists()


def test_a_library_with_uncommitted_changes_is_refused(tmp_path, plugin):
    root = _library(tmp_path)
    (root / "notes.txt").write_text("work in progress", encoding="utf-8")
    result = _add(root, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1 and "not committed" in result.output
    assert not (root / "versions").exists()


def test_a_library_with_no_git_identity_is_refused(tmp_path, plugin, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = _library(tmp_path, name="")
    result = _add(root, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1 and "no git `user.name`" in result.output
    assert not (root / "versions").exists()


def test_a_directory_that_isnt_its_own_repository_is_refused(tmp_path, plugin):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "framework.yaml").write_text(MANIFEST, encoding="utf-8")
    result = _add(plain, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1 and "not a git repository" in result.output

    root = _library(tmp_path)
    inner = root / "nested"
    inner.mkdir()
    (inner / "framework.yaml").write_text(MANIFEST, encoding="utf-8")
    result = _add(inner, _export(tmp_path, "b.csv", V1), "11.7")
    assert result.exit_code == 1 and "not the top of its own" in result.output


@pytest.mark.parametrize("marker", ["source", "pyproject"])
def test_a_library_inside_a_policyforge_checkout_is_refused(tmp_path, plugin, marker):
    """80's R1: licensed content never enters the tool's own tree, even as a
    repository nested inside a checkout."""
    checkout = tmp_path / "policyforge-checkout"
    if marker == "source":
        (checkout / "src" / "policyforge").mkdir(parents=True)
        (checkout / "src" / "policyforge" / "__init__.py").write_text("", encoding="utf-8")
    else:
        checkout.mkdir()
        (checkout / "pyproject.toml").write_text(
            '[project]\nname = "policyforge"\n', encoding="utf-8"
        )
    root = _library(checkout)
    result = _add(root, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1 and "inside a PolicyForge checkout" in result.output
    assert not (root / "versions").exists()


@pytest.mark.parametrize("version", ["../escape", "v 11", "", "-rf", "1..2", "11.", "x.lock"])
def test_a_version_that_cant_name_a_folder_and_tag_is_refused(tmp_path, plugin, version):
    root = _library(tmp_path)
    result = _add(root, _export(tmp_path, "a.csv", V1), version)
    assert result.exit_code != 0
    assert not (root / "versions").exists() and _clean(root)


# ---- a failure part-way leaves the library as it was -----------------------------------


class _Calls(list):
    refuse = False


def test_a_parse_the_plugin_refuses_writes_nothing(tmp_path, plugin):
    plugin.refuse = True
    root = _library(tmp_path)
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1 and "not an export this parser reads" in result.output
    assert _unchanged(root, before) and not (root / "versions").exists()


def test_a_commit_that_fails_leaves_the_library_as_it_was(tmp_path, plugin):
    root = _library(tmp_path)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "b.csv", V2), "11.8")
    assert result.exit_code == 1 and "git commit failed" in result.output
    assert _unchanged(root, before)
    assert not (root / "versions" / "11.8").exists()
    assert not (root / "changes").exists()
    assert (root / "CURRENT").read_text(encoding="utf-8") == "11.7\n"


# ---- b5 on PR 458 (6509): case, and a rollback that owns only what it made ----------


def test_a_version_differing_only_by_case_is_refused_and_the_other_kept(tmp_path, plugin):
    """Refused by name, not by what the filesystem says: on a case-insensitive
    one `11.7-A` would be `11.7-a`'s folder."""
    root = _library(tmp_path)
    first = _export(tmp_path, "a.csv", V1)
    assert _add(root, first, "11.7-a").exit_code == 0
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "b.csv", V2), "11.7-A")
    assert result.exit_code == 1 and "case ignored" in result.output
    assert _unchanged(root, before)
    assert (root / "versions" / "11.7-a" / "export" / "a.csv").read_bytes() == first.read_bytes()


def test_a_rollback_never_removes_a_folder_it_did_not_create(tmp_path, plugin, monkeypatch):
    """The collision itself, made without relying on the filesystem: the
    version's folder is already there when `add_version` starts (as a
    case-insensitive one would make it), and the add must fail without
    touching it."""
    from policyforge import library as lib

    root = _library(tmp_path)
    existing = root / "versions" / "11.7" / "export"
    existing.mkdir(parents=True)
    (existing / "kept.csv").write_text("someone else's", encoding="utf-8")
    monkeypatch.setattr(lib, "check_add", lambda *a, **k: ("0" * 64, "Example Reviewer"))
    library = lib.open_library(root)
    with pytest.raises(FileExistsError):
        lib.add_version(
            library,
            _export(tmp_path, "a.csv", V1),
            "11.7",
            parse=lambda export, version: (_parse(export, version).controls, {}),
            content_root=None,
            now="2026-10-12T00:00:00+00:00",
            tool={},
        )
    assert (existing / "kept.csv").read_text(encoding="utf-8") == "someone else's"


def test_a_first_version_whose_commit_fails_leaves_no_current(tmp_path, plugin):
    root = _library(tmp_path)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    result = _add(root, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1 and "git commit failed" in result.output
    assert not (root / "CURRENT").exists()
    assert not (root / "versions").exists()
    assert _clean(root)


def test_a_tag_that_fails_after_the_commit_says_the_version_is_committed(
    tmp_path, plugin, monkeypatch
):
    from policyforge import library as lib

    real = lib._git

    def git(root, *args):
        if args[:1] == ("tag",) and len(args) == 2:
            return subprocess.CompletedProcess(args, 1, "", "tag refused (invented)")
        return real(root, *args)

    monkeypatch.setattr(lib, "_git", git)
    root = _library(tmp_path)
    result = _add(root, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1
    assert "Version 11.7 is committed, but `git tag v11.7` failed" in result.output
    assert (root / "versions" / "11.7" / "controls.json").exists()


def test_a_tag_with_no_folder_is_refused_before_anything_is_written(tmp_path, plugin):
    """b5 on PR 458 (6521): the tag alone is enough (its folder deleted by
    hand), or the add would end "committed, but `git tag` failed"."""
    root = _library(tmp_path)
    _git(root, "tag", "v11.7")
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "a.csv", V1), "11.7")
    assert result.exit_code == 1 and "already in" in result.output
    assert not (root / "versions").exists() and not (root / "CURRENT").exists()
    assert _git(root, "rev-parse", "HEAD").stdout == before and _clean(root)


def test_a_change_log_already_there_is_refused_and_kept(tmp_path, plugin):
    """b5 on PR 458 (6521): a committed `changes/<prev>-to-<new>.md` for a
    version not yet added is never overwritten, and the version isn't added."""
    root = _library(tmp_path)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    log = root / "changes" / "11.7-to-11.8.md"
    log.parent.mkdir()
    log.write_text("earlier notes\n", encoding="utf-8")
    _git(root, "add", "changes")
    _git(root, "commit", "-q", "-m", "notes")
    result = _add(root, _export(tmp_path, "b.csv", V2), "11.8")
    assert result.exit_code == 1 and "is already there" in result.output
    assert log.read_text(encoding="utf-8") == "earlier notes\n"
    assert not (root / "versions" / "11.8").exists()
    assert (root / "CURRENT").read_text(encoding="utf-8") == "11.7\n"
    assert _clean(root)


# ---- part 2: a generated document names the catalog version it came from ----------


def _sha(path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_a_library_versions_catalog_is_traced_to_its_version_and_export(tmp_path, plugin):
    from policyforge.library import catalog_trace

    root = _library(tmp_path)
    export = _export(tmp_path, "a.csv", V1)
    assert _add(root, export, "11.7").exit_code == 0
    catalog = root / "versions" / "11.7" / "controls.json"
    assert catalog_trace(catalog, "invented-csf") == {
        "catalog": "invented-csf",
        "sha256": _sha(catalog),
        "library": {"version": "11.7", "export_sha256": _sha(export)},
    }


def test_a_catalog_outside_any_library_names_no_version(tmp_path, plugin):
    """A copy of a version's catalog included: the copy has no provenance
    beside it, so it can't say which export it came from."""
    from policyforge.library import catalog_trace

    root = _library(tmp_path)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    copy = tmp_path / "elsewhere" / "controls.json"
    copy.parent.mkdir()
    copy.write_bytes((root / "versions" / "11.7" / "controls.json").read_bytes())
    trace = catalog_trace(copy, "invented-csf")
    assert trace["library"] == "none" and trace["sha256"] == _sha(copy)


def test_a_version_folders_catalog_that_isnt_the_recorded_one_names_no_version(tmp_path, plugin):
    from policyforge.library import catalog_trace

    root = _library(tmp_path)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    catalog = root / "versions" / "11.7" / "controls.json"
    catalog.write_bytes(catalog.read_bytes() + b"\n")
    assert catalog_trace(catalog, "x")["library"] == (
        "none: this controls.json isn't the one version 11.7 recorded"
    )
    (catalog.parent / "provenance.json").write_text("{not json", encoding="utf-8")
    assert catalog_trace(catalog, "x")["library"] == (
        "none: the provenance.json beside it can't be read"
    )


def test_a_provenance_naming_another_version_names_no_version(tmp_path, plugin):
    """A version folder renamed by hand: its provenance names the version it
    was added as, and the folder's name isn't that."""
    from policyforge.library import catalog_trace

    root = _library(tmp_path)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    moved = root / "versions" / "11.8"
    (root / "versions" / "11.7").rename(moved)
    assert catalog_trace(moved / "controls.json", "x")["library"] == (
        "none: this controls.json isn't the one version 11.8 recorded"
    )


def test_inputs_catalogs_are_listed_once_and_an_input_with_none_is_named():
    from policyforge.cli.documents import _catalog_keys

    a = {"catalog": "a", "sha256": "1" * 64, "library": "none"}
    b = {"catalog": "b", "sha256": "2" * 64, "library": {"version": "11.7"}}
    assert _catalog_keys([("one.md", [a, b]), ("two.md", [b]), ("old.md", None)]) == {
        "catalogs": [a, b],
        "catalogs_not_recorded": ["old.md"],
    }
    assert _catalog_keys([("one.md", [a])]) == {"catalogs": [a]}


PUBLIC_CATALOG = [
    {
        "control_id": "IA-5",
        "title": "Authenticator Management",
        "framework": "NIST 800-53",
        "framework_version": "Rev 5",
        "control_statement": "Manage authenticators.",
    }
]


class _Writer:
    def __init__(self, text):
        self.text = text

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=self.text, model="fake")

    def check(self):
        return True


def test_a_document_drafted_from_a_library_version_names_it_and_its_hash(
    tmp_path, plugin, monkeypatch
):
    """443's "done when": synthesize from a version's controls.json, then
    generate; the Standard's `generated_by.catalogs` names the version and
    the export's hash, and the catalog outside the library says `none`."""
    import frontmatter

    from policyforge.llm.ledger import wrap

    root = _library(tmp_path)
    export = _export(tmp_path, "a.csv", V1)
    assert _add(root, export, "11.7").exit_code == 0
    version = root / "versions" / "11.7" / "controls.json"
    public = tmp_path / "public" / "controls.json"
    public.parent.mkdir()
    public.write_text(json.dumps(PUBLIC_CATALOG), encoding="utf-8")
    crosswalk = tmp_path / "crosswalk.json"
    crosswalk.write_text("{}", encoding="utf-8")
    config = {
        "org": {"name": "Acme", "industry": "Health"},
        "llm": {"provider": "local", "base_url": "http://localhost:11434/v1", "model": "q"},
    }
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_mod, "load_config", lambda: config)

    monkeypatch.setattr(cli_mod, "get_provider", lambda c: _Writer("- Manage authenticators."))
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "synthesize",
            "--topic",
            "Access Control",
            "--nist-controls",
            "IA-5",
            "--controls",
            str(public),
            "--controls",
            str(version),
            "--crosswalk",
            str(crosswalk),
            "--out-dir",
            str(tmp_path / "synthesis"),
        ],
    )
    assert result.exit_code == 0, result.output

    writer = _Writer("# Access Control Standard\n\nStaff must manage authenticators.\n")
    monkeypatch.setattr(cli_mod, "get_provider", lambda c: wrap(writer, c))
    out = tmp_path / "out" / "access-control.md"
    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "generate",
            "--tier",
            "standard",
            "--synthesis",
            str(tmp_path / "synthesis" / "access-control.md"),
            "--out",
            str(out),
            "--history-dir",
            str(tmp_path / "history"),
        ],
    )
    assert result.exit_code == 0, result.output
    stamp = frontmatter.loads(out.read_text(encoding="utf-8")).metadata["generated_by"]
    assert [(c["sha256"], c["library"]) for c in stamp["catalogs"]] == [
        (_sha(public), "none"),
        (_sha(version), {"version": "11.7", "export_sha256": _sha(export)}),
    ]
    assert "catalogs_not_recorded" not in stamp
    assert stamp["models"], "the model's attribution is kept beside the catalogs"


# ---- 461: the library stores every file as received, on every checkout ----------
#
# Each test sets `core.autocrlf=true` itself (Git for Windows' default, and
# this machine's system setting), so the case holds on CI's Linux too.

V1_CRLF = V1.replace("\n", "\r\n")


def _autocrlf(root):
    _git(root, "config", "core.autocrlf", "true")
    return root


def _blob(root, path) -> bytes:
    """The bytes git stored, not what the working tree shows."""
    return subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", f"HEAD:{path}"],
        capture_output=True,
        check=True,
    ).stdout


def _clone(root, tmp_path) -> Path:
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "-c", "core.autocrlf=true", "clone", "-q", str(root), str(clone)],
        capture_output=True,
        check=True,
    )
    return clone


def test_a_clone_under_autocrlf_holds_the_bytes_received_and_traces_the_version(tmp_path, plugin):
    from policyforge.library import catalog_trace

    root = _autocrlf(_library(tmp_path))
    export = _export(tmp_path, "a.csv", V1_CRLF)
    result = _add(root, export, "11.7")
    assert result.exit_code == 0, result.output
    assert "Wrote the library's .gitattributes: `* -text`" in result.output
    assert "Versions added before" not in result.output
    assert _blob(root, ".gitattributes") == b"* -text\n"

    received = export.read_bytes()
    assert b"\r\n" in received
    assert _blob(root, "versions/11.7/export/a.csv") == received
    clone = _clone(root, tmp_path)
    for name in ("export/a.csv", "controls.json", "provenance.json"):
        original = (root / "versions" / "11.7" / name).read_bytes()
        assert (clone / "versions" / "11.7" / name).read_bytes() == original, name
    provenance = json.loads((clone / "versions/11.7/provenance.json").read_text(encoding="utf-8"))
    assert _sha(clone / "versions/11.7/export/a.csv") == provenance["export"]["sha256"]
    assert _sha(clone / "versions/11.7/export/a.csv") == _sha(export)
    assert catalog_trace(clone / "versions/11.7/controls.json", "x")["library"] == {
        "version": "11.7",
        "export_sha256": _sha(export),
    }


def test_after_the_rule_every_tracked_file_is_what_git_stored(tmp_path, plugin):
    """A `framework.yaml` written with CRLF and committed under autocrlf is LF
    in git. Once conversion is off, its CRLF copy would read as modified, and
    the next add would refuse the library as uncommitted. Compared byte for
    byte, because `git status` sees it only when a timestamp is ambiguous.

    Made as a user makes it: the CRLF file written, then added and committed
    as a new file under autocrlf. (Re-committing a CRLF copy over an LF blob
    commits nothing and leaves the index's size stale, so the tree reads as
    modified before the add: measured on Linux git, CI's.)"""
    root = tmp_path / "csf-library"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for key, value in (
        ("user.name", "Example Reviewer"),
        ("user.email", "reviewer@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "true"),
    ):
        _git(root, "config", key, value)
    (root / "framework.yaml").write_bytes(MANIFEST.replace("\n", "\r\n").encode("utf-8"))
    _git(root, "add", "framework.yaml")
    _git(root, "commit", "-q", "-m", "CRLF manifest, stored LF")
    assert _blob(root, "framework.yaml") == MANIFEST.encode("utf-8")
    assert (root / "framework.yaml").read_bytes() != _blob(root, "framework.yaml")
    assert _clean(root)
    assert _add(root, _export(tmp_path, "a.csv", V1_CRLF), "11.7").exit_code == 0
    for name in _git(root, "ls-files").stdout.split():
        assert (root / name).read_bytes() == _blob(root, name), name
    assert _add(root, _export(tmp_path, "b.csv", V2), "11.8").exit_code == 0


def test_a_binary_export_is_held_as_received(tmp_path, plugin):
    """The `.xlsx` case: content git reads as binary (a NUL byte)."""
    root = _autocrlf(_library(tmp_path))
    export = tmp_path / "exports" / "a.xlsx"
    export.parent.mkdir()
    export.write_bytes(V1_CRLF.replace("access policy.", "access policy.\x00").encode("utf-8"))
    assert _add(root, export, "11.7").exit_code == 0
    assert _blob(root, "versions/11.7/export/a.xlsx") == export.read_bytes()
    clone = _clone(root, tmp_path)
    assert (clone / "versions/11.7/export/a.xlsx").read_bytes() == export.read_bytes()


def test_a_library_made_before_the_rule_gets_it_on_the_next_add_and_is_told(tmp_path, plugin):
    root = _autocrlf(_library(tmp_path))
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    _git(root, "rm", "-q", ".gitattributes")
    _git(root, "commit", "-q", "-m", "as a library made before 461 would be")

    result = _add(root, _export(tmp_path, "b.csv", V2.replace("\n", "\r\n")), "11.8")
    assert result.exit_code == 0, result.output
    assert "Wrote the library's .gitattributes" in result.output
    assert "Versions added before this commit may have been stored" in result.output
    assert "re-clone the library" in result.output
    assert _blob(root, ".gitattributes") == b"* -text\n"
    assert _git(root, "show", "--name-only", "--format=", "v11.8").stdout.split()[0] == (
        ".gitattributes"
    )
    assert _clean(root)


def test_a_libraries_own_attributes_are_kept_and_the_rule_added_last(tmp_path, plugin):
    """Last, because a later line wins: `*.csv text` above it is overridden."""
    root = _autocrlf(_library(tmp_path))
    (root / ".gitattributes").write_bytes(b"# ours\n*.csv text")
    _git(root, "add", ".gitattributes")
    _git(root, "commit", "-q", "-m", "attributes")
    export = _export(tmp_path, "a.csv", V1_CRLF)
    result = _add(root, export, "11.7")
    assert result.exit_code == 0, result.output
    assert "Added to the library's .gitattributes" in result.output
    assert _blob(root, ".gitattributes") == b"# ours\n*.csv text\n* -text\n"
    assert _blob(root, "versions/11.7/export/a.csv") == export.read_bytes()


def test_a_library_already_ending_with_the_rule_is_left_alone(tmp_path, plugin):
    root = _autocrlf(_library(tmp_path))
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 0
    result = _add(root, _export(tmp_path, "b.csv", V2), "11.8")
    assert result.exit_code == 0, result.output
    assert ".gitattributes" not in result.output
    assert ".gitattributes" not in _git(root, "show", "--name-only", "--format=", "v11.8").stdout


@pytest.mark.parametrize(
    "nested",
    [b"* text\n", b"*.csv text=auto\n", b"* !text\n"],
    ids=["text set", "text=auto", "text unspecified"],
)
def test_a_nested_attributes_file_that_converts_is_refused_and_nothing_added(
    tmp_path, plugin, nested
):
    """The root's rule is overridden by one nearer the file: git's own answer
    is what's checked, so this is refused rather than stored converted. Each
    of git's converting states: set, `auto`, and unspecified (left to
    `core.autocrlf`)."""
    root = _autocrlf(_library(tmp_path))
    (root / "versions").mkdir()
    (root / "versions" / ".gitattributes").write_bytes(nested)
    _git(root, "add", "versions")
    _git(root, "commit", "-q", "-m", "nested")
    before = _git(root, "rev-parse", "HEAD").stdout
    result = _add(root, _export(tmp_path, "a.csv", V1_CRLF), "11.7")
    assert result.exit_code == 1
    assert "Git would convert line endings in versions/11.7/" in result.output
    assert _git(root, "rev-parse", "HEAD").stdout == before
    assert not (root / "versions" / "11.7").exists() and not (root / ".gitattributes").exists()
    assert (root / "versions" / ".gitattributes").read_bytes() == nested
    assert _clean(root)


def test_a_failed_commit_puts_the_libraries_attributes_back(tmp_path, plugin):
    root = _library(tmp_path)
    (root / ".gitattributes").write_bytes(b"*.md text\n")
    _git(root, "add", ".gitattributes")
    _git(root, "commit", "-q", "-m", "attributes")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    assert _add(root, _export(tmp_path, "a.csv", V1), "11.7").exit_code == 1
    assert (root / ".gitattributes").read_bytes() == b"*.md text\n"
    assert _clean(root)
