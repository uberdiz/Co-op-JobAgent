"""Re-check the model's claims against the real page, in plain Python (no LLM).

LLMs - especially small free-tier ones - can invent postings, quotes and emails. For each lead we download the URL
ourselves and require the evidence quote to be on the page. Pages that cannot be read without a browser
(JavaScript-only sites such as Workday) are marked 'unverifiable' and shown to the human with a warning instead.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable, Optional

import requests

from .models import Lead

UA = "Mozilla/5.0 (compatible; coop-job-scout/1.0; personal job search)"
_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def fetch_text(url: str, timeout: int = 20) -> Optional[str]:
    """Plain-text content of a page, or None if it can't be fetched."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code >= 400 or (ctype and "html" not in ctype and "text" not in ctype):
            return None
        return html.unescape(_TAG.sub(" ", r.text))
    except requests.RequestException:
        return None


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9@. ]+", " ", s.lower())).strip()


@dataclass
class Verification:
    status: str          # 'verified' | 'unverifiable' | 'failed'
    note: str
    contact_email: Optional[str]


def verify_lead(lead: Lead, fetcher: Callable[[str], Optional[str]] = fetch_text, min_overlap: float = 0.8) -> Verification:
    text = fetcher(lead.url)
    if not text or len(text) < 200:
        return Verification("unverifiable", "page could not be read automatically - open the link and check it yourself", lead.contact_email)

    page = normalize(text)
    quote = normalize(lead.evidence_quote)
    if quote and quote in page:
        found = True
    else:
        words = [w for w in quote.split() if len(w) > 2]
        page_words = set(page.split())
        found = bool(words) and sum(w in page_words for w in words) / len(words) >= min_overlap
    if not found:
        return Verification("failed", "evidence quote not found on the page (possible invented posting)", lead.contact_email)

    email = lead.contact_email
    note = "quote found on the page"
    if email and email.lower() not in text.lower():
        email, note = None, "quote found; contact email was NOT on the page, so it was dropped"
    return Verification("verified", note, email)
