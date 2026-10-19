"""Entry point: python -m scout.main

Environment variables
  GEMINI_API_KEY      required (free key from https://aistudio.google.com/apikey)
  GEMINI_MODEL        default gemini-2.5-flash (Google Search grounding is free on this model)
  PROFILE_YAML        your private profile as YAML text (GitHub secret). If unset, config/profile.yaml is used.
  SEEN_PATH           where reported URLs are remembered; local path or gs://bucket/seen.json (default ./data/seen.json)
  DIGEST_DIR          where digests are written; local dir or gs://bucket/digests (default ./data/digests)
  GMAIL_OAUTH_JSON    optional authorized-user JSON; enables Gmail drafts + the digest in your inbox
  PRINT_DIGEST=1      also print the digest to the log (leave OFF in a public repo - logs are public)
  DRY_RUN=1           don't write the seen list and don't touch Gmail
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import yaml

from . import agent, gmail_drafts
from .filters import evaluate
from .models import Lead, ScoutResult
from .store import SeenStore, write_text
from .verify import fetch_text, verify_lead

log = logging.getLogger("scout")
ROOT = Path(__file__).resolve().parent.parent


def load_config() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    raw = os.environ.get("PROFILE_YAML")
    if raw:
        profile = yaml.safe_load(raw)
    else:
        path = ROOT / "config" / "profile.yaml"
        if not path.exists():
            raise SystemExit("No profile found. Set the PROFILE_YAML secret, or copy config/profile.example.yaml to "
                             "config/profile.yaml and fill it in (that file is git-ignored).")
        profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    watch = yaml.safe_load((ROOT / "config" / "employers.yaml").read_text(encoding="utf-8"))["watchlist"]
    return profile, watch


def render_digest(today, accepted: List[Tuple[Lead, str]], rejected: List[Tuple[Lead, List[str]]],
                  model_rejected: List[Any], notes: str, draft_urls: set, home: str) -> str:
    out = [f"# Job scout digest - {today.isoformat()}", ""]
    out.append(f"**{len(accepted)} new eligible lead(s)**, {len(rejected) + len(model_rejected)} rejected.")
    out.append("Nothing was sent or submitted. Drafts (if any) are in Gmail > Drafts for you to review.\n")
    out.append("## New leads")
    if not accepted:
        out.append("_None this time._")
    for i, (l, vnote) in enumerate(accepted, 1):
        out += [f"### {i}. {l.title} - {l.employer}",
                f"- Link: {l.url}", f"- Type: {l.kind.replace('_', ' ')}",
                f"- Location: {l.location} (~{l.drive_minutes_from_home or '?'} min from {home}), {l.work_mode}",
                f"- Pay: {l.pay_text or 'not stated'} ({l.paid})",
                f"- Posted: {l.posted_date or 'not shown'} | Deadline: {l.deadline or 'none shown'}",
                f"- How to apply: {l.apply_method}",
                f"- Contact email (checked on the page): {l.contact_email or 'none'}",
                f"- Why it fits: {l.why_fit}",
                f"- Evidence: \"{l.evidence_quote}\"",
                f"- Check: {vnote}"]
        out.append("- **Gmail draft created** - review and send it yourself." if l.url in draft_urls
                   else "- Action for you: open the link and apply yourself; the agent never submits applications.")
        out.append("")
    out.append("## Rejected by the hard rules")
    for l, why in rejected:
        out.append(f"- {l.title} - {l.employer} ({l.url}): {'; '.join(why)}")
    for r in model_rejected:
        out.append(f"- {r.title} - {r.employer} ({r.url}): {r.reason}")
    if not rejected and not model_rejected:
        out.append("_None._")
    out += ["", "## Notes from the research pass", notes or "_None._", ""]
    return "\n".join(out)


def run(fetcher=fetch_text) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    dry = os.environ.get("DRY_RUN") == "1"
    model = os.environ.get("GEMINI_MODEL", agent.DEFAULT_MODEL)
    profile, watch = load_config()
    cand, search = profile["candidate"], profile["search"]
    today = datetime.now(ZoneInfo(search.get("timezone", "America/New_York"))).date()

    store = SeenStore(os.environ.get("SEEN_PATH", str(ROOT / "data" / "seen.json")))
    client = agent.make_client()

    report = agent.research(client, model=model, system=agent.build_system_prompt(profile, watch),
                            tasks=agent.build_tasks(profile, watch, today))
    result: ScoutResult = agent.extract(client, model=model, report=report)

    accepted: List[Tuple[Lead, str]] = []
    rejected: List[Tuple[Lead, List[str]]] = []
    seen_this_run = set()
    for lead in result.leads:
        v = evaluate(lead, today=today, seen_urls=store.urls | seen_this_run, max_drive_minutes=search["max_drive_minutes"],
                     max_posting_age_days=search["max_posting_age_days"])
        if not v.ok:
            rejected.append((lead, v.reasons))
            continue
        check = verify_lead(v.lead, fetcher)
        if check.status == "failed":
            rejected.append((lead, [check.note]))
            continue
        clean = v.lead.model_copy(update={"contact_email": check.contact_email})
        accepted.append((clean, f"{check.status} - {check.note}"))
        seen_this_run.add(clean.url.rstrip("/"))

    draft_urls: set = set()
    service = None if dry else gmail_drafts.service_from_env()
    if service:
        eligible = [l for l, note in accepted if l.contact_email and note.startswith("verified")]
        for lead in eligible[: search["max_drafts_per_run"]]:
            try:
                gmail_drafts.create_draft(service, gmail_drafts.build_draft_message(lead, cand))
                draft_urls.add(lead.url)
                log.info("created Gmail draft for %s", lead.employer)
            except Exception:
                log.exception("draft failed for %s", lead.employer)

    digest = render_digest(today, accepted, rejected, result.rejected, result.notes, draft_urls, search["home_base"])
    out_dir = os.environ.get("DIGEST_DIR", str(ROOT / "data" / "digests"))
    write_text(f"{out_dir.rstrip('/')}/digest-{today.isoformat()}.md", digest)
    if os.environ.get("PRINT_DIGEST") == "1":
        print(digest)
    else:
        log.info("digest written (%d leads); not printed because PRINT_DIGEST is not 1", len(accepted))

    if service:
        try:
            gmail_drafts.insert_digest(service, gmail_drafts.build_digest_message(
                f"Job scout digest {today.isoformat()} ({len(accepted)} new)", digest, cand["email"]))
        except Exception:
            log.exception("could not insert digest into Gmail")

    if not dry:
        store.add(l.url for l, _ in accepted)
        store.save()
    return 0


if __name__ == "__main__":
    sys.exit(run())
