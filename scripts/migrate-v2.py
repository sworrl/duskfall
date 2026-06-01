#!/usr/bin/env python3
"""Duskfall v1.1.0 -> v2.0.0 Migration — Zero-Knowledge Encryption.

This script migrates the existing user table to the new encrypted schema:
1. Adds new columns (email_encrypted, display_name_encrypted, etc.)
2. Encrypts existing plaintext PII with per-user DEKs
3. Creates blind email indexes for uniqueness checks
4. Drops old plaintext columns after migration
5. Preserves all user accounts including the admin

Run from the repo root:
    cd backend && python ../scripts/migrate-v2.py

IMPORTANT: Back up the database before running this migration.
"""
import base64
import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import text, inspect
from app.core.database import engine, SessionLocal, init_db
from app.core.config import settings
from app.core.auth import hash_password
from app.core.crypto import (
    generate_dek, generate_kek_salt, derive_kek, wrap_dek,
    encrypt_field, hash_email_blind, encrypt_json_field,
)

# Ensure all models are imported
import app.models  # noqa: F401


def column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    insp = inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table)]
    return column in columns


def migrate():
    print("=" * 60)
    print("  DUSKFALL v2.0.0 MIGRATION — Zero-Knowledge Encryption")
    print("=" * 60)
    print()

    if settings.DUSKFALL_MODE == "hosted" and not settings.EMAIL_INDEX_PEPPER:
        print("ERROR: EMAIL_INDEX_PEPPER must be set in .env for hosted mode.")
        print("Generate one: python -c \"import secrets; print(secrets.token_hex(32))\"")
        sys.exit(1)

    # Step 1: Add new columns if they don't exist
    print("[1/5] Adding new columns to users table...")
    with engine.begin() as conn:
        new_columns = {
            "kek_salt": "VARCHAR(44)",
            "wrapped_dek": "TEXT",
            "email_encrypted": "TEXT DEFAULT ''",
            "email_blind_index": "VARCHAR(64)",
            "display_name_encrypted": "TEXT DEFAULT ''",
            "totp_secret_encrypted": "TEXT DEFAULT ''",
            "preferences_encrypted": "TEXT DEFAULT ''",
            "signing_key": "VARCHAR(128)",
        }
        for col_name, col_type in new_columns.items():
            if not column_exists(conn, "users", col_name):
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
                print(f"  + Added column: {col_name}")
            else:
                print(f"  = Column exists: {col_name}")

        # Add unique index on email_blind_index if not exists
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_blind_index "
                "ON users (email_blind_index) WHERE email_blind_index IS NOT NULL"
            ))
            print("  + Created unique index on email_blind_index")
        except Exception as e:
            print(f"  = Index already exists or skipped: {e}")

    # Step 2: Check if old columns exist (need migration)
    print("\n[2/5] Checking for old plaintext columns...")
    has_old_email = False
    has_old_display = False
    has_old_totp = False
    has_old_prefs = False

    with engine.begin() as conn:
        has_old_email = column_exists(conn, "users", "email")
        has_old_display = column_exists(conn, "users", "display_name")
        has_old_totp = column_exists(conn, "users", "totp_secret")
        has_old_prefs = column_exists(conn, "users", "preferences")

    if not has_old_email and not has_old_display:
        print("  No old columns found — migration already complete or fresh install.")
        print("  Done!")
        return

    print(f"  Old columns found: email={has_old_email}, display_name={has_old_display}, "
          f"totp_secret={has_old_totp}, preferences={has_old_prefs}")

    # Step 3: Migrate existing user data
    print("\n[3/5] Encrypting existing user data...")
    print("  NOTE: Since we don't know user passwords, we'll use a temporary")
    print("  migration key. Users will need to reset on first login.")
    print()

    # For migration, we use a deterministic key derived from the server secret.
    # This is NOT zero-knowledge yet — it becomes zero-knowledge when users
    # change their password (which re-encrypts with their password-derived key).
    # The admin account gets a special migration path.
    MIGRATION_PASSWORD = "duskfall_migration_v2_temp"

    db = SessionLocal()
    try:
        # Build query dynamically based on which columns exist
        cols = ["id", "username"]
        if has_old_email:
            cols.append("email")
        if has_old_display:
            cols.append("display_name")
        if has_old_totp:
            cols.append("totp_secret")
        if has_old_prefs:
            cols.append("preferences")

        result = db.execute(text(f"SELECT {', '.join(cols)} FROM users"))
        users = result.fetchall()
        print(f"  Found {len(users)} users to migrate")

        for row in users:
            idx = 0
            user_id = row[idx]; idx += 1
            username = row[idx]; idx += 1
            old_email = row[idx] if has_old_email else ""; idx += (1 if has_old_email else 0)
            old_display = row[idx] if has_old_display else ""; idx += (1 if has_old_display else 0)
            old_totp = row[idx] if has_old_totp else ""; idx += (1 if has_old_totp else 0)
            old_prefs = row[idx] if has_old_prefs else None

            # Skip if already migrated (has encrypted data)
            check = db.execute(text(
                "SELECT wrapped_dek FROM users WHERE id = :id"
            ), {"id": user_id}).fetchone()
            if check and check[0]:
                print(f"  = {username}: already migrated, skipping")
                continue

            # Generate encryption keys using migration password
            salt = generate_kek_salt()
            kek = derive_kek(MIGRATION_PASSWORD, salt)
            dek = generate_dek()

            # Encrypt fields
            email_enc = encrypt_field(old_email, dek)
            blind_idx = hash_email_blind(old_email, settings.EMAIL_INDEX_PEPPER) if old_email and settings.EMAIL_INDEX_PEPPER else None
            display_enc = encrypt_field(old_display, dek)
            totp_enc = encrypt_field(old_totp, dek) if old_totp else ""

            prefs_enc = ""
            if old_prefs:
                import json
                if isinstance(old_prefs, str):
                    old_prefs = json.loads(old_prefs)
                prefs_enc = encrypt_json_field(old_prefs, dek)

            # Store encrypted data
            db.execute(text("""
                UPDATE users SET
                    kek_salt = :salt,
                    wrapped_dek = :dek,
                    email_encrypted = :email,
                    email_blind_index = :blind,
                    display_name_encrypted = :display,
                    totp_secret_encrypted = :totp,
                    preferences_encrypted = :prefs
                WHERE id = :id
            """), {
                "salt": base64.b64encode(salt).decode("ascii"),
                "dek": wrap_dek(dek, kek),
                "email": email_enc,
                "blind": blind_idx,
                "display": display_enc,
                "totp": totp_enc,
                "prefs": prefs_enc,
                "id": user_id,
            })

            print(f"  + {username}: encrypted (email={'yes' if old_email else 'no'}, "
                  f"display={'yes' if old_display else 'no'}, totp={'yes' if old_totp else 'no'})")

        db.commit()
        print(f"\n  All {len(users)} users migrated successfully.")

    finally:
        db.close()

    # Step 4: Drop old plaintext columns
    print("\n[4/5] Dropping old plaintext columns...")
    with engine.begin() as conn:
        for col in ["email", "display_name", "totp_secret", "preferences"]:
            if column_exists(conn, "users", col):
                conn.execute(text(f"ALTER TABLE users DROP COLUMN {col}"))
                print(f"  - Dropped column: {col}")

    # Step 5: Re-encrypt admin account with known password
    print("\n[5/5] Setting up admin account for first login...")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if admin_password:
        db = SessionLocal()
        try:
            admin = db.execute(text(
                "SELECT id, username FROM users WHERE role = 'admin' LIMIT 1"
            )).fetchone()
            if admin:
                # Re-encrypt with the provided admin password
                salt = generate_kek_salt()
                kek = derive_kek(admin_password, salt)

                # First decrypt with migration key
                old_salt_row = db.execute(text(
                    "SELECT kek_salt, wrapped_dek, email_encrypted, display_name_encrypted "
                    "FROM users WHERE id = :id"
                ), {"id": admin[0]}).fetchone()

                from app.core.crypto import unwrap_dek, decrypt_field
                old_salt = base64.b64decode(old_salt_row[0])
                old_kek = derive_kek(MIGRATION_PASSWORD, old_salt)
                old_dek = unwrap_dek(old_salt_row[1], old_kek)

                # Decrypt old data
                email = decrypt_field(old_salt_row[2], old_dek)
                display = decrypt_field(old_salt_row[3], old_dek)

                # Re-encrypt with admin's real password
                new_dek = generate_dek()
                new_salt = generate_kek_salt()
                new_kek = derive_kek(admin_password, new_salt)

                db.execute(text("""
                    UPDATE users SET
                        password_hash = :pw,
                        kek_salt = :salt,
                        wrapped_dek = :dek,
                        email_encrypted = :email,
                        display_name_encrypted = :display
                    WHERE id = :id
                """), {
                    "pw": hash_password(admin_password),
                    "salt": base64.b64encode(new_salt).decode("ascii"),
                    "dek": wrap_dek(new_dek, new_kek),
                    "email": encrypt_field(email, new_dek),
                    "display": encrypt_field(display, new_dek),
                    "id": admin[0],
                })
                db.commit()
                print(f"  Admin '{admin[1]}' re-encrypted with provided password.")
            else:
                print("  No admin user found.")
        finally:
            db.close()
    else:
        print("  Set ADMIN_PASSWORD env var to re-encrypt admin with a known password.")
        print("  Without it, admin must use the migration password to login first,")
        print("  then change password via the UI.")
        print(f"  Temporary migration password: {MIGRATION_PASSWORD}")

    print()
    print("=" * 60)
    print("  MIGRATION COMPLETE")
    print("=" * 60)
    print()
    print("  Next steps:")
    print("  1. Verify login works")
    print("  2. All users should change passwords (re-encrypts with their key)")
    print("  3. Delete this migration script from the server")
    print()


if __name__ == "__main__":
    migrate()
