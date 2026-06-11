"""
Draftly – Users router.

Endpoints
---------
GET   /users/me              → current user profile
PATCH /users/me/preferences  → update tone + signature
GET   /users/me/logs         → audit log for current user
"""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.models.models import User, AuditLog
from app.models.schemas import (
    UserResponse,
    UserPreferencesUpdate,
    AuditLogListResponse,
    AuditLogResponse,
)
from app.utils.auth_deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserResponse, summary="Get current user profile")
async def get_profile(current_user: User = Depends(get_current_user)):
    """Returns the authenticated user's profile and preferences."""
    return UserResponse.model_validate(current_user)


@router.patch(
    "/me/preferences",
    response_model=UserResponse,
    summary="Update tone preference and/or signature",
)
async def update_preferences(
    body:         UserPreferencesUpdate,
    current_user: User          = Depends(get_current_user),
    db:           AsyncSession  = Depends(get_db),
):
    """
    Update the user's writing preferences:
    - `tone_preference`: formal | concise | friendly | auto
    - `signature`: text appended to every generated draft
    """
    if body.tone_preference is not None:
        current_user.tone_preference = body.tone_preference
    if body.signature is not None:
        current_user.signature = body.signature

    await db.flush()
    await db.refresh(current_user)
    logger.info("Preferences updated for user %d", current_user.id)
    return UserResponse.model_validate(current_user)


@router.get(
    "/me/logs",
    response_model=AuditLogListResponse,
    summary="Get audit log entries for current user",
)
async def get_logs(
    limit:        int          = Query(default=50, ge=1, le=200),
    offset:       int          = Query(default=0, ge=0),
    current_user: User         = Depends(get_current_user),
    db:           AsyncSession = Depends(get_db),
):
    """
    Returns chronological audit log entries for the current user.
    Useful for monitoring draft activity and debugging.
    """
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.user_id == current_user.id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    logs = result.scalars().all()
    return AuditLogListResponse(
        logs  = [AuditLogResponse.model_validate(l) for l in logs],
        count = len(logs),
    )
