#!/usr/bin/env python3
"""Exchange a Google oauth_token cookie for a Keep master token.

This does NOT open a browser or sign you in. Open
https://accounts.google.com/EmbeddedSetup in a browser, sign in, click
"I agree", then copy the oauth_token cookie (starts with oauth2_4/) from
DevTools -> Application/Storage -> Cookies -> accounts.google.com.

The cookie is single-use and short-lived: exchange it immediately. The
resulting master token is stored in ~/.config/keep/token (mode 600).
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import secrets
import sys
from pathlib import Path

import gpsoauth

CFG = Path.home() / ".config" / "keep"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--email", help="Google account email (prompted if omitted)")
    p.add_argument("--cookie-file", type=Path,
                   help="read the oauth_token from this file instead of prompting")
    p.add_argument("--force", action="store_true",
                   help="retry even if this cookie was already attempted")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    CFG.mkdir(parents=True, exist_ok=True)
    os.chmod(CFG, 0o700)

    email = args.email or os.environ.get("KEEP_EMAIL") or input("Google account email: ").strip()
    if args.cookie_file:
        cookie = args.cookie_file.expanduser().read_text(encoding="utf-8", errors="replace").strip()
    else:
        cookie = getpass.getpass("oauth_token cookie (starts oauth2_4/, hidden): ").strip()
    if cookie.startswith("oauth_token="):
        cookie = cookie.split("=", 1)[1]
    if not cookie.startswith("oauth2_4/"):
        print("warning: that does not look like an oauth_token cookie "
              "(usually starts with oauth2_4/). Continuing anyway.", file=sys.stderr)

    digest = hashlib.sha256(cookie.encode()).hexdigest()
    last_file = CFG / "last-attempt"
    if not args.force and last_file.exists():
        if last_file.read_text().strip() == digest:
            print("This is the same cookie as the previous attempt, and Google "
                  "tokens are single-use. Sign out of the Google account (or use "
                  "a fresh Incognito window), redo EmbeddedSetup, and copy the "
                  "new value. Use --force only if you are sure it was never "
                  "exchanged.", file=sys.stderr)
            return 2
    last_file.write_text(digest + "\n")
    os.chmod(last_file, 0o600)

    id_file = CFG / "android-id"
    if id_file.exists():
        android_id = id_file.read_text().strip()
    else:
        android_id = secrets.token_hex(8)  # 16 hex chars, like an Android ID
        id_file.write_text(android_id + "\n")
        os.chmod(id_file, 0o600)

    response = gpsoauth.exchange_token(email, cookie, android_id)
    token = response.get("Token")
    if not token:
        detail = {k: v for k, v in response.items() if k in ("Error", "ErrorDetail")}
        print(f"Exchange failed: {detail or response}", file=sys.stderr)
        print("The oauth_token is single-use and expires quickly. "
              "Go back to EmbeddedSetup and copy a fresh one.", file=sys.stderr)
        return 1

    print("Checking the token with Google Keep...")
    try:
        import gkeepapi
        keep = gkeepapi.Keep()
        keep.authenticate(email, token)
    except Exception as exc:  # noqa: BLE001 - show Google's message
        print(f"Token was issued but Keep authentication failed: {exc}", file=sys.stderr)
        print("Not saving it. Common causes: wrong account, Workspace device policy, "
              "or a stale cookie.", file=sys.stderr)
        return 1

    token_file = CFG / "token"
    token_file.write_text(token + "\n")
    os.chmod(token_file, 0o600)
    (CFG / "email").write_text(email + "\n")

    print(f"OK: master token saved for {email}")
    print(f"    {token_file} (mode 600)")
    print("Next: ~/keep-migration/run-import.sh --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
