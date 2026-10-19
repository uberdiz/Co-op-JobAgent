# Co-op Job Agent

A free, scheduled job scout for a high-school student looking for a **paid IT / software / AI co-op**. Twice a week it
searches the web with **Google Gemini** (Google Search grounding + page reading), keeps only opportunities the student can
actually take, double-checks every claim against the real page, and sends a digest plus **Gmail drafts** to review.

> **It finds and prepares. It never applies.** The code can't send email or submit forms: it only creates Gmail *drafts*
> and drops a digest into your own inbox (a test fails if the Gmail module ever calls `send`). You press Send.
> No Claude / Anthropic API and no paid service is required: Gemini's free tier covers it.

## How it works

```
GitHub Actions (Mon + Thu, or "Run workflow")
   |
   |-- Gemini 2.5 Flash + Google Search + URL context  ->  research report (one call per search query / employer group)
   |-- Gemini structured output                         ->  Lead objects (pydantic schema)
   |-- scout/filters.py   hard rules in code: paid, in person, within N minutes of home, no college / 18+ / degree /
   |                      clearance, posting <= 90 days old, deadline not passed, not already reported
   |-- scout/verify.py    downloads each posting page and requires the quoted evidence to be on it (catches invented
   |                      postings); drops contact emails that are not on the page
   '-- Gmail API          up to 3 DRAFTS (verified leads with a contact email) + the digest inserted into your inbox
```

| File | Purpose |
|---|---|
| `config/profile.example.yaml` | Template for your profile: facts, rules, queries, draft wording. **Copy and fill in privately.** |
| `config/employers.yaml` | Watchlist of employers whose careers pages are checked each run |
| `scout/agent.py` | Gemini prompts, research calls (rate-limit retries), structured extraction |
| `scout/filters.py` | Hard eligibility rules (the model's judgment is not trusted for these) |
| `scout/verify.py` | Plain-Python check of quotes and emails against the real page |
| `scout/gmail_drafts.py` | Draft + digest creation (never sends) |
| `.github/workflows/scout.yml` | Scheduled run on GitHub Actions |
| `scripts/deploy.sh` | Optional Google Cloud Run deployment instead of GitHub Actions |
| `tests/` | 28 tests with Gemini and Gmail mocked |

## Privacy: this repo is public, your profile is not

Your name, phone number, email and facts live **only** in a GitHub secret (`PROFILE_YAML`) or in a git-ignored
`config/profile.yaml`. Workflow logs and summaries of a public repo are public, so the digest is **not** printed there; you
read it in Gmail. If you make the repo private, set the repo variable `PRIVATE_REPO=true` to also get the digest in each run's Summary.

## Setup on GitHub (about 15 minutes)

1. **Gemini key (free):** create one at https://aistudio.google.com/apikey.
2. **Profile:** copy `config/profile.example.yaml`, fill it in (true facts only: the agent may claim nothing else), and paste the whole
   YAML into a secret named `PROFILE_YAML`.
3. **Secrets:** repo > Settings > Secrets and variables > Actions > **New repository secret**:
   - `GEMINI_API_KEY` (required)
   - `PROFILE_YAML` (required)
   - `GMAIL_OAUTH_JSON` (optional, for Gmail drafts + digest; see below)
4. **Test run:** Actions tab > **Job scout** > **Run workflow** with *Dry run* ticked. The workflow's log shows only counts.
   Without Gmail set up, use a local dry run (below) to read the digest, or make the repo private and set `PRIVATE_REPO=true`.
5. The schedule (`30 11 * * 1,4`, UTC) then runs every Monday and Thursday morning Eastern.

### Gmail drafts + digest in your inbox (optional)

1. Google Cloud Console > create a project > APIs & Services > enable **Gmail API**.
2. OAuth consent screen: user type *External*, add yourself as a test user, then **Publish app** ("In production"). In *Testing* mode Google
   expires the token after 7 days. Google will show an "unverified app" warning for your own personal app; that is normal.
3. Credentials > **Create OAuth client ID > Desktop app**, download as `client_secret.json`.
4. On your own computer: `pip install -r requirements.txt && python scripts/gmail_auth.py client_secret.json` (opens a browser; approve
   "compose drafts" and "insert mail"). Paste the contents of the generated `gmail-oauth.json` into the `GMAIL_OAUTH_JSON` secret, then delete the file.

## Run it locally (dry run)

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config/profile.example.yaml config/profile.yaml   # fill it in (git-ignored)
export GEMINI_API_KEY=...                            # PowerShell: $env:GEMINI_API_KEY="..."
DRY_RUN=1 PRINT_DIGEST=1 python -m scout.main
```

## Free-tier facts and limits (checked Oct 2026 - verify on Google's pricing page)

- Gemini 2.5 Flash / Flash-Lite: free tier, and **Grounding with Google Search is free up to 500 requests/day** on those two models
  (newer Gemini 3.x models charge for grounding). URL context is free. Each run makes about 10 calls. Override with `GEMINI_MODEL`.
- On the free tier Google may use prompts and responses to improve its products. The prompts contain your profile *facts*, so leave out
  anything you would not want shared.
- Free-tier rate limits are low; the code pauses between calls and retries on 429 errors.
- **A small free model makes mistakes.** That is why `verify.py` re-checks the quote on the real page, and leads on pages that cannot be
  read automatically (JavaScript-only job sites) are flagged **unverified** and never get a draft. Always open the link and read the posting
  before contacting anyone.
- **Not yet tested against the live Gemini API.** The logic is unit-tested with mocks; the first real run may need a small fix. Run the dry run first.

## Customizing

- Distance, age of postings, queries and rules: `search:` in your profile. More employers to watch: `config/employers.yaml`.
- Draft wording: `draft_line`, `availability_line`, `coop_blurb` and `signature` in your profile (short and true).
- The agent can only claim what is listed under `facts`. Update it when your situation changes.

## Optional: Google Cloud Run instead of GitHub Actions

`scripts/deploy.sh` deploys the same code as a Cloud Run Job with Cloud Scheduler. Store your Gemini key as the `gemini-api-key` secret and
your profile as the `coop-profile` secret (commands are in the script header), then run `PROJECT_ID=... ./scripts/deploy.sh` from Cloud Shell.
