#!/usr/bin/env python3
"""One-time interactive Google Calendar authorization.

Run this yourself in a terminal (it needs to open a browser):

    python3 setup_gcal.py

It looks for client_secret.json in the project root (downloaded from
Google Cloud Console: APIs & Services -> Credentials -> your OAuth client
-> Download JSON), walks you through the consent screen, and saves a
refresh token to ~/.gcal_token.json. After that, the background server
can sync silently — no browser needed again unless you revoke access.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET = os.path.join(ROOT, "client_secret.json")
TOKEN_PATH = os.path.expanduser("~/.gcal_token.json")
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    if not os.path.exists(CLIENT_SECRET):
        sys.exit(
            "Missing client_secret.json in %s\n"
            "Download it from Google Cloud Console: APIs & Services -> "
            "Credentials -> your OAuth client (Desktop app) -> Download JSON, "
            "save it here as client_secret.json, then re-run this script." % ROOT)

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)
    print("Saved token to %s" % TOKEN_PATH)
    print("Google Calendar sync is ready. Try it from the app's "
          "'Sync to Calendar' button, or run:\n"
          "  python3 -c \"from src.services.gcal import sync_schedule; "
          "print(sync_schedule())\"")


if __name__ == "__main__":
    main()
