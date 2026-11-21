from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AccountBase(DeclarativeBase):
    pass


class ManagerAccount(AccountBase):
    __tablename__ = "manager_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signup_key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    signup_request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    deletion_key_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    deletion_request_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    deletion_session_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    mfa_pending_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    mfa_pending_session_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_pending_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mfa_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mfa_recovery_hashes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))


class ManagerSession(AccountBase):
    __tablename__ = "manager_sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    login_key_hash: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    login_request_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
