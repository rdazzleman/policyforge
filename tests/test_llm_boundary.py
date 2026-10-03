"""Which content may be sent to which provider.

The rule under test is one sentence — licensed content only reaches a model
you run yourself — but almost every way of getting it wrong is a way of
being wrong in the permissive direction. So the cases that matter are the
ones where something is unknown, mis-declared, or only half local: an
unrecognised provider, a `local` provider pointed at a public URL, a
cascade whose cheap half is on localhost and whose fallback is Anthropic, a
framework directory with no manifest.

Every one of those resolves towards refusal here, and each has its own test,
because a fail-closed default that quietly stopped being the default is
indistinguishable from a working control right up until it matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.llm import boundary
from policyforge.llm.boundary import (
    LICENSED,
    LOCAL,
    ORG_INTERNAL,
    PUBLIC_DOMAIN,
    SELF_HOSTED,
    THIRD_PARTY,
    BoundaryViolation,
    ceilings,
    classify_path,
    classify_provider,
    enforce,
)

LOCAL_LLM = {"provider": "local", "base_url": "http://localhost:11434/v1"}
HOSTED_LLM = {"provider": "anthropic", "model": "claude-sonnet-5"}


# ---- providers ------------------------------------------------------------


@pytest.mark.parametrize(
    ("llm", "expected"),
    [
        ({"provider": "anthropic", "model": "claude-sonnet-5"}, THIRD_PARTY),
        ({"provider": "bedrock", "model": "anthropic.claude"}, THIRD_PARTY),
        ({"provider": "vertex", "model": "gemini"}, THIRD_PARTY),
        ({"provider": "local", "base_url": "http://localhost:11434/v1"}, LOCAL),
        ({"provider": "local", "base_url": "http://127.0.0.1:8080/v1"}, LOCAL),
        ({"provider": "openai-compat", "base_url": "http://[::1]:8080/v1"}, LOCAL),
        ({"provider": "openai-compat", "base_url": "http://192.168.1.5:8000/v1"}, SELF_HOSTED),
        ({"provider": "openai-compat", "base_url": "http://10.0.0.7:8000/v1"}, SELF_HOSTED),
        ({"provider": "openai-compat", "base_url": "http://gpu.internal:8000/v1"}, SELF_HOSTED),
        ({"provider": "openai-compat", "base_url": "https://api.together.xyz/v1"}, THIRD_PARTY),
        ({"provider": "litellm", "model": "openrouter/deepseek/deepseek-v4"}, THIRD_PARTY),
        ({"provider": "litellm", "api_base": "http://localhost:11434"}, LOCAL),
    ],
)
def test_providers_are_classified_by_where_the_bytes_go(llm, expected):
    assert classify_provider(llm).klass == expected


def test_the_local_alias_does_not_make_a_hosted_endpoint_local():
    """`provider: local` names the protocol, not the network.

    Someone who points the OpenAI-compatible provider at a hosted vLLM
    endpoint has not made it local by choosing the alias, and reading the
    alias as a guarantee is the one misreading with a licence attached.
    """
    classification = classify_provider(
        {"provider": "local", "base_url": "https://api.deepinfra.com/v1/openai"}
    )
    assert classification.klass == THIRD_PARTY


def test_an_unrecognised_provider_is_treated_as_exposed():
    assert classify_provider({"provider": "something-new"}).klass == THIRD_PARTY
    assert classify_provider({}).klass == THIRD_PARTY


def test_a_provider_with_no_endpoint_is_treated_as_exposed():
    """An OpenAI-compatible block missing its base_url reaches the factory's
    own error eventually; until then it must not read as local."""
    assert classify_provider({"provider": "openai-compat"}).klass == THIRD_PARTY


def test_a_cascade_is_as_exposed_as_its_most_exposed_half():
    """Which half answers depends on a runtime failure, so both count."""
    classification = classify_provider(
        {"provider": "cascade", "primary": LOCAL_LLM, "escalate_to": HOSTED_LLM}
    )
    assert classification.klass == THIRD_PARTY
    assert "cascade" in classification.reason


def test_a_cascade_of_two_local_models_stays_local():
    classification = classify_provider(
        {
            "provider": "cascade",
            "primary": LOCAL_LLM,
            "escalate_to": {"provider": "local", "base_url": "http://localhost:8080/v1"},
        }
    )
    assert classification.klass == LOCAL


def test_a_declaration_outranks_the_inference():
    """The operator knows their own network; this module is guessing.

    A self-hosted model behind a public DNS name reads as third-party here
    and is not, and the alternative to a declaration is the operator giving
    up on the check entirely.
    """
    classification = classify_provider(
        {
            "provider": "openai-compat",
            "base_url": "https://llm.example.com/v1",
            "classification": "self-hosted",
        }
    )
    assert classification.klass == SELF_HOSTED
    assert classification.declared


def test_a_declaration_naming_no_real_class_is_refused():
    with pytest.raises(ValueError, match="not a provider class"):
        classify_provider({"provider": "anthropic", "classification": "internal-ish"})


# ---- content --------------------------------------------------------------


def _framework(root: Path, name: str, manifest: str | None) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "controls.json").write_text("[]", encoding="utf-8")
    if manifest:
        (directory / "framework.yaml").write_text(manifest, encoding="utf-8")
    return directory


def test_a_public_domain_catalog_is_public_domain(tmp_path):
    root = tmp_path / "data" / "frameworks"
    directory = _framework(root, "nist", "id: nist-800-53-r5\nlicence: public-domain\n")
    config = {"frameworks": {"search_paths": [str(root)]}}

    classification = classify_path(directory / "controls.json", config)
    assert classification.klass == PUBLIC_DOMAIN
    assert classification.framework_id == "nist-800-53-r5"


def test_a_licensed_catalog_is_licensed(tmp_path):
    root = tmp_path / "frameworks"
    directory = _framework(root, "hitrust", "id: hitrust-csf\nlicence: licensed\n")
    config = {"frameworks": {"search_paths": [str(root)]}}

    assert classify_path(directory / "controls.json", config).klass == LICENSED


def test_a_catalog_with_no_manifest_is_licensed(tmp_path):
    """Deferring to the registry's existing rule: undeclared is not safe."""
    root = tmp_path / "frameworks"
    directory = _framework(root, "mystery", None)
    config = {"frameworks": {"search_paths": [str(root)]}}

    classification = classify_path(directory / "controls.json", config)
    assert classification.klass == LICENSED
    assert "no manifest" in classification.reason


def test_anything_under_local_content_is_licensed(tmp_path):
    """The directory is gitignored because of what people put in it.

    A raw MyCSF CSV dropped there has no manifest beside it and never will;
    the location is the declaration.
    """
    root = tmp_path / "local_content"
    root.mkdir()
    export = root / "CSFLibraryReport.csv"
    export.write_text("a,b\n", encoding="utf-8")
    config = {"frameworks": {"search_paths": [str(root)]}}

    assert classify_path(export, config).klass == LICENSED


def test_the_organizations_own_documents_are_organization_internal(tmp_path):
    document = tmp_path / "output" / "standards" / "access-control.md"
    document.parent.mkdir(parents=True)
    document.write_text("# Access Control\n", encoding="utf-8")

    assert classify_path(document, {}).klass == ORG_INTERNAL


# ---- the pairing ----------------------------------------------------------


def test_licensed_content_may_not_leave_for_a_third_party(tmp_path):
    root = tmp_path / "local_content"
    root.mkdir()
    export = root / "export.csv"
    export.write_text("a,b\n", encoding="utf-8")
    config = {"llm": HOSTED_LLM, "frameworks": {"search_paths": [str(root)]}}

    with pytest.raises(BoundaryViolation) as excinfo:
        enforce(export, config)

    message = str(excinfo.value)
    assert "REFUSED" in message
    assert "licensed" in message
    # The refusal has to say what to do about it, or the next thing that
    # happens is somebody deleting the check. It names the one route for
    # licensed content (#11: the data-agreement opt-in) and no longer sends
    # anyone to `llm.classification`, which cannot widen the licensed ceiling:
    # the loop where `self-hosted` was refused and only a false `local` passed.
    assert "llm.licensed_agreement" in message
    assert "llm.classification" not in message


def test_licensed_content_may_go_to_a_local_model(tmp_path):
    root = tmp_path / "local_content"
    root.mkdir()
    export = root / "export.csv"
    export.write_text("a,b\n", encoding="utf-8")
    config = {"llm": LOCAL_LLM, "frameworks": {"search_paths": [str(root)]}}

    assert enforce(export, config).allowed


def test_licensed_content_may_not_go_to_a_self_hosted_model(tmp_path):
    """Self-hosted is inside the org and still outside the default ceiling.

    Whether a licence permits a copy on the organization's own GPU box is a
    question about that licence, and the default answer this project gives
    is the one that cannot breach it.
    """
    root = tmp_path / "local_content"
    root.mkdir()
    export = root / "export.csv"
    export.write_text("a,b\n", encoding="utf-8")
    config = {
        "llm": {"provider": "openai-compat", "base_url": "http://10.0.0.7:8000/v1"},
        "frameworks": {"search_paths": [str(root)]},
    }

    with pytest.raises(BoundaryViolation):
        enforce(export, config)


def test_the_organizations_own_documents_may_go_to_a_hosted_model(tmp_path):
    """The ordinary case, and the one the tool exists to perform."""
    document = tmp_path / "synthesis.md"
    document.write_text("# Synthesis\n", encoding="utf-8")

    assert enforce(document, {"llm": HOSTED_LLM}).allowed


# ---- ceilings -------------------------------------------------------------


def test_the_defaults_restrict_only_licensed_content():
    resolved = ceilings({})
    assert resolved[LICENSED] == LOCAL
    assert resolved[ORG_INTERNAL] == THIRD_PARTY
    assert resolved[PUBLIC_DOMAIN] == THIRD_PARTY


def test_a_ceiling_can_be_tightened(tmp_path):
    document = tmp_path / "synthesis.md"
    document.write_text("# Synthesis\n", encoding="utf-8")
    config = {"llm": {**HOSTED_LLM, "boundary": {"organization-internal": "self-hosted"}}}

    with pytest.raises(BoundaryViolation):
        enforce(document, config)


def test_a_ceiling_cannot_be_loosened():
    """Config tightens only.

    A line of YAML is the wrong weight for "our HITRUST export may go to
    OpenRouter". Declaring the provider says the same thing in a place where
    it reads as a claim about the network, which is what it is.
    """
    with pytest.raises(ValueError, match="more permissive"):
        ceilings({"llm": {"boundary": {"licensed": "third-party"}}})


def test_a_ceiling_naming_nothing_real_is_refused():
    with pytest.raises(ValueError, match="not a content class"):
        ceilings({"llm": {"boundary": {"propietary": "local"}}})
    with pytest.raises(ValueError, match="not a provider class"):
        ceilings({"llm": {"boundary": {"licensed": "on-prem"}}})


def test_the_matrix_renders_every_class():
    rendered = boundary.matrix({})
    for name in boundary.CONTENT_CLASSES + boundary.PROVIDER_CLASSES:
        assert name in rendered
