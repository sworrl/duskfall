#!/usr/bin/env python3
"""Delete stale admin accounts and create a fresh admin.

Use when the prior account's password is lost. Because PII is zero-knowledge
encrypted (KEK derived from password), the old encrypted email / display name
/ TOTP secret / preferences are unrecoverable — this script wipes them by
generating fresh keys for the new account.

Run from the repo root (must be on the host that has the backend venv):
    cd backend && source venv/bin/activate
    python ../scripts/reset-admin.py --delete admin,root --username admin --email me@example.com

Flags:
    --delete       comma-separated usernames to delete first (empty = none)
    --username     username for the new admin (required)
    --email        email for the new admin (optional, blank = none)
    --display      display name for the new admin (default: username)
    --tier         billing tier (default: commander)
    --yes          skip the interactive confirmation
"""
import argparse
import base64
import os
import secrets
import sys

# Make the backend package importable when invoked from anywhere
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.core.auth import hash_password  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.crypto import (  # noqa: E402
    generate_dek, generate_kek_salt, derive_kek, wrap_dek,
    encrypt_field, hash_email_blind,
)
from app.models.user import User, DeviceAPIKey  # noqa: E402
import app.models  # noqa: F401,E402


def cascade_delete(db, user: User) -> None:
    """Drop rows that FK back to this user before deleting the user row."""
    n_keys = db.query(DeviceAPIKey).filter(DeviceAPIKey.user_id == user.id).delete(synchronize_session=False)
    if n_keys:
        print(f"    - dropped {n_keys} device API keys")

    # Best-effort: other tables that may FK to user_id. Import lazily so a
    # missing model in a stripped-down build doesn't kill the script.
    for module_path, attr_name in [
        ("app.models.adsb_track", "AircraftGroup"),
        ("app.models.federation", "FederationPeer"),
        ("app.models.evidence", "Evidence"),
        ("app.models.feed", "FeedEvent"),
    ]:
        try:
            mod = __import__(module_path, fromlist=[attr_name])
            cls = getattr(mod, attr_name, None)
            if cls is None or not hasattr(cls, "user_id"):
                continue
            n = db.query(cls).filter(cls.user_id == user.id).delete(synchronize_session=False)
            if n:
                print(f"    - dropped {n} rows from {cls.__tablename__}")
        except ImportError:
            continue
        except Exception as e:
            print(f"    ! could not clean {module_path}.{attr_name}: {e}")

    db.delete(user)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--delete", default="", help="Comma-separated usernames to delete (empty = none)")
    p.add_argument("--username", required=True, help="Username for the new admin")
    p.add_argument("--email", default="", help="Email for the new admin (optional)")
    p.add_argument("--display", default="", help="Display name (default: username)")
    p.add_argument("--tier", default="commander", help="Billing tier (default: commander)")
    p.add_argument("--yes", action="store_true", help="Skip interactive confirmation")
    return p.parse_args()


def main():
    args = parse_args()
    targets = [t.strip() for t in args.delete.split(",") if t.strip()]
    new_username = args.username.strip()
    new_email = args.email.strip()
    new_display = args.display.strip() or new_username

    if not args.yes:
        print("About to:")
        if targets:
            print(f"  - DELETE users (cascading FKs): {targets}")
        print(f"  - CREATE admin '{new_username}' (role=admin, tier={args.tier})")
        print("  - All encrypted PII on the deleted accounts will be unrecoverable.")
        resp = input("Proceed? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            sys.exit(1)

    init_db()  # idempotent — creates tables if missing

    db = SessionLocal()
    try:
        if targets:
            found = db.query(User).filter(User.username.in_(targets)).all()
            if found:
                print(f"Deleting {len(found)} matching user(s):")
                for u in found:
                    print(f"  id={u.id} username={u.username} role={u.role} last_login={u.last_login}")
                    cascade_delete(db, u)
                db.commit()
                print()
            else:
                print(f"No users matched {targets} — nothing to delete.\n")

        # Don't collide with an existing user of the new username
        if db.query(User).filter(User.username == new_username).first():
            print(f"ERROR: user '{new_username}' still exists (not in --delete list). Aborting before overwrite.")
            sys.exit(2)

        password = secrets.token_urlsafe(24)
        salt = generate_kek_salt()
        kek = derive_kek(password, salt)
        dek = generate_dek()

        admin = User(
            username=new_username,
            password_hash=hash_password(password),
            role="admin",
            billing_tier=args.tier,
            is_active=True,
            email_verified=False,
            kek_salt=base64.b64encode(salt).decode("ascii"),
            wrapped_dek=wrap_dek(dek, kek),
            email_encrypted=encrypt_field(new_email, dek) if new_email else "",
            email_blind_index=(
                hash_email_blind(new_email, settings.EMAIL_INDEX_PEPPER)
                if new_email and settings.EMAIL_INDEX_PEPPER
                else None
            ),
            display_name_encrypted=encrypt_field(new_display, dek),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print("=" * 60)
        print("  NEW ADMIN ACCOUNT CREATED")
        print("=" * 60)
        print(f"  Username: {admin.username}")
        print(f"  Password: {password}")
        print(f"  User ID:  {admin.id}")
        print(f"  Role:     {admin.role}")
        print(f"  Tier:     {admin.billing_tier}")
        print(f"  Email:    {new_email or '(none)'}")
        print("=" * 60)
        print("  Save this password — it will not be shown again.")
        print("  Login, then change the password and (re)enroll 2FA.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
