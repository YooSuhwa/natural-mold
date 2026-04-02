#!/usr/bin/env python3
"""One-time script to obtain Google OAuth2 refresh_token for Workspace APIs.

Usage:
  1. Go to Google Cloud Console → APIs & Services → Credentials
  2. Create OAuth 2.0 Client ID (Application type: Desktop)
  3. Enable Gmail API and Google Calendar API
  4. Download the client secret JSON file
  5. Run: python scripts/google_oauth_setup.py path/to/client_secret.json

The script will open your browser for consent, then print the refresh_token.
Copy it to your .env file as GOOGLE_OAUTH_REFRESH_TOKEN.
"""

from __future__ import annotations

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/google_oauth_setup.py <client_secret.json>")
        print()
        print("Steps:")
        print("  1. Go to https://console.cloud.google.com/apis/credentials")
        print("  2. Create OAuth 2.0 Client ID (Desktop app)")
        print("  3. Enable Gmail API and Google Calendar API")
        print("  4. Download client_secret JSON")
        print("  5. Run this script with the JSON file path")
        sys.exit(1)

    client_secret_file = sys.argv[1]

    try:
        with open(client_secret_file) as f:
            json.load(f)  # Validate JSON
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    creds = flow.run_local_server(port=8090, prompt="consent", access_type="offline")

    print()
    print("=" * 60)
    print("OAuth2 setup complete! Add these to your .env file:")
    print("=" * 60)
    print()
    print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
