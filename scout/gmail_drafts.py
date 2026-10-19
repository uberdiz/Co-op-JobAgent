"""Gmail helpers. SAFETY: this module only CREATES DRAFTS and INSERTS a digest into the user's own mailbox.
It never calls users.messages.send / users.drafts.send - a test enforces that. A human presses Send.
"""
from __future__ import annotations

import base64
import json
import os
from email.message import EmailMessage
from typing import Any, Dict, Optional

from .models import Lead

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",  # create drafts
    "https://www.googleapis.com/auth/gmail.insert",   # insert the digest message into the user's own inbox
]


def draft_body(lead: Lead, cand: Dict[str, Any]) -> str:
    """Short, template-based draft. Wording comes from the profile; only `lead.why_fit` is model-written (one sentence)."""
    town = cand["home"].split(",")[0]
    who = f"I'm {cand['name']}, a {town} resident and senior in the IT program at {cand['school_short']}."
    if lead.kind == "advertised_opening":
        opener = f"I'm interested in the {lead.title} opening at {lead.employer}. {who}"
    elif lead.kind == "high_school_program":
        opener = f"I'm interested in {lead.title} at {lead.employer}. {who}"
    else:
        opener = f"{who} I'm looking for a paid co-op, and {lead.employer}'s work looks like a great place to learn."
    return (
        f"Hello,\n\n{opener} {lead.why_fit.strip()}\n\n"
        f"{cand['draft_line'].strip()} {cand['availability_line'].strip()}\n\n"
        f"{cand['coop_blurb'].strip()} Would you be open to a short conversation?\n\n"
        f"Thank you,\n\n{cand['signature'].strip()}\n"
    )


def draft_subject(lead: Lead, cand: Dict[str, Any]) -> str:
    if lead.kind == "advertised_opening":
        return f"{cand['school_short']} senior interested in {lead.title}"
    return f"{cand['school_short']} IT student seeking a co-op - {lead.employer}"


def _raw(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def build_draft_message(lead: Lead, cand: Dict[str, Any]) -> EmailMessage:
    if not lead.contact_email:
        raise ValueError("lead has no verified contact email")
    m = EmailMessage()
    m["To"] = lead.contact_email
    m["From"] = cand["email"]
    m["Subject"] = draft_subject(lead, cand)
    m.set_content(draft_body(lead, cand))
    return m


def build_digest_message(subject: str, markdown: str, to_self: str) -> EmailMessage:
    m = EmailMessage()
    m["To"] = to_self
    m["From"] = to_self
    m["Subject"] = subject
    m.set_content(markdown)
    return m


def service_from_env() -> Optional[Any]:
    """Build a Gmail API client from GMAIL_OAUTH_JSON (authorized-user JSON). None if not configured."""
    raw = os.environ.get("GMAIL_OAUTH_JSON")
    if not raw:
        return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def create_draft(service: Any, msg: EmailMessage) -> str:
    out = service.users().drafts().create(userId="me", body={"message": {"raw": _raw(msg)}}).execute()
    return out["id"]


def insert_digest(service: Any, msg: EmailMessage) -> str:
    out = service.users().messages().insert(userId="me", body={"raw": _raw(msg), "labelIds": ["INBOX", "UNREAD"]}).execute()
    return out["id"]
