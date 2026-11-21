from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.domain.errors import AppError
from app.models import Property, PropertyInstruction
from app.services.field_cipher import protect_text
from app.services.schedule_freshness import bump_property

PUBLIC_FIELDS = {"parking", "notes", "wifi_network"}
SECRET_FIELDS = {"access_code", "entry_code", "door_code"}
ALLOWED_FIELDS = PUBLIC_FIELDS | SECRET_FIELDS


def latest_instruction(session: Session, property_id: uuid.UUID) -> PropertyInstruction | None:
    return session.scalar(
        select(PropertyInstruction)
        .where(PropertyInstruction.property_id == property_id)
        .order_by(PropertyInstruction.instruction_version.desc())
        .limit(1)
    )


def effective_public(instruction: PropertyInstruction) -> dict[str, object]:
    source = dict(instruction.source_fields)
    overrides = dict(instruction.local_overrides)
    merged = dict(source)
    merged.update(overrides)
    return merged


def apply_source_fields(
    session: Session,
    *,
    clock: Clock,
    prop: Property,
    fields: dict[str, object],
    source_version: str | None,
    author_subject: str,
    reason: str,
    key_material: str,
) -> PropertyInstruction:
    _reject_unknown(fields)
    public, secret = _split(fields)
    current = latest_instruction(session, prop.id)
    local_overrides = {} if current is None else dict(current.local_overrides)
    local_secret = None if current is None else current.local_secret_ciphertext
    return _append(
        session,
        clock=clock,
        prop=prop,
        source_fields=public,
        local_overrides=local_overrides,
        source_secret=_protect_secret(secret, key_material),
        local_secret=local_secret,
        source_version=source_version,
        author_subject=author_subject,
        reason=reason,
        key_material=key_material,
        supersedes=None if current is None else current.id,
        version=(1 if current is None else current.instruction_version + 1),
    )


def apply_manager_edit(
    session: Session,
    *,
    clock: Clock,
    prop: Property,
    expected_version: int,
    local_overrides: dict[str, object],
    remove_fields: list[str],
    reason: str,
    author_subject: str,
    key_material: str,
) -> PropertyInstruction:
    if not reason.strip():
        raise AppError("validation_error", "A reason is required.", 422)
    overlap = set(local_overrides).intersection(remove_fields)
    if overlap:
        raise AppError("validation_error", "A field cannot be both set and removed.", 422)
    _reject_unknown({**local_overrides, **dict.fromkeys(remove_fields, "")})
    current = latest_instruction(session, prop.id)
    if current is None or current.instruction_version != expected_version:
        raise AppError("stale_version", "Instruction version is stale.", 409)
    public_overrides, secret_overrides = _split(local_overrides)
    merged_public = dict(current.local_overrides)
    merged_public.update(public_overrides)
    for field_name in remove_fields:
        merged_public.pop(field_name, None)
    local_secret = current.local_secret_ciphertext
    if secret_overrides:
        local_secret = _protect_secret(secret_overrides, key_material)
    if any(field_name in SECRET_FIELDS for field_name in remove_fields):
        local_secret = None
    return _append(
        session,
        clock=clock,
        prop=prop,
        source_fields=dict(current.source_fields),
        local_overrides=merged_public,
        source_secret=current.source_secret_ciphertext,
        local_secret=local_secret,
        source_version=current.source_version,
        author_subject=author_subject,
        reason=reason.strip(),
        key_material=key_material,
        supersedes=current.id,
        version=current.instruction_version + 1,
    )


def serialize_instruction(instruction: PropertyInstruction) -> dict[str, object]:
    return {
        "property_id": str(instruction.property_id),
        "instruction_version": instruction.instruction_version,
        "source_fields": instruction.source_fields,
        "local_overrides": instruction.local_overrides,
        "effective_fields": effective_public(instruction),
        "provenance": {
            "source_version": instruction.source_version,
            "author_subject": instruction.author_subject,
            "change_reason": instruction.change_reason,
        },
    }


def _append(
    session: Session,
    *,
    clock: Clock,
    prop: Property,
    source_fields: dict[str, object],
    local_overrides: dict[str, object],
    source_secret: bytes | None,
    local_secret: bytes | None,
    source_version: str | None,
    author_subject: str,
    reason: str,
    key_material: str,
    supersedes: uuid.UUID | None,
    version: int,
) -> PropertyInstruction:
    effective = dict(source_fields)
    effective.update(local_overrides)
    encoded = json.dumps(effective, sort_keys=True, separators=(",", ":")).encode("utf-8")
    row = PropertyInstruction(
        id=uuid.uuid4(),
        created_at=clock.now(),
        property_id=prop.id,
        source_version=source_version,
        source_fields=source_fields,
        local_overrides=local_overrides,
        source_secret_ciphertext=source_secret,
        local_secret_ciphertext=local_secret,
        effective_encrypted=protect_text(encoded.decode("utf-8"), key_material),
        effective_digest=hashlib.sha256(encoded).hexdigest(),
        instruction_version=version,
        supersedes_id=supersedes,
        author_subject=author_subject,
        change_reason=reason,
    )
    session.add(row)
    session.flush()
    bump_property(session, prop.id, clock.now())
    return row


def _split(fields: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    public = {key: value for key, value in fields.items() if key in PUBLIC_FIELDS}
    secret = {key: value for key, value in fields.items() if key in SECRET_FIELDS}
    return public, secret


def _protect_secret(secret: dict[str, object], key_material: str) -> bytes | None:
    if not secret:
        return None
    for value in secret.values():
        text = str(value)
        if "synthetic" not in text:
            raise AppError("validation_error", "Only synthetic access text can be stored in M2.", 422)
    return protect_text(json.dumps(secret, sort_keys=True), key_material)


def _reject_unknown(fields: dict[str, object]) -> None:
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise AppError("validation_error", "Instruction field is not supported.", 422, {"fields": sorted(unknown)})
