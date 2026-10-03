"""Licensed content to a hosted provider under a declared data agreement (#11).

The user's ruling (confirmed to ba directly, #11 comment 1412; 80's spec
1405): an organisation may declare, in its own config, that a NAMED hosted
provider is covered by its data agreement for licensed content. The first
loosening of `llm/boundary.py`, so every test here that matters is a
must-refuse arm, and each goes through the REAL config path: a
`config/config.yaml` in the working directory, loaded as the CLI loads it
(1d's checklist, 1408). 80's rulings 1415: a declared `local` the endpoint
disproves is refused on every channel; the opt-in covers `llm:` only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from policyforge.config import load_config
from policyforge.ingest.schema import LICENSED_MARKER, catalog_json
from policyforge.llm import boundary, ledger
from policyforge.llm.base import LLMProvider, LLMResponse
from policyforge.llm.boundary import BoundaryViolation, enforce

pytestmark = pytest.mark.own_config

HOSTED = "provider: anthropic\n  model: claude-sonnet-5\n"


@pytest.fixture(autouse=True)
def _no_ambient_endpoint_variables(monkeypatch):
    """A covered call is refused while any variable holds a URL (b5's 1439),
    and a CI runner sets several (GITHUB_SERVER_URL, say). Removed here by a
    rule of this file's own, not by `endpoint_overrides()`, so a test that
    expects a call ALLOWED measures the code and not the machine it ran on."""
    import os

    for name, value in list(os.environ.items()):
        upper = name.upper()
        if "://" in value or any(w in upper for w in ("URL", "BASE", "ENDPOINT", "PROXY")):
            monkeypatch.delenv(name, raising=False)


def _project(tmp_path: Path, monkeypatch, llm_yaml: str) -> dict:
    """A project whose config/config.yaml holds `llm:` as given, read the way
    the CLI reads it. Returns the loaded config."""
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "config.yaml").write_text("llm:\n  " + llm_yaml, encoding="utf-8")
    (tmp_path / "local_content" / "hitrust").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    return load_config()


def _licensed(tmp_path: Path) -> Path:
    """A licensed catalog, licensed by where it sits (under local_content/)."""
    path = tmp_path / "local_content" / "hitrust" / "controls.json"
    path.write_text(
        catalog_json([{"control_id": "01.a", "title": "An invented control"}]), encoding="utf-8"
    )
    return Path("local_content/hitrust/controls.json")


def _soc2_marked_outside(tmp_path: Path) -> Path:
    """A SOC 2 catalog carrying its licence in the file, saved OUTSIDE
    local_content/: licensed by its marker (#10). The declared-provider case
    GH#462's SOC 2 PR left pending on this issue."""
    path = tmp_path / "output" / "soc2" / "controls.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"control_id": "CC1.1", "title": "An invented criterion", "framework": "aicpa-tsc"}]
    path.write_text(catalog_json(rows, licence=LICENSED_MARKER), encoding="utf-8")
    return Path("output/soc2/controls.json")


# ---- the default is unchanged ------------------------------------------------------


def test_with_no_declaration_a_hosted_provider_is_refused_as_before(tmp_path, monkeypatch):
    config = _project(tmp_path, monkeypatch, HOSTED)
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    message = str(refused.value)
    assert "REFUSED: licensed content -> third-party provider" in message
    assert "llm.licensed_agreement" in message, "the refusal names the opt-in (spec 5)"
    assert "llm.classification" not in message, "and no longer the self-hosted loop"


# ---- the one route in --------------------------------------------------------------


def test_a_declared_provider_receives_licensed_content_under_its_true_class(tmp_path, monkeypatch):
    config = _project(
        tmp_path,
        monkeypatch,
        HOSTED + "  licensed_agreement:\n    provider: anthropic\n    reference: DPA-7\n",
    )
    decision = enforce(_licensed(tmp_path), config)
    assert decision.allowed
    assert decision.agreement is not None and decision.agreement.reference == "DPA-7"
    # Truthful naming (spec 2): still third-party, never relabelled local.
    assert decision.provider.klass == boundary.THIRD_PARTY
    assert "local provider" not in decision.explain()
    assert "allowed under: llm.licensed_agreement: anthropic" in decision.explain()


def test_a_model_scoped_declaration_covers_that_model(tmp_path, monkeypatch):
    config = _project(
        tmp_path,
        monkeypatch,
        HOSTED + "  licensed_agreement:\n    provider: anthropic\n    model: claude-sonnet-5\n",
    )
    assert enforce(_licensed(tmp_path), config).allowed


def test_the_soc2_catalog_reaches_a_declared_provider_and_no_other(tmp_path, monkeypatch):
    """GH#462's pending case: a marked SOC 2 catalog outside local_content/."""
    declared = _project(
        tmp_path, monkeypatch, HOSTED + "  licensed_agreement:\n    provider: anthropic\n"
    )
    assert enforce(_soc2_marked_outside(tmp_path), declared).allowed
    undeclared = _project(tmp_path, monkeypatch, HOSTED)
    with pytest.raises(BoundaryViolation):
        enforce(_soc2_marked_outside(tmp_path), undeclared)


# ---- must-refuse arms (1d's checklist, each through the real config) -------------------


@pytest.mark.parametrize(
    "agreement",
    [
        pytest.param("    provider: openrouter\n", id="another provider"),
        pytest.param("    provider: anthropic-proxy\n", id="a name that starts like it"),
        pytest.param("    provider: anthro\n", id="a name it starts like"),
        pytest.param(
            "    provider: anthropic\n    model: claude-opus-5-5\n",
            id="this provider, another model",
        ),
    ],
)
def test_a_declaration_that_does_not_cover_this_provider_still_refuses(
    tmp_path, monkeypatch, agreement
):
    config = _project(tmp_path, monkeypatch, HOSTED + "  licensed_agreement:\n" + agreement)
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    assert "which does not cover this provider" in str(refused.value)


def test_the_refusal_names_the_fields_an_endpoint_provider_needs(tmp_path, monkeypatch):
    """80 on #66: the route's shape sentence said "(provider, and optionally
    model and reference)" after base_url and vendor became required for
    endpoint providers. Both refusals that describe the shape name them."""
    config = _project(tmp_path, monkeypatch, HOSTED)
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    malformed = _project(tmp_path, monkeypatch, HOSTED + "  licensed_agreement: anthropic\n")
    with pytest.raises(ValueError) as shape:
        enforce(_licensed(tmp_path), malformed)
    for message in (str(refused.value), str(shape.value)):
        assert "base_url" in message and "vendor" in message, message


@pytest.mark.parametrize(
    "name", ["POLICYFORGE_LICENSED_AGREEMENT", "LICENSED_AGREEMENT", "POLICYFORGE_LLM_PROVIDER"]
)
def test_no_environment_variable_opens_it(tmp_path, monkeypatch, name):
    monkeypatch.setenv(name, "anthropic")
    config = _project(tmp_path, monkeypatch, HOSTED)
    with pytest.raises(BoundaryViolation):
        enforce(_licensed(tmp_path), config)


def test_a_cascade_is_covered_only_if_every_half_that_may_answer_is(tmp_path, monkeypatch):
    cascade = (
        "provider: cascade\n  primary:\n    provider: anthropic\n    model: a\n"
        "  escalate_to:\n    provider: gemini\n    model: b\n"
        "  licensed_agreement:\n    provider: anthropic\n"
    )
    config = _project(tmp_path, monkeypatch, cascade)
    with pytest.raises(BoundaryViolation):
        enforce(_licensed(tmp_path), config)


@pytest.mark.parametrize(
    "block, why",
    [
        ("  licensed_agreement: anthropic\n", "should be a mapping"),
        ("  licensed_agreement:\n    reference: x\n", "names no provider"),
        ("  licensed_agreement:\n    provider: anthropic\n    modle: x\n", "does not read"),
    ],
    ids=["a bare string", "no provider", "a misspelt key"],
)
def test_a_malformed_declaration_is_refused_not_ignored(tmp_path, monkeypatch, block, why):
    config = _project(tmp_path, monkeypatch, HOSTED + block)
    with pytest.raises(ValueError, match=why):
        enforce(_licensed(tmp_path), config)


# ---- a declared `local` the endpoint disproves (80's ruling 1415) ---------------------


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("provider: anthropic\n  classification: local\n", id="a hosted API"),
        pytest.param(
            "provider: openai-compat\n  base_url: http://10.0.0.5/v1\n  classification: local\n",
            id="RFC 1918",
        ),
        pytest.param(
            "provider: openai-compat\n  base_url: https://api.example.com/v1\n"
            "  classification: local\n",
            id="a public URL",
        ),
        pytest.param(
            "provider: openai-compat\n  base_url: http://127.0.0.1@api.example.com/v1\n"
            "  classification: local\n",
            id="userinfo before a public host",
        ),
    ],
)
def test_a_false_local_declaration_is_refused(tmp_path, monkeypatch, block):
    config = _project(tmp_path, monkeypatch, block)
    with pytest.raises(ValueError, match="is not this machine"):
        enforce(_licensed(tmp_path), config)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:11434/v1", "http://[::1]:11434/v1", "http://LOCALHOST:11434/v1"]
)
def test_a_true_local_declaration_still_passes(tmp_path, monkeypatch, url):
    """The passing case, so the refusal cannot simply fire on every declaration."""
    config = _project(
        tmp_path,
        monkeypatch,
        f"provider: openai-compat\n  base_url: {url}\n  classification: local\n",
    )
    assert enforce(_licensed(tmp_path), config).allowed


def test_a_false_local_declaration_is_refused_on_the_embed_channel_too(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # own_config: stand outside the checkout
    with pytest.raises(ValueError, match=r"embed\.classification is 'local'"):
        boundary.classify_endpoint(
            {"base_url": "http://10.0.0.9/v1", "classification": "local"},
            default_url="http://localhost:11434",
            key="embed",
        )


def test_the_opt_in_does_not_cover_the_embed_channel_and_says_so(tmp_path, monkeypatch):
    """80's Q2 ruling: `llm:` only in this version, stated as a fact."""
    config = _project(
        tmp_path, monkeypatch, HOSTED + "  licensed_agreement:\n    provider: anthropic\n"
    )
    content = boundary.ContentClassification(boundary.LICENSED, "a licensed passage")
    endpoint = boundary.classify_endpoint(
        {"base_url": "https://embed.example.com"}, default_url="", key="embed"
    )
    decision = boundary.check(content, endpoint, config)
    assert not decision.allowed
    advice = boundary.refusal_advice(decision, config, otherwise="", channel="embed")
    assert "covers `llm:` only in this version" in advice


# ---- recorded, measured on the ledger FILE (spec 4) -----------------------------------


class _Fake(LLMProvider):
    model = "claude-sonnet-5"

    def generate(self, *, system, prompt, max_tokens=4096, temperature=0.2):
        return LLMResponse(text="def parse(path):\n    return []\n", model=self.model)

    def check(self):
        return True


def _rows() -> list[dict]:
    path = ledger.DEFAULT_LEDGER_PATH
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _generate_parser(tmp_path, monkeypatch, llm_yaml, *, real=False):
    """The real `generate-parser`, with only the model replaced (or, with
    `real`, not even that): the config is loaded, the boundary enforced, and
    the provider wrapped in the real ledger."""
    import policyforge.cli as cli_mod

    _project(tmp_path, monkeypatch, llm_yaml)
    if not real:
        monkeypatch.setattr("policyforge.llm.base._build_provider", lambda config: _Fake())
    sample = tmp_path / "local_content" / "hitrust" / "sample.csv"
    sample.write_text("Control Reference,Level\n01.a Invented,Level 1\n", encoding="utf-8")
    return CliRunner().invoke(
        cli_mod.cli,
        [
            "generate-parser",
            "--framework",
            "hitrust",
            "--sample",
            "local_content/hitrust/sample.csv",
            "--out",
            str(tmp_path / "out_loader.py"),
            "--yes",
        ],
    )


def test_a_licensed_call_under_the_declaration_records_it_in_the_ledger_file(tmp_path, monkeypatch):
    _generate_parser(
        tmp_path,
        monkeypatch,
        HOSTED + "  licensed_agreement:\n    provider: anthropic\n    reference: DPA-7\n",
    )
    (row,) = _rows()
    assert row["site"] == "generate-parser"
    assert row["content_class"] == boundary.LICENSED, "generate-parser now names what it sent"
    assert row["provider_class"] == boundary.THIRD_PARTY, "never recorded as local"
    assert row["licensed_agreement"] == {
        "provider": "anthropic",
        "party": "anthropic",
        "model": "",
        "reference": "DPA-7",
    }


def test_a_licensed_call_to_a_local_model_records_no_agreement(tmp_path, monkeypatch):
    """A row never claims an agreement it did not need."""
    _generate_parser(
        tmp_path,
        monkeypatch,
        "provider: openai-compat\n  model: m\n  base_url: http://localhost:11434/v1\n"
        "  licensed_agreement:\n    provider: anthropic\n",
    )
    (row,) = _rows()
    assert row["content_class"] == boundary.LICENSED
    assert row["licensed_agreement"] is None


def test_an_undeclared_hosted_provider_sends_nothing_and_writes_no_row(tmp_path, monkeypatch):
    result = _generate_parser(tmp_path, monkeypatch, HOSTED)
    assert result.exit_code != 0
    assert "llm.licensed_agreement" in result.output
    assert not ledger.DEFAULT_LEDGER_PATH.exists() or _rows() == []


# ---- the agreement binds to the PARTY (80's ruling 1432) -----------------------------

OPENROUTER_HOST = "provider: openai-compat\n  model: m\n  base_url: https://openrouter.ai/api/v1\n"


def test_an_openai_compat_agreement_binds_to_its_host(tmp_path, monkeypatch):
    """The intended OpenRouter case: the declaration names the endpoint."""
    config = _project(
        tmp_path,
        monkeypatch,
        OPENROUTER_HOST
        + "  licensed_agreement:\n    provider: openai-compat\n"
        + "    base_url: https://OpenRouter.ai/somewhere/else\n",
    )
    decision = enforce(_licensed(tmp_path), config)
    assert decision.allowed, "host matched case-folded; scheme and path ignored"
    assert decision.agreement.party() == "openrouter.ai"


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param("https://api.vendor-b.example/v1", id="another host"),
        pytest.param("https://openrouter.ai:8443/api/v1", id="the right host, another port"),
        pytest.param("https://openrouter.ai.example.com/api/v1", id="a look-alike host"),
    ],
)
def test_an_openai_compat_agreement_for_another_endpoint_refuses(tmp_path, monkeypatch, declared):
    config = _project(
        tmp_path,
        monkeypatch,
        OPENROUTER_HOST
        + f"  licensed_agreement:\n    provider: openai-compat\n    base_url: {declared}\n",
    )
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    assert "(openrouter.ai)" in str(refused.value), "the refusal names the configured host"


@pytest.mark.parametrize(
    "configured, declared",
    [
        pytest.param(
            "https://openrouter.ai:443/api/v1", "https://openrouter.ai", id="https :443 configured"
        ),
        pytest.param(
            "https://openrouter.ai/api/v1", "https://openrouter.ai:443", id="https :443 declared"
        ),
        pytest.param(
            "http://llm.lan.example:80/v1", "http://llm.lan.example", id="http :80 configured"
        ),
    ],
)
def test_a_written_default_port_is_the_same_endpoint(tmp_path, monkeypatch, configured, declared):
    """80 on #66: `:443` on an https URL is the endpoint with no port written,
    so it is covered; it used to be refused with identical hosts on both sides."""
    config = _project(
        tmp_path,
        monkeypatch,
        f"provider: openai-compat\n  model: m\n  base_url: {configured}\n"
        + f"  licensed_agreement:\n    provider: openai-compat\n    base_url: {declared}\n",
    )
    assert enforce(_licensed(tmp_path), config).allowed


@pytest.mark.parametrize(
    "configured, declared",
    [
        pytest.param("https://openrouter.ai:80/api/v1", "https://openrouter.ai", id="https :80"),
        pytest.param("http://llm.lan.example:443/v1", "http://llm.lan.example", id="http :443"),
    ],
)
def test_a_port_that_is_not_its_schemes_default_still_differs(
    tmp_path, monkeypatch, configured, declared
):
    """The neighbours of the normalisation: only the scheme's OWN default is dropped."""
    config = _project(
        tmp_path,
        monkeypatch,
        f"provider: openai-compat\n  model: m\n  base_url: {configured}\n"
        + f"  licensed_agreement:\n    provider: openai-compat\n    base_url: {declared}\n",
    )
    with pytest.raises(BoundaryViolation):
        enforce(_licensed(tmp_path), config)


def test_a_lan_model_declared_with_its_own_base_url_is_the_truthful_route(tmp_path, monkeypatch):
    """80's 1415: a LAN box is self-hosted, not local; the opt-in is its route."""
    config = _project(
        tmp_path,
        monkeypatch,
        "provider: openai-compat\n  model: m\n  base_url: http://10.0.0.5:11434/v1\n"
        "  licensed_agreement:\n    provider: openai-compat\n"
        "    base_url: http://10.0.0.5:11434/v1\n",
    )
    decision = enforce(_licensed(tmp_path), config)
    assert decision.allowed and decision.provider.klass == boundary.SELF_HOSTED
    assert decision.agreement.party() == "http://10.0.0.5:11434", "plain http stays visible"


@pytest.mark.parametrize(
    "configured, declared, shown",
    [
        pytest.param(
            "http://openrouter.ai/api/v1",
            "https://openrouter.ai",
            "at 'openrouter.ai', which does not cover this provider (http://openrouter.ai)",
            id="http against https",
        ),
        pytest.param(
            "https://10.0.0.5:11434/v1",
            "http://10.0.0.5:11434",
            "at 'http://10.0.0.5:11434', which does not cover this provider (10.0.0.5:11434)",
            id="https against http",
        ),
    ],
)
def test_the_scheme_belongs_to_the_agreement(tmp_path, monkeypatch, configured, declared, shown):
    """80's ruling 1446: the scheme is compared exactly. Plain http hands the
    text to every hop between, which is not the party the agreement names.
    The refusal prints both sides as the matcher compares them, so they never
    read the same (80's 1469)."""
    config = _project(
        tmp_path,
        monkeypatch,
        f"provider: openai-compat\n  model: m\n  base_url: {configured}\n"
        f"  licensed_agreement:\n    provider: openai-compat\n    base_url: {declared}\n",
    )
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    assert shown in str(refused.value).replace("\n", " ")


@pytest.mark.parametrize(
    "llm_yaml, why",
    [
        pytest.param(
            "provider: openai-compat\n  base_url: https://openrouter.ai/v1\n"
            "  licensed_agreement:\n    provider: openai-compat\n",
            "no base_url",
            id="openai-compat with no base_url",
        ),
        pytest.param(
            "provider: litellm\n  model: openrouter/x\n"
            "  licensed_agreement:\n    provider: litellm\n",
            "no vendor",
            id="litellm with no api_base and no vendor",
        ),
        pytest.param(
            "provider: litellm\n  model: x\n  api_base: https://proxy.example/v1\n"
            "  licensed_agreement:\n    provider: litellm\n    vendor: openai\n",
            "no base_url",
            id="litellm with an api_base but no base_url",
        ),
    ],
)
def test_a_protocol_provider_declared_without_its_party_is_malformed(
    tmp_path, monkeypatch, llm_yaml, why
):
    config = _project(tmp_path, monkeypatch, llm_yaml)
    with pytest.raises(ValueError, match=why):
        enforce(_licensed(tmp_path), config)


def test_a_litellm_agreement_binds_to_the_vendor_the_model_names(tmp_path, monkeypatch):
    config = _project(
        tmp_path,
        monkeypatch,
        "provider: litellm\n  model: anthropic/claude-sonnet-5\n"
        "  licensed_agreement:\n    provider: litellm\n    vendor: anthropic\n",
    )
    assert enforce(_licensed(tmp_path), config).allowed


@pytest.mark.parametrize(
    "model",
    [
        pytest.param("openrouter/deepseek", id="another vendor"),
        pytest.param("claude-sonnet-5", id="a bare model: its vendor cannot be resolved"),
    ],
)
def test_a_litellm_model_that_is_not_the_declared_vendor_refuses(tmp_path, monkeypatch, model):
    config = _project(
        tmp_path,
        monkeypatch,
        f"provider: litellm\n  model: {model}\n"
        "  licensed_agreement:\n    provider: litellm\n    vendor: anthropic\n",
    )
    with pytest.raises(BoundaryViolation):
        enforce(_licensed(tmp_path), config)


# ---- a declared `local` on a hosted litellm vendor (1d's 1426) -------------------------


@pytest.mark.parametrize("model", ["openai/gpt-4o", "anthropic/claude-sonnet-5", "gpt-4o"])
def test_litellm_with_no_api_base_declared_local_on_a_hosted_vendor_is_refused(
    tmp_path, monkeypatch, model
):
    config = _project(
        tmp_path, monkeypatch, f"provider: litellm\n  model: {model}\n  classification: local\n"
    )
    with pytest.raises(ValueError, match="is not this machine"):
        enforce(_licensed(tmp_path), config)


@pytest.mark.parametrize("model", ["ollama/qwen3:14b", "ollama_chat/qwen3:14b"])
def test_litellm_ollama_with_no_api_base_declared_local_still_passes(tmp_path, monkeypatch, model):
    """litellm sends ollama/... to this machine by default: the passing case."""
    config = _project(
        tmp_path, monkeypatch, f"provider: litellm\n  model: {model}\n  classification: local\n"
    )
    assert enforce(_licensed(tmp_path), config).allowed


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("https://gpu.elsewhere.example:11434", id="a public host"),
        pytest.param("http://localhost:11434", id="loopback too (the blanket rule)"),
    ],
)
def test_a_declared_local_ollama_model_is_refused_while_the_environment_can_move_it(
    tmp_path, monkeypatch, value
):
    """1d's 1441: litellm reads OLLAMA_API_BASE before its localhost default,
    so with no api_base the claim holds only while no variable could move it.
    No agreement is involved: this is the default boundary."""
    monkeypatch.setenv("OLLAMA_API_BASE", value)
    config = _project(
        tmp_path, monkeypatch, "provider: litellm\n  model: ollama/q\n  classification: local\n"
    )
    with pytest.raises(ValueError, match="OLLAMA_API_BASE") as refused:
        enforce(_licensed(tmp_path), config)
    assert value not in str(refused.value), "names the variable, never its value"


def test_a_loopback_api_base_is_the_route_ollama_api_base_cannot_move(tmp_path, monkeypatch):
    """The passing case the refusal points to: an explicit loopback api_base.
    (A proxy can still carry it over http: that is issue 69, not this.)"""
    monkeypatch.setenv("OLLAMA_API_BASE", "https://gpu.elsewhere.example:11434")
    config = _project(
        tmp_path,
        monkeypatch,
        "provider: litellm\n  model: ollama/q\n  api_base: http://localhost:11434\n"
        "  classification: local\n",
    )
    assert enforce(_licensed(tmp_path), config).allowed


def test_without_a_declaration_litellm_with_no_api_base_is_still_inferred_third_party(
    tmp_path, monkeypatch
):
    """The declared-local check reads the vendor; the default inference does not
    change: litellm with no api_base stays third-party, closed."""
    monkeypatch.chdir(tmp_path)  # own_config: stand outside the checkout
    inferred = boundary.classify_provider({"provider": "litellm", "model": "ollama/qwen3"})
    assert inferred.klass == boundary.THIRD_PARTY


# ---- an environment endpoint override under an agreement (b5's 1430) ------------------


@pytest.mark.parametrize(
    "name", ["ANTHROPIC_BASE_URL", "OPENROUTER_API_BASE", "AWS_ENDPOINT_URL_BEDROCK_RUNTIME"]
)
def test_an_endpoint_override_in_the_environment_refuses_a_covered_call(
    tmp_path, monkeypatch, name
):
    monkeypatch.setenv(name, "http://127.0.0.1:9/v1")
    config = _project(
        tmp_path, monkeypatch, HOSTED + "  licensed_agreement:\n    provider: anthropic\n"
    )
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    assert name in str(refused.value), "the refusal names the variable"
    assert "does not cover" not in str(refused.value), (
        "the agreement DOES cover this provider; the variable is the reason (80 on #66)"
    )


def test_an_endpoint_override_does_not_touch_content_that_needs_no_agreement(tmp_path, monkeypatch):
    """Only a call the agreement would carry is refused: organisation text to a
    hosted provider is unaffected (the default is unchanged)."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/v1")
    config = _project(tmp_path, monkeypatch, HOSTED)
    org = tmp_path / "output" / "draft.md"
    org.parent.mkdir(parents=True, exist_ok=True)
    org.write_text("# Our own draft\n", encoding="utf-8")
    assert enforce(Path("output/draft.md"), config).allowed


@pytest.mark.parametrize(
    "name, value",
    [
        pytest.param("POLICYFORGE_UNRELATED", "https://elsewhere.example/v1", id="any name, a URL"),
        pytest.param("GITHUB_SERVER_URL", "https://git.example", id="a CI runner's URL"),
        pytest.param(
            "HTTPS_PROXY_URL", "http://proxy.example:3128", id="a proxy-like unlisted name"
        ),
        pytest.param("SOME_TOOL_URLS", "a, https://elsewhere.example", id="a URL in a list"),
        pytest.param("ANTHROPIC_BASE_URL", "elsewhere.example:9", id="an override name, no scheme"),
    ],
)
def test_a_covered_call_is_refused_by_the_variables_value_or_its_name(
    tmp_path, monkeypatch, name, value
):
    """b5's 1439: a name rule missed AWS_BEDROCK_RUNTIME_ENDPOINT,
    AZURE_OPENAI_ENDPOINT and WATSONX_URL. A variable is refused by its VALUE
    (any http(s) URL) whatever its name, and by its NAME whatever its value."""
    monkeypatch.setenv(name, value)
    config = _project(
        tmp_path, monkeypatch, HOSTED + "  licensed_agreement:\n    provider: anthropic\n"
    )
    with pytest.raises(BoundaryViolation) as refused:
        enforce(_licensed(tmp_path), config)
    assert name in str(refused.value), "the refusal names the variable"
    assert value not in str(refused.value), "and never prints its value"


@pytest.mark.parametrize(
    "value", ["plain words", "C:\\tools\\bin", "ftp://files.example/x", "https://", "http:/x"]
)
def test_a_variable_holding_no_http_url_does_not_refuse(tmp_path, monkeypatch, value):
    """The passing case: the value rule reads http(s) URLs with a host, not
    every value that has a colon in it."""
    monkeypatch.setenv("POLICYFORGE_UNRELATED", value)
    config = _project(
        tmp_path, monkeypatch, HOSTED + "  licensed_agreement:\n    provider: anthropic\n"
    )
    assert enforce(_licensed(tmp_path), config).allowed


#: b5's three measured misses (1439): the litellm model, the variable that
#: moved it, and the dummy settings the SDK needs to build a request. No real
#: credential: every one is a placeholder, and the real ones are removed.
_LISTENER_CASES = [
    pytest.param(
        "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
        "bedrock",
        "AWS_BEDROCK_RUNTIME_ENDPOINT",
        {
            "AWS_ACCESS_KEY_ID": "dummy",
            "AWS_SECRET_ACCESS_KEY": "dummy",
            "AWS_REGION_NAME": "us-east-1",
        },
        id="bedrock",
    ),
    pytest.param(
        "azure/gpt-5-mini",
        "azure",
        "AZURE_OPENAI_ENDPOINT",
        {"AZURE_API_KEY": "dummy", "AZURE_API_VERSION": "2024-10-21"},
        id="azure",
    ),
    pytest.param(
        "watsonx/ibm/granite-3-8b-instruct",
        "watsonx",
        "WATSONX_URL",
        {"WATSONX_TOKEN": "dummy", "WATSONX_PROJECT_ID": "dummy"},
        id="watsonx",
    ),
]

_REAL_CREDENTIALS = (
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AZURE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "WATSONX_APIKEY",
    "WATSONX_API_KEY",
    "WATSONX_TOKEN",
)


@pytest.mark.parametrize("model, vendor, variable, settings", _LISTENER_CASES)
def test_a_listener_behind_an_override_receives_nothing(
    tmp_path, monkeypatch, model, vendor, variable, settings
):
    """b5's canary, end to end (1439): the real generate-parser and the REAL
    litellm provider, an agreement covering the vendor, and the vendor's own
    endpoint variable pointed at an HTTP listener on this machine. The
    listener records every request until it is shut down, so a send at any
    point during the run is seen."""
    import http.server
    import threading

    pytest.importorskip("litellm")
    received: list[bytes] = []

    class Record(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            received.append(self.rfile.read(length))
            self.send_response(500)
            self.end_headers()

        do_GET = do_POST

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Record)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for name in _REAL_CREDENTIALS:
            monkeypatch.delenv(name, raising=False)
        for name, value in settings.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv(variable, f"http://127.0.0.1:{server.server_address[1]}")
        result = _generate_parser(
            tmp_path,
            monkeypatch,
            f"provider: litellm\n  model: {model}\n"
            f"  licensed_agreement:\n    provider: litellm\n    vendor: {vendor}\n",
            real=True,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert received == [], f"the listener received {len(received)} request(s)"
    assert result.exit_code != 0
    assert variable in result.output, "the refusal names the variable"
    assert not ledger.DEFAULT_LEDGER_PATH.exists() or _rows() == []


# ---- the standard proxy names (80's ruling 1446) ---------------------------------------

_PROXY_VALUE = "http://proxy.example:3128"
_LAN_HTTP = (
    "provider: openai-compat\n  model: m\n  base_url: http://10.0.0.5:11434/v1\n"
    "  licensed_agreement:\n    provider: openai-compat\n    base_url: http://10.0.0.5:11434\n"
)


@pytest.mark.parametrize(
    "name", ["HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "all_proxy"]
)
def test_a_standard_proxy_does_not_refuse_an_https_destination(tmp_path, monkeypatch, name):
    """A proxy routes the connection; for https the TLS session still ends at
    the named host. These names only, in either case."""
    monkeypatch.setenv(name, _PROXY_VALUE)
    config = _project(
        tmp_path, monkeypatch, HOSTED + "  licensed_agreement:\n    provider: anthropic\n"
    )
    assert enforce(_licensed(tmp_path), config).allowed


@pytest.mark.parametrize("name", ["HTTP_PROXY", "ALL_PROXY", "http_proxy"])
def test_a_proxy_refuses_an_http_destination(tmp_path, monkeypatch, name):
    """Over plain http the proxy reads the text: httpx 0.28.1 sends even
    http://localhost through HTTP_PROXY (measured on #66). So for an http
    destination the proxy names are not exempt."""
    monkeypatch.setenv(name, _PROXY_VALUE)
    config = _project(tmp_path, monkeypatch, _LAN_HTTP)
    with pytest.raises(BoundaryViolation, match=f"(?i){name}"):  # Windows upper-cases names
        enforce(_licensed(tmp_path), config)


def test_a_proxy_refuses_an_ollama_vendor_agreement(tmp_path, monkeypatch):
    """1d's 1478: an ollama vendor with no api_base goes to http://localhost,
    so HTTP_PROXY is not exempt under an agreement for it either."""
    monkeypatch.setenv("HTTP_PROXY", _PROXY_VALUE)
    config = _project(
        tmp_path,
        monkeypatch,
        "provider: litellm"
        + chr(10)
        + "  model: ollama/q"
        + chr(10)
        + "  licensed_agreement:"
        + chr(10)
        + "    provider: litellm"
        + chr(10)
        + "    vendor: ollama"
        + chr(10),
    )
    with pytest.raises(BoundaryViolation, match="HTTP_PROXY"):
        enforce(_licensed(tmp_path), config)


def test_no_proxy_never_refuses(tmp_path, monkeypatch):
    """NO_PROXY only removes proxying, so it is exempt for http too."""
    monkeypatch.setenv("NO_PROXY", "localhost, http://10.0.0.5")
    config = _project(tmp_path, monkeypatch, _LAN_HTTP)
    assert enforce(_licensed(tmp_path), config).allowed


def test_a_proxy_refuses_a_declared_local_ollama_model(tmp_path, monkeypatch):
    """ollama's default is http://localhost, which HTTP_PROXY carries."""
    monkeypatch.setenv("HTTP_PROXY", _PROXY_VALUE)
    config = _project(
        tmp_path, monkeypatch, "provider: litellm\n  model: ollama/q\n  classification: local\n"
    )
    with pytest.raises(ValueError, match="HTTP_PROXY"):
        enforce(_licensed(tmp_path), config)


def test_the_ledger_records_the_party_not_the_protocol(tmp_path, monkeypatch):
    _generate_parser(
        tmp_path,
        monkeypatch,
        OPENROUTER_HOST
        + "  licensed_agreement:\n    provider: openai-compat\n"
        + "    base_url: https://openrouter.ai/api/v1\n    reference: DPA-9\n",
    )
    (row,) = _rows()
    assert row["licensed_agreement"]["provider"] == "openai-compat"
    assert row["licensed_agreement"]["party"] == "openrouter.ai"
