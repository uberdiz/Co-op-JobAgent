"""Deterministic eligibility checks, applied AFTER the model's extraction.

The model can be wrong or optimistic, so hard rules live in code, not in the prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Set

from .models import Lead

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
COLLEGE_WORDS = re.compile(
    r"(currently enrolled|enrolled in (a|an)? ?(bachelor|master|degree|college|university)|pursuing (a|an)? ?(bachelor|master|degree)|"
    r"undergraduate|graduate student|rising (junior|senior)|degree program)", re.I)


@dataclass
class Verdict:
    ok: bool
    reasons: List[str] = field(default_factory=list)
    lead: Optional[Lead] = None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def evaluate(lead: Lead, *, today: date, seen_urls: Set[str], max_drive_minutes: int = 30,
             max_posting_age_days: int = 90) -> Verdict:
    """Return a Verdict. ``lead`` in the verdict has a sanitized contact_email."""
    reasons: List[str] = []

    if not lead.url.lower().startswith(("http://", "https://")):
        reasons.append("url is not http(s)")
    if lead.url.rstrip("/") in seen_urls:
        reasons.append("already reported")
    if lead.paid == "unpaid":
        reasons.append("unpaid (a school co-op requires minimum wage or higher)")
    if lead.requires_college_enrollment:
        reasons.append("requires college enrollment")
    if lead.requires_age_18_plus:
        reasons.append("requires age 18+")
    if lead.requires_degree_or_clearance:
        reasons.append("requires a degree or security clearance")
    if COLLEGE_WORDS.search(lead.evidence_quote or "") and not lead.high_school_friendly_evidence:
        reasons.append("evidence quote suggests college-level requirement")
    if lead.work_mode == "remote":
        reasons.append("fully remote (co-op needs on-site supervision)")
    if lead.drive_minutes_from_home is not None and lead.drive_minutes_from_home > max_drive_minutes:
        reasons.append(f"drive {lead.drive_minutes_from_home} min > {max_drive_minutes} min limit")
    if not (lead.evidence_quote or "").strip():
        reasons.append("no evidence quote")

    posted = _parse_date(lead.posted_date)
    if posted and (today - posted).days > max_posting_age_days:
        reasons.append(f"posting is {(today - posted).days} days old")
    deadline = _parse_date(lead.deadline)
    if deadline and deadline < today:
        reasons.append("deadline has passed")

    email = (lead.contact_email or "").strip()
    clean = lead.model_copy()
    if email and not EMAIL_RE.match(email):
        clean.contact_email = None
    elif email:
        clean.contact_email = email.lower()

    return Verdict(ok=not reasons, reasons=reasons, lead=clean)
