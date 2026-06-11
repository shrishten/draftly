"""
Draftly – draft management & send service.

Responsibilities
----------------
- Create EmailDraft records from incoming email data + AI output
- Approve / edit / reject drafts
- Send approved drafts via Gmail with idempotency & retry logic
- Write AuditLog entries for every significant event
"""
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from googleapiclient.errors import HttpError

from app.models.models import EmailDraft, SentEmail, AuditLog, DraftStatus, User
from app.services import gmail_service, ai_service
from app.config import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()


# ── Audit helper ──────────────────────────────────────────────────────────────

async def _log(
    db: AsyncSession,
    event: str,
    user_id: int | None = None,
    draft_id: int | None = None,
    detail: str | None = None,
    level: str = "INFO",
) -> None:
    entry = AuditLog(
        user_id  = user_id,
        draft_id = draft_id,
        event    = event,
        detail   = detail,
        level    = level,
    )
    db.add(entry)
    await db.flush()


# ── Draft creation (fetch + generate) ────────────────────────────────────────

async def create_draft_for_email(
    user: User,
    message_id: str,
    tone_override: str | None,
    db: AsyncSession,
) -> EmailDraft:
    """
    1. Fetch full email from Gmail.
    2. Fetch user's sent emails for style sampling.
    3. Call AI to generate reply.
    4. Persist EmailDraft.
    """
    # 1 – Fetch the target email
    email_data = await gmail_service.fetch_full_email(user, message_id)

    # 2 – Fetch sent email samples for style matching
    sent_samples = await gmail_service.fetch_sent_emails(user)

    # 3 – Determine tone
    tone = tone_override or user.tone_preference

    # 4 – Generate AI draft
    draft_content = await ai_service.generate_draft(
        sender             = email_data["sender"],
        subject            = email_data["subject"],
        body               = email_data["body"],
        tone               = tone,
        signature          = user.signature,
        sent_email_samples = sent_samples,
    )

    # 5 – Persist draft
    draft = EmailDraft(
        user_id              = user.id,
        original_message_id  = email_data["message_id"],
        original_thread_id   = email_data["thread_id"],
        original_sender      = email_data["sender"],
        original_subject     = email_data["subject"],
        original_body        = email_data["body"],
        original_received_at = email_data.get("received_at"),
        draft_subject        = draft_content["subject"],
        draft_body           = draft_content["body"],
        tone_used            = tone,
        idempotency_key      = str(uuid.uuid4()),
    )
    db.add(draft)
    await db.flush()
    await db.refresh(draft)

    await _log(db, "draft_generated", user_id=user.id, draft_id=draft.id,
               detail=f"tone={tone}, message_id={message_id}")
    logger.info("Draft %d created for user %d (message_id=%s)", draft.id, user.id, message_id)
    return draft


# ── Review actions ────────────────────────────────────────────────────────────

async def approve_draft(
    draft: EmailDraft,
    edited_body: str | None,
    db: AsyncSession,
) -> EmailDraft:
    """Mark a draft as approved (optionally with an edited body)."""
    if draft.status not in (DraftStatus.PENDING, DraftStatus.REJECTED):
        raise ValueError(f"Cannot approve draft in status '{draft.status}'")

    if edited_body:
        draft.edited_body = edited_body
        draft.status      = DraftStatus.EDITED
        event             = "draft_edited_and_approved"
    else:
        draft.status = DraftStatus.APPROVED
        event        = "draft_approved"

    draft.approved_at = datetime.now(timezone.utc)
    await db.flush()
    await _log(db, event, user_id=draft.user_id, draft_id=draft.id)
    return draft


async def reject_draft(
    draft: EmailDraft,
    reason: str | None,
    db: AsyncSession,
) -> EmailDraft:
    """Mark a draft as rejected."""
    if draft.status == DraftStatus.SENT:
        raise ValueError("Cannot reject an already-sent draft.")

    draft.status     = DraftStatus.REJECTED
    draft.last_error = reason
    await db.flush()
    await _log(db, "draft_rejected", user_id=draft.user_id, draft_id=draft.id, detail=reason)
    return draft


# ── Send with retries ─────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(HttpError),
    reraise=True,
)
async def _send_with_retry(user: User, draft: EmailDraft) -> dict:
    """Attempt to send the draft via Gmail; retries up to 3 times on HttpError."""
    return await gmail_service.send_reply(
        user                    = user,
        to_address              = draft.original_sender,
        subject                 = draft.draft_subject,
        body                    = draft.final_body(),
        thread_id               = draft.original_thread_id,
        in_reply_to_message_id  = draft.original_message_id,
    )


async def send_draft(
    user: User,
    draft: EmailDraft,
    db: AsyncSession,
) -> SentEmail:
    """
    Send an approved/edited draft.
    - Guards against double-send via idempotency_key
    - Retries on transient Gmail errors
    - Logs success or failure
    """
    if draft.status not in (DraftStatus.APPROVED, DraftStatus.EDITED):
        raise ValueError(f"Draft must be approved before sending (current: {draft.status})")

    # Idempotency guard – check if already sent
    existing = await db.execute(
        select(SentEmail).where(SentEmail.draft_id == draft.id)
    )
    if existing.scalar_one_or_none():
        raise ValueError("Draft has already been sent (idempotency guard).")

    draft.send_attempts += 1

    try:
        result = await _send_with_retry(user, draft)
    except Exception as exc:
        draft.status     = DraftStatus.FAILED
        draft.last_error = str(exc)
        await db.flush()
        await _log(
            db, "draft_send_failed",
            user_id=draft.user_id, draft_id=draft.id,
            detail=str(exc), level="ERROR"
        )
        logger.error("Failed to send draft %d after retries: %s", draft.id, exc)
        raise

    # Mark draft as sent
    draft.status          = DraftStatus.SENT
    draft.sent_message_id = result["id"]
    draft.sent_at         = datetime.now(timezone.utc)

    # Persist SentEmail record
    sent_record = SentEmail(
        user_id          = user.id,
        draft_id         = draft.id,
        gmail_message_id = result["id"],
        thread_id        = result.get("threadId", draft.original_thread_id),
        to_address       = draft.original_sender,
        subject          = draft.draft_subject,
        body_snippet     = draft.final_body()[:300],
    )
    db.add(sent_record)
    await db.flush()
    await db.refresh(sent_record)

    await _log(
        db, "draft_sent",
        user_id=draft.user_id, draft_id=draft.id,
        detail=f"gmail_message_id={result['id']}"
    )
    logger.info("Draft %d sent successfully (Gmail msg ID: %s)", draft.id, result["id"])
    return sent_record


# ── Query helpers ─────────────────────────────────────────────────────────────

async def get_draft_or_404(draft_id: int, user: User, db: AsyncSession) -> EmailDraft:
    result = await db.execute(
        select(EmailDraft).where(
            EmailDraft.id == draft_id,
            EmailDraft.user_id == user.id,
        )
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Draft not found.")
    return draft
