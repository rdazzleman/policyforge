"""The shell answers "what does this send anywhere" for its own provider.

**The question is situational and that decides the design.** A user asking
it is sitting inside a model interface at the time — they are talking to
zardoz — so the answer must describe *the provider this shell is
configured with*. An answer about a hypothetical provider is worse than no
answer, because it is confidently about the wrong thing.

So the skill names the provider, the model and the endpoint before it
shows the table. Same rule as a citation count needing its catalogs: an
answer without the configuration it describes cannot be checked by the
person reading it.
"""

from __future__ import annotations

import pytest

from policyforge.zardoz.skills import SKILLS, _boundary


class _State:
    """The two attributes this skill reads, and nothing else."""

    def __init__(self, config):
        self.config = config


LOCAL = {
    "llm": {
        "provider": "openai-compat",
        "model": "qwen3:14b",
        "base_url": "http://localhost:11434/v1",
    }
}
HOSTED = {"llm": {"provider": "anthropic", "model": "claude-sonnet-5"}}


def test_the_skill_is_registered_and_routable():
    assert "boundary" in SKILLS
    assert SKILLS["boundary"].run is _boundary
    assert SKILLS["boundary"].arguments == {}


@pytest.mark.parametrize(
    "config, expected",
    [(LOCAL, "openai-compat"), (HOSTED, "anthropic")],
    ids=["local", "hosted"],
)
def test_it_names_the_provider_it_answered_for(config, expected):
    """The line that stops the answer being about the wrong thing."""
    answer = _boundary(_State(config), [])

    assert expected in answer.splitlines()[0]
    assert config["llm"]["model"] in answer.splitlines()[0]


def test_it_names_the_endpoint_when_one_is_configured():
    answer = _boundary(_State(LOCAL), [])

    assert "http://localhost:11434/v1" in answer


def test_a_local_endpoint_is_classified_local_so_licensed_content_is_allowed():
    """The one route on which a licensed catalog's text never leaves the
    machine. If this reads `third-party`, the boundary refuses the only
    configuration that can hold licensed content — so it is asserted
    rather than assumed.
    """
    answer = _boundary(_State(LOCAL), [])

    assert "Classified as: local" in answer


def test_a_hosted_provider_is_classified_third_party():
    answer = _boundary(_State(HOSTED), [])

    assert "third-party" in answer


def test_an_unconfigured_shell_says_so_rather_than_describing_nothing():
    """`(none configured)` beats a blank, which reads as a rendering bug —
    and the classification still has to be the conservative one.
    """
    answer = _boundary(_State({}), [])

    assert "(none configured)" in answer
    assert "third-party" in answer


def test_the_default_ceilings_are_stated_rather_than_left_silent():
    """A reader cannot tell "nothing tightened" from "the tightening
    section failed to render" unless one of them says so."""
    answer = _boundary(_State(LOCAL), [])

    assert "No ceiling is tightened by config" in answer


def test_a_tightened_ceiling_is_reported_with_what_it_was_tightened_to():
    """My first version of this asserted `"Tightened" in answer or "No
    ceiling" in answer` — true whichever branch ran, so it could not fail.
    The config also has to genuinely tighten: setting `licensed` to `local`
    changes nothing, because that is already its default.
    """
    config = {
        "llm": {
            "provider": "openai-compat",
            "base_url": "http://localhost:11434/v1",
            "boundary": {"organization-internal": "self-hosted"},
        }
    }

    answer = _boundary(_State(config), [])

    assert "Tightened by config (llm.boundary):" in answer
    assert "organization-internal -> at most self-hosted" in answer
    assert "No ceiling is tightened" not in answer


CASCADE = {
    "llm": {
        "provider": "cascade",
        "primary": {
            "provider": "openai-compat",
            "model": "qwen3:14b",
            "base_url": "http://localhost:11434/v1",
        },
        "escalate_to": {"provider": "anthropic", "model": "claude-sonnet-5"},
    }
}


def test_a_cascade_names_both_halves_rather_than_claiming_no_model():
    """A cascade has no model of its own.

    The first version said `model (no model set), via the provider's
    default endpoint` — false, where *two, one per half* was true. The
    classification line below it already named both halves, so the header
    contradicted the line under it. Found in review by ba.
    """
    header = _boundary(_State(CASCADE), []).splitlines()[0]

    assert "(no model set)" not in header
    assert "qwen3:14b" in header
    assert "claude-sonnet-5" in header
    assert "http://localhost:11434/v1" in header


def test_a_cascade_half_with_no_model_still_says_so():
    """Naming both halves must not mean inventing one that is absent."""
    config = {"llm": {"provider": "cascade", "primary": {"provider": "local"}}}

    header = _boundary(_State(config), []).splitlines()[0]

    assert "(no model set)" in header
    assert "(unset)" in header
