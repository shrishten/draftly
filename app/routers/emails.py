"""
Draftly – Emails router.

Endpoints
---------
GET  /emails/inbox          → fetch unread emails from Gmail
GET  /emails/{message_id}   → fetch a single email in full
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from googleapiclient.errors import HttpError

from app.db.database import get_db
from app.models.models import User
from app.models.schemas import FetchEmailsResponse, IncomingEmailSummary
from app.services import gmail_service
from app.utils.auth_deps import get_current_user
from app.config import get_settings

logger   = logging.getLogger(__name__)
router   = APIRouter(prefix="/emails", tags=["Emails"])
settings = get_settings()


@router.get(
    "/inbox",
    response_model=FetchEmailsResponse,
    summary="Fetch unread emails from Gmail inbox",
)
async def get_inbox(
    max_results:  int  = Query(default=10, ge=1, le=50, description="Max emails to return"),
    current_user: User = Depends(get_current_user),
):
    """
    Returns a list of unread emails from the authenticated user's Gmail inbox.
    Each item includes sender, subject, snippet, thread ID, and timestamp.
    """
    try:
        emails = await gmail_service.fetch_unread_emails(
            user        = current_user,
            max_results = max_results,
        )
    except HttpError as exc:
        logger.error("Gmail API error for user %d: %s", current_user.id, exc)
        if exc.resp.status == 401:
            raise HTTPException(status_code=401, detail="Gmail token expired. Please re-authenticate.")
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    return FetchEmailsResponse(emails=emails, count=len(emails))


@router.get(
    "/{message_id}",
    summary="Fetch a single email by Gmail message ID",
)
async def get_email(
    message_id:   str,
    current_user: User = Depends(get_current_user),
):
    """
    Fetches the full content of a single Gmail message by its message ID.
    """
    try:
        email_data = await gmail_service.fetch_full_email(
            user       = current_user,
            message_id = message_id,
        )
    except HttpError as exc:
        if exc.resp.status == 404:
            raise HTTPException(status_code=404, detail="Email not found.")
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}")

    return email_data
