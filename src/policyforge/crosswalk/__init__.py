"""An organization's own decisions about how one framework maps onto 800-53.

The published crosswalks this project loads are pairs with no reasoning:
NIST's HIPAA-to-800-53 export says `164.308(a)(5)(ii)(B)` goes with `SI-3`
and nothing about how, or how much. `overlay` records what an organization
decided about each pair — accepted, rejected, or added — with the evidence
and who decided; `propose` is where a model suggests those decisions for a
person to review.
"""
