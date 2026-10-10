"""Scheme data quality checks; never turn an internal identifier into a title."""

import re
from collections.abc import Mapping


IDENTIFIER = re.compile(
    r"(?<!\w)(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{8,32})(?!\w)",
    re.IGNORECASE,
)
GENERIC_NAMES = {"", "home", "homepage", "myscheme", "my scheme", "scheme details",
                 "untitled source", "unnamed scheme", "government schemes", "healthcare schemes"}
PRESENTATION_FIELDS = ("benefits", "eligibility", "beneficiary_categories", "required_documents")
TEXT_PRESENTATION_FIELDS = (
    "description",
    "government_authority",
    "geographic_coverage",
    "income_conditions",
    "application_process",
    "helpline",
)


def scheme_name_issue(name) -> str | None:
    if not isinstance(name, str) or name.strip().casefold() in GENERIC_NAMES:
        return "A reliable scheme name is unavailable. Check the official source before approval."
    if IDENTIFIER.search(name):
        return "The scheme name appears to contain an internal identifier. Source verification is required."
    return None


def sanitize_scheme_candidate(candidate: Mapping) -> dict:
    """Remove identifier-like values before source data is persisted or rendered.

    A crawler can return a generated result key in a list item (for example,
    ``finder-families-c8090b25``).  Those values are not useful scheme facts and
    must not become user-facing content.  The original scheme name is kept
    untouched so a bad title can be quarantined and reviewed rather than being
    silently rewritten into a name we did not find in the source.
    """
    result = dict(candidate)
    for field in PRESENTATION_FIELDS:
        values = result.get(field) or []
        if not isinstance(values, list):
            result[field] = []
            continue
        result[field] = [
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip() and not IDENTIFIER.search(value)
        ]
    for field in TEXT_PRESENTATION_FIELDS:
        value = result.get(field)
        if isinstance(value, str) and IDENTIFIER.search(value):
            # Do not rewrite a source-backed sentence.  Omit the polluted
            # field and let the UI explain that it was not published reliably.
            result[field] = None
    return result


def scheme_quality_issue(record) -> str | None:
    value = record.get if isinstance(record, Mapping) else lambda field: getattr(record, field, None)
    issue = scheme_name_issue(value("name"))
    if issue:
        return issue
    # These are presentation fields, not URLs or database relationship keys.
    for field in ("benefits", "eligibility", "beneficiary_categories", "required_documents"):
        if any(IDENTIFIER.search(str(part)) for part in (value(field) or [])):
            return "Scheme information contains internal identifiers. Source verification is required."
    for field in TEXT_PRESENTATION_FIELDS:
        if IDENTIFIER.search(str(value(field) or "")):
            return "Scheme information contains internal identifiers. Source verification is required."
    return None


def quarantine_unreliable_schemes(db) -> int:
    """Reversible repair for legacy test pollution; preserve original text and IDs."""
    from sqlalchemy import select
    from .models import AuditLog, HealthcareScheme

    changed = 0
    for record in db.scalars(select(HealthcareScheme)).all():
        issue = scheme_quality_issue(record)
        if issue and record.verification_status not in {"REJECTED", "DUPLICATE", "REVIEW_REQUIRED"}:
            previous = record.verification_status
            record.verification_status = "REVIEW_REQUIRED"
            record.active_status = "REVIEW_REQUIRED"
            db.add(AuditLog(action="QUARANTINE_UNRELIABLE_SCHEME", resource="healthcare_scheme",
                            detail={"scheme_id": record.id, "previous_status": previous, "reason": issue}))
            changed += 1
    db.commit()
    return changed


if __name__ == "__main__":
    from .db import SessionLocal
    with SessionLocal() as session:
        print(f"Flagged {quarantine_unreliable_schemes(session)} scheme record(s) for review.")
