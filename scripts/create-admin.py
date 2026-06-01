#!/usr/bin/env python3
"""Create initial admin account for Duskfall hosted mode.

Run from the backend directory:
    python ../scripts/create-admin.py
"""
import base64
import secrets
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.database import SessionLocal, init_db
from app.core.auth import hash_password
from app.core.config import settings
from app.core.crypto import (
    generate_dek, generate_kek_salt, derive_kek, wrap_dek,
    encrypt_field, hash_email_blind,
)
from app.models.user import User

# Ensure all models are imported so tables get created
import app.models  # noqa: F401


def create_admin():
    # Ensure tables exist
    print("Initializing database tables...")
    init_db()

    # Generate secure password
    password = secrets.token_urlsafe(48)

    # Configurable admin credentials
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_email = os.environ.get("ADMIN_EMAIL", f"{admin_user}@localhost")

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == admin_user).first()
        if existing:
            print(f"Admin '{admin_user}' already exists (role={existing.role})")
            print("Resetting password and re-encrypting...")
            existing.password_hash = hash_password(password)
            existing.role = "admin"
            existing.billing_tier = "commander"
            existing.is_active = True

            # Re-encrypt with new password
            salt = generate_kek_salt()
            kek = derive_kek(password, salt)
            dek = generate_dek()
            existing.kek_salt = base64.b64encode(salt).decode("ascii")
            existing.wrapped_dek = wrap_dek(dek, kek)
            existing.email_encrypted = encrypt_field(admin_email, dek)
            if settings.EMAIL_INDEX_PEPPER:
                existing.email_blind_index = hash_email_blind(admin_email, settings.EMAIL_INDEX_PEPPER)
            existing.display_name_encrypted = encrypt_field(admin_user.title(), dek)
            db.commit()
        else:
            # Create encryption keys
            salt = generate_kek_salt()
            kek = derive_kek(password, salt)
            dek = generate_dek()

            admin = User(
                username=admin_user,
                password_hash=hash_password(password),
                role="admin",
                billing_tier="commander",
                is_active=True,
                kek_salt=base64.b64encode(salt).decode("ascii"),
                wrapped_dek=wrap_dek(dek, kek),
                email_encrypted=encrypt_field(admin_email, dek),
                email_blind_index=hash_email_blind(admin_email, settings.EMAIL_INDEX_PEPPER) if settings.EMAIL_INDEX_PEPPER else None,
                display_name_encrypted=encrypt_field(admin_user.title(), dek),
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)
            print(f"Admin account created: DF-US serial assigned")

        print()
        print("=" * 60)
        print("  DUSKFALL ADMIN CREDENTIALS")
        print("=" * 60)
        print(f"  Username: {admin_user}")
        print(f"  Password: {password}")
        print(f"  Role:     admin")
        print(f"  Tier:     commander")
        print("=" * 60)
        print()
        print("  SAVE THIS PASSWORD — it will not be shown again.")
        print("  ALL USER DATA IS ENCRYPTED WITH THIS PASSWORD.")
        print("  If you lose it, your admin PII is irrecoverable.")
        print("  Enable 2FA immediately after first login.")
        print()

    finally:
        db.close()


if __name__ == "__main__":
    create_admin()
