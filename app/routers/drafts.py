"""
Draftly – Drafts router.

Endpoints
---------
POST /drafts/generate              → generate AI draft for an email
GET  /drafts                       → list all drafts for current user
GET  /drafts/{draft_id}            → get a single draft
POST /drafts/{draft_id}/approve    → approve (optionally with edits)
POST /drafts/{draft_id}/reject     → reject draft
POST /drafts/{draft_id}/send       → send an approved draft
DELETE /drafts/{draft_id}          → delete a pending/rejected draft
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.db.database import get_db
from app.models.models import User, EmailDraft, DraftStatus
from app.models.schemas import (
    GenerateDraftRequest,
    DraftResponse,
    DraftListResponse,
    ApproveDraftRequest,
    RejectDraftRequest,
    SendDraftResponse,
)
from app.services import draft_service
from app.utils.auth_deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/drafts", tags=["Drafts"])


@router.post(
    "/generate",
    response_model=DraftResponse,
    status_code=201,
    summary="Generate an AI reply draft for an email",
)
async def generate_draft(
    body:         GenerateDraftRequest,
    current_user: User          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
    """
    Fetches the specified Gmail message, analyses the user's writing style,
    and generates an AI reply draft using Claude.

    The draft is saved with status `pending` and must be approved before sending.
    """
    try:
        draft = await draft_service.create_draft_for_email(
            user           = current_user,
            message_id     = body.message_id,
            tone_override  = body.tone,
            db             = db,
        )
    except Exception as exc:
        logger.error("Draft generation failed for user %d: %s", current_user.id, exc)
        raise HTTPException(status_code=500, detail=f"Draft generation failed: {exc}")

    return DraftResponse.model_validate(draft)


@router.get(
    "",
    response_model=DraftListResponse,
    summary="List all drafts for the current user",
)
async def list_drafts(
    status:       str | None   = Query(default=None, description="Filter by status (pending, approved, sent, …)"),
    limit:        int          = Query(default=20, ge=1, le=100),
    offset:       int          = Query(default=0, ge=0),
    current_user: User         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """Returns all drafts belonging to the current user, optionally filtered by status."""
    query = select(EmailDraft).where(EmailDraft.user_id == current_user.id)

    if status:
        try:
            status_enum = DraftStatus(status)
            query = query.where(EmailDraft.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid values: {[s.value for s in DraftStatus]}"
            )

    query  = query.order_by(EmailDraft.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    drafts = result.scalars().all()

    return DraftListResponse(
        drafts=[DraftResponse.model_validate(d) for d in drafts],
        count=len(drafts),
    )


@router.get(
    "/{draft_id}",
    response_model=DraftResponse,
    summary="Get a single draft",
)
async def get_draft(
    draft_id:     int,
    current_user: User         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    draft = await draft_service.get_draft_or_404(draft_id, current_user, db)
    return DraftResponse.model_validate(draft)


@router.post(
    "/{draft_id}/approve",
    response_model=DraftResponse,
    summary="Approve a draft (optionally with edited body)",
)
async def approve_draft(
    draft_id:     int,
    body:         ApproveDraftRequest,
    current_user: User          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
    """
    Marks a draft as approved.
    If `edited_body` is provided, the user's edited version replaces the AI draft.
    Only approved/edited drafts can be sent.
    """
    draft = await draft_service.get_draft_or_404(draft_id, current_user, db)
    try:
        draft = await draft_service.approve_draft(
            draft       = draft,
            edited_body = body.edited_body,
            db          = db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return DraftResponse.model_validate(draft)


@router.post(
    "/{draft_id}/reject",
    response_model=DraftResponse,
    summary="Reject a draft",
)
async def reject_draft(
    draft_id:     int,
    body:         RejectDraftRequest,
    current_user: User          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
    """Marks a draft as rejected. Rejected drafts are kept for history but cannot be sent."""
    draft = await draft_service.get_draft_or_404(draft_id, current_user, db)
    try:
        draft = await draft_service.reject_draft(draft=draft, reason=body.reason, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return DraftResponse.model_validate(draft)


@router.post(
    "/{draft_id}/send",
    response_model=SendDraftResponse,
    summary="Send an approved draft via Gmail",
)
async def send_draft(
    draft_id:     int,
    current_user: User          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
    """
    Sends the approved/edited draft through Gmail.
    - Enforces idempotency (cannot send the same draft twice).
    - Retries up to 3 times on transient Gmail errors.
    - Maintains thread integrity using correct message IDs and headers.
    """
    draft = await draft_service.get_draft_or_404(draft_id, current_user, db)
    try:
        sent = await draft_service.send_draft(user=current_user, draft=draft, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to send email: {exc}")

    return SendDraftResponse(
        draft_id         = draft.id,
        gmail_message_id = sent.gmail_message_id,
        thread_id        = sent.thread_id,
        status           = draft.status,
        sent_at          = sent.sent_at,
    )


@router.delete(
    "/{draft_id}",
    status_code=204,
    summary="Delete a pending or rejected draft",
)
async def delete_draft(
    draft_id:     int,
    current_user: User          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
    """Permanently deletes a draft. Only pending or rejected drafts can be deleted."""
    draft = await draft_service.get_draft_or_404(draft_id, current_user, db)

    if draft.status in (DraftStatus.SENT, DraftStatus.APPROVED, DraftStatus.EDITED):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete a draft with status '{draft.status}'. Only pending/rejected drafts can be deleted."
        )

    await db.execute(delete(EmailDraft).where(EmailDraft.id == draft_id))
    logger.info("Draft %d deleted by user %d", draft_id, current_user.id)
