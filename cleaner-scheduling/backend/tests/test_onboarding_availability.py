"""M4 Onboarding and availability email tests.

Tests for backend/app/services/onboarding_email.py and availability_email.py covering:
- Onboarding preview includes cleaner language in URL
- Availability email queued for active cleaners with _queue call
- Current behavior: SMS-opted-out cleaners STILL get availability emails (product question)
- Email language preference and fallback

NOTE: There is no email-unsubscribe concept in the app. The only filtering in _queue is:
- Cleaner.active (must be True)
- Cleaner.email_ciphertext (must exist)
- CleanerWeeklyHours.starting_address_ciphertext (must exist)
- Weekly hours match the target day
- No existing outbox or cleaner opt-in for that day

sms_consent_status is NOT checked for availability emails.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.clock import FrozenClock
from app.config import Settings
from app.db import get_engine, session_factory
from app.models import Cleaner, CleanerAccessLink, CleanerWeeklyHours, OutboxEvent
from app.models.workspace_connection import WorkspaceConnection
from app.services.availability_email import _queue
from app.services.field_cipher import protect_text
from app.workspaces import LEGACY_WORKSPACE_ID, Workspace
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    pass

NY = ZoneInfo("America/New_York")


def _migrator_session() -> sessionmaker[Session]:
    """Create session factory using migrator credentials (bypasses RLS)."""
    return sessionmaker(bind=get_engine(os.environ["MIGRATION_DATABASE_URL"]), expire_on_commit=False)


def _create_workspace_with_connection(settings: Settings, clock: FrozenClock) -> uuid.UUID:
    """Create a non-legacy workspace with completed connection."""
    factory = _migrator_session()
    workspace_id = uuid.uuid4()

    with factory() as s:
        workspace = Workspace(id=workspace_id, name="Availability Test", created_at=clock.now())
        s.add(workspace)
        s.flush()

        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})

        connection = WorkspaceConnection(
            workspace_id=workspace_id,
            token_ciphertext=protect_text("test-token", settings.field_encryption_secret),
            connected_at=clock.now(),
            completed_at=clock.now(),
        )
        s.add(connection)
        s.commit()

    return workspace_id


def _create_cleaner(
    factory: sessionmaker[Session],
    settings: Settings,
    clock: FrozenClock,
    workspace_id: uuid.UUID,
    *,
    active: bool = True,
    with_email: bool = True,
    sms_opted_out: bool = False,
    weekday: int,
) -> uuid.UUID:
    """Create a cleaner with weekly hours in the specified workspace."""
    cleaner_id = uuid.uuid4()

    with factory() as s:
        s.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})

        link_id = uuid.uuid4()
        link = CleanerAccessLink(
            id=link_id,
            created_at=clock.now(),
            cleaner_id=cleaner_id,
            token_hash=hashlib.sha256(f"jobs:{cleaner_id}".encode()).hexdigest(),
            expires_at=clock.now() + timedelta(days=365),
            purpose="jobs",
        )
        s.add(link)

        cleaner = Cleaner(
            id=cleaner_id,
            created_at=clock.now(),
            updated_at=clock.now(),
            version=1,
            display_name="Test Cleaner",
            phone_ciphertext=protect_text("+15551234567", settings.field_encryption_secret),
            email_ciphertext=(
                protect_text("cleaner@example.com", settings.field_encryption_secret) if with_email else None
            ),
            phone_lookup_hmac=hashlib.sha256(f"test:{cleaner_id}".encode()).hexdigest(),
            active=active,
            sms_consent_status="opted_out" if sms_opted_out else "opted_in",
            opted_out_at=clock.now() if sms_opted_out else None,
            default_work_intervals=[],
            eligibility_policy="all_except_exclusions",
        )
        s.add(cleaner)
        s.flush()

        weekly = CleanerWeeklyHours(
            cleaner_id=cleaner_id,
            version=1,
            updated_at=clock.now(),
            days=[{"weekday": weekday, "start": "09:00", "end": "17:00"}],
            starting_address_ciphertext=protect_text("123 Main St", settings.field_encryption_secret),
        )
        s.add(weekly)
        s.commit()

    return cleaner_id


class TestOnboardingEmail:
    """Tests for onboarding_email service."""

    def test_onboarding_preview_includes_language(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """Onboarding preview includes cleaner's language in URL.

        Mutation: Always use English regardless of cleaner preference at onboarding_email.py:61-62.
        Fails: assert "lang=es" in body
        """
        from app.api.routes.onboarding import onboarding_token
        from app.services.onboarding_email import onboarding_preview

        factory = session_factory(settings, LEGACY_WORKSPACE_ID)
        cleaner_id = uuid.uuid4()
        grant_id = uuid.uuid4()

        token = onboarding_token(grant_id, settings, LEGACY_WORKSPACE_ID)

        with factory() as s:
            cleaner = Cleaner(
                id=cleaner_id,
                created_at=clock.now(),
                updated_at=clock.now(),
                version=1,
                display_name="María García",
                phone_ciphertext=protect_text("+15551234568", settings.field_encryption_secret),
                email_ciphertext=protect_text("maria@example.com", settings.field_encryption_secret),
                phone_lookup_hmac=hashlib.sha256(f"lang:{cleaner_id}".encode()).hexdigest(),
                active=True,
                sms_consent_status="pending",
                default_work_intervals=[],
                eligibility_policy="all_except_exclusions",
                preferred_language="es",
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

            preview = onboarding_preview(s, settings, clock, cleaner_id, grant_id)
            body = str(preview.get("body", ""))

            assert "lang=es" in body, f"Spanish language should be in URL, got body: {body[:200]}"


class TestAvailabilityEmailQueue:
    """Tests for availability_email _queue function with real calls."""

    def test_active_cleaner_gets_availability_queued(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """Active cleaner with email gets availability outbox row via real _queue call.

        Mutation: Add Cleaner.active.is_(False) to _queue WHERE clause at availability_email.py:120.
        Fails: assert event is not None
        """
        workspace_id = _create_workspace_with_connection(settings, clock)

        clock_at_9am = FrozenClock(clock.now().astimezone(NY).replace(hour=9, minute=30).astimezone(NY))
        day = clock_at_9am.now().astimezone(NY).date() + timedelta(days=1)

        factory = session_factory(settings, workspace_id)

        active_cleaner_id = _create_cleaner(
            _migrator_session(), settings, clock, workspace_id, active=True, with_email=True, weekday=day.weekday()
        )

        test_settings = settings.model_copy(update={"dispatch_local_time": "20:00"})

        with factory() as s:
            _queue(s, test_settings, clock_at_9am, day)

        with factory() as s:
            event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "cleaner.availability_request",
                    OutboxEvent.aggregate_id == active_cleaner_id,
                )
            )
            assert event is not None, "Active cleaner should get availability email queued"

    def test_inactive_cleaner_does_not_get_availability_queued(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """Inactive cleaner does NOT get availability outbox row via real _queue call.

        Mutation: Remove both Cleaner.active checks at availability_email.py:90 AND :120.
        Fails: assert inactive_event is None
        """
        workspace_id = _create_workspace_with_connection(settings, clock)

        clock_at_9am = FrozenClock(clock.now().astimezone(NY).replace(hour=9, minute=30).astimezone(NY))
        day = clock_at_9am.now().astimezone(NY).date() + timedelta(days=1)

        factory = session_factory(settings, workspace_id)

        inactive_cleaner_id = _create_cleaner(
            _migrator_session(), settings, clock, workspace_id, active=False, with_email=True, weekday=day.weekday()
        )
        active_cleaner_id = _create_cleaner(
            _migrator_session(), settings, clock, workspace_id, active=True, with_email=True, weekday=day.weekday()
        )

        test_settings = settings.model_copy(update={"dispatch_local_time": "20:00"})

        with factory() as s:
            _queue(s, test_settings, clock_at_9am, day)

        with factory() as s:
            inactive_event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "cleaner.availability_request",
                    OutboxEvent.aggregate_id == inactive_cleaner_id,
                )
            )
            assert inactive_event is None, "Inactive cleaner should NOT get availability email queued"

            active_event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "cleaner.availability_request",
                    OutboxEvent.aggregate_id == active_cleaner_id,
                )
            )
            assert active_event is not None, "Active cleaner (positive control) should get availability email"

    def test_cleaner_without_email_excluded(self, session: Session, settings: Settings, clock: FrozenClock) -> None:
        """Cleaner without email does NOT get availability outbox row via real _queue call.

        Mutation: Remove both email_ciphertext checks at availability_email.py:91 AND :121.
        Fails: assert no_email_event is None
        """
        workspace_id = _create_workspace_with_connection(settings, clock)

        clock_at_9am = FrozenClock(clock.now().astimezone(NY).replace(hour=9, minute=30).astimezone(NY))
        day = clock_at_9am.now().astimezone(NY).date() + timedelta(days=1)

        factory = session_factory(settings, workspace_id)

        no_email_cleaner_id = _create_cleaner(
            _migrator_session(), settings, clock, workspace_id, active=True, with_email=False, weekday=day.weekday()
        )
        with_email_cleaner_id = _create_cleaner(
            _migrator_session(), settings, clock, workspace_id, active=True, with_email=True, weekday=day.weekday()
        )

        test_settings = settings.model_copy(update={"dispatch_local_time": "20:00"})

        with factory() as s:
            _queue(s, test_settings, clock_at_9am, day)

        with factory() as s:
            no_email_event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "cleaner.availability_request",
                    OutboxEvent.aggregate_id == no_email_cleaner_id,
                )
            )
            assert no_email_event is None, "Cleaner without email should NOT get availability email queued"

            with_email_event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "cleaner.availability_request",
                    OutboxEvent.aggregate_id == with_email_cleaner_id,
                )
            )
            assert with_email_event is not None, "Cleaner with email (positive control) should get availability email"

    def test_sms_opted_out_cleaner_still_gets_availability_queued(
        self, session: Session, settings: Settings, clock: FrozenClock
    ) -> None:
        """SMS-opted-out cleaner STILL gets availability email via real _queue call.

        CURRENT BEHAVIOR: _queue does NOT check sms_consent_status.
        This test pins that behavior as a product question - there is no email-unsubscribe
        concept in the app, so SMS opt-out status does not affect email delivery.

        If this behavior should change, update _queue to filter by sms_consent_status.
        Fails: assert event is not None
        """
        workspace_id = _create_workspace_with_connection(settings, clock)

        clock_at_9am = FrozenClock(clock.now().astimezone(NY).replace(hour=9, minute=30).astimezone(NY))
        day = clock_at_9am.now().astimezone(NY).date() + timedelta(days=1)

        factory = session_factory(settings, workspace_id)

        opted_out_cleaner_id = _create_cleaner(
            _migrator_session(),
            settings,
            clock,
            workspace_id,
            active=True,
            with_email=True,
            sms_opted_out=True,
            weekday=day.weekday(),
        )

        test_settings = settings.model_copy(update={"dispatch_local_time": "20:00"})

        with factory() as s:
            _queue(s, test_settings, clock_at_9am, day)

        with factory() as s:
            event = s.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.topic == "cleaner.availability_request",
                    OutboxEvent.aggregate_id == opted_out_cleaner_id,
                )
            )
            assert event is not None, "SMS-opted-out cleaner still gets availability email (current behavior)"


class TestEmailLanguage:
    """Tests for email language utilities."""

    def test_email_language_respects_preference(self, settings: Settings) -> None:
        """Email language should use cleaner's preference.

        Mutation: Always return English at cleaner_language.py.
        Fails: assert lang == "es"
        """
        from app.services.cleaner_language import email_language

        cleaner = type(
            "Cleaner",
            (),
            {"preferred_language": "es", "reading_languages": ["en"]},
        )()

        lang = email_language(cleaner)
        assert lang == "es", f"Should use cleaner's preferred language, got {lang}"

    def test_email_language_fallback_to_reading(self, settings: Settings) -> None:
        """Email language should fallback to reading_languages in priority order.

        Mutation: Return None instead of reading_languages at cleaner_language.py.
        Fails: assert lang == "es"
        """
        from app.services.cleaner_language import email_language

        cleaner = type(
            "Cleaner",
            (),
            {"preferred_language": None, "reading_languages": ["es", "pt"]},
        )()

        lang = email_language(cleaner)
        assert lang == "es", f"Should use first available reading language in priority order, got {lang}"

    def test_email_language_none_when_no_match(self, settings: Settings) -> None:
        """Email language returns None when no supported language found.

        Mutation: Default to 'en' instead of None at cleaner_language.py.
        Fails: assert lang is None
        """
        from app.services.cleaner_language import email_language

        cleaner = type(
            "Cleaner",
            (),
            {"preferred_language": None, "reading_languages": ["de", "fr"]},
        )()

        lang = email_language(cleaner)
        assert lang is None, f"Should return None for unsupported languages, got {lang}"
