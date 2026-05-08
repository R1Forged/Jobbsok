from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fetch_gmail import GMAIL_SCOPES


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Gmail OAuth token for the job-search agent.")
    parser.add_argument(
        "--credentials",
        default="secrets/gmail_credentials.json",
        help="Path to the OAuth client credentials JSON from Google Cloud.",
    )
    parser.add_argument(
        "--token",
        default="secrets/gmail_token.json",
        help="Where to write the authorized Gmail user token JSON.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL and prompt for the final redirect URL instead of opening a browser.",
    )
    args = parser.parse_args()

    credentials_path = Path(args.credentials)
    token_path = Path(args.token)
    if not credentials_path.exists():
        raise SystemExit(
            f"Missing Gmail credentials file: {credentials_path}\n"
            "Download an OAuth desktop/client JSON from Google Cloud and save it there."
        )
    try:
        credentials_payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid Gmail credentials JSON: {credentials_path}") from exc
    client_config = credentials_payload.get("installed") or credentials_payload.get("web") or {}
    if not client_config.get("client_id") or not client_config.get("client_secret"):
        raise SystemExit(
            f"Incomplete Gmail credentials file: {credentials_path}\n"
            "The file must be the full OAuth client JSON downloaded from Google Cloud, "
            "including both client_id and client_secret. A client ID alone is not enough."
        )

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise SystemExit("Missing Gmail OAuth dependency. Run: pip install -r requirements.txt") from exc

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GMAIL_SCOPES)
    if args.no_browser:
        flow.redirect_uri = "http://localhost"
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        print("Open this URL in your browser, approve access, then paste the final redirected URL here.")
        print(auth_url)
        redirected_url = input("Final redirected URL: ").strip()
        flow.fetch_token(authorization_response=redirected_url)
        credentials = flow.credentials
    else:
        credentials = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")

    token_payload = json.loads(credentials.to_json())
    has_refresh_token = bool(token_payload.get("refresh_token"))
    print(f"Wrote Gmail token: {token_path}")
    print(f"Scopes: {', '.join(GMAIL_SCOPES)}")
    print(f"Refresh token present: {has_refresh_token}")
    print("Keep this file secret. For GitHub Actions, store its full JSON as GMAIL_TOKEN_JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
