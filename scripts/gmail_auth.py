"""One-time, run on YOUR computer: authorize Gmail draft access and save the token JSON.

Prereq: create an OAuth client (type "Desktop app") in Google Cloud Console > APIs & Services > Credentials,
download it as client_secret.json next to this repo, and PUBLISH the OAuth consent screen ("In production"),
otherwise Google expires the refresh token after 7 days.

    python scripts/gmail_auth.py client_secret.json

Writes gmail-oauth.json (git-ignored). Upload it to Secret Manager - see README.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, ".")
from scout.gmail_drafts import SCOPES  # noqa: E402


def main() -> None:
    secret = sys.argv[1] if len(sys.argv) > 1 else "client_secret.json"
    flow = InstalledAppFlow.from_client_secrets_file(secret, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    with open("gmail-oauth.json", "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print("Saved gmail-oauth.json (contains a refresh token - keep it secret, never commit it).")


if __name__ == "__main__":
    main()
