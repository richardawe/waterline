"""Create a Waterline ops user for /collections/ops (run on the server, from
backend/, with DATABASE_URL in .env):

    python scripts/collections_create_ops_user.py --name "Ada Obi" --email ada@waterline.ng --role Admin

Prompts for a password, then prints the TOTP secret + otpauth:// URI to add
to an authenticator app (Google Authenticator, 1Password, Authy). The ops
login always needs the 6-digit code."""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.collections import totp  # noqa: E402
from app.collections.models import OpsUser  # noqa: E402
from app.collections.passwords import hash_password, password_problem  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=["Ops", "Admin"], default="Ops")
    parser.add_argument("--reset-totp", action="store_true", help="issue a new TOTP secret for an existing user")
    args = parser.parse_args()
    email = args.email.strip().lower()

    db = SessionLocal()
    existing = db.execute(select(OpsUser).where(OpsUser.email == email)).scalar_one_or_none()
    if existing and not args.reset_totp:
        print(f"{email} already exists. Use --reset-totp to issue a new authenticator secret.")
        return 1
    secret = totp.new_secret()
    if existing:
        existing.totp_secret = secret
    else:
        password = getpass.getpass("Password: ")
        problem = password_problem(password)
        if problem:
            print(problem)
            return 1
        if getpass.getpass("Repeat password: ") != password:
            print("Passwords don't match.")
            return 1
        db.add(OpsUser(name=args.name, email=email, password_hash=hash_password(password), role=args.role, totp_secret=secret))
    db.commit()
    print(f"\nAuthenticator secret for {email}: {secret}")
    print(f"otpauth URI (paste into an authenticator or a QR generator you trust):\n{totp.provisioning_uri(secret, email)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
