"""
Draftly – ORM models (User, EmailDraft, SentEmail, AuditLog).
"""
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    String, Text, DateTime, Enum, ForeignKey, Boolean, Integer
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ────────────────────────────────────────────────────────────────────

class DraftStatus(str, enum.Enum):
    PENDING   = "pending"    # AI-generated, awaiting review
    APPROVED  = "approved"   # User approved
    EDITED    = "edited"     # User edited then approved
    REJECTED  = "rejected"   # User rejected
    SENT      = "sent"       # Successfully sent via Gmail
    FAILED    = "failed"     # Send failed after retries


class TonePreference(str, enum.Enum):
    FORMAL    = "formal"
    CONCISE   = "concise"
    FRIENDLY  = "friendly"
    AUTO      = "auto"       # Infer from user's sent emails


# ── Models ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    # Encrypted OAuth2 tokens (stored as cipher-text)
    encrypted_access_token:  Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expiry:             Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Preferences
    tone_preference: Mapped[str] = mapped_column(
        Enum(TonePreference), default=TonePreference.AUTO
    )
    signature:       Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    drafts:      Mapped[list["EmailDraft"]] = relationship(back_populates="user")
    sent_emails: Mapped[list["SentEmail"]]  = relationship(back_populates="user")
    audit_logs:  Mapped[list["AuditLog"]]   = relationship(back_populates="user")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


class EmailDraft(Base):
    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # Original incoming email metadata
    original_message_id: Mapped[str]       = mapped_column(String(255), index=True)
    original_thread_id:  Mapped[str]       = mapped_column(String(255))
    original_sender:     Mapped[str]       = mapped_column(String(255))
    original_subject:    Mapped[str]       = mapped_column(String(500))
    original_body:       Mapped[str]       = mapped_column(Text)
    original_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # AI-generated draft
    draft_subject: Mapped[str]       = mapped_column(String(500))
    draft_body:    Mapped[str]       = mapped_column(Text)
    tone_used:     Mapped[str]       = mapped_column(String(50), default=TonePreference.AUTO)

    # User modifications
    edited_body:    Mapped[str | None] = mapped_column(Text)   # set if user edits
    status:         Mapped[str]        = mapped_column(
        Enum(DraftStatus), default=DraftStatus.PENDING, index=True
    )

    # Send tracking
    idempotency_key:  Mapped[str | None] = mapped_column(String(255), unique=True)
    send_attempts:    Mapped[int]        = mapped_column(Integer, default=0)
    last_error:       Mapped[str | None] = mapped_column(Text)
    sent_message_id:  Mapped[str | None] = mapped_column(String(255))  # Gmail message ID after send

    created_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="drafts")

    def final_body(self) -> str:
        """Return edited body if available, otherwise original AI draft."""
        return self.edited_body if self.edited_body else self.draft_body

    def __repr__(self) -> str:
        return f"<EmailDraft id={self.id} status={self.status}>"


class SentEmail(Base):
    """Record of every email successfully sent through Draftly."""
    __tablename__ = "sent_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:          Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    draft_id:         Mapped[int] = mapped_column(ForeignKey("email_drafts.id"), nullable=False)
    gmail_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    thread_id:        Mapped[str] = mapped_column(String(255))
    to_address:       Mapped[str] = mapped_column(String(255))
    subject:          Mapped[str] = mapped_column(String(500))
    body_snippet:     Mapped[str] = mapped_column(Text)   # first 300 chars for quick preview
    sent_at:          Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="sent_emails")

    def __repr__(self) -> str:
        return f"<SentEmail id={self.id} gmail_id={self.gmail_message_id}>"


class AuditLog(Base):
    """Immutable event log for monitoring and debugging."""
    __tablename__ = "audit_logs"

    id:         Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:    Mapped[int | None]    = mapped_column(ForeignKey("users.id"), index=True)
    draft_id:   Mapped[int | None]    = mapped_column(Integer)
    event:      Mapped[str]           = mapped_column(String(100))    # e.g. "draft_generated"
    detail:     Mapped[str | None]    = mapped_column(Text)
    level:      Mapped[str]           = mapped_column(String(20), default="INFO")
    created_at: Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped["User | None"] = relationship(back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} event={self.event}>"
