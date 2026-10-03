"""The roles a security programme fills, for tools and for teams.

A generated procedure has to name things: the system where an account is
disabled, the console where a scan is reviewed, the team that answers for
the outcome. Today the generator is handed a flat list — `vendors: [Okta,
CrowdStrike]` — and has to work out for itself what each product *is* before
it can decide where the name belongs. It usually gets Okta right and hedges
on the rest, which is how a document ends up saying "the identity provider
(Okta)" in one section and "[Identity Provider]" in the next.

The fix is to name the role rather than infer it. `identity_provider: Okta`
says what Okta is *for*, which is the only fact the substitution needs, and
turns a judgement call into a lookup.

This module is the vocabulary that makes that possible: the set of roles a
security programme actually fills, each with the placeholder a document
writes when nobody has filled it. Two properties matter more than
completeness:

* **The keys are stable.** They appear in config files people maintain, so
  renaming one silently breaks a document set. Add roles; do not rename.
* **The labels read as English.** `[Identity Provider]` is a placeholder a
  reader understands and can fill in; `[identity_provider]` is a leaked
  implementation detail, and `[Square-Bracket Vendor]` — what this wrote
  before — tells a reader nothing about what is missing.

The lists are deliberately not exhaustive. They cover the categories that
appear in NIST 800-53 and HIPAA Security Rule procedures, which is the
ground this project generates over. An organization with a tool that fits
none of them can still pass a plain list, and roles can be added here
without touching anything else.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Role:
    """One thing an organization needs a tool, or a team, to be."""

    key: str
    #: What a document writes when this role is unfilled, without the
    #: brackets. Title Case, because it appears in prose.
    label: str
    description: str
    #: Control families this role typically shows up in. Advisory only —
    #: used to tell the generator which roles are worth mentioning for the
    #: topic at hand rather than listing the whole inventory every time.
    families: tuple[str, ...] = field(default_factory=tuple)

    @property
    def placeholder(self) -> str:
        return f"[{self.label}]"


def _roles(*rows: tuple[str, str, str, tuple[str, ...]]) -> dict[str, Role]:
    return {
        key: Role(key, label, description, families) for key, label, description, families in rows
    }


#: Tool categories, in the order a reviewer would expect to read them:
#: identity first, then endpoint, then detection, then the platform, then the
#: systems of record a procedure hands off to.
VENDOR_ROLES: dict[str, Role] = _roles(
    # Identity and access
    (
        "identity_provider",
        "Identity Provider",
        "Directory and authentication authority — where accounts live and sessions are issued.",
        ("AC", "IA"),
    ),
    ("sso", "Single Sign-On", "Federated sign-on to downstream applications.", ("AC", "IA")),
    (
        "mfa",
        "Multi-Factor Authentication",
        "Second-factor enrolment and enforcement, where it is not the identity provider.",
        ("IA",),
    ),
    (
        "privileged_access",
        "Privileged Access Management",
        "Vaulting, brokering and session recording for administrative access.",
        ("AC", "AU"),
    ),
    (
        "password_manager",
        "Password Manager",
        "Credential storage for people, as distinct from machine secrets.",
        ("IA",),
    ),
    (
        "secrets_manager",
        "Secrets Manager",
        "Machine credentials, API keys and certificates.",
        ("IA", "SC"),
    ),
    # Endpoint and device
    (
        "endpoint_protection",
        "Endpoint Detection and Response",
        "Endpoint telemetry, detection and containment.",
        ("SI", "IR"),
    ),
    (
        "mdm",
        "Device Management",
        "Enrolment, configuration baselines and remote wipe.",
        ("CM", "MP"),
    ),
    (
        "disk_encryption",
        "Disk Encryption",
        "Full-disk encryption and recovery-key escrow.",
        ("SC", "MP"),
    ),
    # Detection and response
    (
        "siem",
        "Log Management Platform",
        "Central log collection, retention and alerting.",
        ("AU", "IR"),
    ),
    (
        "monitoring",
        "Monitoring Platform",
        "Availability and performance monitoring, and its alert routing.",
        ("SI", "CP"),
    ),
    ("on_call", "On-Call Paging", "How a human is woken up for an incident.", ("IR",)),
    # Vulnerability and configuration
    (
        "vulnerability_scanner",
        "Vulnerability Scanner",
        "Authenticated scanning and finding management.",
        ("RA", "SI"),
    ),
    (
        "cspm",
        "Cloud Security Posture Management",
        "Cloud misconfiguration detection against a baseline.",
        ("CM", "RA"),
    ),
    (
        "patch_management",
        "Patch Management",
        "Deploying and reporting on operating system and package updates.",
        ("SI", "CM"),
    ),
    (
        "code_scanning",
        "Code Scanning",
        "Static analysis, dependency and secret scanning in the pipeline.",
        ("SA", "SI"),
    ),
    # Platform
    ("cloud_provider", "Cloud Provider", "Where production runs.", ("CM", "SC", "CP")),
    (
        "code_repository",
        "Source Control",
        "Where code and infrastructure definitions live, and where review happens.",
        ("CM", "SA"),
    ),
    (
        "ci_cd",
        "CI/CD Platform",
        "Build and deploy automation, and its approval gates.",
        ("CM", "SA"),
    ),
    (
        "container_platform",
        "Container Platform",
        "Orchestration and workload isolation.",
        ("SC", "CM"),
    ),
    ("backup", "Backup System", "Backup execution, retention and restore testing.", ("CP",)),
    # Network and data
    ("firewall", "Network Firewall", "Perimeter and segmentation enforcement.", ("SC",)),
    (
        "remote_access",
        "Remote Access",
        "VPN or zero-trust access to internal systems.",
        ("AC", "SC"),
    ),
    (
        "email_security",
        "Email Security",
        "Phishing, malware and impersonation controls on mail.",
        ("SI",),
    ),
    (
        "dlp",
        "Data Loss Prevention",
        "Detection and blocking of sensitive data leaving the estate.",
        ("SC", "MP"),
    ),
    ("key_management", "Key Management Service", "Encryption key lifecycle and custody.", ("SC",)),
    # Systems of record a procedure hands off to
    (
        "ticketing",
        "Ticketing System",
        "Where work, approvals and exceptions are recorded.",
        ("AC", "CM", "IR"),
    ),
    (
        "hris",
        "HR System",
        "The joiner/mover/leaver signal a provisioning process depends on.",
        ("AC", "PS"),
    ),
    (
        "asset_inventory",
        "Asset Inventory",
        "Authoritative list of systems and their owners.",
        ("CM", "PM"),
    ),
    (
        "grc_platform",
        "Compliance Platform",
        "Control monitoring and evidence collection.",
        ("CA", "PM"),
    ),
    ("vendor_risk", "Vendor Risk Platform", "Third-party assessment and monitoring.", ("SR", "SA")),
    (
        "training",
        "Security Awareness Training",
        "Assignment and completion tracking for staff training.",
        ("AT",),
    ),
    (
        "documentation",
        "Documentation Platform",
        "Where policies and procedures are published and read.",
        ("PM",),
    ),
)


#: Team functions. Fewer, and broader, than the tool list on purpose: this
#: project's model is one topic, one accountable team, so the useful
#: granularity is "who answers for this", not "who has a seat in the
#: meeting". A team that owns nothing does not need to be listed.
TEAM_ROLES: dict[str, Role] = _roles(
    (
        "security_engineering",
        "Security Engineering",
        "Builds and runs security controls and tooling.",
        ("SC", "SI", "CM"),
    ),
    (
        "security_operations",
        "Security Operations",
        "Detection, triage and incident response.",
        ("IR", "AU", "SI"),
    ),
    (
        "identity_access",
        "Identity and Access Management",
        "Account lifecycle, entitlements and access review.",
        ("AC", "IA"),
    ),
    (
        "grc",
        "Governance, Risk and Compliance",
        "Policy, risk register, audits and assessor liaison.",
        ("CA", "PM", "RA"),
    ),
    (
        "it_operations",
        "IT Operations",
        "Endpoints, corporate systems, helpdesk and asset handling.",
        ("CM", "MP", "PE"),
    ),
    (
        "platform_engineering",
        "Platform Engineering",
        "Cloud infrastructure, availability and disaster recovery.",
        ("CP", "SC", "CM"),
    ),
    (
        "application_engineering",
        "Application Engineering",
        "Product code, its dependencies and its deployment.",
        ("SA", "SI"),
    ),
    (
        "data_engineering",
        "Data Engineering",
        "Data pipelines, warehousing and retention.",
        ("MP", "SC"),
    ),
    (
        "people_operations",
        "People Operations",
        "Hiring, onboarding, offboarding and screening.",
        ("PS", "AT"),
    ),
    ("legal", "Legal", "Contracts, breach notification and regulatory obligations.", ("PM", "IR")),
    (
        "privacy",
        "Privacy",
        "Data subject rights, minimisation and privacy impact assessments.",
        ("PT", "PM"),
    ),
    (
        "facilities",
        "Facilities",
        "Physical premises, access badging and environmental controls.",
        ("PE",),
    ),
    ("procurement", "Procurement", "Vendor onboarding, contracts and third-party review.", ("SR",)),
    (
        "executive",
        "Executive Leadership",
        "Accountability, risk acceptance and resourcing.",
        ("PM",),
    ),
)


def resolve_role(key: str, roles: dict[str, Role]) -> Role | None:
    """Look a role up forgivingly.

    People write `identity-provider`, `Identity Provider` and
    `identity_provider` for the same thing, and a config file that silently
    ignored two of the three would be worse than one that rejected them.
    """
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in roles:
        return roles[normalized]
    for role in roles.values():
        if role.label.lower() == key.strip().lower():
            return role
    return None


def unknown_roles(keys, roles: dict[str, Role]) -> list[str]:
    """Keys that match no role, so a caller can say so rather than drop them."""
    return sorted(key for key in keys if resolve_role(key, roles) is None)
