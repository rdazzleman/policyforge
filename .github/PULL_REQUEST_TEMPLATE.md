## What this changes

<!-- What behaviour is different after this merges, and why. -->

## How it was verified

<!-- `python scripts/check.py` is the baseline. If this touches a model path,
say what you measured and against which model — a claim about model
behaviour that was reasoned about rather than run is worth flagging as
such. -->

- [ ] `python scripts/check.py` passes

## Commitments and boundaries

[docs/commitments.md](../docs/commitments.md) lists behaviour this project
states as a commitment. Tick anything this PR touches — ticking a box is not
a problem, it is a request for the right reviewer.

- [ ] Changes what may be sent to which provider (`llm/boundary.py`, `llm/channel.py`)
- [ ] Changes what the ledger records (`llm/ledger.py`)
- [ ] Changes a prompt, or how retrieved text is fenced into one
- [ ] Adds or changes an outbound endpoint (also update `ALLOWED_HOSTS` and, if it receives content, `docs/subprocessors.md`)
- [ ] Reads a new environment variable (also update `DECLARED_ENV_READERS`; add to `_CREDENTIALS` if it is a credential)
- [ ] Touches the generated-parser gate (`ingest/parser_gate.py`)
- [ ] Touches how licensed/BYOC content is read or written
- [ ] Changes a path that writes to a live system (publish, edit, pull)
- [ ] **Changes a stated commitment.** If so: major version bump and a `Security` entry in [CHANGELOG.md](../CHANGELOG.md)
- [ ] None of the above

## Documentation

- [ ] Behaviour described in [docs/](../docs/) is still accurate, or was updated in this PR
- [ ] Measurements affected by a prompt change have a new epoch rather than edited numbers
- [ ] No licensed or organization-specific content is included
