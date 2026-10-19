"""Data models for the job scout. These are also the JSON schema the LLM must fill in.

Every field is required (nullable ones are Optional without a default) because Gemini's structured-output
schema handling is most reliable that way.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Lead(BaseModel):
    """One real opportunity found on the web."""

    employer: str
    title: str
    url: str = Field(description="Direct URL of the posting or program page (official site or a reputable job board).")
    kind: Literal["advertised_opening", "high_school_program", "speculative_inquiry"]
    location: str
    drive_minutes_from_home: Optional[int] = Field(description="Estimated drive time from the candidate's home; null if unsure.")
    pay_text: Optional[str] = Field(description="Pay exactly as written in the posting; null if not stated.")
    paid: Literal["paid", "unpaid", "unknown"]
    posted_date: Optional[str] = Field(description="ISO date (YYYY-MM-DD) the posting went live; null if not shown.")
    deadline: Optional[str] = Field(description="ISO date (YYYY-MM-DD) of the application deadline; null if none.")
    work_mode: Literal["onsite", "hybrid", "remote", "unknown"]
    requires_college_enrollment: bool = Field(
        description="True if the posting requires being enrolled in college or a degree program.")
    requires_age_18_plus: bool
    requires_degree_or_clearance: bool = Field(
        description="True if a completed degree, a certification beyond entry level, or a security clearance is required.")
    high_school_friendly_evidence: Optional[str] = Field(
        description="Short verbatim quote showing high-school students may apply; null if none.")
    contact_email: Optional[str] = Field(
        description="An email address that appears verbatim on the posting or the employer's own site. Never guess; null if none.")
    apply_method: str = Field(description="How to apply, e.g. 'Workday portal', 'email info@x.com', 'contact form'.")
    why_fit: str = Field(description="One short sentence (max 30 words) on why this fits the candidate, using only the listed facts.")
    evidence_quote: str = Field(description="Short verbatim quote copied from the page proving the posting is real and what the role is.")


class RejectedLead(BaseModel):
    employer: str
    title: str
    url: str
    reason: str


class ScoutResult(BaseModel):
    leads: List[Lead]
    rejected: List[RejectedLead]
    notes: str = Field(description="Anything the human should know: blocked sites, ambiguous postings, gaps.")
