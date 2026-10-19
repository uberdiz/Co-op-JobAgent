from __future__ import annotations

import pathlib
from datetime import date
from types import SimpleNamespace

import pytest

from scout import agent, gmail_drafts, main as scout_main
from scout.filters import evaluate
from scout.models import Lead, RejectedLead, ScoutResult
from scout.store import SeenStore
from scout.verify import verify_lead

TODAY = date(2026, 10, 19)
PAGE = ("Acme IT careers. Help Desk Intern - Springfield - paid - high school students welcome. "
        "Apply by emailing jobs@acme.example. " * 6)


def make_lead(**kw) -> Lead:
    base = dict(
        employer="Acme IT", title="Help Desk Intern", url="https://acme.example/jobs/1", kind="advertised_opening",
        location="Springfield, MA", drive_minutes_from_home=10, pay_text="$18/hr", paid="paid", posted_date="2026-10-10",
        deadline=None, work_mode="onsite", requires_college_enrollment=False, requires_age_18_plus=False,
        requires_degree_or_clearance=False, high_school_friendly_evidence="High school students welcome",
        contact_email="Jobs@Acme.example", apply_method="email", why_fit="They repair PCs at the school's Repair Cafe.",
        evidence_quote="Help Desk Intern - Springfield - paid - high school students welcome")
    base.update(kw)
    return Lead(**base)


CAND = {"name": "Alex Student", "home": "Springfield, MA", "email": "me@example.com", "school_short": "Example Tech",
        "draft_line": "I build small Python projects (portfolio below) and plan to work full-time after I graduate in June 2027.",
        "availability_line": "I can work weekends and holidays.", "coop_blurb": "Example Tech's co-op is paid.",
        "signature": "Alex Student\nSpringfield, MA"}


# ---------------------------------------------------------------- filters
def test_good_lead_passes_and_email_is_normalized():
    v = evaluate(make_lead(), today=TODAY, seen_urls=set())
    assert v.ok and v.lead.contact_email == "jobs@acme.example"


@pytest.mark.parametrize("kw,fragment", [
    (dict(paid="unpaid"), "unpaid"),
    (dict(requires_college_enrollment=True), "college"),
    (dict(requires_age_18_plus=True), "18+"),
    (dict(requires_degree_or_clearance=True), "degree"),
    (dict(work_mode="remote"), "remote"),
    (dict(drive_minutes_from_home=45), "drive"),
    (dict(posted_date="2026-06-01"), "days old"),
    (dict(deadline="2026-10-01"), "deadline"),
    (dict(url="ftp://x"), "url"),
    (dict(evidence_quote=" "), "evidence"),
])
def test_rules_reject(kw, fragment):
    v = evaluate(make_lead(**kw), today=TODAY, seen_urls=set())
    assert not v.ok and any(fragment in r for r in v.reasons)


def test_duplicate_url_rejected():
    assert "already reported" in evaluate(make_lead(), today=TODAY, seen_urls={"https://acme.example/jobs/1"}).reasons


def test_college_wording_in_evidence_rejected_without_hs_evidence():
    lead = make_lead(evidence_quote="Must be currently enrolled in a degree program", high_school_friendly_evidence=None)
    assert not evaluate(lead, today=TODAY, seen_urls=set()).ok


def test_bad_email_is_dropped_not_guessed():
    v = evaluate(make_lead(contact_email="not-an-email"), today=TODAY, seen_urls=set())
    assert v.ok and v.lead.contact_email is None


# ---------------------------------------------------------------- verify (no LLM)
def test_verify_accepts_quote_on_page_and_keeps_email_on_page():
    v = verify_lead(make_lead(contact_email="jobs@acme.example"), fetcher=lambda u: PAGE)
    assert v.status == "verified" and v.contact_email == "jobs@acme.example"


def test_verify_drops_email_not_on_page():
    v = verify_lead(make_lead(contact_email="ceo@acme.example"), fetcher=lambda u: PAGE)
    assert v.status == "verified" and v.contact_email is None


def test_verify_rejects_invented_quote():
    v = verify_lead(make_lead(evidence_quote="Senior Quantum Engineer needed immediately in Boston"), fetcher=lambda u: PAGE)
    assert v.status == "failed"


def test_verify_unreadable_page_is_flagged_not_rejected():
    assert verify_lead(make_lead(), fetcher=lambda u: None).status == "unverifiable"
    assert verify_lead(make_lead(), fetcher=lambda u: "short").status == "unverifiable"


# ---------------------------------------------------------------- store
def test_store_roundtrip(tmp_path):
    p = str(tmp_path / "seen.json")
    s = SeenStore(p)
    s.add(["https://a.example/x/", "https://b.example"])
    s.save()
    assert SeenStore(p).urls == {"https://a.example/x", "https://b.example"}


# ---------------------------------------------------------------- gmail safety
def test_gmail_module_never_sends():
    src = pathlib.Path(gmail_drafts.__file__).read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    assert ".send(" not in code and "messages().send" not in code and "drafts().send" not in code


def test_draft_needs_verified_email_and_is_short():
    with pytest.raises(ValueError):
        gmail_drafts.build_draft_message(make_lead(contact_email=None), CAND)
    body = gmail_drafts.build_draft_message(make_lead(contact_email="a@b.co"), CAND).get_content()
    assert "weekend" in body and "June 2027" in body and "Example Tech" in body and len(body.split()) < 150
    assert "drive myself" not in body.lower()


# ---------------------------------------------------------------- Gemini agent (mocked client)
class FakeModels:
    def __init__(self, texts, parsed=None, fail_first=0):
        self.texts, self.parsed, self.calls, self.fail_first = list(texts), parsed, [], fail_first

    def generate_content(self, *, model, contents, config):
        self.calls.append(SimpleNamespace(model=model, contents=contents, config=config))
        if self.fail_first > 0:
            self.fail_first -= 1
            from google.genai import errors
            raise errors.ClientError(429, {"error": {"message": "rate limited", "status": "RESOURCE_EXHAUSTED"}})
        if getattr(config, "response_schema", None) is not None:
            return SimpleNamespace(text=self.parsed.model_dump_json(), parsed=self.parsed)
        return SimpleNamespace(text=self.texts.pop(0), parsed=None)


class FakeClient:
    def __init__(self, models): self.models = models


PROFILE = {"candidate": {"name": "Alex Student", "school": "Example Tech", "graduation": "June 2027", "facts": ["Does X"]},
           "search": {"home_base": "Springfield, MA", "max_drive_minutes": 30, "rules": ["Must be paid"],
                      "interests_in_priority_order": ["Software"], "queries": ["help desk Springfield", "intern Springfield"]}}
WATCH = [{"name": f"E{i}", "website": f"https://e{i}.example", "location": "X"} for i in range(5)]


def test_research_runs_one_search_per_query_and_watchlist_chunk_with_search_tools():
    tasks = agent.build_tasks(PROFILE, WATCH, TODAY, chunk=4)
    assert len(tasks) == 2 + 2                                  # 2 queries + 2 watchlist chunks (4 + 1)
    fm = FakeModels([f"report {i}" for i in range(len(tasks))])
    out = agent.research(FakeClient(fm), model="m", system="s", tasks=tasks, pause_seconds=0)
    assert len(fm.calls) == 4 and "report 0" in out and "report 3" in out
    tools = fm.calls[0].config.tools
    assert any(t.google_search for t in tools) and any(t.url_context for t in tools)


def test_research_retries_on_rate_limit(monkeypatch):
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    fm = FakeModels(["ok"], fail_first=2)
    out = agent.research(FakeClient(fm), model="m", system="s", tasks=["t"], pause_seconds=0)
    assert "ok" in out and len(fm.calls) == 3


def test_extract_uses_structured_output():
    res = ScoutResult(leads=[make_lead()], rejected=[], notes="n")
    fm = FakeModels([], parsed=res)
    got = agent.extract(FakeClient(fm), model="m", report="report")
    assert got.leads[0].employer == "Acme IT" and fm.calls[0].config.response_mime_type == "application/json"


# ---------------------------------------------------------------- end to end
def _setup_env(monkeypatch, tmp_path, dry):
    monkeypatch.setenv("PROFILE_YAML", pathlib.Path(__file__).with_name("profile_fixture.yaml").read_text(encoding="utf-8"))
    monkeypatch.setenv("SEEN_PATH", str(tmp_path / "seen.json"))
    monkeypatch.setenv("DIGEST_DIR", str(tmp_path / "digests"))
    monkeypatch.setenv("GEMINI_PAUSE_SECONDS", "0")
    monkeypatch.setenv("PRINT_DIGEST", "1")
    monkeypatch.delenv("GMAIL_OAUTH_JSON", raising=False)
    if dry:
        monkeypatch.setenv("DRY_RUN", "1")
    else:
        monkeypatch.delenv("DRY_RUN", raising=False)


def _fake_gemini(monkeypatch, result):
    def make():
        return FakeClient(FakeModels(["report"] * 50, parsed=result))
    monkeypatch.setattr(scout_main.agent, "make_client", make)


def test_end_to_end_dry_run(tmp_path, monkeypatch, capsys):
    good = make_lead()
    unpaid = make_lead(url="https://acme.example/jobs/2", paid="unpaid")
    invented = make_lead(url="https://acme.example/jobs/3", evidence_quote="A totally different job posting nobody wrote")
    result = ScoutResult(leads=[good, unpaid, invented],
                         rejected=[RejectedLead(employer="BigCo", title="SWE co-op", url="https://big.example", reason="college only")],
                         notes="one site blocked")
    _setup_env(monkeypatch, tmp_path, dry=True)
    _fake_gemini(monkeypatch, result)
    assert scout_main.run(fetcher=lambda u: PAGE) == 0
    digest = next((tmp_path / "digests").glob("digest-*.md")).read_text(encoding="utf-8")
    assert "1 new eligible lead" in digest and "Help Desk Intern" in digest
    assert "unpaid" in digest and "college only" in digest and "possible invented posting" in digest
    assert "Nothing was sent" in digest
    assert not (tmp_path / "seen.json").exists()                  # dry run does not record leads
    assert "Job scout digest" in capsys.readouterr().out          # PRINT_DIGEST=1


def test_digest_not_printed_by_default(tmp_path, monkeypatch, capsys):
    _setup_env(monkeypatch, tmp_path, dry=True)
    monkeypatch.delenv("PRINT_DIGEST")
    _fake_gemini(monkeypatch, ScoutResult(leads=[make_lead()], rejected=[], notes=""))
    scout_main.run(fetcher=lambda u: PAGE)
    assert "Help Desk Intern" not in capsys.readouterr().out       # public-repo logs must not leak leads


def test_non_dry_run_creates_drafts_only_for_verified_leads(tmp_path, monkeypatch):
    calls = []

    class FakeService:
        def users(self): return self
        def drafts(self): return SimpleNamespace(create=lambda **kw: (calls.append("draft") or SimpleNamespace(execute=lambda: {"id": "d1"})))
        def messages(self): return SimpleNamespace(insert=lambda **kw: (calls.append("insert") or SimpleNamespace(execute=lambda: {"id": "m1"})))

    _setup_env(monkeypatch, tmp_path, dry=False)
    _fake_gemini(monkeypatch, ScoutResult(leads=[make_lead(contact_email="jobs@acme.example")], rejected=[], notes=""))
    monkeypatch.setattr(scout_main.gmail_drafts, "service_from_env", lambda: FakeService())
    assert scout_main.run(fetcher=lambda u: PAGE) == 0
    assert calls == ["draft", "insert"]
    assert SeenStore(str(tmp_path / "seen.json")).urls == {"https://acme.example/jobs/1"}


def test_unverifiable_page_gets_no_draft(tmp_path, monkeypatch):
    calls = []
    _setup_env(monkeypatch, tmp_path, dry=False)
    _fake_gemini(monkeypatch, ScoutResult(leads=[make_lead(contact_email="jobs@acme.example")], rejected=[], notes=""))
    monkeypatch.setattr(scout_main.gmail_drafts, "service_from_env", lambda: SimpleNamespace(
        users=lambda: SimpleNamespace(drafts=lambda: calls.append("draft"), messages=lambda: SimpleNamespace(
            insert=lambda **kw: SimpleNamespace(execute=lambda: {"id": "m"})))))
    scout_main.run(fetcher=lambda u: None)
    assert "draft" not in calls
