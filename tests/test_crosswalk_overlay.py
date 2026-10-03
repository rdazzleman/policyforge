"""A per-organization crosswalk file over a catalog's published mapping.

The property everything else rests on: an overlay seeded from the published
mapping changes nothing, and each departure from it — a rejection, an
addition, a pair still under review — reaches the crosswalk as exactly that.
"""

from __future__ import annotations

import re

import pytest

from policyforge.crosswalk.overlay import (
    ACCEPTED,
    REJECTED,
    MappingRow,
    NotAnchorableError,
    OverlayError,
    apply_overlays,
    check_overlay,
    dump_overlay,
    load_overlay,
    load_overlays,
    overlay_digests,
    parse_overlay,
    seed_overlay,
)
from policyforge.ingest.schema import Control, ControlEnhancement
from policyforge.mapping.crosswalk import NIST_ANCHOR, build_crosswalk

HIPAA = "HIPAA Security Rule"


def _catalogs():
    nist = [
        Control(
            control_id=cid,
            title=cid,
            framework="NIST 800-53",
            framework_version="r5",
            control_statement="x",
        )
        for cid in ("SI-3", "IR-6", "AT-2", "RA-3")
    ]
    hipaa = [
        Control(
            control_id="164.308(a)(5)(i)",
            title="Security awareness and training",
            framework=HIPAA,
            framework_version="45 CFR 164",
            control_statement="Implement a security awareness and training program.",
            source_crosswalk={NIST_ANCHOR: "AT-2"},
            enhancements=[
                ControlEnhancement(
                    enhancement_id="164.308(a)(5)(ii)(B)",
                    title="Protection from malicious software",
                    baseline="Required",
                    description="Procedures for guarding against and reporting malicious software.",
                    source_crosswalk={NIST_ANCHOR: "SI-3, RA-3"},
                )
            ],
        ),
        Control(
            control_id="164.308(a)(1)(i)",
            title="Security management process",
            framework=HIPAA,
            framework_version="45 CFR 164",
            control_statement="Implement policies and procedures to prevent security violations.",
            source_crosswalk={NIST_ANCHOR: "RA-3"},
        ),
    ]
    return nist + hipaa


def _mapped(controls):
    return {
        nist: sorted(entry.get("hipaa", [])) for nist, entry in build_crosswalk(controls).items()
    }


def test_a_seeded_overlay_changes_nothing():
    before = _mapped(_catalogs())
    controls = _catalogs()
    overlay = seed_overlay(controls, HIPAA)

    apply_overlays(controls, [overlay])

    assert _mapped(controls) == before


def test_a_seeded_overlay_survives_a_round_trip_through_yaml(tmp_path):
    controls = _catalogs()
    path = tmp_path / "hipaa.yaml"
    path.write_text(dump_overlay(seed_overlay(controls, HIPAA)), encoding="utf-8")

    apply_overlays(controls, [load_overlay(path)])

    assert _mapped(controls) == _mapped(_catalogs())


def test_a_rejected_pair_is_gone_and_an_added_one_is_present():
    controls = _catalogs()
    overlay = seed_overlay(controls, HIPAA)
    rows = overlay.requirements["164.308(a)(5)(ii)(B)"]
    next(r for r in rows if r.control == "RA-3").status = REJECTED
    rows.append(MappingRow(control="IR-6", relationship="superset", status=ACCEPTED))

    apply_overlays(controls, [overlay])
    mapped = _mapped(controls)

    assert "164.308(a)(5)(ii)(B)" in mapped["IR-6"]
    assert "164.308(a)(5)(ii)(B)" not in mapped["RA-3"]
    # The other requirement mapping to RA-3 is untouched by that rejection.
    assert "164.308(a)(1)(i)" in mapped["RA-3"]


def test_a_proposed_pair_does_not_reach_the_crosswalk():
    controls = _catalogs()
    overlay = seed_overlay(controls, HIPAA)
    overlay.requirements["164.308(a)(1)(i)"].append(MappingRow(control="IR-6"))

    apply_overlays(controls, [overlay])

    assert "164.308(a)(1)(i)" not in _mapped(controls).get("IR-6", [])


def test_a_requirement_the_overlay_does_not_list_keeps_the_published_mapping():
    """Reviewing some requirements must not unmap the rest."""
    controls = _catalogs()
    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {"164.308(a)(1)(i)": [{"control": "IR-6", "status": "accepted"}]},
        }
    )

    apply_overlays(controls, [overlay])
    mapped = _mapped(controls)

    assert "164.308(a)(5)(ii)(B)" in mapped["SI-3"]
    assert "164.308(a)(1)(i)" in mapped["IR-6"]
    assert "164.308(a)(1)(i)" not in mapped["RA-3"]


def test_a_requirement_with_every_pair_rejected_maps_to_nothing():
    controls = _catalogs()
    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {"164.308(a)(1)(i)": [{"control": "RA-3", "status": "rejected"}]},
        }
    )

    apply_overlays(controls, [overlay])

    assert "164.308(a)(1)(i)" not in _mapped(controls).get("RA-3", [])


def test_an_overlay_for_a_framework_that_is_not_loaded_touches_nothing():
    """Most commands load 800-53 alone; a HITRUST overlay must not break them."""
    controls = _catalogs()
    overlay = parse_overlay(
        {
            "framework": "HITRUST CSF",
            "requirements": {"01.a": [{"control": "IR-6", "status": "accepted"}]},
        }
    )

    assert apply_overlays(controls, [overlay]) == 0
    assert _mapped(controls) == _mapped(_catalogs())


def test_a_misspelled_framework_whose_ids_are_loaded_is_refused():
    """Reproduced in review: a typo applied nothing, silently ignoring every decision."""
    overlay = parse_overlay(
        {
            "framework": "HIPPA Security Rule",
            "requirements": {"164.308(a)(1)(i)": [{"control": "IR-6", "status": "accepted"}]},
        }
    )

    with pytest.raises(OverlayError, match="HIPAA Security Rule"):
        apply_overlays(_catalogs(), [overlay])


@pytest.mark.parametrize(
    ("row", "complaint"),
    [
        ({"control": "SI-3", "status": "acepted"}, "status 'acepted'"),
        ({"control": "SI-3", "relationship": "same"}, "relationship 'same'"),
        ({"status": "accepted"}, "needs a `control:`"),
    ],
)
def test_a_typo_that_would_change_what_is_mapped_is_refused(row, complaint):
    """A misspelled status would otherwise read as not-accepted and silently unmap."""
    with pytest.raises(OverlayError, match=re.escape(complaint)):
        parse_overlay({"framework": HIPAA, "requirements": {"164.308(a)(1)(i)": [row]}})


def test_an_overlay_names_its_framework():
    with pytest.raises(OverlayError, match="framework"):
        parse_overlay({"requirements": {}})


def test_invalid_yaml_names_the_file(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("framework: [unclosed\n", encoding="utf-8")
    with pytest.raises(OverlayError, match=r"broken\.yaml"):
        load_overlay(path)


def test_a_missing_directory_is_no_overlays(tmp_path):
    assert load_overlays(tmp_path / "nowhere") == []


def test_stale_and_unreviewed_entries_are_reported_not_dropped():
    controls = _catalogs()
    overlay = parse_overlay(
        {
            "framework": HIPAA,
            "requirements": {
                "164.999(z)": [{"control": "SI-3", "status": "accepted"}],
                "164.308(a)(5)(ii)(B)": [
                    {"control": "SI-3", "status": "accepted"},
                    {"control": "ZZ-9", "status": "proposed"},
                ],
            },
        }
    )

    check = check_overlay(overlay, controls)

    assert check.unknown_requirements == ["164.999(z)"]
    assert check.unknown_controls == [("164.308(a)(5)(ii)(B)", "ZZ-9")]
    # RA-3 is published for that requirement, and the overlay neither
    # accepts nor rejects it: a catalog update nobody has looked at.
    assert check.unreviewed_published == [("164.308(a)(5)(ii)(B)", "RA-3")]
    assert check.proposed == 1
    assert not check.is_clean


# ---- applied wherever catalogs are loaded -------------------------------


def _write_catalogs(tmp_path):
    import dataclasses
    import json

    controls = _catalogs()
    nist = [dataclasses.asdict(c) for c in controls if c.framework.startswith("NIST")]
    hipaa = [dataclasses.asdict(c) for c in controls if c.framework == HIPAA]
    nist_path, hipaa_path = tmp_path / "nist.json", tmp_path / "hipaa.json"
    nist_path.write_text(json.dumps(nist), encoding="utf-8")
    hipaa_path.write_text(json.dumps(hipaa), encoding="utf-8")
    return nist_path, hipaa_path


def _map(tmp_path, monkeypatch):
    import json

    from click.testing import CliRunner

    from policyforge.cli import cli

    nist_path, hipaa_path = _write_catalogs(tmp_path)
    out = tmp_path / "crosswalk.json"
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli,
        ["map", "--controls", str(nist_path), "--controls", str(hipaa_path), "--out", str(out)],
    )
    crosswalk = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return result, crosswalk


def test_the_map_command_uses_the_organizations_overlay(tmp_path, monkeypatch):
    (tmp_path / "config" / "crosswalks").mkdir(parents=True)
    (tmp_path / "config" / "crosswalks" / "hipaa.yaml").write_text(
        "framework: HIPAA Security Rule\n"
        "requirements:\n"
        "  164.308(a)(5)(ii)(B):\n"
        "    - {control: SI-3, status: accepted}\n"
        "    - {control: RA-3, status: rejected}\n",
        encoding="utf-8",
    )

    result, crosswalk = _map(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert crosswalk["SI-3"] == {"hipaa": ["164.308(a)(5)(ii)(B)"]}
    assert crosswalk["RA-3"] == {"hipaa": ["164.308(a)(1)(i)"]}


def test_without_an_overlay_the_map_command_is_unchanged(tmp_path, monkeypatch):
    result, crosswalk = _map(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert sorted(crosswalk["RA-3"]["hipaa"]) == ["164.308(a)(1)(i)", "164.308(a)(5)(ii)(B)"]


def test_an_unreadable_overlay_stops_the_command_rather_than_being_skipped(tmp_path, monkeypatch):
    (tmp_path / "config" / "crosswalks").mkdir(parents=True)
    (tmp_path / "config" / "crosswalks" / "hipaa.yaml").write_text(
        "framework: HIPAA Security Rule\n"
        "requirements:\n"
        "  164.308(a)(5)(ii)(B):\n"
        "    - {control: RA-3, status: rejectd}\n",
        encoding="utf-8",
    )

    result, crosswalk = _map(tmp_path, monkeypatch)

    assert result.exit_code != 0
    assert "rejectd" in result.output
    assert crosswalk is None


def test_zardoz_sees_the_same_overlay(tmp_path, monkeypatch):
    from policyforge.zardoz.shell import ShellState
    from policyforge.zardoz.skills import _controls

    nist_path, hipaa_path = _write_catalogs(tmp_path)
    (tmp_path / "config" / "crosswalks").mkdir(parents=True)
    (tmp_path / "config" / "crosswalks" / "hipaa.yaml").write_text(
        "framework: HIPAA Security Rule\n"
        "requirements:\n"
        "  164.308(a)(5)(ii)(B):\n"
        "    - {control: RA-3, status: rejected}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    state = ShellState(controls_paths=[nist_path, hipaa_path])

    crosswalk = build_crosswalk(_controls(state))

    assert crosswalk["RA-3"] == {"hipaa": ["164.308(a)(1)(i)"]}


def test_the_built_crosswalk_is_byte_identical_across_processes(tmp_path):
    """String hashing is randomised per process; the output must not depend on it.

    `build_crosswalk` iterated a set of NIST ids, so `map` wrote the same
    crosswalk with its keys in a different order on every run. Found while
    checking that a seeded overlay changes nothing: the two files differed,
    and so did two runs with no overlay at all.
    """
    import json
    import os
    import subprocess
    import sys

    nist_path, hipaa_path = _write_catalogs(tmp_path)
    script = (
        "import json, sys\n"
        "from policyforge.ingest.schema import load_controls\n"
        "from policyforge.mapping.crosswalk import build_crosswalk\n"
        "controls = load_controls(sys.argv[1]) + load_controls(sys.argv[2])\n"
        "print(json.dumps(build_crosswalk(controls)))\n"
    )
    outputs = set()
    for seed in ("1", "2", "3", "4"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", script, str(nist_path), str(hipaa_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=True,
        )
        outputs.add(result.stdout)
    assert len(outputs) == 1, outputs
    assert json.loads(outputs.pop())


# ---- `policyforge crosswalk seed` and `check` ----------------------------


def _cli(tmp_path, monkeypatch, *args):
    from click.testing import CliRunner

    from policyforge.cli import cli

    nist_path, hipaa_path = _write_catalogs(tmp_path)
    monkeypatch.chdir(tmp_path)
    controls = ["--controls", str(nist_path), "--controls", str(hipaa_path)]
    return CliRunner().invoke(cli, ["crosswalk", *args, *controls])


def test_seed_writes_the_published_mapping_and_check_finds_it_clean(tmp_path, monkeypatch):
    seeded = _cli(tmp_path, monkeypatch, "seed")
    assert seeded.exit_code == 0, seeded.output
    path = tmp_path / "config" / "crosswalks" / "hipaa-security-rule.yaml"
    overlay = load_overlay(path)
    assert [r.control for r in overlay.requirements["164.308(a)(5)(ii)(B)"]] == ["SI-3", "RA-3"]
    assert "4 published pairs" in seeded.output

    checked = _cli(tmp_path, monkeypatch, "check", "--strict")
    assert checked.exit_code == 0, checked.output
    assert "matches the catalogs" in checked.output


def test_seed_refuses_to_overwrite_decisions(tmp_path, monkeypatch):
    assert _cli(tmp_path, monkeypatch, "seed").exit_code == 0
    path = tmp_path / "config" / "crosswalks" / "hipaa-security-rule.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("accepted", "rejected", 1), "utf-8")

    again = _cli(tmp_path, monkeypatch, "seed")

    assert again.exit_code != 0
    assert "--force" in again.output
    assert "rejected" in path.read_text(encoding="utf-8")


def test_seed_reads_the_published_mapping_not_an_existing_overlay(tmp_path, monkeypatch):
    """Seeding through the overlay would copy its rejections in as the published state."""
    assert _cli(tmp_path, monkeypatch, "seed").exit_code == 0
    path = tmp_path / "config" / "crosswalks" / "hipaa-security-rule.yaml"
    other = tmp_path / "config" / "crosswalks" / "zz-edited.yaml"
    other.write_text(
        "framework: HIPAA Security Rule\n"
        "requirements:\n"
        "  164.308(a)(5)(ii)(B):\n"
        "    - {control: RA-3, status: rejected}\n",
        encoding="utf-8",
    )

    result = _cli(tmp_path, monkeypatch, "seed", "--force")

    assert result.exit_code == 0, result.output
    rows = load_overlay(path).requirements["164.308(a)(5)(ii)(B)"]
    assert [r.control for r in rows] == ["SI-3", "RA-3"]


def test_check_strict_fails_on_stale_entries(tmp_path, monkeypatch):
    (tmp_path / "config" / "crosswalks").mkdir(parents=True)
    (tmp_path / "config" / "crosswalks" / "hipaa.yaml").write_text(
        "framework: HIPAA Security Rule\n"
        "requirements:\n"
        "  164.308(a)(5)(ii)(B):\n"
        "    - {control: SI-3, status: accepted}\n"
        "    - {control: ZZ-9, status: proposed}\n",
        encoding="utf-8",
    )

    result = _cli(tmp_path, monkeypatch, "check", "--strict")

    assert result.exit_code == 1
    assert "unknown control: 164.308(a)(5)(ii)(B) -> ZZ-9" in result.output
    assert "unreviewed: 164.308(a)(5)(ii)(B) -> RA-3" in result.output
    assert "1 pair(s) needing review" in result.output


def test_check_with_no_overlays_says_how_to_start(tmp_path, monkeypatch):
    result = _cli(tmp_path, monkeypatch, "check")
    assert result.exit_code == 0
    assert "crosswalk seed" in result.output


# ---------------------------------------------------------------------------
# Catalogs that state conditions rather than controls
#
# 45 CFR 171 defines information blocking and then sets out the exceptions, so
# its entries are conditions under which a practice is *not* blocking. There is
# no obligation for an 800-53 control to correspond to, which makes the mapping
# wrong rather than unreviewed — and an overlay's whole job is to hold that
# mapping.
# ---------------------------------------------------------------------------

CONDITIONS_FRAMEWORK = "45 CFR 171"


def _conditions_catalog():
    """Two 171 sections, shaped as the real catalog is: no source_crosswalk."""
    return [
        Control(
            control_id="171.203",
            title="Security exception",
            framework=CONDITIONS_FRAMEWORK,
            framework_version="45 CFR Part 171",
            control_statement="...",
            enhancements=[
                ControlEnhancement(
                    enhancement_id="171.203(a)", title="", baseline="", description="..."
                )
            ],
        ),
        Control(
            control_id="171.201",
            title="Preventing harm exception",
            framework=CONDITIONS_FRAMEWORK,
            framework_version="45 CFR Part 171",
            control_statement="...",
        ),
    ]


def test_seeding_a_conditions_catalog_is_refused():
    with pytest.raises(OverlayError) as excinfo:
        seed_overlay(_conditions_catalog(), CONDITIONS_FRAMEWORK)

    assert CONDITIONS_FRAMEWORK in str(excinfo.value)


def test_the_refusal_says_why_and_where_to_read_more():
    """A refusal that only refuses teaches the user to work around it.

    The message has to carry the reason, because the natural next move
    after "cannot" is to find another way to do the same thing — and here
    the other ways (hand-writing the overlay, proposing with a model) are
    worse than the one being refused.
    """
    with pytest.raises(OverlayError) as excinfo:
        seed_overlay(_conditions_catalog(), CONDITIONS_FRAMEWORK)

    message = str(excinfo.value)
    assert "conditions" in message
    assert "cfr-171-information-blocking/README.md" in message


def test_the_refusal_beats_the_empty_overlay_it_replaces():
    """Nothing is returned, rather than an overlay with every row empty.

    Before this guard the same call produced 76 rows reading `0 published
    pairs, 76 with none` and exited 0. That is not a refusal a reader can
    tell from "this catalog's crosswalk has not been published yet", and it
    is a filled-in worklist for `crosswalk propose`.
    """
    with pytest.raises(OverlayError):
        seed_overlay(_conditions_catalog(), CONDITIONS_FRAMEWORK)


def test_a_normal_framework_still_seeds():
    """The guard must not cost the case it is not about."""
    overlay = seed_overlay(_catalogs(), HIPAA)

    assert overlay.requirements
    assert any(rows for rows in overlay.requirements.values())


@pytest.mark.parametrize("written", ["45 CFR 171", "45 cfr 171", "  45   CFR   171 "])
def test_the_name_is_matched_however_it_was_typed(written):
    """`--framework` is typed by a person, so spacing and case vary."""
    with pytest.raises(OverlayError):
        seed_overlay(_conditions_catalog(), written)


@pytest.mark.parametrize("other", ["45 CFR 164", "45 CFR 1710", "HIPAA Security Rule"])
def test_a_merely_similar_name_is_not_refused(other):
    """Matched whole, not by prefix.

    `45 CFR 1710` does not exist today, and that is the point: a guard that
    refused it would be keying on a prefix, and the next real catalog whose
    name starts the same way would be silently unseedable.
    """
    from policyforge.crosswalk.overlay import _refusal_reason

    assert _refusal_reason(other) is None


def test_the_manifest_spelling_is_refused_too():
    """This repo calls the catalog two things, and both must refuse.

    `controls.json` declares `45 CFR 171`; `framework.yaml` says
    `Information Blocking (45 CFR Part 171)`. The manifest is where a
    person looks up what a catalog is called, so the second spelling is the
    one a careful user is most likely to type — and matching only the typed
    string let it through to "no requirements found", which is a politer
    spelling of the empty overlay this guard replaces.
    """
    with pytest.raises(NotAnchorableError):
        seed_overlay(_conditions_catalog(), "Information Blocking (45 CFR Part 171)")


def test_the_refusal_names_the_catalog_as_the_data_declares_it():
    """However it was typed, the message names what the catalog calls itself."""
    with pytest.raises(NotAnchorableError) as excinfo:
        seed_overlay(_conditions_catalog(), "Information Blocking (45 CFR Part 171)")

    assert str(excinfo.value).startswith("45 CFR 171 cannot anchor")


def test_the_two_refusals_are_different_types():
    """A reviewer should not be the only thing telling them apart.

    Both were `OverlayError`, and 1d's review probe passed `[]` as controls,
    hit the empty-catalog error, and read it as the guard — nearly reporting
    that `45 CFR 164` was refused when it was not.
    """
    with pytest.raises(NotAnchorableError):
        seed_overlay(_conditions_catalog(), CONDITIONS_FRAMEWORK)

    with pytest.raises(OverlayError) as excinfo:
        seed_overlay(_catalogs(), "Some Framework Nobody Loaded")
    assert not isinstance(excinfo.value, NotAnchorableError)


def test_an_unknown_name_names_what_was_actually_loaded():
    """ "No requirements found" reads as "no mapping published yet".

    That is a claim about the crosswalk; the truth is a claim about the
    name. They want opposite responses, so the message lists the frameworks
    present and marks any that cannot anchor one.
    """
    with pytest.raises(OverlayError) as excinfo:
        seed_overlay(_conditions_catalog(), "45 CFR 164")

    message = str(excinfo.value)
    assert "45 CFR 164" in message
    assert "'45 CFR 171' (cannot anchor a crosswalk)" in message


def test_a_similar_name_is_not_swept_into_the_refusal():
    """`45 CFR 164` and `45 CFR 1710` are different catalogs, not prefixes."""
    for other in ("45 CFR 164", "45 CFR 1710"):
        with pytest.raises(OverlayError) as excinfo:
            seed_overlay(_conditions_catalog(), other)
        assert not isinstance(excinfo.value, NotAnchorableError)


@pytest.mark.parametrize(
    "name",
    ["HIPAA 45 CFR 164 and 171 combined", "Guidance on 45 CFR 160 164 171"],
)
def test_the_token_match_is_gated_on_the_loaded_catalog(name):
    """The token rule is not an unbounded substring match.

    "Every distinguishing word appears" invites false positives on its
    face, so this pins what actually bounds it: the rule only consults a
    framework the loaded catalogs declare. A name carrying all of
    `{45, cfr, 171}` refuses when 171 is loaded, and gets the
    empty-catalog error when it is not — the same string, two answers,
    decided by the data rather than by the string.

    Both answers err toward refusing rather than seeding, which is the
    safe direction here: a wrongly-refused seed is a message, a wrongly
    seeded one is a crosswalk asserting something no document says.

    Built by policyforge-1d in review, as names that satisfy the token
    rule without being that catalog.
    """
    with pytest.raises(NotAnchorableError):
        seed_overlay(_conditions_catalog(), name)

    with pytest.raises(OverlayError) as excinfo:
        seed_overlay(_catalogs(), name)
    assert not isinstance(excinfo.value, NotAnchorableError)


def test_an_overlays_digest_survives_a_line_ending_change(tmp_path):
    """**The same overlay, checked out on two platforms, must record the
    same digest.**

    `.gitattributes` pins only `*.md` to LF, so a
    `config/crosswalks/*.yaml` gets whatever `core.autocrlf` decides. A
    team that commits its overlays *and* the provenance file beside the
    crosswalk therefore writes one digest on Windows and computes another
    on Linux.

    This hashed raw bytes until 2026-09-21, and the consequence is a
    **hard failure** rather than a warning: `synthesize` raises *"was not
    built from the crosswalk overlays now in config/crosswalks/"* and
    tells the operator to rebuild — blaming the overlays for a checkout
    difference, on a crosswalk that is correct.

    Measured before fixing rather than assumed: the same overlay gave
    `edba2eba…` with LF and `154707dc…` with CRLF.
    """
    overlay = b"- source: NIST 800-53 AC-2\n  target: HIPAA 164.308(a)(3)(i)\n"

    lf = tmp_path / "lf"
    lf.mkdir()
    (lf / "hipaa.yaml").write_bytes(overlay)

    crlf = tmp_path / "crlf"
    crlf.mkdir()
    (crlf / "hipaa.yaml").write_bytes(overlay.replace(b"\n", b"\r\n"))

    assert overlay_digests(lf) == overlay_digests(crlf)


def test_a_real_content_change_still_moves_the_digest(tmp_path):
    """The other half, and the one that makes the test above meaningful:
    normalising line endings must not normalise away an edit. A guard
    that made every overlay hash alike would pass the test above and
    report nothing stale, ever."""
    first = tmp_path / "a"
    first.mkdir()
    (first / "hipaa.yaml").write_bytes(b"- source: NIST 800-53 AC-2\n")

    second = tmp_path / "b"
    second.mkdir()
    (second / "hipaa.yaml").write_bytes(b"- source: NIST 800-53 AC-3\n")

    assert overlay_digests(first) != overlay_digests(second)
