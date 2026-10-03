"""`--name` becomes a directory, so it is held to the shape of a slug.

`history` and `import-confluence` built their storage path as
`history_dir / tier / name` without validating it, unlike every other path
in this project, which goes through `slugify` first. Verified before fixing:
`--name ../../../../tmp/pwned` resolved to `C:\\tmp\\pwned\\index.jsonl`,
entirely outside the history store, and `..\\..\\evil` climbed out of
`.history` into `output/`.

Local CLI, so the realistic case is a mistake rather than an attacker — but
a version stream written somewhere nobody looks for it again is a silent
loss either way.
"""

from __future__ import annotations

import pytest
from click.exceptions import UsageError

from policyforge.cli import _checked_slug


@pytest.mark.parametrize(
    "name",
    [
        "access-control",
        "incident-response",
        "a",
        "a1",
        "policy-2026",
        "vendor-risk-management",
    ],
)
def test_a_real_slug_is_accepted_unchanged(name):
    assert _checked_slug(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "../../../../tmp/pwned",
        "..\\..\\evil",
        "../sibling",
        "with/slash",
        "with\\backslash",
        "..",
        ".",
        "",
    ],
)
def test_anything_that_could_leave_the_directory_is_refused(name):
    with pytest.raises(UsageError):
        _checked_slug(name)


@pytest.mark.parametrize(
    "name",
    [
        "Access-Control",  # uppercase is not what slugify produces
        "access_control",  # underscore is not a hyphen
        "access control",  # a space would become part of a directory name
        "access--control",  # doubled hyphen: slugify collapses these
        "-access",
        "access-",
        "access.control",
        "naïve-policy",
    ],
)
def test_a_name_this_tool_would_not_have_written_is_refused(name):
    """Rejected rather than silently slugified.

    The corrected form is the one the caller has to type next time to read
    the history back, so they need to see it — a quiet rewrite means their
    next `history --name` call misses.
    """
    with pytest.raises(UsageError):
        _checked_slug(name)


def test_the_error_suggests_the_name_that_would_have_worked():
    with pytest.raises(UsageError) as caught:
        _checked_slug("Access Control")
    assert "access-control" in str(caught.value)


def test_the_error_explains_why_rather_than_just_refusing():
    with pytest.raises(UsageError) as caught:
        _checked_slug("../escape")
    message = str(caught.value)
    assert "directory name" in message
    assert "outside" in message


def test_an_unslugifiable_name_still_suggests_something_usable():
    """A name that slugifies to nothing must not suggest an empty string."""
    with pytest.raises(UsageError) as caught:
        _checked_slug("日本語")
    assert "''" not in str(caught.value)
