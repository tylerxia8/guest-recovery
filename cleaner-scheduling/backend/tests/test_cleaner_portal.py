"""M4 Cleaner portal service tests.

Tests for backend/app/services/cleaner_coordination.py covering:
- Link authentication
- Revoked/expired/inactive rejection
- Portal token generation
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import timedelta

import pytest
from app.clock import FrozenClock
from app.config import Settings
from app.db import get_engine, session_factory
from app.domain.errors import AppError
from app.models import Cleaner, CleanerAccessLink
from app.services.field_cipher import protect_text
from app.workspaces import LEGACY_WORKSPACE_ID
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


def _migrator_session() -> sessionmaker[Session]:
    """Create session factory using migrator credentials (bypasses RLS).

    Note: The migrator role requires BYPASSRLS to seed test data visible across RLS contexts.
    """
    engine = get_engine(os.environ["MIGRATION_DATABASE_URL"])
    return sessionmaker(bind=engine, expire_on_commit=False)


class TestCleanerPortal:
    """Tests for cleaner_coordination portal service."""

    def test_link_auth_validates_token(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Portal authentication validates token against stored hash.

        Mutation: Accept any token regardless of hash.
        Fails: assert exc_info.value.code == "link_unavailable"
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)
        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()
        token = portal_token(grant_id, settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Portal Test Cleaner",
                phone_ciphertext=protect_text("+15551234569", settings.field_encryption_secret),
                email_ciphertext=protect_text("portal@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"portal:{cleaner_id}".encode()).hexdigest(),
                active=True,
                sms_consent_status="opted_in",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
            s.add(cleaner)
            s.flush()

            grant = CleanerAccessLink(
                id=grant_id,
                created_at=clock.now(),
                cleaner_id=cleaner_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=clock.now() + timedelta(days=7),
                purpose="jobs",
            )
            s.add(grant)
            s.commit()

            result = authenticate_portal(s, settings, clock, token)
            assert result.id == cleaner_id

            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token + "x")
            assert exc_info.value.code == "link_unavailable"

    def test_forged_token_with_matching_hash_rejected(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """A forged token whose hash matches the grant is rejected by HMAC check.

        This tests the HMAC/workspace binding at cleaner_coordination.py:129-131 specifically.
        We seed a grant with a hash matching the forged token, so hash lookup passes,
        but the forged token has the wrong HMAC signature.

        Mutation: Remove hmac.compare_digest check at cleaner_coordination.py:129-131.
        Fails: assert exc_info.value.code == "link_unavailable"
        """
        from app.services.cleaner_coordination import authenticate_portal

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)
        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()

        forged_token = f"{grant_id.hex}.forged_signature_that_will_not_match"

        with factory() as s:
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Forged Token Cleaner",
                phone_ciphertext=protect_text("+15551234599", settings.field_encryption_secret),
                email_ciphertext=protect_text("forged@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"forged:{cleaner_id}".encode()).hexdigest(),
                active=True,
                sms_consent_status="opted_in",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
            s.add(cleaner)
            s.flush()

            grant = CleanerAccessLink(
                id=grant_id,
                created_at=clock.now(),
                cleaner_id=cleaner_id,
                token_hash=hashlib.sha256(forged_token.encode()).hexdigest(),
                expires_at=clock.now() + timedelta(days=7),
                purpose="jobs",
            )
            s.add(grant)
            s.commit()

            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, forged_token)
            assert exc_info.value.code == "link_unavailable"

    def test_cross_workspace_token_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """A token created in workspace A is rejected when redeemed in workspace B.

        This tests the workspace binding in the HMAC at cleaner_coordination.py:129-131.
        The token is valid for workspace A, but we try to authenticate in workspace B.

        Mutation: Remove hmac.compare_digest check at cleaner_coordination.py:129-131.
        Fails: assert exc_info.value.code == "link_unavailable"
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        workspace_a = uuid.uuid4()
        workspace_b = uuid.uuid4()

        factory_a = session_factory(settings, workspace_a)
        factory_b = session_factory(settings, workspace_b)
        migrator = _migrator_session()

        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()

        token_for_workspace_a = portal_token(grant_id, settings, workspace_a)

        cleaner_id_b = uuid.uuid4()
        grant_id_b = uuid.uuid4()

        with migrator() as s:
            for ws_id in (workspace_a, workspace_b):
                s.execute(
                    text(
                        "INSERT INTO workspaces (id, created_at, name) VALUES (:id, :now, :name) ON CONFLICT DO NOTHING"
                    ),
                    {"id": ws_id, "now": clock.now(), "name": f"Workspace {ws_id}"},
                )

            for ws_id, cid, gid in [
                (workspace_a, cleaner_id, grant_id),
                (workspace_b, cleaner_id_b, grant_id_b),
            ]:
                s.execute(
                    text(
                        "INSERT INTO cleaners (id, workspace_id, created_at, updated_at, version, "
                        "display_name, phone_ciphertext, email_ciphertext, phone_lookup_hmac, "
                        "active, sms_consent_status, default_work_intervals, eligibility_policy) "
                        "VALUES (:id, :ws, :now, :now, 1, :name, :phone, :email, :hmac, "
                        "true, 'opted_in', '[]', 'all_except_exclusions')"
                    ),
                    {
                        "id": cid,
                        "ws": ws_id,
                        "now": clock.now(),
                        "name": f"Cross Workspace Cleaner {ws_id}",
                        "phone": protect_text(f"+1555123459{ws_id.int % 10}", settings.field_encryption_secret),
                        "email": protect_text(f"cross{ws_id.int % 100}@example.com", settings.field_encryption_secret),
                        "hmac": hashlib.sha256(f"cross:{cid}".encode()).hexdigest(),
                    },
                )

                s.execute(
                    text(
                        "INSERT INTO cleaner_access_links (id, workspace_id, created_at, cleaner_id, "
                        "token_hash, expires_at, purpose) "
                        "VALUES (:id, :ws, :now, :cleaner_id, :hash, :expires, 'jobs')"
                    ),
                    {
                        "id": gid,
                        "ws": ws_id,
                        "now": clock.now(),
                        "cleaner_id": cid,
                        "hash": hashlib.sha256(token_for_workspace_a.encode()).hexdigest(),
                        "expires": clock.now() + timedelta(days=7),
                    },
                )
            s.commit()

        with factory_a() as s:
            result = authenticate_portal(s, settings, clock, token_for_workspace_a)
            assert result.id == cleaner_id

        with factory_b() as s:
            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token_for_workspace_a)
            assert exc_info.value.code == "link_unavailable"

    def test_rls_blocks_cross_workspace_grant_lookup(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """RLS on both cleaner_access_links and cleaners prevents cross-workspace auth.

        Seed cleaners in workspace A and B, seed grants in workspace A (one for positive
        test, one with hash of token signed for workspace B). Present the B-signed token
        in workspace B session - RLS blocks both the grant lookup and cleaner lookup.

        Mutation: Drop RLS on both cleaner_access_links and cleaners tables.
        Fails: assert exc_info.value.code == "link_unavailable" (workspace B lookup)
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        workspace_a = uuid.uuid4()
        workspace_b = uuid.uuid4()

        factory_a = session_factory(settings, workspace_a)
        factory_b = session_factory(settings, workspace_b)
        migrator = _migrator_session()

        cleaner_id_a = uuid.uuid4()
        cleaner_id_b = uuid.uuid4()
        grant_id_for_positive = uuid.uuid4()
        grant_id_for_rls_test = uuid.uuid4()
        token_for_positive = portal_token(grant_id_for_positive, settings, workspace_a)
        token_for_rls_test = portal_token(grant_id_for_rls_test, settings, workspace_b)

        with migrator() as s:
            for ws_id in (workspace_a, workspace_b):
                s.execute(
                    text(
                        "INSERT INTO workspaces (id, created_at, name) VALUES (:id, :now, :name) ON CONFLICT DO NOTHING"
                    ),
                    {"id": ws_id, "now": clock.now(), "name": f"RLS Test Workspace {ws_id}"},
                )

            s.execute(
                text(
                    "INSERT INTO cleaners (id, workspace_id, created_at, updated_at, version, "
                    "display_name, phone_ciphertext, email_ciphertext, phone_lookup_hmac, "
                    "active, sms_consent_status, default_work_intervals, eligibility_policy) "
                    "VALUES (:id, :ws, :now, :now, 1, :name, :phone, :email, :hmac, "
                    "true, 'opted_in', '[]', 'all_except_exclusions')"
                ),
                {
                    "id": cleaner_id_a,
                    "ws": workspace_a,
                    "now": clock.now(),
                    "name": "RLS Test Cleaner A",
                    "phone": protect_text("+15551234597", settings.field_encryption_secret),
                    "email": protect_text("rls-a@example.com", settings.field_encryption_secret),
                    "hmac": hashlib.sha256(f"rls:{cleaner_id_a}".encode()).hexdigest(),
                },
            )

            s.execute(
                text(
                    "INSERT INTO cleaners (id, workspace_id, created_at, updated_at, version, "
                    "display_name, phone_ciphertext, email_ciphertext, phone_lookup_hmac, "
                    "active, sms_consent_status, default_work_intervals, eligibility_policy) "
                    "VALUES (:id, :ws, :now, :now, 1, :name, :phone, :email, :hmac, "
                    "true, 'opted_in', '[]', 'all_except_exclusions')"
                ),
                {
                    "id": cleaner_id_b,
                    "ws": workspace_b,
                    "now": clock.now(),
                    "name": "RLS Test Cleaner B",
                    "phone": protect_text("+15551234598", settings.field_encryption_secret),
                    "email": protect_text("rls-b@example.com", settings.field_encryption_secret),
                    "hmac": hashlib.sha256(f"rls:{cleaner_id_b}".encode()).hexdigest(),
                },
            )

            s.execute(
                text(
                    "INSERT INTO cleaner_access_links (id, workspace_id, created_at, cleaner_id, "
                    "token_hash, expires_at, purpose) "
                    "VALUES (:id, :ws, :now, :cleaner_id, :hash, :expires, 'jobs')"
                ),
                {
                    "id": grant_id_for_positive,
                    "ws": workspace_a,
                    "now": clock.now(),
                    "cleaner_id": cleaner_id_a,
                    "hash": hashlib.sha256(token_for_positive.encode()).hexdigest(),
                    "expires": clock.now() + timedelta(days=7),
                },
            )

            s.execute(
                text(
                    "INSERT INTO cleaner_access_links (id, workspace_id, created_at, cleaner_id, "
                    "token_hash, expires_at, purpose) "
                    "VALUES (:id, :ws, :now, :cleaner_id, :hash, :expires, 'jobs')"
                ),
                {
                    "id": grant_id_for_rls_test,
                    "ws": workspace_a,
                    "now": clock.now(),
                    "cleaner_id": cleaner_id_a,
                    "hash": hashlib.sha256(token_for_rls_test.encode()).hexdigest(),
                    "expires": clock.now() + timedelta(days=7),
                },
            )
            s.commit()

        with factory_a() as s:
            result = authenticate_portal(s, settings, clock, token_for_positive)
            assert result.id == cleaner_id_a, "Positive check: token works in workspace A"

        with factory_b() as s:
            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token_for_rls_test)
            assert exc_info.value.code == "link_unavailable", "RLS on both tables should block lookup in workspace B"

    def test_workspace_signing_prevents_token_reuse(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """A token signed for workspace A fails in workspace B even with matching hash.

        Create grant_id_b in workspace B, but seed its token_hash with the hash of
        portal_token(grant_id_b, settings, workspace_a). Present that token in B.
        The hash lookup passes but HMAC fails because the token was signed for A.

        Mutation: Remove workspace_id from identity at cleaner_coordination.py:53.
        Fails: assert exc_info.value.code == "link_unavailable"
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        workspace_a = uuid.uuid4()
        workspace_b = uuid.uuid4()

        factory_b = session_factory(settings, workspace_b)
        migrator = _migrator_session()

        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()

        token_signed_for_a = portal_token(grant_id, settings, workspace_a)

        with migrator() as s:
            s.execute(
                text("INSERT INTO workspaces (id, created_at, name) VALUES (:id, :now, :name) ON CONFLICT DO NOTHING"),
                {"id": workspace_b, "now": clock.now(), "name": f"Signing Test Workspace {workspace_b}"},
            )

            s.execute(
                text(
                    "INSERT INTO cleaners (id, workspace_id, created_at, updated_at, version, "
                    "display_name, phone_ciphertext, email_ciphertext, phone_lookup_hmac, "
                    "active, sms_consent_status, default_work_intervals, eligibility_policy) "
                    "VALUES (:id, :ws, :now, :now, 1, :name, :phone, :email, :hmac, "
                    "true, 'opted_in', '[]', 'all_except_exclusions')"
                ),
                {
                    "id": cleaner_id,
                    "ws": workspace_b,
                    "now": clock.now(),
                    "name": "Signing Test Cleaner",
                    "phone": protect_text("+15551234596", settings.field_encryption_secret),
                    "email": protect_text("signing@example.com", settings.field_encryption_secret),
                    "hmac": hashlib.sha256(f"signing:{cleaner_id}".encode()).hexdigest(),
                },
            )

            s.execute(
                text(
                    "INSERT INTO cleaner_access_links (id, workspace_id, created_at, cleaner_id, "
                    "token_hash, expires_at, purpose) "
                    "VALUES (:id, :ws, :now, :cleaner_id, :hash, :expires, 'jobs')"
                ),
                {
                    "id": grant_id,
                    "ws": workspace_b,
                    "now": clock.now(),
                    "cleaner_id": cleaner_id,
                    "hash": hashlib.sha256(token_signed_for_a.encode()).hexdigest(),
                    "expires": clock.now() + timedelta(days=7),
                },
            )
            s.commit()

        with factory_b() as s:
            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token_signed_for_a)
            assert exc_info.value.code == "link_unavailable"

    def test_revoked_link_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Revoked links are rejected.

        Mutation: Don't check revoked_at in authenticate_portal.
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)
        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()
        token = portal_token(grant_id, settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Revoked Test Cleaner",
                phone_ciphertext=protect_text("+15551234570", settings.field_encryption_secret),
                email_ciphertext=protect_text("revoked@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"revoked:{cleaner_id}".encode()).hexdigest(),
                active=True,
                sms_consent_status="opted_in",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
            s.add(cleaner)
            s.flush()

            grant = CleanerAccessLink(
                id=grant_id,
                created_at=clock.now(),
                cleaner_id=cleaner_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=clock.now() + timedelta(days=7),
                purpose="jobs",
                revoked_at=clock.now() - timedelta(hours=1),
            )
            s.add(grant)
            s.commit()

            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token)
            assert exc_info.value.code == "link_unavailable"

    def test_expired_link_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Expired links are rejected.

        Mutation: Don't check expires_at in authenticate_portal.
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)
        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()
        token = portal_token(grant_id, settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Expired Test Cleaner",
                phone_ciphertext=protect_text("+15551234571", settings.field_encryption_secret),
                email_ciphertext=protect_text("expired@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"expired:{cleaner_id}".encode()).hexdigest(),
                active=True,
                sms_consent_status="opted_in",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
            s.add(cleaner)
            s.flush()

            grant = CleanerAccessLink(
                id=grant_id,
                created_at=clock.now() - timedelta(days=10),
                cleaner_id=cleaner_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=clock.now() - timedelta(days=1),
                purpose="jobs",
            )
            s.add(grant)
            s.commit()

            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token)
            assert exc_info.value.code == "link_unavailable"

    def test_inactive_cleaner_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Links for inactive cleaners are rejected.

        Mutation: Don't check cleaner.active in authenticate_portal.
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)
        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()
        token = portal_token(grant_id, settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Inactive Test Cleaner",
                phone_ciphertext=protect_text("+15551234572", settings.field_encryption_secret),
                email_ciphertext=protect_text("inactive@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"inactive:{cleaner_id}".encode()).hexdigest(),
                active=False,
                sms_consent_status="opted_in",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
            s.add(cleaner)
            s.flush()

            grant = CleanerAccessLink(
                id=grant_id,
                created_at=clock.now(),
                cleaner_id=cleaner_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=clock.now() + timedelta(days=7),
                purpose="jobs",
            )
            s.add(grant)
            s.commit()

            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token)
            assert exc_info.value.code == "link_unavailable"

    def test_wrong_purpose_rejected(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Portal auth rejects grants with wrong purpose.

        Mutation: Don't check purpose in authenticate_portal.
        """
        from app.services.cleaner_coordination import authenticate_portal, portal_token

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)
        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()
        token = portal_token(grant_id, settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="Wrong Purpose Cleaner",
                phone_ciphertext=protect_text("+15551234573", settings.field_encryption_secret),
                email_ciphertext=protect_text("wrong@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"wrong:{cleaner_id}".encode()).hexdigest(),
                active=True,
                sms_consent_status="opted_in",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
            )
            s.add(cleaner)
            s.flush()

            grant = CleanerAccessLink(
                id=grant_id,
                created_at=clock.now(),
                cleaner_id=cleaner_id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=clock.now() + timedelta(days=7),
                purpose="onboarding",
            )
            s.add(grant)
            s.commit()

            with pytest.raises(AppError) as exc_info:
                authenticate_portal(s, settings, clock, token)
            assert exc_info.value.code == "link_unavailable"


class TestPortalTokenGeneration:
    """Tests for portal token generation."""

    def test_portal_token_deterministic(self, settings: Settings) -> None:
        """Portal token should be deterministic for same inputs.

        Mutation: Add randomness to token generation.
        """
        from app.services.cleaner_coordination import portal_token

        grant_id = uuid.uuid4()
        token1 = portal_token(grant_id, settings, LEGACY_WORKSPACE_ID)
        token2 = portal_token(grant_id, settings, LEGACY_WORKSPACE_ID)

        assert token1 == token2, "Same inputs should produce same token"

    def test_portal_token_unique_per_grant(self, settings: Settings) -> None:
        """Portal tokens should be unique per grant ID.

        Mutation: Use fixed token for all grants.
        """
        from app.services.cleaner_coordination import portal_token

        grant_id1 = uuid.uuid4()
        grant_id2 = uuid.uuid4()
        token1 = portal_token(grant_id1, settings, LEGACY_WORKSPACE_ID)
        token2 = portal_token(grant_id2, settings, LEGACY_WORKSPACE_ID)

        assert token1 != token2, "Different grants should have different tokens"
