"""A catalog written before a field existed still loads, with its default.

This lived in the GovRAMP parser's tests, which left this tree for the
licensed-framework plugin (#32, part 3). It exercises only the public
`schema.load_controls` on a hand-written file, so it stays public (1d on #50).
"""

from __future__ import annotations

import json

from policyforge.ingest.schema import load_controls


def test_a_catalog_written_before_these_fields_existed_still_loads(tmp_path):
    """Files on disk outlive the schema that wrote them, and re-running an
    ETL to read an old catalog is a poor trade for a default."""
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            [
                {
                    "control_id": "X-1",
                    "title": "An invented control",
                    "framework": "Invented Framework",
                    "framework_version": "v1",
                }
            ]
        ),
        encoding="utf-8",
    )

    control = load_controls(path)[0]
    assert control.parameter_values == {}
    assert control.additional_requirements == ""
