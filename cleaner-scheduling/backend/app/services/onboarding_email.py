from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock
from app.config import Settings
from app.domain.errors import AppError
from app.models import Cleaner, CleanerAccessLink, EmailDelivery
from app.services.audit import write_audit
from app.services.cleaner_language import email_language, local_date, localized_email
from app.services.email_addresses import normalize_email
from app.services.field_cipher import protect_text, reveal_text
from app.services.gmail import read_credentials
from app.services.workspace_lock import SCHEDULE_PUBLICATION_LOCK, acquire_workspace_lock, get_workspace_id
from app.workspaces import LEGACY_WORKSPACE_ID


def _recipient(cleaner: Cleaner, settings: Settings) -> str:
    if not cleaner.active or not cleaner.email_ciphertext:
        raise AppError("cleaner_email_missing", "Save an email for every active cleaner before finishing setup.", 409)
    try:
        address = normalize_email(reveal_text(cleaner.email_ciphertext, settings.field_encryption_secret))
    except ValueError:
        raise AppError(
            "cleaner_email_invalid", "Save a valid email for every active cleaner before finishing setup.", 409
        ) from None
    if address is None:
        raise AppError("cleaner_email_missing", "Save an email for every active cleaner before finishing setup.", 409)
    return address


def onboarding_preview(
    session: Session, settings: Settings, clock: Clock, cleaner_id: uuid.UUID, grant_id: uuid.UUID | None
) -> dict[str, object]:
    from app.api.routes.onboarding import onboarding_token

    cleaner = session.scalar(select(Cleaner).where(Cleaner.id == cleaner_id).with_for_update())
    grant = session.scalar(select(CleanerAccessLink).where(CleanerAccessLink.id == grant_id).with_for_update())
    if cleaner is None:
        raise AppError("cleaner_unavailable", "The invited cleaner is no longer available.", 409)
    recipient = _recipient(cleaner, settings)
    if (
        grant is None
        or grant.cleaner_id != cleaner_id
        or grant.purpose != "onboarding"
        or grant.revoked_at is not None
        or grant.expires_at <= clock.now() + timedelta(hours=1)
    ):
        raise AppError("onboarding_link_unavailable", "The onboarding invitation needs a fresh link.", 409)
    token = onboarding_token(grant.id, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID))
    if not hmac.compare_digest(grant.token_hash, hashlib.sha256(token.encode()).hexdigest()):
        raise AppError("onboarding_link_unavailable", "The onboarding invitation needs a fresh link.", 409)
    language = email_language(cleaner)
    query = f"?lang={language}" if language in {"es", "pt"} else ""
    url = settings.cleaner_portal_base_url.rstrip("/") + f"/onboarding{query}#" + token
    expires = grant.expires_at.astimezone(ZoneInfo("America/New_York")).strftime("%B %d, %Y at %I:%M %p ET")
    preview: dict[str, object] = {
        "cleaner_id": str(cleaner.id),
        "grant_id": str(grant.id),
        "expires_at": grant.expires_at.isoformat(),
        "recipient": recipient,
        "subject": "Your Turnover onboarding link",
        "body": (
            f"Hi {cleaner.display_name},\n\n"
            "Your property manager invited you to Turnover. "
            "Set your working hours, breaks and starting address here:\n\n"
            f"{url}\n\nThis private link expires {expires}. Do not forward it.\n"
        ),
    }
    preview["subject"], preview["body"] = localized_email(
        cleaner,
        "onboarding",
        str(preview["subject"]),
        str(preview["body"]),
        {"url": url, "expires": local_date(grant.expires_at)},
    )
    preview["fingerprint"] = hmac.new(
        settings.field_encryption_secret.encode(), json.dumps(preview, sort_keys=True).encode(), hashlib.sha256
    ).hexdigest()
    return preview


def onboarding_email_status(session: Session) -> dict[str, int]:
    counts = dict.fromkeys(("queued", "sending", "accepted", "uncertain", "failed", "stale"), 0)
    for status, count in session.execute(
        select(EmailDelivery.status, func.count())
        .where(EmailDelivery.kind == "onboarding")
        .group_by(EmailDelivery.status)
    ):
        counts[status] = count
    counts["total"] = sum(counts.values())
    return counts


def queue_onboarding_emails(session: Session, settings: Settings, clock: Clock) -> dict[str, int]:
    from app.api.routes.onboarding import onboarding_token

    if (
        not settings.gmail_onboarding_send_enabled
        or not settings.hospitable_read_only
        or not settings.workspace_signup_enabled
    ):
        raise AppError(
            "onboarding_email_disabled", "Onboarding email delivery must be enabled before finishing setup.", 409
        )
    try:
        read_credentials(settings.gmail_credentials_file)
    except (OSError, ValueError):
        raise AppError(
            "onboarding_email_unavailable", "Configure the private email sender before finishing setup.", 409
        ) from None
    acquire_workspace_lock(session, get_workspace_id(session), SCHEDULE_PUBLICATION_LOCK)
    cleaners = list(
        session.scalars(select(Cleaner).where(Cleaner.active.is_(True)).order_by(Cleaner.id).with_for_update())
    )
    for cleaner in cleaners:
        _recipient(cleaner, settings)
    now = clock.now()
    for cleaner in cleaners:
        key = f"onboarding-initial:{cleaner.id}"
        existing = session.scalar(select(EmailDelivery).where(EmailDelivery.message_key == key).with_for_update())
        if existing is not None:
            continue
        grants = list(
            session.scalars(
                select(CleanerAccessLink)
                .where(
                    CleanerAccessLink.cleaner_id == cleaner.id,
                    CleanerAccessLink.purpose == "onboarding",
                    CleanerAccessLink.revoked_at.is_(None),
                )
                .order_by(CleanerAccessLink.expires_at.desc())
                .with_for_update()
            )
        )
        grant = next((row for row in grants if row.expires_at > now + timedelta(hours=24)), None)
        if grant is None:
            for prior in grants:
                prior.revoked_at = now
            grant_id = uuid.uuid4()
            token = onboarding_token(grant_id, settings, session.info.get("workspace_id", LEGACY_WORKSPACE_ID))
            grant = CleanerAccessLink(
                id=grant_id,
                created_at=now,
                cleaner_id=cleaner.id,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=now + timedelta(days=7),
                revoked_at=None,
                purpose="onboarding",
            )
            session.add(grant)
            session.flush()
        preview = onboarding_preview(session, settings, clock, cleaner.id, grant.id)
        delivery = EmailDelivery(
            id=uuid.uuid4(),
            created_at=now,
            updated_at=now,
            version=1,
            cleaner_id=cleaner.id,
            cleaner_schedule_version_id=None,
            kind="onboarding",
            offer_id=None,
            onboarding_grant_id=grant.id,
            service_date=now.astimezone(ZoneInfo("America/New_York")).date(),
            message_key=key,
            fingerprint=str(preview["fingerprint"]),
            snapshot_ciphertext=protect_text(json.dumps(preview, sort_keys=True), settings.field_encryption_secret),
            status="queued",
            attempt_count=0,
            next_attempt_at=now,
            claimed_at=None,
            accepted_at=None,
            error_code=None,
        )
        session.add(delivery)
        write_audit(
            session,
            clock=clock,
            actor_type="system",
            actor_subject="onboarding-email",
            action="email.onboarding_queued",
            entity_type="email_delivery",
            entity_id=delivery.id,
            correlation_id=delivery.id,
            reason=None,
            redacted_changes={"cleaner_id": str(cleaner.id), "grant_id": str(grant.id)},
            request_id=None,
        )
    session.flush()
    return onboarding_email_status(session)


def drain_onboarding_emails(factory: sessionmaker[Session], settings: Settings, clock: Clock) -> dict[str, int]:
    from app.services.email_automation import _drain

    if (
        not settings.gmail_onboarding_send_enabled
        or not settings.hospitable_read_only
        or not settings.workspace_signup_enabled
    ):
        return {"disabled": 1}
    try:
        read_credentials(settings.gmail_credentials_file)
    except (OSError, ValueError):
        return {"disabled": 1}
    return _drain(factory, settings, clock, kinds=("onboarding",))
