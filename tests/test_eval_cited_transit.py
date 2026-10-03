"""A cited block may not say "in transit" where its own citations never do (#174).

`_tags` asks whether a reference is one the synthesis carries, never whether
the sentence under it came from that reference. The instances here are real,
from epoch 21's glm run (2026-09-17), and each passed every existing check:

- "encrypt ePHI at rest and in transit" under HIPAA 164.312(a)(2)(iv), whose
  text is "a mechanism to encrypt and decrypt electronic protected health
  information";
- "encryption in transit" under ARC-AMPE SC-28, which is protection of
  information AT REST.

The check is narrow on purpose, and sized before it was written: across that
run's 59 documents (1,096 cited blocks) "in transit" fired 3 times, all real.
"At rest" fired 4 times, mostly on defensible glosses, and is left out.
"""

from __future__ import annotations

from evals.runner import run_generation, unfounded_transit

ENCRYPT = (
    "- Implement a mechanism to encrypt and decrypt electronic protected health "
    "information. [HIPAA 164.312(a)(2)(iv) Addressable]"
)
TRANSMISSION = (
    "- Implement technical security measures to guard against unauthorized access to "
    "electronic protected health information that is being transmitted over an "
    "electronic communications network. [HIPAA 164.312(e)(1)]"
)
BACKUP_TEST = (
    "- Test backup information by decrypting and transporting a random sample of "
    "backup files. [ARC-AMPE CP-9(1) AE Mandatory]"
)

INSTANCE = (
    "## Encryption\n\n"
    "1. SRE enables encryption and decryption of ePHI at rest and in transit using "
    "the [Key Management Service] [HIPAA 164.312(a)(2)(iv) Addressable].\n"
)


def test_the_real_instance_fires():
    found = unfounded_transit(INSTANCE, ENCRYPT)

    assert len(found) == 1
    assert "at rest and in transit" in found[0]


def test_a_block_whose_citation_states_transmission_is_quiet():
    """**The passing twin.** Glossing HIPAA's transmission specification as
    "in transit" is a faithful paraphrase. Measured: a heading "ePHI in
    Storage and Transit" under the transmission specs is exactly this."""
    document = (
        "## Transmission Security\n\n"
        "1. Protect ePHI in transit over every network. [HIPAA 164.312(e)(1)]\n"
    )

    assert unfounded_transit(document, TRANSMISSION) == []


def test_transmission_under_a_different_citation_does_not_ground_it():
    """**The whole-synthesis trap.** The topic that produced the instance says
    "transporting" once, in an unrelated backup-test control. Compared with
    the whole synthesis, that word grounds an invention in a sentence the
    block never cited; compared with the block's own citations, it does not."""
    synthesis = "\n".join([ENCRYPT, BACKUP_TEST])

    assert len(unfounded_transit(INSTANCE, synthesis)) == 1


def test_the_same_citation_stating_transmission_does_ground_it():
    """The other half of the premise rule: when a line carrying the block's
    citation mentions transmission, the block is quiet."""
    grounded = ENCRYPT.replace("information.", "information in storage and during transmission.")

    assert unfounded_transit(INSTANCE, grounded) == []


def test_encryption_in_transit_cited_to_a_control_about_data_at_rest_fires():
    document = (
        "## Cryptographic Protection\n\n"
        "1. Platform Security enables encryption in transit for every channel carrying "
        "sensitive information. [ARC-AMPE SC-28 AE Mandatory]\n"
    )
    synthesis = (
        "- Protect the confidentiality and integrity of information at rest. "
        "[ARC-AMPE SC-28 AE Mandatory]"
    )

    assert len(unfounded_transit(document, synthesis)) == 1


def test_an_uncited_block_is_not_this_checks_business():
    """No citation, so no reference to test the sentence against. An uncited
    binding sentence is #196's gate, not this one."""
    document = "## Scope\n\nData in transit is covered by the network team.\n"

    assert unfounded_transit(document, ENCRYPT) == []


def test_in_motion_is_the_same_claim():
    document = INSTANCE.replace("in transit", "in motion")

    assert len(unfounded_transit(document, ENCRYPT)) == 1


def test_a_title_case_heading_is_still_a_claim():
    """Real headings capitalise it ("Protect Integrity of ePHI in Storage and
    Transit"). 9b found dropping the case flag passed every other test."""
    document = (
        "## Encryption In Transit [HIPAA 164.312(a)(2)(iv) Addressable]\n\n"
        "1. Enable the mechanism.\n"
    )

    assert len(unfounded_transit(document, ENCRYPT)) == 1


def test_a_capitalised_grounding_word_still_grounds():
    """The grounding side needs the case flag too: HIPAA states the standard
    as "Transmission security", capitalised at the start of the line."""
    synthesis = "- Transmission security: guard ePHI sent over networks. [HIPAA 164.312(e)(1)]"
    document = "## Transmission\n\n1. Protect ePHI in transit. [HIPAA 164.312(e)(1)]\n"

    assert unfounded_transit(document, synthesis) == []


def test_transport_grounds_transit():
    """**A twin per synonym** (1d's finding on #292): with one test for the
    family, `transport` and `transfer` could each be dropped unnoticed. MP-5
    states media protection "during transport", which a document faithfully
    glosses as "media in transit"."""
    synthesis = (
        "- Protect and control digital and non-digital media during transport "
        "outside of controlled areas. [NIST 800-53 MP-5]"
    )
    document = "## Media\n\n1. Encrypt media in transit to the offsite vault. [NIST 800-53 MP-5]\n"

    assert unfounded_transit(document, synthesis) == []


def test_transfer_grounds_transit():
    synthesis = (
        "- Enforce approved authorizations for controlling the flow of information "
        "when it is transferred between connected systems. [NIST 800-53 AC-4]"
    )
    document = (
        "## Flow Control\n\n1. Inspect information in transit between systems. [NIST 800-53 AC-4]\n"
    )

    assert unfounded_transit(document, synthesis) == []


def test_words_that_merely_contain_transit_are_not_claims():
    document = (
        "## Encryption\n\n"
        "1. Use an intransitive, transitory key. [HIPAA 164.312(a)(2)(iv) Addressable]\n"
    )

    assert unfounded_transit(document, ENCRYPT) == []


class _Fixed:
    def __init__(self, text: str):
        self.text = text

    def generate(self, **kwargs):
        from policyforge.llm.base import LLMResponse

        return LLMResponse(text=self.text, model="fake")


def test_the_generation_grader_fails_the_instance_and_passes_its_twin():
    """**Wired into the real grader**, with a passing arm: the same document
    without the invented qualifier passes, so a failure is this check
    speaking and not another one."""
    case = {"tier": "standard", "synthesis": ENCRYPT}
    faithful = (
        "# Encryption Standard\n\n## Requirements\n\n"
        "- Staff must implement a mechanism to encrypt and decrypt ePHI. "
        "[HIPAA 164.312(a)(2)(iv) Addressable]\n"
    )
    invented = faithful.replace(
        "encrypt and decrypt ePHI.", "encrypt and decrypt ePHI at rest and in transit."
    )

    assert run_generation(case, _Fixed(faithful)).passed
    result = run_generation(case, _Fixed(invented))
    assert not result.passed
    assert "says 'in transit' where its citations never do" in result.detail
